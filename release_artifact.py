"""Bounded release verification against a final, public HTTPS download URL."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
from urllib.parse import urlsplit

from core import ApiError

MAX_APK_BYTES = 512 * 1024 * 1024
VERIFY_SECONDS = 60


def public_target(url: str) -> tuple[str, str, str]:
    """Resolve once and return a vetted address; the connection must use that IP."""
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.port not in (None, 443)
                or parts.username is not None or parts.password is not None or parts.fragment
                or re.search(r"[\s\\\x00-\x1f\x7f]", url)):
            raise ValueError("invalid HTTPS target")
        host = parts.hostname.encode("idna").decode("ascii")
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses:
            raise ValueError("no address")
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global or ip.is_multicast or ip.is_reserved:
                raise ValueError("non-public address")
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        return host, addresses[0][4][0], target
    except (ValueError, UnicodeError, OSError) as exc:
        raise ApiError(409, "UNSAFE_DOWNLOAD_URL", "安装包必须使用公网 HTTPS 直链") from exc


class PublicHttpsConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str):
        super().__init__(host, port=443, timeout=15, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        # No proxy, redirect, or second DNS resolution. TLS still validates the original host.
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def verify(download_url: str, expected_sha256: str, expected_size: int) -> None:
    if (isinstance(expected_size, bool) or not isinstance(expected_size, int)
            or not 0 < expected_size <= MAX_APK_BYTES):
        raise ApiError(409, "INVALID_APK_SIZE", "安装包大小必须在 1 字节至 512 MiB 之间")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ApiError(409, "INVALID_APK_SHA256", "安装包摘要格式无效")
    deadline = time.monotonic() + VERIFY_SECONDS
    host, address, target = public_target(download_url)
    connection = PublicHttpsConnection(host, address)
    digest = hashlib.sha256()
    size = 0
    try:
        connection.request("GET", target, headers={
            "User-Agent": "MyChuannong-ReleaseGate/1.0", "Accept-Encoding": "identity",
        })
        with connection.getresponse() as response:
            if response.status != 200:
                raise ApiError(409, "APK_DOWNLOAD_UNAVAILABLE", "安装包地址必须直接返回文件，不能跳转")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) != expected_size):
                raise ApiError(409, "APK_SIZE_MISMATCH", "下载文件大小与发布元数据不一致")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ApiError(409, "APK_DOWNLOAD_UNAVAILABLE", "安装包不可使用传输内容压缩")
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("release verification deadline")
                if connection.sock is not None:
                    connection.sock.settimeout(min(15, remaining))
                # Read at most one byte beyond the declared size, including chunked responses.
                chunk = response.read1(min(64 * 1024, expected_size - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > expected_size:
                    raise ApiError(409, "APK_SIZE_MISMATCH", "安装包超过声明大小，已停止下载")
                digest.update(chunk)
    except (OSError, http.client.HTTPException) as exc:
        raise ApiError(409, "APK_DOWNLOAD_UNAVAILABLE", "下载地址当前无法完成发布校验") from exc
    finally:
        connection.close()
    if size != expected_size:
        raise ApiError(409, "APK_SIZE_MISMATCH", "下载文件大小与发布元数据不一致")
    if digest.hexdigest() != expected_sha256.lower():
        raise ApiError(409, "APK_SHA256_MISMATCH", "下载文件 SHA-256 与发布元数据不一致")
