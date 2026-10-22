"""Production entry point: Waitress -> WSGI adapter -> existing API routes."""

from __future__ import annotations

import logging

from waitress import serve

import server
from logging_config import configure_logging
from runtime_config import RUNTIME
from wsgi import application


def prepare_runtime() -> None:
    if not RUNTIME.is_production:
        raise RuntimeError("production.py requires MY_CHUANNONG_ENV=production")
    configure_logging(RUNTIME.log_path)
    RUNTIME.backup_path.mkdir(parents=True, exist_ok=True)
    server._safe_chmod(RUNTIME.backup_path, 0o750)
    server.initialize_database()
    server.ensure_production_database_ready()


def main() -> None:
    prepare_runtime()
    server.account_analytics.start_retention_worker(server.connection)
    logging.getLogger("mychuannong.runtime").info(
        "production runtime starting",
        extra={"event": "RUNTIME_START"},
    )
    serve(
        application,
        host=RUNTIME.host,
        port=RUNTIME.port,
        threads=RUNTIME.waitress_threads,
        trusted_proxy=RUNTIME.trusted_proxy,
        trusted_proxy_count=1,
        trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
        clear_untrusted_proxy_headers=True,
        expose_tracebacks=False,
        max_request_body_size=RUNTIME.max_body_bytes,
        connection_limit=200,
        channel_timeout=120,
        ident="MyChuannong",
    )


if __name__ == "__main__":
    main()
