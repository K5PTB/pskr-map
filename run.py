"""
Entry point that sets the Windows event loop policy before uvicorn
imports anything, ensuring aiomqtt's add_reader/remove_writer calls
land on SelectorEventLoop, not ProactorEventLoop.
"""
import sys

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    import tomllib
    from pathlib import Path

    cfg = {}
    try:
        cfg = tomllib.loads(Path("config.toml").read_text())
    except Exception:
        pass

    host = cfg.get("server", {}).get("host", "0.0.0.0")
    port = cfg.get("server", {}).get("port", 8765)

    uvicorn.run("backend.main:app", host=host, port=port, log_level="info")
