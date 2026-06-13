# LAN fan-out via Mosquitto bridge

Instead of every client on your LAN opening its own TLS connection to
`mqtt.pskreporter.info`, you can bridge the feed into a local Mosquitto
broker.  All LAN clients — including multiple instances of pskr-map —
subscribe to the local broker.  The upstream connection is shared.

```
mqtt.pskreporter.info
        │  one TLS connection (bridge)
        ▼
  Local Mosquitto  ←── pskr-map, other LAN tools
```

The bridge's topic subscription is the **ceiling** of what's available
locally.  Bridging `pskr/filter/v2/#` gives every LAN client access to
all bands and modes; they narrow it with their own subscriptions.

---

## Bridge configuration

Create `/etc/mosquitto/conf.d/pskreporter.conf` (or add to your main
`mosquitto.conf`):

```
connection pskreporter
address mqtt.pskreporter.info:1884

# TLS — use your system CA bundle
bridge_cafile /etc/ssl/certs/ca-certificates.crt

# Subscribe to the full v2 filtered feed (all bands, all modes)
# Direction "in" = upstream → local.  QoS 0 matches the feed's native QoS.
topic pskr/filter/v2/# in 0

# Don't try to resume a persistent session; spots are ephemeral QoS 0
cleansession true

# Give the bridge connection a unique client ID
remote_clientid pskreporter-bridge-YOURCALL
```

Reload Mosquitto:

```bash
sudo systemctl reload mosquitto
```

Verify the bridge is receiving spots:

```bash
mosquitto_sub -h localhost -t "pskr/filter/v2/#" -v | head -20
```

You should see JSON payloads within a few seconds.

---

## Pointing pskr-map at the local broker

Edit `config.toml`:

```toml
[broker]
host    = "localhost"   # or LAN IP for remote clients
port    = 1883
use_tls = false
```

No other changes.  The subscription filter, SQLite buffer, and dynamic
re-subscribe all work identically — they just talk to the local broker
instead of the upstream one.

---

## Multiple pskr-map instances on the LAN

Each instance connects to the local broker and manages its own SQLite
database.  They share the upstream feed but have independent spot
buffers, filter states, and display windows.  There is no coordination
needed between instances.

---

## Notes

- **The local broker does not buffer spots.**  QoS 0 messages are
  fire-and-forget; if pskr-map is down, those spots are gone.  The
  SQLite rolling window inside pskr-map is the only replay store.

- **`cleansession true`** is correct here.  A persistent bridge session
  with QoS 0 data gains nothing and can accumulate stale state on the
  broker.

- **CA bundle path** varies by OS:
  - Debian/Ubuntu/Raspberry Pi OS: `/etc/ssl/certs/ca-certificates.crt`
  - Fedora/RHEL: `/etc/pki/tls/certs/ca-bundle.crt`
  - macOS (Homebrew Mosquitto): `/opt/homebrew/etc/ca-certificates.crt`
    or pass `bridge_insecure true` for local testing only.

- **`remote_clientid`** should be unique per bridge.  If two brokers
  bridge the same feed with the same client ID, the upstream server may
  disconnect one of them.
