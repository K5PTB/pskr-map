import re as _re
import time
import aiosqlite
from .models import Spot

# Matches 4-, 6-, or 8-character Maidenhead grid squares (public for main.py)
GRID_RE = _re.compile(
    r'^[A-Ra-r]{2}([0-9]{2}([A-Xa-x]{2}([0-9]{2})?)?)?$'
)

_CREATE = """
CREATE TABLE IF NOT EXISTS spots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seq_num     INTEGER NOT NULL,
    freq        INTEGER NOT NULL,
    mode        TEXT    NOT NULL,
    snr         INTEGER NOT NULL,
    timestamp   INTEGER NOT NULL,
    tx_call     TEXT    NOT NULL,
    tx_grid     TEXT,
    rx_call     TEXT    NOT NULL,
    rx_grid     TEXT,
    tx_dxcc     INTEGER,
    rx_dxcc     INTEGER,
    band        TEXT    NOT NULL,
    received_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_seq     ON spots(seq_num);
CREATE        INDEX IF NOT EXISTS idx_ts      ON spots(timestamp);
CREATE        INDEX IF NOT EXISTS idx_mode    ON spots(mode);
CREATE        INDEX IF NOT EXISTS idx_band    ON spots(band);
"""

_COLS = (
    "seq_num", "freq", "mode", "snr", "timestamp",
    "tx_call", "tx_grid", "rx_call", "rx_grid",
    "tx_dxcc", "rx_dxcc", "band",
)

# Maps DB column names → wire field names (same as Spot.to_wire())
_COL_TO_WIRE = {
    "seq_num": "seq", "timestamp": "ts",
}


async def init_db(path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.executescript(_CREATE)
    await db.commit()
    return db


async def insert_spot(db: aiosqlite.Connection, spot: Spot) -> None:
    await db.execute(
        """INSERT OR IGNORE INTO spots
           (seq_num, freq, mode, snr, timestamp,
            tx_call, tx_grid, rx_call, rx_grid,
            tx_dxcc, rx_dxcc, band, received_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (spot.sq, spot.f, spot.md, spot.rp, spot.t,
         spot.sc, spot.sl, spot.rc, spot.rl,
         spot.sa, spot.ra, spot.b, int(time.time())),
    )
    await db.commit()


async def query_spots(
    db: aiosqlite.Connection,
    bands: list[str],
    modes: list[str],
    max_age_min: int,
    call_type: str = "",   # "tx", "rx", or ""
    call_value: str = "",  # callsign or grid prefix
) -> list[dict]:
    # Empty list means nothing selected — return nothing rather than bypassing the filter
    if not bands or not modes:
        return []

    cutoff = int(time.time()) - max_age_min * 60
    params: list = [cutoff]
    where = ["timestamp >= ?"]

    where.append(f"band IN ({','.join('?'*len(bands))})")
    params.extend(bands)
    where.append(f"mode IN ({','.join('?'*len(modes))})")
    params.extend(modes)

    val = call_value.strip()
    if call_type in ("tx", "rx") and val:
        if GRID_RE.match(val):
            # Prefix-match: "FN31" matches FN31, FN31pr, FN31pr23
            col = "tx_grid" if call_type == "tx" else "rx_grid"
            where.append(f"{col} LIKE ?")
            params.append(val.upper() + "%")
        else:
            col = "tx_call" if call_type == "tx" else "rx_call"
            where.append(f"UPPER({col}) = ?")
            params.append(val.upper())

    sql = f"""
        SELECT {', '.join(_COLS)}
        FROM spots
        WHERE {' AND '.join(where)}
        ORDER BY timestamp DESC
        LIMIT 10000
    """
    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()
    return [
        {_COL_TO_WIRE.get(k, k): v for k, v in zip(_COLS, r)}
        for r in rows
    ]


async def query_monitors(
    db: aiosqlite.Connection,
    bands: list[str],
    modes: list[str],
    max_age_min: int,
    call_value: str = "",   # optional: grid prefix (LIKE) or exact rx_call
) -> list[dict]:
    """Return one spot per unique RX station — the most recent within the window."""
    if not bands or not modes:
        return []
    cutoff = int(time.time()) - max_age_min * 60
    b_ph = ','.join('?' * len(bands))
    m_ph = ','.join('?' * len(modes))
    cols = ', '.join(f's.{c}' for c in _COLS)

    inner_where = ["timestamp >= ?", f"band IN ({b_ph})", f"mode IN ({m_ph})", "rx_grid IS NOT NULL"]
    params: list = [cutoff] + list(bands) + list(modes)

    val = call_value.strip().upper()
    if val:
        if GRID_RE.match(val):
            inner_where.append("rx_grid LIKE ?")
            params.append(val + "%")
        else:
            inner_where.append("UPPER(rx_call) = ?")
            params.append(val)

    sql = f"""
        SELECT {cols}
        FROM spots s
        JOIN (
            SELECT rx_call, MAX(timestamp) AS max_ts
            FROM spots
            WHERE {' AND '.join(inner_where)}
            GROUP BY rx_call
        ) m ON s.rx_call = m.rx_call AND s.timestamp = m.max_ts
        WHERE s.rx_grid IS NOT NULL
        GROUP BY s.rx_call
        LIMIT 5000
    """
    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()
    return [
        {_COL_TO_WIRE.get(k, k): v for k, v in zip(_COLS, r)}
        for r in rows
    ]


async def get_stats(db: aiosqlite.Connection) -> dict:
    async with db.execute("SELECT COUNT(*) FROM spots") as cur:
        (total,) = await cur.fetchone()
    cutoff = int(time.time()) - 60
    async with db.execute(
        "SELECT COUNT(*) FROM spots WHERE received_at >= ?", (cutoff,)
    ) as cur:
        (per_min,) = await cur.fetchone()
    return {"db_total": total, "spots_last_min": per_min}


async def prune_spots(db: aiosqlite.Connection, ttl_minutes: int) -> int:
    cutoff = int(time.time()) - ttl_minutes * 60
    async with db.execute(
        "DELETE FROM spots WHERE received_at < ?", (cutoff,)
    ) as cur:
        deleted = cur.rowcount
    await db.commit()
    return deleted
