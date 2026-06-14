import asyncio
import logging
import re
import ssl
from collections.abc import Callable, Awaitable
from typing import Optional

import aiomqtt
import aiosqlite

from .database import insert_spot
from .models import Spot

log = logging.getLogger(__name__)

SpotCallback = Callable[[Spot], Awaitable[None]]

_BASE = "pskr/filter/v2"

_GRID4_RE = re.compile(r'^[A-Ra-r]{2}\d{2}$')

# Canonical sets that the UI knows about.  When the selection covers all of
# these, collapse to a + wildcard — one subscription instead of N, and future
# bands/modes added by PSK Reporter are received automatically.  Spots for
# unknown bands or modes are stored normally; the display filter handles them.
_ALL_BANDS = frozenset([
    "160m", "80m", "60m", "40m", "30m", "20m",
    "17m",  "15m", "12m", "10m", "6m",  "2m", "70cm",
])
_ALL_MODES = frozenset([
    "FT8", "FT4", "CW", "JS8", "WSPR",
    "PSK31", "RTTY", "JT9", "JT65", "Q65",
])


def build_topics(bands: list[str], modes: list[str], call_value: str = "") -> list[str]:
    """
    Produce the minimal set of MQTT topic strings for the given filters.

    call_value: exact callsign or 4-character grid square.  When set, two
    topic patterns are generated per (band, mode) pair — one matching the
    TX position, one the RX position — so the broker delivers only spots
    where that station or grid appears on either end of the path.
    """
    # Empty bands or modes = nothing selected → no subscription at all.
    if not bands or not modes:
        return []

    # Use + wildcard when all known bands/modes are selected — one subscription
    # instead of N, and future bands/modes from PSK Reporter come through
    # automatically.  Spots for unknown bands/modes are stored normally; the
    # display filter handles them.
    bw = _ALL_BANDS.issubset(set(bands))  # band wildcard
    mw = _ALL_MODES.issubset(set(modes))  # mode wildcard

    val = call_value.strip().upper()

    if not val:
        if bw and mw:
            return [f"{_BASE}/#"]
        if bw and not mw:
            return [f"{_BASE}/+/{m}/#" for m in modes]
        if mw and not bw:
            return [f"{_BASE}/{b}/#" for b in bands]
        return [f"{_BASE}/{b}/{m}/#" for b in bands for m in modes]

    # With a call/grid value we need explicit topic patterns so the broker
    # filters before transmitting.  Topic levels after band+mode are:
    #   {tx_call}/{rx_call}/{tx_grid}/{rx_grid}/{tx_dxcc}/{rx_dxcc}
    # Use # after the matched level so we don't need to count trailing levels.
    if _GRID4_RE.match(val):
        suffixes = [f"/+/+/{val}/#",   # tx_grid match
                    f"/+/+/+/{val}/#"]  # rx_grid match
    else:
        suffixes = [f"/{val}/#",        # tx_call match
                    f"/+/{val}/#"]      # rx_call match

    if bw and mw:
        prefixes = [f"{_BASE}/+/+"]
    elif bw and not mw:
        prefixes = [f"{_BASE}/+/{m}" for m in modes]
    elif mw and not bw:
        prefixes = [f"{_BASE}/{b}/+" for b in bands]
    else:
        prefixes = [f"{_BASE}/{b}/{m}" for b in bands for m in modes]

    seen: set[str] = set()
    topics: list[str] = []
    for p in prefixes:
        for s in suffixes:
            t = p + s
            if t not in seen:
                seen.add(t)
                topics.append(t)
    return topics


class MqttManager:
    def __init__(self, broker_host: str, broker_port: int, use_tls: bool):
        self._host = broker_host
        self._port = broker_port
        self._use_tls = use_tls
        self._current_topics: set[str] = set()
        self._filter_queue: asyncio.Queue[tuple[list[str], list[str], str]] = asyncio.Queue()
        self._db: Optional[aiosqlite.Connection] = None
        self._callback: Optional[SpotCallback] = None

    def set_filter(self, bands: list[str], modes: list[str], call_value: str = "") -> None:
        self._filter_queue.put_nowait((bands, modes, call_value))

    async def run(
        self,
        db: aiosqlite.Connection,
        callback: SpotCallback,
        initial_bands: list[str],
        initial_modes: list[str],
        initial_call_value: str = "",
    ) -> None:
        self._db = db
        self._callback = callback
        self._current_topics = set(build_topics(initial_bands, initial_modes, initial_call_value))

        while True:
            try:
                await self._connect_and_run()
            except aiomqtt.MqttError as exc:
                log.warning("MQTT error: %s — reconnecting in 5 s", exc)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                return

    async def _connect_and_run(self) -> None:
        tls_ctx = ssl.create_default_context() if self._use_tls else None
        async with aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            tls_context=tls_ctx,
        ) as client:
            log.info("MQTT connected to %s:%d", self._host, self._port)
            for topic in self._current_topics:
                await client.subscribe(topic, qos=0)
                log.info("Subscribed: %s", topic)

            watcher = asyncio.create_task(self._watch_filter(client))
            try:
                async for message in client.messages:
                    spot = Spot.from_payload(message.payload)
                    if spot is None:
                        continue
                    if spot.b not in _ALL_BANDS or spot.md not in _ALL_MODES:
                        continue
                    await insert_spot(self._db, spot)
                    await self._callback(spot)
            finally:
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass

    async def _watch_filter(self, client: aiomqtt.Client) -> None:
        while True:
            bands, modes, call_value = await self._filter_queue.get()
            new_topics = set(build_topics(bands, modes, call_value))
            to_unsub = self._current_topics - new_topics
            to_sub = new_topics - self._current_topics

            for topic in to_unsub:
                try:
                    await client.unsubscribe(topic)
                    log.info("Unsubscribed: %s", topic)
                except Exception as exc:
                    log.warning("Unsubscribe failed (%s): %s", topic, exc)

            for topic in to_sub:
                await client.subscribe(topic, qos=0)
                log.info("Subscribed: %s", topic)

            self._current_topics = new_topics
