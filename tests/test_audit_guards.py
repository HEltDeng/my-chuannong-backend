"""Regression tests for the independent preview's source-audit fixes."""
import io
import json
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core
import server
from wsgi import application


class AuditGuardsTest(unittest.TestCase):
    def test_learning_backend_test_runtime_cannot_target_operational_databases(self):
        old = core.DB_PATH
        try:
            for target in (
                core.ROOT / 'data/my-chuannong.db',
                core.ROOT / 'data/my-chuannong-v105.db',
                core.RUNTIME.database_path,
            ):
                with self.subTest(path=target.name), self.assertRaises(RuntimeError):
                    core.configure_test_runtime(target)
                self.assertEqual(old, core.DB_PATH)
            with tempfile.TemporaryDirectory() as temp:
                core.configure_test_runtime(Path(temp) / 'synthetic-test.db')
                self.assertEqual((Path(temp) / 'synthetic-test.db').resolve(), core.DB_PATH)
        finally:
            core.DB_PATH = old

    def test_unconfigured_release_certificate_rejects_publication(self):
        old_path, old_server = core.DB_PATH, server.DB_PATH
        old_uploads = server.UPLOADS
        try:
            with tempfile.TemporaryDirectory() as temp:
                database = Path(temp) / 'synthetic-test.db'
                core.configure_test_runtime(database)
                server.DB_PATH = database
                server.UPLOADS = Path(temp) / 'uploads'
                server.initialize_database()
                digest, salt = server.password_record(secrets.token_urlsafe(24))
                with server.connection() as db:
                    db.execute("""INSERT INTO users(
                        id, username, password_hash, password_salt, role, account_type,
                        nickname, admin_role, admin_enabled, account_status, created_at)
                        VALUES('audit-admin','audit-admin',?,?,'ADMIN','ADMIN',
                        'Synthetic','SUPER_ADMIN',1,'ACTIVE',?)""", (digest,salt,server.now_ms()))
                    token = server.issue_token(db,'audit-admin')
                body = json.dumps({'latestVersionCode':999, 'latestVersionName':'audit-only',
                    'minSupportedVersionCode':1, 'status':'PUBLISHED'}).encode()
                environ = {'REQUEST_METHOD':'PUT','PATH_INFO':'/api/v1/admin/version/android',
                    'wsgi.input':io.BytesIO(body),'CONTENT_TYPE':'application/json',
                    'CONTENT_LENGTH':str(len(body)),'HTTP_AUTHORIZATION':'Bearer '+token,
                    'REMOTE_ADDR':'127.0.0.1'}
                status = []
                with patch.object(server,'OFFICIAL_SIGNING_CERTIFICATE_SHA256',''):
                    payload = json.loads(b''.join(application(environ,lambda s,h:status.append(s))))
                self.assertTrue(status[0].startswith('409'))
                self.assertEqual('RELEASE_NOT_CONFIGURED',payload['error']['code'])
        finally:
            core.DB_PATH, server.DB_PATH, server.UPLOADS = old_path, old_server, old_uploads


if __name__ == '__main__':
    unittest.main()
