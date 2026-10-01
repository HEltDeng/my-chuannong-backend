"""WSGI bridge that preserves the existing ApiHandler business contract."""

from __future__ import annotations

import io
import uuid
from email.message import Message
from http import HTTPStatus
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import server
from core import json_response
from runtime_config import RUNTIME


StartResponse = Callable[[str, list[tuple[str, str]]], Any]


def _headers_from_environ(environ: dict[str, Any]) -> Message:
    headers = Message()
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = "-".join(part.capitalize() for part in key[5:].split("_"))
            headers[name] = str(value)
    if environ.get("CONTENT_TYPE"):
        headers["Content-Type"] = str(environ["CONTENT_TYPE"])
    if environ.get("CONTENT_LENGTH"):
        headers["Content-Length"] = str(environ["CONTENT_LENGTH"])
    return headers


class WsgiApiHandler(server.ApiHandler):
    """A socket-free ApiHandler instance backed by a WSGI environ."""

    def __init__(self, environ: dict[str, Any]):
        self.environ = environ
        self.command = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/")) or "/"
        query = str(environ.get("QUERY_STRING", ""))
        self.path = path + (f"?{query}" if query else "")
        self.request_version = str(environ.get("SERVER_PROTOCOL", "HTTP/1.1"))
        self.protocol_version = "HTTP/1.1"
        self.requestline = f"{self.command} {path} {self.request_version}"
        self.headers = _headers_from_environ(environ)
        self.rfile = environ["wsgi.input"]
        self.wfile = io.BytesIO()
        self.client_address = (str(environ.get("REMOTE_ADDR", "unknown")), 0)
        self.close_connection = True
        self._wsgi_status = 200
        self._wsgi_headers: list[tuple[str, str]] = []

    def send_response(self, code: int, message: str | None = None) -> None:
        self._wsgi_status = int(code)

    def send_header(self, keyword: str, value: str) -> None:
        self._wsgi_headers.append((str(keyword), str(value)))

    def end_headers(self) -> None:
        return


def _host_name(value: str) -> str:
    try:
        return (urlsplit(f"//{value}").hostname or "").lower()
    except ValueError:
        return ""


def _policy_error(
    start_response: StartResponse, status: int, code: str, message: str
) -> Iterable[bytes]:
    request_id = uuid.uuid4().hex
    encoded, content_type = json_response(
        status, {"ok": False, "error": {"code": code, "message": message}}
    )
    phrase = HTTPStatus(status).phrase
    start_response(
        f"{status} {phrase}",
        [
            ("Content-Type", content_type),
            ("Content-Length", str(len(encoded))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Request-ID", request_id),
        ],
    )
    return [encoded]


def application(environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
    if RUNTIME.is_production:
        host = _host_name(str(environ.get("HTTP_HOST", "")))
        if host not in RUNTIME.allowed_hosts:
            return _policy_error(start_response, 400, "INVALID_HOST", "请求 Host 无效")
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/"))
        origin = str(environ.get("HTTP_ORIGIN", "")).rstrip("/")
        if (
            method in {"POST", "PUT", "PATCH", "DELETE"}
            and path.startswith("/api/v1/admin/")
            and origin
            and origin != RUNTIME.admin_origin
        ):
            return _policy_error(start_response, 403, "ORIGIN_REJECTED", "管理请求来源无效")

    handler = WsgiApiHandler(environ)
    handler.dispatch(handler.command)
    status = handler._wsgi_status
    phrase = HTTPStatus(status).phrase if status in HTTPStatus._value2member_map_ else "Unknown"
    start_response(f"{status} {phrase}", handler._wsgi_headers)
    return [handler.wfile.getvalue()]
