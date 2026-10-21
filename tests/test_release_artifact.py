import hashlib
import io
import socket
import unittest
from unittest.mock import patch

from core import ApiError
import release_artifact as artifact


class Response(io.BytesIO):
    def __init__(self, data, *, status=200, headers=None):
        super().__init__(data)
        self.status, self.headers = status, headers or {}
        self.read_bytes = 0

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        value = super().read1(size)
        self.read_bytes += len(value)
        return value


class ReleaseArtifactTest(unittest.TestCase):
    def addresses(self, *values):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (v, 443)) for v in values]

    def test_public_resolution_rejects_private_mixed_and_reserved_results(self):
        for address in ['127.0.0.1','10.0.0.1','169.254.169.254','192.168.0.1',
                        '100.64.0.1','224.0.0.1','::1','fc00::1','::ffff:127.0.0.1']:
            with self.subTest(address=address), patch.object(socket,'getaddrinfo',return_value=self.addresses('8.8.8.8',address)):
                with self.assertRaises(ApiError): artifact.public_target('https://downloads.example.test/app.apk')

    def test_unsafe_urls_never_resolve_or_connect(self):
        with patch.object(socket,'getaddrinfo') as dns:
            for url in ['file:///tmp/a','http://example.test/a','https://u:p@example.test/a',
                        'https://example.test:444/a','https://example.test/a#x',
                        'https://example.test/\nx','https://example.test\\@evil.test/a']:
                with self.subTest(url=url), self.assertRaises(ApiError): artifact.public_target(url)
            dns.assert_not_called()

    def test_tls_connects_to_vetted_ip_and_checks_original_hostname(self):
        with patch.object(socket,'create_connection') as connect, patch.object(artifact.ssl,'create_default_context') as tls:
            connection = artifact.PublicHttpsConnection('downloads.example.test','8.8.8.8')
            connection.connect()
            connect.assert_called_once_with(('8.8.8.8',443),timeout=15)
            tls.return_value.wrap_socket.assert_called_once_with(connect.return_value,server_hostname='downloads.example.test')
            connection.close()

    def verify(self, response, size=3, digest=None):
        with patch.object(socket,'getaddrinfo',return_value=self.addresses('8.8.8.8')), patch.object(artifact,'PublicHttpsConnection') as factory:
            factory.return_value.getresponse.return_value=response
            try:
                artifact.verify('https://downloads.example.test/a.apk',digest or hashlib.sha256(b'apk').hexdigest(),size)
            finally:
                factory.return_value.close.assert_called_once()
            return factory

    def test_exact_bytes_and_digest_are_verified(self):
        self.verify(Response(b'apk',headers={'Content-Length':'3'}))
        self.verify(Response(b'apk'))
        for response,digest in [(Response(b'ap'),None),(Response(b'apk'),'0'*64)]:
            with self.assertRaises(ApiError): self.verify(response,digest=digest)

    def test_chunked_overflow_stops_at_declared_size_plus_one(self):
        response=Response(b'x'*100_000)
        with self.assertRaises(ApiError) as error: self.verify(response)
        self.assertEqual('APK_SIZE_MISMATCH',error.exception.code)
        self.assertEqual(4,response.read_bytes)

    def test_redirect_and_oversized_length_are_rejected_before_body(self):
        for response in [Response(b'ignored',status=302,headers={'Location':'http://127.0.0.1/'}),
                         Response(b'ignored',headers={'Content-Length':'99999'}),
                         Response(b'ignored',headers={'Content-Encoding':'gzip'})]:
            with self.assertRaises(ApiError): self.verify(response)
            self.assertEqual(0,response.read_bytes)

    def test_invalid_metadata_never_resolves(self):
        with patch.object(socket,'getaddrinfo') as dns:
            for size in [0,-1,True,artifact.MAX_APK_BYTES+1]:
                with self.assertRaises(ApiError): artifact.verify('https://example.test/a','a'*64,size)
            with self.assertRaises(ApiError): artifact.verify('https://example.test/a','invalid',3)
            dns.assert_not_called()

    def test_deadline_prevents_continued_reads(self):
        response=Response(b'apk')
        with patch.object(artifact.time,'monotonic',side_effect=[0,61]):
            with self.assertRaises(ApiError) as error: self.verify(response)
        self.assertEqual('APK_DOWNLOAD_UNAVAILABLE',error.exception.code)
        self.assertEqual(0,response.read_bytes)
