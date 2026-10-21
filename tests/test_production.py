import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cryptography.fernet import Fernet
from PIL import Image

import core
import manage
import server
from logging_config import JsonLogFormatter, redact
from runtime_config import ConfigurationError, load_runtime_config
from scripts.database_tools import backup_database, restore_database, verify_backup
from wsgi import application


ROOT = Path(__file__).resolve().parents[1]


class ProductionUnitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "database" / "test.db"
        core.configure_test_runtime(self.database)
        server.DB_PATH = self.database
        server.UPLOADS = self.root / "uploads"
        server.phone_hash_secret.cache_clear()
        server.pii_cipher.cache_clear()
        server.RATE_LIMITER.reset()
        server.initialize_database()

    def tearDown(self):
        server.phone_hash_secret.cache_clear()
        server.pii_cipher.cache_clear()
        server.RATE_LIMITER.reset()
        self.temp.cleanup()

    def wsgi_call(
        self,
        method,
        path,
        data=None,
        token=None,
        headers=None,
        raw_body=None,
        content_length=None,
    ):
        body = (
            raw_body
            if raw_body is not None
            else json.dumps(data).encode("utf-8") if data is not None else b""
        )
        parts = path.split("?", 1)
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": parts[0],
            "QUERY_STRING": parts[1] if len(parts) == 2 else "",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8787",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_HOST": "localhost:8787",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": (
                str(content_length)
                if content_length is not None
                else str(len(body)) if body else ""
            ),
            "wsgi.input": io.BytesIO(body),
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": True,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        if token:
            environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        for key, value in (headers or {}).items():
            environ["HTTP_" + key.upper().replace("-", "_")] = value
        captured = {}

        def start_response(status, response_headers):
            captured["status"] = int(status.split()[0])
            captured["headers"] = dict(response_headers)

        payload = b"".join(application(environ, start_response))
        return captured["status"], json.loads(payload), captured["headers"]

    def test_production_config_fails_closed_and_accepts_complete_values(self):
        with self.assertRaises(ConfigurationError):
            load_runtime_config({"MY_CHUANNONG_ENV": "production"})
        environment = {
            "MY_CHUANNONG_ENV": "production",
            "MY_CHUANNONG_DEBUG": "false",
            "MY_CHUANNONG_HOST": "127.0.0.1",
            "MY_CHUANNONG_PORT": "18787",
            "MY_CHUANNONG_DB_PATH": str(self.root / "state" / "production.db"),
            "MY_CHUANNONG_UPLOAD_PATH": str(self.root / "state" / "uploads"),
            "MY_CHUANNONG_LOG_PATH": str(self.root / "logs" / "backend.jsonl"),
            "MY_CHUANNONG_BACKUP_PATH": str(self.root / "backups"),
            "MY_CHUANNONG_PHONE_HASH_SECRET": secrets.token_urlsafe(48),
            "MY_CHUANNONG_PII_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "MY_CHUANNONG_ALLOWED_HOSTS": "api.example.test",
            "MY_CHUANNONG_ADMIN_ORIGIN": "https://api.example.test",
            "MY_CHUANNONG_TRUSTED_PROXY": "127.0.0.1",
        }
        config = load_runtime_config(environment)
        self.assertTrue(config.is_production)
        self.assertFalse(config.debug)
        self.assertTrue(config.database_path.is_absolute())
        with self.assertRaises(ConfigurationError):
            load_runtime_config(dict(environment, MY_CHUANNONG_DEBUG="true"))
        with self.assertRaises(ConfigurationError):
            load_runtime_config(dict(environment, MY_CHUANNONG_PHONE_HASH_SECRET="PLACEHOLDER"))
        with self.assertRaises(ConfigurationError):
            load_runtime_config(dict(environment, MY_CHUANNONG_TRUSTED_PROXY="10.0.0.2"))
        with self.assertRaises(ConfigurationError):
            load_runtime_config(dict(environment, MY_CHUANNONG_ADMIN_ORIGIN="https://admin.example.test"))
        ipv6_environment = dict(
            environment,
            MY_CHUANNONG_HOST="::1",
            MY_CHUANNONG_ALLOWED_HOSTS="::1",
            MY_CHUANNONG_ADMIN_ORIGIN="https://[::1]",
        )
        self.assertEqual("::1", load_runtime_config(ipv6_environment).host)
        for rejected_host in (
            "0.0.0.0",
            "::",
            "10.0.0.2",
            "192.168.1.10",
            "8.8.8.8",
            "localhost",
        ):
            with self.subTest(rejected_host=rejected_host):
                with self.assertRaises(ConfigurationError):
                    load_runtime_config(
                        dict(environment, MY_CHUANNONG_HOST=rejected_host)
                    )
        self.assertEqual(28800, config.admin_session_seconds)
        self.assertEqual(
            3600,
            load_runtime_config(
                dict(environment, MY_CHUANNONG_ADMIN_SESSION_SECONDS="3600")
            ).admin_session_seconds,
        )
        with self.assertRaises(ConfigurationError):
            load_runtime_config(
                dict(environment, MY_CHUANNONG_ADMIN_SESSION_SECONDS="1799")
            )
        with self.assertRaises(ConfigurationError):
            load_runtime_config(
                dict(environment, MY_CHUANNONG_ADMIN_SESSION_SECONDS="86401")
            )

    def test_release_history_clears_unresolvable_legacy_operator_references(self):
        # A historical app_versions row may contain a human-readable operator
        # label rather than a users.id. The migration must preserve the
        # release row while making the new FK-backed history insertable.
        db = sqlite3.connect(self.database)
        try:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute(
                "UPDATE app_versions SET created_by=?,updated_by=? WHERE platform='android'",
                ("legacy-operator", "legacy-operator"),
            )
            db.commit()
        finally:
            db.close()

        server.initialize_database()

        with server.connection() as db:
            current = db.execute(
                "SELECT created_by,updated_by FROM app_versions WHERE platform='android'"
            ).fetchone()
            history = db.execute(
                "SELECT created_by,updated_by FROM app_release_versions "
                "WHERE platform='android' ORDER BY version_code DESC LIMIT 1"
            ).fetchone()
            self.assertIsNone(current["created_by"])
            self.assertIsNone(current["updated_by"])
            self.assertIsNone(history["created_by"])
            self.assertIsNone(history["updated_by"])
            self.assertEqual("ok", db.execute("PRAGMA integrity_check").fetchone()[0])

    def test_clean_database_wal_integrity_and_default_state(self):
        server.initialize_database()
        with server.connection() as database:
            self.assertEqual("wal", database.execute("PRAGMA journal_mode").fetchone()[0])
            self.assertEqual(1, database.execute("PRAGMA foreign_keys").fetchone()[0])
            self.assertEqual("ok", database.execute("PRAGMA integrity_check").fetchone()[0])
            for table in (
                "users",
                "reservations",
                "lost_found_posts",
                "feedback",
                "announcements",
                "venues",
                "venue_time_slots",
                "campus_posts",
                "campus_providers",
                "campus_bookings",
                "app_update_policies",
            ):
                expected = 1 if table == "app_update_policies" else 0
                self.assertEqual(expected, database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], table)
            # The three editor slots are required configuration, with no published content.
            guides = database.execute("SELECT body,image,url,status FROM campus_guides").fetchall()
            self.assertEqual(3, len(guides))
            for guide in guides:
                self.assertFalse(guide["body"] or guide["image"] or guide["url"])
                self.assertEqual("DRAFT", guide["status"])
            version = database.execute(
                "SELECT status,latest_version_name,latest_version_code FROM app_versions WHERE platform='android'"
            ).fetchone()
            self.assertEqual("DRAFT", version["status"])
            self.assertEqual("1.0.0", version["latest_version_name"])
            self.assertEqual(10, version["latest_version_code"])
            policy = database.execute(
                "SELECT * FROM app_update_policies WHERE platform='android'"
            ).fetchone()
            self.assertEqual(0, policy["update_enabled"])
            self.assertEqual(0, policy["force_update_enabled"])
            self.assertEqual(1, policy["policy_revision"])
            self.assertEqual(0, database.execute("SELECT COUNT(*) FROM resource_items").fetchone()[0])

    def test_consistent_backup_verification_and_restore(self):
        with server.connection() as database:
            database.execute(
                "INSERT INTO announcements(id,title,content,published_at,updated_at) VALUES('before','before','before',1,1)"
            )
        result = backup_database(self.database, self.root / "backups", retention_days=14)
        verification = verify_backup(result["backup"])
        self.assertTrue(verification["valid"])
        self.assertTrue(verification["manifestMatches"])
        with server.connection() as database:
            database.execute("DELETE FROM announcements")
            database.execute(
                "INSERT INTO announcements(id,title,content,published_at,updated_at) VALUES('after','after','after',2,2)"
            )
        restored = restore_database(result["backup"], self.database, self.root / "safety")
        self.assertTrue(restored["safetyBackup"].is_file())
        with closing(sqlite3.connect(self.database)) as database:
            ids = {row[0] for row in database.execute("SELECT id FROM announcements")}
        self.assertEqual({"before"}, ids)

    def test_restore_requires_complete_matching_manifest(self):
        result = backup_database(
            self.database, self.root / "strict-backups", retention_days=14
        )
        self.assertTrue(verify_backup(result["backup"])["valid"])

        original_manifest = json.loads(
            result["manifest"].read_text(encoding="utf-8")
        )

        def variant(name, *, manifest_change=None, invalid_json=False, tamper=False):
            directory = self.root / "adversarial" / name
            directory.mkdir(parents=True)
            backup = directory / f"{name}.db"
            shutil.copy2(result["backup"], backup)
            if manifest_change != "missing":
                manifest = dict(original_manifest)
                manifest["filename"] = backup.name
                if callable(manifest_change):
                    manifest_change(manifest)
                manifest_path = backup.with_suffix(".json")
                manifest_path.write_text(
                    "{" if invalid_json else json.dumps(manifest),
                    encoding="utf-8",
                )
            if tamper:
                with closing(sqlite3.connect(backup)) as database:
                    database.execute(
                        "INSERT INTO announcements(id,title,content,published_at,updated_at) "
                        "VALUES('tampered','tampered','tampered',1,1)"
                    )
                    database.commit()
            return backup

        cases = {
            "missing": variant("missing", manifest_change="missing"),
            "wrong-sha": variant(
                "wrong-sha",
                manifest_change=lambda value: value.update(sha256="0" * 64),
            ),
            "wrong-size": variant(
                "wrong-size",
                manifest_change=lambda value: value.update(bytes=value["bytes"] + 1),
            ),
            "wrong-filename": variant(
                "wrong-filename",
                manifest_change=lambda value: value.update(filename="other.db"),
            ),
            "wrong-format": variant(
                "wrong-format",
                manifest_change=lambda value: value.update(format="unsupported-v0"),
            ),
            "invalid-json": variant("invalid-json", invalid_json=True),
            "tampered": variant("tampered", tamper=True),
        }
        for name, backup in cases.items():
            with self.subTest(name=name):
                verification = verify_backup(backup)
                self.assertFalse(verification["valid"])
                self.assertTrue(verification["manifestErrors"])
                with self.assertRaises(RuntimeError):
                    restore_database(
                        backup,
                        self.root / "restore-targets" / f"{name}.db",
                        self.root / "restore-safety" / name,
                    )

    def test_rbac_invalid_admin_roles_fail_closed(self):
        valid_password = secrets.token_urlsafe(24)
        valid_digest, valid_salt = server.password_record(valid_password)
        with server.connection() as database:
            database.execute(
                """INSERT INTO users(
                       id,username,password_hash,password_salt,role,account_type,nickname,
                       admin_permissions_json,admin_role,admin_enabled,account_status,created_at
                   ) VALUES('valid-super','valid-super',?,?, 'ADMIN','ADMIN','Valid',
                            '["*"]','SUPER_ADMIN',1,'ACTIVE',?)""",
                (valid_digest, valid_salt, server.now_ms()),
            )

        invalid_tokens = []
        for suffix, role_value in (
            ("null", None),
            ("invalid", "INVALID"),
            ("wrong-case", "super_admin"),
        ):
            password = secrets.token_urlsafe(24)
            digest, salt = server.password_record(password)
            admin_id = f"invalid-admin-{suffix}"
            username = f"invalid-admin-{suffix}"
            with server.connection() as database:
                database.execute(
                    """INSERT INTO users(
                           id,username,password_hash,password_salt,role,account_type,nickname,
                           admin_permissions_json,admin_role,admin_enabled,account_status,created_at
                       ) VALUES(?,?,?,?, 'ADMIN','ADMIN','Invalid','["*"]',?,1,'ACTIVE',?)""",
                    (admin_id, username, digest, salt, role_value, server.now_ms()),
                )
                row = database.execute(
                    "SELECT * FROM users WHERE id=?", (admin_id,)
                ).fetchone()
                self.assertEqual("", server.admin_role(row))
                self.assertEqual(set(), server.admin_permissions(row))
                invalid_tokens.append(server.issue_token(database, admin_id))

            status, body, _ = self.wsgi_call(
                "POST",
                "/api/v1/auth/login",
                {"username": username, "password": password},
            )
            self.assertEqual(403, status)
            self.assertEqual("ADMIN_ROLE_INVALID", body["error"]["code"])

        with mock.patch.object(
            server,
            "RUNTIME",
            SimpleNamespace(is_production=True),
        ):
            server.initialize_database()
        with server.connection() as database:
            roles = {
                row["id"]: row["admin_role"]
                for row in database.execute(
                    "SELECT id,admin_role FROM users WHERE id LIKE 'invalid-admin-%'"
                )
            }
        self.assertIsNone(roles["invalid-admin-null"])
        self.assertEqual("INVALID", roles["invalid-admin-invalid"])
        self.assertEqual("super_admin", roles["invalid-admin-wrong-case"])
        with self.assertRaisesRegex(RuntimeError, "invalid role"):
            server.ensure_production_database_ready()
        for token in invalid_tokens:
            status, body, _ = self.wsgi_call(
                "GET", "/api/v1/admin/overview", token=token
            )
            self.assertEqual(403, status)
            self.assertEqual("ADMIN_ROLE_INVALID", body["error"]["code"])

        with server.connection() as database:
            database.execute("DELETE FROM users WHERE id LIKE 'invalid-admin-%'")
        server.ensure_production_database_ready()
        status, login, _ = self.wsgi_call(
            "POST",
            "/api/v1/auth/login",
            {"username": "valid-super", "password": valid_password},
        )
        self.assertEqual(200, status)
        self.assertEqual(
            200,
            self.wsgi_call(
                "GET", "/api/v1/admin/overview", token=login["data"]["token"]
            )[0],
        )

    def test_admin_cli_boundaries_and_session_revocation(self):
        with mock.patch.object(
            server,
            "RUNTIME",
            SimpleNamespace(is_production=True),
        ), mock.patch.object(
            manage, "read_password", side_effect=AssertionError("must not prompt")
        ):
            with self.assertRaises(ValueError):
                manage.create_admin("forbidden-production-admin")

        username = "development-cli-admin"
        old_password = secrets.token_urlsafe(24)
        with mock.patch.object(
            manage, "read_password", return_value=old_password
        ), mock.patch("builtins.print"):
            manage.create_admin(username)
        with server.connection() as database:
            admin = database.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
            self.assertEqual("SUPER_ADMIN", server.admin_role(admin))
            old_token = server.issue_token(database, admin["id"])
            self.assertTrue(
                database.execute(
                    "SELECT 1 FROM sessions WHERE token_hash=?",
                    (hashlib.sha256(old_token.encode()).hexdigest(),),
                ).fetchone()
            )

        new_password = secrets.token_urlsafe(24)
        with mock.patch.object(
            manage, "read_password", return_value=new_password
        ), mock.patch("builtins.print"):
            manage.reset_password(username)
        with server.connection() as database:
            updated = database.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
            self.assertFalse(
                server.verify_password(
                    old_password, updated["password_hash"], updated["password_salt"]
                )
            )
            self.assertTrue(
                server.verify_password(
                    new_password, updated["password_hash"], updated["password_salt"]
                )
            )
            self.assertEqual(
                0,
                database.execute(
                    "SELECT COUNT(*) FROM sessions WHERE user_id=?", (updated["id"],)
                ).fetchone()[0],
            )

    def test_user_and_admin_session_ttl_and_runtime_enforcement(self):
        user_password = secrets.token_urlsafe(24)
        admin_password = secrets.token_urlsafe(24)
        user_digest, user_salt = server.password_record(user_password)
        admin_digest, admin_salt = server.password_record(admin_password)
        with server.connection() as database:
            database.execute(
                """INSERT INTO users(id,username,password_hash,password_salt,role,
                       account_type,nickname,account_status,created_at)
                   VALUES('ttl-user','ttl-user',?,?,'USER','PHONE','User','ACTIVE',?)""",
                (user_digest, user_salt, server.now_ms()),
            )
            database.execute(
                """INSERT INTO users(id,username,password_hash,password_salt,role,
                       account_type,nickname,admin_role,admin_enabled,account_status,created_at)
                   VALUES('ttl-admin','ttl-admin',?,?,'ADMIN','ADMIN','Admin',
                          'SUPER_ADMIN',1,'ACTIVE',?)""",
                (admin_digest, admin_salt, server.now_ms()),
            )
            user_token = server.issue_token(database, "ttl-user")
            admin_token = server.issue_token(database, "ttl-admin")
            user_session = database.execute(
                "SELECT created_at,expires_at FROM sessions WHERE token_hash=?",
                (hashlib.sha256(user_token.encode()).hexdigest(),),
            ).fetchone()
            admin_session = database.execute(
                "SELECT created_at,expires_at FROM sessions WHERE token_hash=?",
                (hashlib.sha256(admin_token.encode()).hexdigest(),),
            ).fetchone()
            self.assertEqual(
                core.SESSION_SECONDS * 1000,
                user_session["expires_at"] - user_session["created_at"],
            )
            self.assertEqual(
                server.RUNTIME.admin_session_seconds * 1000,
                admin_session["expires_at"] - admin_session["created_at"],
            )
            database.execute(
                "UPDATE sessions SET expires_at=? WHERE token_hash=?",
                (
                    server.now_ms() - 1,
                    hashlib.sha256(admin_token.encode()).hexdigest(),
                ),
            )
        self.assertEqual(
            401,
            self.wsgi_call(
                "GET", "/api/v1/admin/overview", token=admin_token
            )[0],
        )

        with server.connection() as database:
            disabled_token = server.issue_token(database, "ttl-admin")
            database.execute(
                "UPDATE users SET admin_enabled=0 WHERE id='ttl-admin'"
            )
        status, body, _ = self.wsgi_call(
            "GET", "/api/v1/admin/overview", token=disabled_token
        )
        self.assertEqual(403, status)
        self.assertEqual("ADMIN_DISABLED", body["error"]["code"])

    def test_admin_api_password_reset_revokes_existing_sessions(self):
        super_password = secrets.token_urlsafe(24)
        target_password = secrets.token_urlsafe(24)
        super_digest, super_salt = server.password_record(super_password)
        target_digest, target_salt = server.password_record(target_password)
        with server.connection() as database:
            for values in (
                (
                    "api-reset-super",
                    "api-reset-super",
                    super_digest,
                    super_salt,
                    "SUPER_ADMIN",
                ),
                (
                    "api-reset-target",
                    "api-reset-target",
                    target_digest,
                    target_salt,
                    "SPORTS_OPERATOR",
                ),
            ):
                database.execute(
                    """INSERT INTO users(
                           id,username,password_hash,password_salt,role,account_type,
                           nickname,admin_role,admin_enabled,account_status,created_at
                       ) VALUES(?,?,?,?, 'ADMIN','ADMIN','Admin',?,1,'ACTIVE',?)""",
                    (*values, server.now_ms()),
                )
            super_token = server.issue_token(database, "api-reset-super")
            target_token = server.issue_token(database, "api-reset-target")

        new_password = secrets.token_urlsafe(24)
        status, _, _ = self.wsgi_call(
            "POST",
            "/api/v1/admin/admins/api-reset-target/reset-password",
            {"password": new_password},
            super_token,
        )
        self.assertEqual(200, status)
        self.assertEqual(
            401,
            self.wsgi_call(
                "GET", "/api/v1/admin/overview/sports", token=target_token
            )[0],
        )
        self.assertEqual(
            401,
            self.wsgi_call(
                "POST",
                "/api/v1/auth/login",
                {"username": "api-reset-target", "password": target_password},
            )[0],
        )
        login_status, _, _ = self.wsgi_call(
            "POST",
            "/api/v1/auth/login",
            {"username": "api-reset-target", "password": new_password},
        )
        self.assertEqual(200, login_status)

    def test_production_dependency_lock_uses_exact_validated_versions(self):
        lock_path = ROOT / "requirements.lock.txt"
        lines = [
            line.strip()
            for line in lock_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertTrue(lines)
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_.-]+==[^=<>!~]+", line) for line in lines))
        locked = {
            name.lower(): version
            for name, version in (line.split("==", 1) for line in lines)
        }
        self.assertEqual("50.0.0", locked["cryptography"])
        self.assertEqual("12.3.0", locked["pillow"])
        self.assertEqual("3.0.2", locked["waitress"])
        self.assertEqual(
            {"cffi", "cryptography", "pillow", "pycparser", "waitress"},
            set(locked),
        )

    def test_wsgi_health_readiness_and_security_headers(self):
        for path, expected in (("/health", "ok"), ("/ready", "ready")):
            status, body, headers = self.wsgi_call("GET", path)
            self.assertEqual(200, status)
            self.assertEqual(expected, body["data"]["status"])
            self.assertNotIn("path", json.dumps(body).lower())
            self.assertEqual("nosniff", headers["X-Content-Type-Options"])
            self.assertTrue(headers["X-Request-ID"])

    def test_request_boundaries_reject_malformed_oversized_and_traversal_input(self):
        status, body, _ = self.wsgi_call(
            "POST", "/api/v1/auth/device", raw_body=b"{"
        )
        self.assertEqual(400, status)
        self.assertEqual("BAD_JSON", body["error"]["code"])

        status, body, _ = self.wsgi_call(
            "POST",
            "/api/v1/auth/device",
            raw_body=b"{}",
            content_length=core.MAX_BODY + 1,
        )
        self.assertEqual(400, status)
        self.assertEqual("BAD_REQUEST", body["error"]["code"])

        status, body, _ = self.wsgi_call("GET", "/uploads/../server.py")
        self.assertEqual(404, status)
        self.assertFalse(body["ok"])

    def test_logging_redacts_credentials_and_pii(self):
        secret = secrets.token_urlsafe(24)
        value = {
            "password": secret,
            "Authorization": f"Bearer {secret}",
            "phone": "13800138000",
            "safe": "retained",
        }
        redacted = json.dumps(redact(value), ensure_ascii=False)
        self.assertNotIn(secret, redacted)
        self.assertNotIn("13800138000", redacted)
        self.assertIn("retained", redacted)
        formatter = JsonLogFormatter()
        import logging

        record = logging.LogRecord("test", logging.INFO, __file__, 1, f"token={secret}", (), None)
        self.assertNotIn(secret, formatter.format(record))

    def test_login_rate_limit_blocks_repeated_brute_force(self):
        for _ in range(8):
            status, _, _ = self.wsgi_call(
                "POST", "/api/v1/auth/login", {"username": "missing-admin", "password": "wrong-password"}
            )
            self.assertEqual(401, status)
        status, body, _ = self.wsgi_call(
            "POST", "/api/v1/auth/login", {"username": "missing-admin", "password": "wrong-password"}
        )
        self.assertEqual(429, status)
        self.assertEqual("RATE_LIMITED", body["error"]["code"])

    def test_registration_and_login_rate_limits_use_five_minute_windows(self):
        observed = []

        def record_window(key, limit, window_seconds):
            observed.append((key.split(":", 1)[0], limit, window_seconds))
            return None

        with mock.patch.object(server.RATE_LIMITER, "check", side_effect=record_window):
            self.wsgi_call(
                "POST",
                "/api/v1/auth/register",
                {"phone": "19100000000", "password": "short"},
            )
            self.wsgi_call(
                "POST",
                "/api/v1/auth/login",
                {"phone": "19100000000", "password": "wrong-password"},
            )

        self.assertIn(("register-ip", 30, 300), observed)
        self.assertIn(("register-identity", 3, 300), observed)
        self.assertIn(("login-ip", 120, 300), observed)
        self.assertIn(("login-identity", 8, 300), observed)

    def test_upload_requires_authentication_and_normalizes_image(self):
        image = Image.new("RGB", (20, 20), "red")
        encoded = io.BytesIO()
        image.save(encoded, "PNG", pnginfo=None)
        image_base64 = base64.b64encode(encoded.getvalue()).decode()
        payload = {
            "type": "LOST",
            "itemName": "测试图片",
            "category": "OTHER",
            "location": "测试地点",
            "eventDate": "2026-08-11",
            "imageBase64": image_base64,
        }
        status, _, _ = self.wsgi_call("POST", "/api/v1/lost-found", payload)
        self.assertEqual(401, status)
        self.assertEqual([], list(server.UPLOADS.glob("*")))

        _, auth, _ = self.wsgi_call(
            "POST", "/api/v1/auth/device", {"installationId": "TEST_ONLY_UPLOAD"}
        )
        status, created, _ = self.wsgi_call(
            "POST", "/api/v1/lost-found", payload, auth["data"]["token"]
        )
        self.assertEqual(201, status)
        image_name = created["data"]["imageUrl"].rsplit("/", 1)[-1]
        stored = server.UPLOADS / image_name
        self.assertTrue(stored.is_file())
        with Image.open(stored) as normalized:
            self.assertEqual("PNG", normalized.format)
            self.assertEqual((20, 20), normalized.size)

        invalid = dict(payload, imageBase64=base64.b64encode(b"<script>alert(1)</script>").decode())
        status, body, _ = self.wsgi_call(
            "POST", "/api/v1/lost-found", invalid, auth["data"]["token"]
        )
        self.assertEqual(400, status)
        self.assertEqual("INVALID_IMAGE", body["error"]["code"])


