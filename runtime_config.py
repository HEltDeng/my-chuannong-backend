"""Explicit Development/Test/Production runtime configuration."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parent
VALID_ENVIRONMENTS = {"development", "test", "production"}
PLACEHOLDER_MARKERS = (
    "PLACEHOLDER",
    "CHANGEME",
    "GENERATE_",
    "<ROOT_DOMAIN>",
    "YOUR_",
)


class ConfigurationError(RuntimeError):
    """Raised before startup when a runtime configuration is unsafe."""


@dataclass(frozen=True)
class RuntimeConfig:
    environment: str
    debug: bool
    host: str
    port: int
    database_path: Path
    upload_path: Path
    log_path: Path
    backup_path: Path
    allowed_hosts: tuple[str, ...]
    admin_origin: str
    trusted_proxy: str
    admin_session_seconds: int
    waitress_threads: int
    sqlite_busy_timeout_ms: int
    max_body_bytes: int
    upload_max_bytes: int
    upload_max_pixels: int

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def _boolean(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _integer(value: str, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Missing required production variable: {name}")
    if any(marker in value.upper() for marker in PLACEHOLDER_MARKERS):
        raise ConfigurationError(f"Production variable still contains a placeholder: {name}")
    return value


def _production_path(environment: Mapping[str, str], name: str) -> Path:
    value = _required(environment, name)
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigurationError(f"{name} must be an absolute path in production")
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise ConfigurationError(f"{name} must be outside the Git checkout in production")
    return resolved


def _validate_secret(environment: Mapping[str, str], name: str, minimum: int = 32) -> str:
    value = _required(environment, name)
    if len(value.encode("utf-8")) < minimum:
        raise ConfigurationError(f"{name} must contain at least {minimum} bytes")
    return value


def load_runtime_config(environment: Mapping[str, str] | None = None) -> RuntimeConfig:
    env = os.environ if environment is None else environment
    mode = env.get("MY_CHUANNONG_ENV", "development").strip().lower()
    if mode not in VALID_ENVIRONMENTS:
        raise ConfigurationError(
            "MY_CHUANNONG_ENV must be development, test, or production"
        )

    debug = _boolean(env.get("MY_CHUANNONG_DEBUG", "false"), "MY_CHUANNONG_DEBUG")
    if mode == "production" and debug:
        raise ConfigurationError("MY_CHUANNONG_DEBUG must be false in production")

    if mode == "production":
        host = _required(env, "MY_CHUANNONG_HOST")
        try:
            production_host_address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ConfigurationError(
                "MY_CHUANNONG_HOST must be one loopback IP address in production"
            ) from exc
        if not production_host_address.is_loopback:
            raise ConfigurationError(
                "MY_CHUANNONG_HOST must be a loopback address in production"
            )
        port_text = _required(env, "MY_CHUANNONG_PORT")
        database_path = _production_path(env, "MY_CHUANNONG_DB_PATH")
        upload_path = _production_path(env, "MY_CHUANNONG_UPLOAD_PATH")
        log_path = _production_path(env, "MY_CHUANNONG_LOG_PATH")
        backup_path = _production_path(env, "MY_CHUANNONG_BACKUP_PATH")
        _validate_secret(env, "MY_CHUANNONG_PHONE_HASH_SECRET")
        encryption_key = _required(env, "MY_CHUANNONG_PII_ENCRYPTION_KEY")
        try:
            Fernet(encryption_key.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise ConfigurationError(
                "MY_CHUANNONG_PII_ENCRYPTION_KEY must be a valid Fernet key"
            ) from exc
        allowed_hosts_text = _required(env, "MY_CHUANNONG_ALLOWED_HOSTS")
        admin_origin = _required(env, "MY_CHUANNONG_ADMIN_ORIGIN")
        trusted_proxy = _required(env, "MY_CHUANNONG_TRUSTED_PROXY")
    else:
        host = env.get("MY_CHUANNONG_HOST", "127.0.0.1").strip() or "127.0.0.1"
        port_text = env.get("MY_CHUANNONG_PORT", "8792")
        database_path = Path(
            env.get("MY_CHUANNONG_DB_PATH", str(ROOT / "data" / "my-chuannong-v105.db"))
        ).expanduser().resolve()
        upload_path = Path(
            env.get("MY_CHUANNONG_UPLOAD_PATH", str(ROOT / "uploads"))
        ).expanduser().resolve()
        log_path = Path(
            env.get("MY_CHUANNONG_LOG_PATH", str(ROOT / "logs" / "backend.jsonl"))
        ).expanduser().resolve()
        backup_path = Path(
            env.get("MY_CHUANNONG_BACKUP_PATH", str(ROOT / "backups"))
        ).expanduser().resolve()
        allowed_hosts_text = env.get(
            "MY_CHUANNONG_ALLOWED_HOSTS", "127.0.0.1,localhost"
        )
        admin_origin = env.get(
            "MY_CHUANNONG_ADMIN_ORIGIN", f"http://127.0.0.1:{port_text}"
        ).strip()
        trusted_proxy = env.get("MY_CHUANNONG_TRUSTED_PROXY", "").strip()

    port = _integer(port_text, "MY_CHUANNONG_PORT", 1, 65535)
    if database_path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise ConfigurationError("MY_CHUANNONG_DB_PATH must name a SQLite database file")
    if log_path.suffix.lower() not in {".log", ".jsonl"}:
        raise ConfigurationError("MY_CHUANNONG_LOG_PATH must name a .log or .jsonl file")

    persistent_paths = {database_path, upload_path, log_path, backup_path}
    if len(persistent_paths) != 4:
        raise ConfigurationError("Database, upload, log, and backup paths must be distinct")

    allowed_hosts = tuple(
        value.strip().lower()
        for value in allowed_hosts_text.split(",")
        if value.strip()
    )
    if not allowed_hosts or "*" in allowed_hosts:
        raise ConfigurationError("MY_CHUANNONG_ALLOWED_HOSTS must be an explicit host list")
    if mode == "production" and any(
        any(marker in host.upper() for marker in PLACEHOLDER_MARKERS)
        for host in allowed_hosts
    ):
        raise ConfigurationError("MY_CHUANNONG_ALLOWED_HOSTS contains a placeholder")

    parsed_origin = urlparse(admin_origin)
    if (
        not parsed_origin.hostname
        or parsed_origin.path not in {"", "/"}
        or parsed_origin.query
        or parsed_origin.fragment
        or parsed_origin.username
        or parsed_origin.password
    ):
        raise ConfigurationError("MY_CHUANNONG_ADMIN_ORIGIN must be an origin without a path")
    if mode == "production" and parsed_origin.scheme != "https":
        raise ConfigurationError("MY_CHUANNONG_ADMIN_ORIGIN must use HTTPS in production")
    if (
        mode == "production"
        and parsed_origin.hostname.lower() not in allowed_hosts
    ):
        raise ConfigurationError(
            "MY_CHUANNONG_ADMIN_ORIGIN hostname must be listed in MY_CHUANNONG_ALLOWED_HOSTS"
        )
    if mode == "production":
        try:
            trusted_proxy_address = ipaddress.ip_address(trusted_proxy)
        except ValueError as exc:
            raise ConfigurationError(
                "MY_CHUANNONG_TRUSTED_PROXY must be one loopback IP address"
            ) from exc
        if not trusted_proxy_address.is_loopback:
            raise ConfigurationError(
                "MY_CHUANNONG_TRUSTED_PROXY must be a loopback address"
            )

    return RuntimeConfig(
        environment=mode,
        debug=debug,
        host=host,
        port=port,
        database_path=database_path,
        upload_path=upload_path,
        log_path=log_path,
        backup_path=backup_path,
        allowed_hosts=allowed_hosts,
        admin_origin=admin_origin.rstrip("/"),
        trusted_proxy=trusted_proxy,
        admin_session_seconds=_integer(
            env.get("MY_CHUANNONG_ADMIN_SESSION_SECONDS", "28800"),
            "MY_CHUANNONG_ADMIN_SESSION_SECONDS",
            30 * 60,
            24 * 60 * 60,
        ),
        waitress_threads=_integer(
            env.get("MY_CHUANNONG_WAITRESS_THREADS", "8"),
            "MY_CHUANNONG_WAITRESS_THREADS",
            2,
            64,
        ),
        sqlite_busy_timeout_ms=_integer(
            env.get("MY_CHUANNONG_SQLITE_BUSY_TIMEOUT_MS", "10000"),
            "MY_CHUANNONG_SQLITE_BUSY_TIMEOUT_MS",
            1000,
            60000,
        ),
        max_body_bytes=_integer(
            env.get("MY_CHUANNONG_MAX_BODY_BYTES", str(6 * 1024 * 1024)),
            "MY_CHUANNONG_MAX_BODY_BYTES",
            1024,
            16 * 1024 * 1024,
        ),
        upload_max_bytes=_integer(
            env.get("MY_CHUANNONG_UPLOAD_MAX_BYTES", str(3 * 1024 * 1024)),
            "MY_CHUANNONG_UPLOAD_MAX_BYTES",
            64 * 1024,
            8 * 1024 * 1024,
        ),
        upload_max_pixels=_integer(
            env.get("MY_CHUANNONG_UPLOAD_MAX_PIXELS", "20000000"),
            "MY_CHUANNONG_UPLOAD_MAX_PIXELS",
            1_000_000,
            50_000_000,
        ),
    )


RUNTIME = load_runtime_config()
