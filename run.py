"""
Entry point that owns the event loop so Windows always gets
SelectorEventLoop — ProactorEventLoop (the Windows default) lacks
add_reader/remove_writer, which aiomqtt requires.
"""
import asyncio
import sys
import tomllib
from pathlib import Path


def _load_server_cfg():
    try:
        return tomllib.loads(Path("config.toml").read_text()).get("server", {})
    except Exception:
        return {}


async def _serve():
    import uvicorn
    cfg = _load_server_cfg()
    server = uvicorn.Server(uvicorn.Config(
        "backend.main:app",
        host=cfg.get("host", "0.0.0.0"),
        port=cfg.get("port", 8765),
        log_level="info",
    ))
    await server.serve()


if __name__ == "__main__":
    if sys.platform == "win32":
        # Explicitly create and own a SelectorEventLoop; don't let uvicorn
        # or asyncio.run() pick up the ProactorEventLoop default.
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_serve())
        except KeyboardInterrupt:
            pass  # uvicorn re-raises Ctrl+C after cleanup; suppress the traceback
        finally:
            loop.close()
    else:
        asyncio.run(_serve())
