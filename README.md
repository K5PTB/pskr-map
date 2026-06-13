# pskr-map

A live web map of amateur radio propagation spots, driven by the
[PSK Reporter](https://pskreporter.info) MQTT feed.  Spots stream in
from the public broker in real time and are stored locally in SQLite so
you can change band/mode filters and re-render the map instantly without
waiting for data to re-populate.

![Screenshot placeholder](docs/screenshot.png)

## How it works

```
PSK Reporter MQTT broker
  mqtt.pskreporter.info:1884 (TLS)
         │
         ▼
  Python backend (FastAPI + aiomqtt)
  ├── writes spots → SQLite  (rolling 2-hour buffer)
  └── pushes new spots → browser via WebSocket
         │
         ▼
  Browser (Leaflet map)
  ├── great-circle arcs, colored by SNR
  └── filter controls re-query SQLite immediately
```

By default the app connects directly to PSK Reporter's public broker.
Optionally, you can route the feed through a **local Mosquitto broker**
to fan it out across a LAN — see [docs/mosquitto-bridge.md](docs/mosquitto-bridge.md).

---

## Prerequisites

### Python

Python **3.11 or later** is required (uses `tomllib` from the standard
library and `asyncio.TaskGroup`).

Check your version:

```bash
python3 --version
```

On Debian/Ubuntu/Raspberry Pi OS:

```bash
sudo apt install python3.11 python3.11-venv
```

On macOS (Homebrew):

```bash
brew install python@3.11
```

### Python packages

The `run.sh` script creates a virtual environment and installs everything
automatically on first run.  To install manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

| Package | Purpose |
|---|---|
| `fastapi` | HTTP + WebSocket server |
| `uvicorn[standard]` | ASGI server (runs FastAPI) |
| `aiomqtt` | Async MQTT client |
| `aiosqlite` | Async SQLite wrapper |
| `pydantic` | Spot schema validation |

### Network access

**Direct mode (default):** outbound TLS to `mqtt.pskreporter.info:1884`
must be permitted by your firewall.  Plain TCP on port `1883` and
WebSocket variants (`1885`, `1886`) are also available — see `config.toml`.

**Local broker mode:** no outbound access needed from pskr-map; the
Mosquitto bridge handles the upstream connection.  See
[docs/mosquitto-bridge.md](docs/mosquitto-bridge.md).

---

## Quick start

```bash
git clone https://github.com/K5PTB/pskr-map.git
cd pskr-map
./run.sh
```

Then open **http://localhost:8765** in a browser.

`run.sh` creates `.venv/` and installs dependencies on the first run,
then starts uvicorn.  Subsequent runs skip the install step.

---

## Configuration

Edit **`config.toml`** before starting:

```toml
[broker]
host      = "mqtt.pskreporter.info"
port      = 1884          # 1883=plain TCP, 1884=TLS, 1885=WS, 1886=WSS
use_tls   = true

[database]
path        = "spots.db"
ttl_minutes = 120         # how long spots are kept in SQLite

[server]
host = "0.0.0.0"          # bind address (use 127.0.0.1 for local-only)
port = 8765

[defaults]
bands                   = ["40m", "20m", "15m"]
modes                   = ["FT8"]
display_max_age_minutes = 30
```

All settings can be changed at runtime via the browser UI except
`broker` and `server`, which require a restart.

---

## Usage

### Filter controls (sidebar)

| Control | What it does |
|---|---|
| **Band checkboxes** | Selects which bands are subscribed from MQTT *and* shown on the map |
| **Mode checkboxes** | Same for modes (FT8, FT4, CW, JS8, WSPR, …) |
| **Display window** | How old a spot can be before it fades off the map (5–240 min) |
| **Highlight callsign** | Your callsign — matching spots are drawn in white |

Changing bands or modes sends a new subscription to the MQTT broker
and immediately replays matching spots from the local SQLite buffer.
Changing only the display window re-queries SQLite without touching MQTT.

### Line colors (SNR)

| Color | SNR |
|---|---|
| Red | ≥ +10 dB |
| Orange | 0 to +10 dB |
| Yellow | −10 to 0 dB |
| Teal | −20 to −10 dB |
| Blue | < −20 dB |

---

## LAN fan-out (optional)

If you already run a Mosquitto broker, you can bridge the PSK Reporter
feed into it once and have multiple pskr-map instances (or other tools)
subscribe locally.  No code changes — just point `config.toml` at your
local broker.

See **[docs/mosquitto-bridge.md](docs/mosquitto-bridge.md)** for the
complete bridge configuration and notes on CA bundle paths across
platforms.

---

## Deployment (VPS or Raspberry Pi)

A minimal systemd unit:

```ini
[Unit]
Description=PSK Reporter live map
After=network-online.target

[Service]
WorkingDirectory=/opt/pskr-map
ExecStart=/opt/pskr-map/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8765
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Save to `/etc/systemd/system/pskr-map.service`, then:

```bash
sudo systemctl enable --now pskr-map
```

For HTTPS, put Nginx or Caddy in front and proxy `/` and `/ws` to
`localhost:8765`.

---

## License

MIT
