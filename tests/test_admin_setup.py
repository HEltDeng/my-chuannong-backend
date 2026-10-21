"""One-time owner setup: isolated database and synthetic credentials only."""
import concurrent.futures
import json
import secrets
import unittest
from types import SimpleNamespace
from unittest import mock

import manage
import server
import test_production as production_tests
import wsgi


class AdminInitialSetupTest(unittest.TestCase):
    setUp = production_tests.ProductionUnitTest.setUp
    tearDown = production_tests.ProductionUnitTest.tearDown
    call = production_tests.ProductionUnitTest.wsgi_call

    def account(self, username="bootstrap-owner", role="SUPER_ADMIN", pending=True):
        password = secrets.token_urlsafe(24)
        digest, salt = server.password_record(password)
        with server.connection() as db:
            db.execute(
                """INSERT INTO users(id,username,password_hash,password_salt,role,account_type,
                   nickname,admin_role,admin_setup_required,created_at)
                   VALUES(?,?,?,?,'ADMIN','ADMIN','Owner',?,?,?)""",
                (username, username, digest, salt, role, int(pending), server.now_ms()),
            )
            token = server.issue_token(db, username)
        return token, password

    def fields(self, username="chosen-owner"):
        password = secrets.token_urlsafe(24)
        return {"username": username, "password": password, "passwordConfirmation": password}

    def test_setup_requires_authenticated_enabled_super_admin(self):
        self.assertEqual(401, self.call("POST", "/api/v1/admin/account/setup", self.fields())[0])
        operator, _ = self.account(role="LIBRARY_OPERATOR", pending=False)
        self.assertEqual(403, self.call("POST", "/api/v1/admin/account/setup", self.fields(), operator)[0])
        with server.connection() as db:
            db.execute("UPDATE users SET admin_role='SUPER_ADMIN',admin_enabled=0")
        self.assertEqual(403, self.call("POST", "/api/v1/admin/account/setup", self.fields(), operator)[0])

    def test_pending_account_only_has_setup_identity_and_logout_access(self):
        token, _ = self.account()
        status, body, _ = self.call("GET", "/api/v1/auth/me", token=token)
        self.assertEqual(200, status)
        self.assertTrue(body["data"]["adminSetupRequired"])
        for path in ("/api/v1/admin/overview", "/api/v1/admin/admins", "/api/v1/admin/campus/summary"):
            status, body, _ = self.call("GET", path, token=token)
            self.assertEqual(409, status, path)
            self.assertEqual("ADMIN_SETUP_REQUIRED", body["error"]["code"])
        self.assertTrue(self.call("GET", "/api/v1/admin/account/setup", token=token)[1]["data"]["setupRequired"])
        self.assertEqual(200, self.call("POST", "/api/v1/auth/logout", {}, token)[0])

    def test_setup_keeps_identity_and_privileges_but_revokes_every_old_session(self):
        token, old_password = self.account()
        with server.connection() as db:
            second_token = server.issue_token(db, "bootstrap-owner")
        fields = self.fields()
        status, body, _ = self.call("POST", "/api/v1/admin/account/setup", fields, token)
        self.assertEqual(200, status, body)
        self.assertTrue(body["data"]["loginRequired"])
        for old_token in (token, second_token):
            self.assertEqual(401, self.call("GET", "/api/v1/auth/me", token=old_token)[0])
        for username in ("bootstrap-owner", fields["username"]):
            self.assertEqual(401, self.call("POST", "/api/v1/auth/login", {"username": username, "password": old_password})[0])
        status, login, _ = self.call("POST", "/api/v1/auth/login", {"username": fields["username"], "password": fields["password"]})
        self.assertEqual(200, status)
        self.assertEqual("bootstrap-owner", login["data"]["user"]["id"])
        self.assertEqual("SUPER_ADMIN", login["data"]["user"]["adminRole"])
        self.assertFalse(login["data"]["user"]["adminSetupRequired"])
        new_token = login["data"]["token"]
        self.assertEqual(200, self.call("GET", "/api/v1/admin/admins", token=new_token)[0])
        self.assertEqual(409, self.call("POST", "/api/v1/admin/account/setup", self.fields(), new_token)[0])
        with server.connection() as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            row = db.execute("SELECT * FROM users").fetchone()
            self.assertIsNotNone(row["admin_setup_completed_at"])
            self.assertNotEqual(fields["password"], row["password_hash"])
            audit = db.execute("SELECT details_json FROM admin_audit_logs WHERE action='ADMIN_INITIAL_SETUP_COMPLETED'").fetchone()[0]
            self.assertNotIn(fields["password"], audit)
            self.assertNotIn(old_password, audit)
            self.assertNotIn(token, audit)

    def test_invalid_fields_leave_credentials_sessions_and_setup_state_intact(self):
        token, old_password = self.account()
        self.account("occupied", pending=False)
        valid = self.fields()
        invalid = [
            {**valid, "username": "occupied"},
            {**valid, "username": "bad name"},
            {**valid, "username": ""},
            {**valid, "password": "short", "passwordConfirmation": "short"},
            {**valid, "password": " " + valid["password"], "passwordConfirmation": " " + valid["password"]},
            {**valid, "passwordConfirmation": "different"},
            {**valid, "password": old_password, "passwordConfirmation": old_password},
            {**valid, "adminRole": "CONTENT_OPERATOR"},
            {**valid, "password": 12345},
        ]
        for data in invalid:
            with self.subTest(fields=list(data)):
                status, _, _ = self.call("POST", "/api/v1/admin/account/setup", data, token)
                self.assertIn(status, (400, 409))
                self.assertEqual(200, self.call("GET", "/api/v1/auth/me", token=token)[0])
        with server.connection() as db:
            owner = db.execute("SELECT * FROM users WHERE id='bootstrap-owner'").fetchone()
            self.assertEqual("bootstrap-owner", owner["username"])
            self.assertTrue(server.verify_password(old_password, owner["password_hash"], owner["password_salt"]))
            self.assertEqual(1, owner["admin_setup_required"])
            self.assertIsNone(owner["admin_setup_completed_at"])

    def test_concurrent_submissions_cannot_replace_completed_setup(self):
        token, _ = self.account()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.call, "POST", "/api/v1/admin/account/setup", self.fields(f"owner-{i}"), token) for i in range(2)]
            statuses = sorted(f.result()[0] for f in futures)
        self.assertEqual([200, 401], statuses)
        with server.connection() as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM admin_audit_logs WHERE action='ADMIN_INITIAL_SETUP_COMPLETED'").fetchone()[0])

    def test_existing_admin_is_not_automatically_reopened_by_migration(self):
        token, _ = self.account(pending=False)
        server.initialize_database()
        self.assertFalse(self.call("GET", "/api/v1/auth/me", token=token)[1]["data"]["adminSetupRequired"])
        self.assertEqual(409, self.call("POST", "/api/v1/admin/account/setup", self.fields(), token)[0])

    def test_inflight_old_password_login_cannot_issue_a_session_after_setup(self):
        token, old_password = self.account()
        original_issue = server.issue_token
        def finish_setup_before_session_is_issued(db, user_id, **kwargs):
            self.assertEqual(200, self.call("POST", "/api/v1/admin/account/setup", self.fields(), token)[0])
            return original_issue(db, user_id, **kwargs)
        with mock.patch.object(server, "issue_token", side_effect=finish_setup_before_session_is_issued):
            status, body, _ = self.call("POST", "/api/v1/auth/login", {"username": "bootstrap-owner", "password": old_password})
        self.assertEqual(401, status)
        self.assertEqual("CREDENTIALS_CHANGED", body["error"]["code"])
        with server.connection() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])

    def test_explicit_migration_only_arms_the_unconfigured_sole_owner(self):
        token, _ = self.account(pending=False)
        with mock.patch.object(server, "RUNTIME", SimpleNamespace(is_production=True)):
            manage.require_initial_admin_setup("bootstrap-owner")
        self.assertTrue(self.call("GET", "/api/v1/auth/me", token=token)[1]["data"]["adminSetupRequired"])
        self.assertEqual(200, self.call("POST", "/api/v1/admin/account/setup", self.fields(), token)[0])
        with mock.patch.object(server, "RUNTIME", SimpleNamespace(is_production=True)):
            with self.assertRaises(ValueError):
                manage.require_initial_admin_setup("chosen-owner")
        self.account("another-admin", pending=False)
        with mock.patch.object(server, "RUNTIME", SimpleNamespace(is_production=True)):
            with self.assertRaises(ValueError):
                manage.require_initial_admin_setup("another-admin")

    def test_setup_rejects_cross_origin_browser_submission_in_production(self):
        token, _ = self.account()
        config = SimpleNamespace(is_production=True, allowed_hosts=("localhost",), admin_origin="https://admin.example.test")
        with mock.patch.object(wsgi, "RUNTIME", config):
            status, body, _ = self.call("POST", "/api/v1/admin/account/setup", self.fields(), token, headers={"Origin": "https://untrusted.example.test"})
        self.assertEqual(403, status)
        self.assertEqual("ORIGIN_REJECTED", body["error"]["code"])


if __name__ == "__main__":
    unittest.main()
