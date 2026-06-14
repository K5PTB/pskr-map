import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

_STATE_FILE = Path("pskr_state.json")

def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log.warning("Could not save state: %s", e)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .database import init_db, query_spots, query_monitors, get_stats, prune_spots, GRID_RE
from .models import Spot
from .mqtt_client import MqttManager

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

cfg = load_config("config.toml")

# --- shared state ------------------------------------------------------------

_clients: set[WebSocket] = set()

# Display filter: what the SQL query uses (pure client-side concern)
_current_filter = {
    "bands": list(cfg.defaults.bands),
    "modes": list(cfg.defaults.modes),
    "display_max_age_minutes": cfg.defaults.display_max_age_minutes,
    "call_type": "",
    "call_value": "",
}

# Feed filter: what MQTT topics we are subscribed to, plus DB TTL
_current_feed = {
    "bands": list(cfg.defaults.bands),
    "modes": list(cfg.defaults.modes),
    "ttl_minutes": cfg.database.ttl_minutes,
    "call_value": "",
}

_saved = _load_state()
if "feed" in _saved:
    _current_feed.update(_saved["feed"])


async def _do_query(db, f: dict) -> list:
    """Route to the right query based on call_type."""
    if f["call_type"] == "monitors":
        return await query_monitors(
            db, f["bands"], f["modes"], f["display_max_age_minutes"],
            f["call_value"],
        )
    return await query_spots(
        db, f["bands"], f["modes"], f["display_max_age_minutes"],
        f["call_type"], f["call_value"],
    )


async def _broadcast(msg: dict) -> None:
    if not _clients:
        return
    data = json.dumps(msg)
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


async def _on_spot(spot: Spot) -> None:
    f = _current_filter
    if not f["bands"] or spot.b not in f["bands"]:
        return
    if not f["modes"] or spot.md not in f["modes"]:
        return
    call_type  = f["call_type"]
    call_value = f["call_value"].strip().upper()
    if call_type in ("tx", "rx") and call_value:
        if GRID_RE.match(call_value):
            grid = (spot.sl if call_type == "tx" else spot.rl) or ""
            if not grid.upper().startswith(call_value):
                return
        else:
            call = (spot.sc if call_type == "tx" else spot.rc) or ""
            if call.upper() != call_value:
                return
    elif call_type == "monitors" and call_value:
        if GRID_RE.match(call_value):
            if not (spot.rl or "").upper().startswith(call_value):
                return
        else:
            if (spot.rc or "").upper() != call_value:
                return
    await _broadcast({"type": "spot", "data": spot.to_wire()})


# --- background tasks --------------------------------------------------------

async def _prune_loop(db):
    while True:
        await asyncio.sleep(300)
        deleted = await prune_spots(db, cfg.database.ttl_minutes)
        if deleted:
            log.info("Pruned %d expired spots", deleted)


async def _stats_loop(db):
    while True:
        await asyncio.sleep(1)
        stats = await get_stats(db)
        await _broadcast({"type": "stats", "data": stats})


# --- lifespan ----------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db = await init_db(cfg.database.path)
    app.state.db = db

    mqtt = MqttManager(cfg.broker.host, cfg.broker.port, cfg.broker.use_tls)
    app.state.mqtt = mqtt

    tasks = [
        asyncio.create_task(
            mqtt.run(db, _on_spot, _current_feed["bands"], _current_feed["modes"],
                     _current_feed.get("call_value", ""))
        ),
        asyncio.create_task(_prune_loop(db)),
        asyncio.create_task(_stats_loop(db)),
    ]

    yield

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await db.close()


app = FastAPI(lifespan=lifespan)


# --- WebSocket endpoint ------------------------------------------------------

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    db = websocket.app.state.db
    mqtt: MqttManager = websocket.app.state.mqtt

    # Bootstrap new client with current state
    await websocket.send_text(json.dumps({"type": "filter_ack", "filter": _current_filter}))
    await websocket.send_text(json.dumps({"type": "feed_ack",   "feed":   _current_feed}))

    spots = await _do_query(db, _current_filter)
    await websocket.send_text(json.dumps({"type": "spot_batch", "data": spots}))

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg.get("type") == "set_filter":
                # Display-only: re-query SQLite, no MQTT change
                _current_filter["bands"]  = msg.get("bands", [])
                _current_filter["modes"]  = msg.get("modes", [])
                _current_filter["display_max_age_minutes"] = int(
                    msg.get("display_max_age_minutes", 30)
                )
                _current_filter["call_type"]  = msg.get("call_type", "")
                _current_filter["call_value"] = msg.get("call_value", "")
                spots = await _do_query(db, _current_filter)
                await _broadcast({"type": "spot_batch", "data": spots})
                await _broadcast({"type": "filter_ack", "filter": _current_filter})

            elif msg.get("type") == "set_feed":
                # Feed: update MQTT subscription and/or TTL, re-query SQLite
                _current_feed["bands"] = msg.get("bands", [])
                _current_feed["modes"] = msg.get("modes", [])
                _current_feed["call_value"] = msg.get("call_value", "")
                if ttl := msg.get("ttl_minutes"):
                    cfg.database.ttl_minutes = int(ttl)
                    _current_feed["ttl_minutes"] = cfg.database.ttl_minutes
                mqtt.set_filter(_current_feed["bands"], _current_feed["modes"],
                               _current_feed.get("call_value", ""))
                _save_state({"feed": _current_feed})
                spots = await _do_query(db, _current_filter)
                await _broadcast({"type": "spot_batch", "data": spots})
                await _broadcast({"type": "feed_ack", "feed": _current_feed})

            elif msg.get("type") == "get_spots":
                spots = await _do_query(db, _current_filter)
                await websocket.send_text(json.dumps({"type": "spot_batch", "data": spots}))

    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)


# --- static files (must be last) ---------------------------------------------

_frontend = Path(__file__).parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="static")
