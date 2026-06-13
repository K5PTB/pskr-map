import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BrokerConfig:
    host: str = "mqtt.pskreporter.info"
    port: int = 1884
    use_tls: bool = True


@dataclass
class DatabaseConfig:
    path: str = "spots.db"
    ttl_minutes: int = 120


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8765


@dataclass
class DefaultsConfig:
    bands: list[str] = field(default_factory=lambda: ["40m", "20m", "15m"])
    modes: list[str] = field(default_factory=lambda: ["FT8"])
    display_max_age_minutes: int = 30


@dataclass
class AppConfig:
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)


def load_config(path: str = "config.toml") -> AppConfig:
    cfg = AppConfig()
    p = Path(path)
    if not p.exists():
        return cfg
    with open(p, "rb") as f:
        data = tomllib.load(f)
    if b := data.get("broker"):
        cfg.broker.host = b.get("host", cfg.broker.host)
        cfg.broker.port = b.get("port", cfg.broker.port)
        cfg.broker.use_tls = b.get("use_tls", cfg.broker.use_tls)
    if d := data.get("database"):
        cfg.database.path = d.get("path", cfg.database.path)
        cfg.database.ttl_minutes = d.get("ttl_minutes", cfg.database.ttl_minutes)
    if s := data.get("server"):
        cfg.server.host = s.get("host", cfg.server.host)
        cfg.server.port = s.get("port", cfg.server.port)
    if d := data.get("defaults"):
        cfg.defaults.bands = d.get("bands", cfg.defaults.bands)
        cfg.defaults.modes = d.get("modes", cfg.defaults.modes)
        cfg.defaults.display_max_age_minutes = d.get(
            "display_max_age_minutes", cfg.defaults.display_max_age_minutes
        )
    return cfg