class ProductionRuntimeSmokeTest(unittest.TestCase):
    def test_waitress_production_runtime_bootstrap_and_health(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("MY_CHUANNONG_")
            }
            bootstrap_password = secrets.token_urlsafe(24)
            phone_secret = secrets.token_urlsafe(48)
            pii_key = Fernet.generate_key().decode()
            environment.update({
                "PYTHONUTF8": "1",
                "MY_CHUANNONG_ENV": "production",
                "MY_CHUANNONG_DEBUG": "false",
                "MY_CHUANNONG_HOST": "127.0.0.1",
                "MY_CHUANNONG_PORT": str(port),
                "MY_CHUANNONG_DB_PATH": str(temp / "state" / "production.db"),
                "MY_CHUANNONG_UPLOAD_PATH": str(temp / "state" / "uploads"),
                "MY_CHUANNONG_LOG_PATH": str(temp / "logs" / "backend.jsonl"),
                "MY_CHUANNONG_BACKUP_PATH": str(temp / "backups"),
                "MY_CHUANNONG_PHONE_HASH_SECRET": phone_secret,
                "MY_CHUANNONG_PII_ENCRYPTION_KEY": pii_key,
                "MY_CHUANNONG_ALLOWED_HOSTS": "127.0.0.1",
                "MY_CHUANNONG_ADMIN_ORIGIN": "https://127.0.0.1",
                "MY_CHUANNONG_TRUSTED_PROXY": "127.0.0.1",
                "MY_CHUANNONG_ADMIN_SESSION_SECONDS": "28800",
                "MY_CHUANNONG_SUPER_ADMIN_USERNAME": "TEST_ONLY_SUPER_ADMIN",
                "MY_CHUANNONG_SUPER_ADMIN_PASSWORD": bootstrap_password,
            })
            placeholder_environment = dict(environment)
            placeholder_environment["MY_CHUANNONG_SUPER_ADMIN_USERNAME"] = (
                "YOUR_ONE_TIME_SUPER_ADMIN_USERNAME"
            )
            placeholder_environment["MY_CHUANNONG_SUPER_ADMIN_PASSWORD"] = (
                "YOUR_ONE_TIME_LONG_RANDOM_PASSWORD"
            )
            rejected_bootstrap = subprocess.run(
                [sys.executable, "manage.py", "bootstrap-production"],
                cwd=ROOT,
                env=placeholder_environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            self.assertNotEqual(0, rejected_bootstrap.returncode)
            bootstrap = subprocess.run(
                [sys.executable, "manage.py", "bootstrap-production"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            self.assertEqual(0, bootstrap.returncode, bootstrap.stderr)
            rejected_create = subprocess.run(
                [sys.executable, "manage.py", "create-admin", "TEST_ONLY_FORBIDDEN"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            self.assertNotEqual(0, rejected_create.returncode)
            self.assertNotIn(bootstrap_password, rejected_create.stdout)
            self.assertNotIn(bootstrap_password, rejected_create.stderr)
            environment.pop("MY_CHUANNONG_SUPER_ADMIN_PASSWORD")
            process = subprocess.Popen(
                [sys.executable, "production.py"],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                deadline = time.time() + 20
                last_error = None
                while time.time() < deadline:
                    try:
                        request = urllib.request.Request(f"http://127.0.0.1:{port}/ready")
                        with urllib.request.urlopen(request, timeout=1) as response:
                            body = json.loads(response.read())
                            self.assertEqual("ready", body["data"]["status"])
                            break
                    except (OSError, urllib.error.URLError) as exc:
                        last_error = exc
                        time.sleep(0.2)
                else:
                    stdout, stderr = process.communicate(timeout=2)
                    self.fail(f"Production runtime did not become ready: {last_error}\n{stdout}\n{stderr}")

                def json_request(path, *, method="GET", data=None, token=None):
                    encoded = json.dumps(data).encode("utf-8") if data is not None else None
                    headers = {"Content-Type": "application/json"} if encoded else {}
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}{path}",
                        data=encoded,
                        headers=headers,
                        method=method,
                    )
                    try:
                        with urllib.request.urlopen(request, timeout=2) as response:
                            return response.status, json.loads(response.read())
                    except urllib.error.HTTPError as error:
                        return error.code, json.loads(error.read())

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/v1/public/bootstrap", timeout=2
                ) as response:
                    self.assertEqual(200, response.status)
                    public_bootstrap = json.loads(response.read())
                    self.assertTrue(public_bootstrap["ok"])
                    self.assertEqual(6, len(public_bootstrap["data"]["flags"]))

                login_status, login = json_request(
                    "/api/v1/auth/login",
                    method="POST",
                    data={
                        "username": "TEST_ONLY_SUPER_ADMIN",
                        "password": bootstrap_password,
                    },
                )
                self.assertEqual(200, login_status)
                admin_token = login["data"]["token"]
                self.assertEqual(
                    200,
                    json_request("/api/v1/admin/overview", token=admin_token)[0],
                )

                device_status, device = json_request(
                    "/api/v1/auth/device",
                    method="POST",
                    data={"installationId": "TEST_ONLY_PRODUCTION_SMOKE"},
                )
                self.assertEqual(200, device_status)
                user_token = device["data"]["token"]
                self.assertEqual(
                    403,
                    json_request("/api/v1/admin/overview", token=user_token)[0],
                )
                self.assertEqual(
                    200,
                    json_request("/api/v1/lost-found", token=user_token)[0],
                )
                version_status, version = json_request(
                    "/api/v1/public/version/android?currentVersionCode=1"
                )
                self.assertEqual(200, version_status)
                self.assertFalse(version["data"]["updateAvailable"])
                invalid_host = urllib.request.Request(
                    f"http://127.0.0.1:{port}/health",
                    headers={"Host": "evil.example"},
                )
                with self.assertRaises(urllib.error.HTTPError) as host_error:
                    urllib.request.urlopen(invalid_host, timeout=2)
                self.assertEqual(400, host_error.exception.code)

                invalid_origin = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/v1/admin/unknown",
                    data=b"{}",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "https://evil.example",
                    },
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as origin_error:
                    urllib.request.urlopen(invalid_origin, timeout=2)
                self.assertEqual(403, origin_error.exception.code)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                process.communicate(timeout=2)
            log_text = (temp / "logs" / "backend.jsonl").read_text(encoding="utf-8")
            self.assertNotIn(bootstrap_password, log_text)
            self.assertNotIn(phone_secret, log_text)
            self.assertNotIn(pii_key, log_text)


if __name__ == "__main__":
    unittest.main()
