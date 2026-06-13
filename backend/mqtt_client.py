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

# PSK Reporter v2 topic structure (levels after base/band/mode):
#   {tx_call} / {tx_grid4} / {rx_call} / {rx_grid4}
_GRID4_RE = re.compile(r'^[A-Ra-r]{2}\d{2}$')


def build_topics(bands: list[str], modes: list[str], call_value: str = "") -> list[str]:
    """
    Produce the minimal set of MQTT topic strings for the given filters.

    call_value: exact callsign or 4-character grid square.  When set, two
    topic patterns are generated per (band, mode) pair — one matching the
    TX position, one the RX position — so the broker delivers only spots
    where that station or grid appears on either end of the path.
    """
    val = call_value.strip().upper()

    if not val:
        if not bands and not modes:
            return [f"{_BASE}/#"]
        if bands and not modes:
            return [f"{_BASE}/{b}/#" for b in bands]
        if modes and not bands:
            return [f"{_BASE}/+/{m}/#" for m in modes]
        return [f"{_BASE}/{b}/{m}/#" for b in bands for m in modes]

    # With a call/grid value we need explicit topic patterns so the broker
    # filters before transmitting.  Topic levels after band+mode are:
    #   {tx_call}/{tx_grid4}/{rx_call}/{rx_grid4}
    # Topic levels after band/mode:
    #   {tx_call}/{rx_call}/{tx_grid}/{rx_grid}/{tx_dxcc}/{rx_dxcc}
    # Use # after the matched level so we don't need to count trailing levels.
    if _GRID4_RE.match(val):
        suffixes = [f"/+/+/{val}/#",   # tx_grid match
                    f"/+/+/+/{val}/#"]  # rx_grid match
    else:
        suffixes = [f"/{val}/#",        # tx_call match
                    f"/+/{val}/#"]      # rx_call match

    if not bands and not modes:
        prefixes = [f"{_BASE}/+/+"]
    elif bands and not modes:
        prefixes = [f"{_BASE}/{b}/+" for b in bands]
    elif modes and not bands:
        prefixes = [f"{_BASE}/+/{m}" for m in modes]
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
