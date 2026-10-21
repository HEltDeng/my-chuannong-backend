"""Structured production logging with explicit sensitive-value redaction."""

from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "phone",
    "phone_number",
    "student_id",
    "real_name",
    "my_chuannong_phone_hash_secret",
    "my_chuannong_pii_encryption_key",
}


def redact(value: Any, key: str = "") -> Any:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in SENSITIVE_KEYS or any(
        marker in normalized for marker in ("password", "secret", "token", "cookie")
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+\-/]+=*", "Bearer [REDACTED]", value)
        text = re.sub(
            r"(?i)(password|secret|token|cookie|authorization)(\s*[=:]\s*)[^\s,&;]+",
            r"\1\2[REDACTED]",
            text,
        )
        return text
    return value


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for field in (
            "request_id",
            "route",
            "method",
            "status",
            "latency_ms",
            "event",
            "client_version_code",
            "latest_version_code",
            "update_decision",
            "policy_revision",
        ):
            if hasattr(record, field):
                payload[field] = redact(getattr(record, field), field)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(log_path: Path, *, console: bool = True) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = JsonLogFormatter()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)
