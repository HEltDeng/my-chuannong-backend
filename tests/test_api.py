import concurrent.futures
import core
import base64
import io
import importlib.util
import json
import secrets
import tempfile
import threading
import unittest
from unittest import mock
from synthetic_fixtures import seed_legacy_test_fixtures
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("my_chuannong_server", ROOT / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        test_database = Path(cls.temp.name) / "test.db"
        core.configure_test_runtime(test_database)
        server.DB_PATH = test_database
        server.UPLOADS = Path(cls.temp.name) / "uploads"
        server.initialize_database()
        seed_legacy_test_fixtures(server)
        certificate = mock.patch.object(server, "OFFICIAL_SIGNING_CERTIFICATE_SHA256", "A" * 64)
        certificate.start()
        cls.addClassCleanup(certificate.stop)
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)
        with server.connection() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        cls.temp.cleanup()

    def call(self, method, path, data=None, token=None):
        body = json.dumps(data).encode() if data is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        parts = urllib.parse.urlsplit(path)
        safe_path = urllib.parse.quote(parts.path)
        safe_query = urllib.parse.quote(parts.query, safe="=&")
        target = safe_path + (f"?{safe_query}" if safe_query else "")
        request = urllib.request.Request(self.base + target, body, headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def device_token(self, identity):
        status, response = self.call("POST", "/api/v1/auth/device", {"installationId": identity})
        self.assertEqual(200, status)
        return response["data"]["token"]

    def test_device_accounts_are_real_anonymous_users_not_test_devices(self):
        installation_id = "real-device-" + secrets.token_hex(8)
        status, response = self.call(
            "POST", "/api/v1/auth/device", {"installationId": installation_id}
        )
        self.assertEqual(200, status)
        self.assertEqual("DEVICE", response["data"]["user"]["accountType"])
        self.assertEqual("川农同学", response["data"]["user"]["nickname"])

        legacy_id = "legacy-device-" + secrets.token_hex(8)
        digest, salt = server.password_record(secrets.token_urlsafe(24))
        with server.connection() as db:
            db.execute(
                """INSERT INTO users(
                       id,username,password_hash,password_salt,role,account_type,
                       nickname,account_status,created_at
                   ) VALUES(?,?,?,?,'USER','DEVICE','测试设备','ACTIVE',?)""",
                (legacy_id, "device_" + secrets.token_hex(16), digest, salt, server.now_ms()),
            )
        server.initialize_database()
        with server.connection() as db:
            migrated = db.execute(
                "SELECT nickname FROM users WHERE id=?", (legacy_id,)
            ).fetchone()
        self.assertEqual("川农同学", migrated["nickname"])

    def test_stable_device_identity_deduplicates_devices_but_not_installations(self):
        scoped_id = "a" * 64
        first = {
            "installationId": "trusted-installation-1-" + secrets.token_hex(4),
            "deviceScopedId": scoped_id,
            "versionName": "1.0.4",
            "versionCode": 36,
        }
        second = {
            **first,
            "installationId": "trusted-installation-2-" + secrets.token_hex(4),
        }
        before_phone = None
        with server.connection() as db:
            before_phone = db.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role='USER' AND account_type='PHONE'"
            ).fetchone()["count"]
        first_status, first_response = self.call("POST", "/api/v1/auth/device", first)
        second_status, second_response = self.call("POST", "/api/v1/auth/device", second)
        self.assertEqual(200, first_status)
        self.assertEqual(200, second_status)
        self.assertEqual(
            first_response["data"]["user"]["id"],
            second_response["data"]["user"]["id"],
        )
        with server.connection() as db:
            device_count = db.execute(
                "SELECT COUNT(*) AS count FROM devices WHERE device_key_hash IS NOT NULL"
            ).fetchone()["count"]
            installation_count = db.execute(
                "SELECT COUNT(*) AS count FROM installations WHERE device_id IS NOT NULL"
            ).fetchone()["count"]
            device = db.execute(
                "SELECT install_count FROM devices WHERE device_key_hash=?",
                (server.device_key_hash(scoped_id),),
            ).fetchone()
            phone_count = db.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role='USER' AND account_type='PHONE'"
            ).fetchone()["count"]
        self.assertEqual(1, device_count)
        self.assertEqual(2, installation_count)
        self.assertEqual(2, device["install_count"])
        self.assertEqual(before_phone, phone_count)

        with server.connection() as db:
            before_legacy_installations = db.execute(
                "SELECT COUNT(*) AS count FROM installations WHERE legacy=1 AND device_id IS NULL"
            ).fetchone()["count"]
            before_devices = db.execute("SELECT COUNT(*) AS count FROM devices").fetchone()["count"]
        legacy_status, legacy_response = self.call(
            "POST",
            "/api/v1/auth/device",
            {"installationId": "legacy-installation-" + secrets.token_hex(4)},
        )
        self.assertEqual(200, legacy_status)
        self.assertEqual("DEVICE", legacy_response["data"]["user"]["accountType"])
        with server.connection() as db:
            self.assertEqual(
                before_legacy_installations + 1,
                db.execute(
                    "SELECT COUNT(*) AS count FROM installations WHERE legacy=1 AND device_id IS NULL"
                ).fetchone()["count"],
            )
            self.assertEqual(
                before_devices,
                db.execute("SELECT COUNT(*) AS count FROM devices").fetchone()["count"],
            )

    def test_z_dashboard_canonical_user_device_installation_metrics(self):
        admin = self.admin_token()

        def overview():
            status, response = self.call("GET", "/api/v1/admin/overview", token=admin)
            self.assertEqual(200, status)
            return response["data"]

        # A first anonymous app use creates one canonical user, one trusted
        # device and one installation, but no PHONE account.
        scoped_id = secrets.token_hex(32)
        first_installation = "metrics-first-" + secrets.token_hex(8)
        first_payload = {
            "installationId": first_installation,
            "deviceScopedId": scoped_id,
            "versionName": "1.0.3",
            "versionCode": 35,
        }
        before = overview()
        first_status, first = self.call("POST", "/api/v1/auth/device", first_payload)
        self.assertEqual(200, first_status)
        anonymous_user_id = first["data"]["user"]["id"]
        after_first = overview()
        self.assertEqual(before["totalUsers"] + 1, after_first["totalUsers"])
        self.assertEqual(before["registeredUsers"], after_first["registeredUsers"])
        self.assertEqual(before["guestUsers"] + 1, after_first["guestUsers"])
        self.assertEqual(before["todayNewUsers"] + 1, after_first["todayNewUsers"])
        self.assertEqual(before["todayNewRegistrations"], after_first["todayNewRegistrations"])
        self.assertEqual(before["totalDevices"] + 1, after_first["totalDevices"])
        self.assertEqual(before["todayNewDevices"] + 1, after_first["todayNewDevices"])
        self.assertEqual(before["totalInstallations"] + 1, after_first["totalInstallations"])
        self.assertEqual(before["todayNewInstallations"] + 1, after_first["todayNewInstallations"])

        # Replaying the same installation must not create another user,
        # device, installation or active-user identity.
        repeated_status, repeated = self.call("POST", "/api/v1/auth/device", first_payload)
        self.assertEqual(200, repeated_status)
        self.assertEqual(anonymous_user_id, repeated["data"]["user"]["id"])
        after_repeat = overview()
        self.assertEqual(
            {key: value for key, value in after_first.items() if key != "asOf"},
            {key: value for key, value in after_repeat.items() if key != "asOf"},
        )

        # Clearing app data creates a new installation, while the stable
        # device-scoped identity remains one device and one anonymous user.
        reinstall_payload = {
            **first_payload,
            "installationId": "metrics-reinstall-" + secrets.token_hex(8),
        }
        reinstall_status, reinstall = self.call("POST", "/api/v1/auth/device", reinstall_payload)
        self.assertEqual(200, reinstall_status)
        self.assertEqual(anonymous_user_id, reinstall["data"]["user"]["id"])
        after_reinstall = overview()
        self.assertEqual(after_repeat["totalUsers"], after_reinstall["totalUsers"])
        self.assertEqual(after_repeat["guestUsers"], after_reinstall["guestUsers"])
        self.assertEqual(after_repeat["totalDevices"], after_reinstall["totalDevices"])
        self.assertEqual(after_repeat["totalInstallations"] + 1, after_reinstall["totalInstallations"])
        self.assertEqual(after_repeat["todayNewInstallations"] + 1, after_reinstall["todayNewInstallations"])
        self.assertEqual(after_repeat["reinstallations"] + 1, after_reinstall["reinstallations"])
        self.assertEqual(after_repeat["todayNewUsers"], after_reinstall["todayNewUsers"])

        # Simulate an anonymous user whose first app use was yesterday.  Its
        # registration today must count as a new PHONE registration while the
        # canonical user cohort remains an existing user.
        yesterday = server.now_ms() - 2 * 24 * 60 * 60 * 1000
        with server.connection() as db:
            source_device = db.execute(
                "SELECT device_id FROM user_devices WHERE user_id=? LIMIT 1",
                (anonymous_user_id,),
            ).fetchone()["device_id"]
            db.execute(
                "UPDATE users SET created_at=? WHERE id=?",
                (yesterday, anonymous_user_id),
            )
            db.execute(
                "UPDATE user_devices SET first_seen_at=? WHERE user_id=?",
                (yesterday, anonymous_user_id),
            )
            db.execute(
                "UPDATE devices SET first_seen_at=? WHERE id=?",
                (yesterday, source_device),
            )
            db.commit()

        # Registering the anonymous identity merges it into one PHONE user;
        # the canonical user total stays flat while the guest cohort drops.
        phone = "19" + "".join(str(secrets.randbelow(10)) for _ in range(9))
        password = secrets.token_urlsafe(18)
        before_register = overview()
        register_status, register = self.call(
            "POST",
            "/api/v1/auth/register",
            {
                "phone": phone,
                "password": password,
                "nickname": "身份统计测试",
                "installationId": reinstall_payload["installationId"],
                "deviceScopedId": scoped_id,
            },
        )
        self.assertEqual(201, register_status)
        registered_user_id = register["data"]["user"]["id"]
        self.assertNotEqual(anonymous_user_id, registered_user_id)
        after_register = overview()
        self.assertEqual(before_register["totalUsers"], after_register["totalUsers"])
        self.assertEqual(before_register["registeredUsers"] + 1, after_register["registeredUsers"])
        self.assertEqual(before_register["guestUsers"] - 1, after_register["guestUsers"])
        self.assertEqual(before_register["totalDevices"], after_register["totalDevices"])
        self.assertEqual(before_register["totalInstallations"], after_register["totalInstallations"])
        self.assertEqual(before_register["todayNewUsers"], after_register["todayNewUsers"])
        self.assertEqual(before_register["todayNewRegistrations"] + 1, after_register["todayNewRegistrations"])

        # The same PHONE user signing in from a second device adds one device
        # and one installation, but no user, guest or DAU identity.
        second_device_payload = {
            "phone": phone,
            "password": password,
            "installationId": "metrics-second-device-" + secrets.token_hex(8),
            "deviceScopedId": secrets.token_hex(32),
            "versionName": "1.0.3",
            "versionCode": 35,
        }
        before_second_device = overview()
        second_status, second = self.call("POST", "/api/v1/auth/login", second_device_payload)
        self.assertEqual(200, second_status)
        self.assertEqual(registered_user_id, second["data"]["user"]["id"])
        after_second_device = overview()
        self.assertEqual(before_second_device["totalUsers"], after_second_device["totalUsers"])
        self.assertEqual(before_second_device["registeredUsers"], after_second_device["registeredUsers"])
        self.assertEqual(before_second_device["guestUsers"], after_second_device["guestUsers"])
        self.assertEqual(before_second_device["dau"], after_second_device["dau"])
        self.assertEqual(before_second_device["active5m"], after_second_device["active5m"])
        self.assertEqual(before_second_device["totalDevices"] + 1, after_second_device["totalDevices"])
        self.assertEqual(before_second_device["totalInstallations"] + 1, after_second_device["totalInstallations"])
        self.assertEqual(before_second_device["todayNewUsers"], after_second_device["todayNewUsers"])

        # A different direct PHONE registration is a new canonical user and a
        # new self-account, without inventing a device or installation row.
        before_second_registration = overview()
        other_phone = "19" + "".join(str(secrets.randbelow(10)) for _ in range(9))
        other_status, other = self.call(
            "POST",
            "/api/v1/auth/register",
            {
                "phone": other_phone,
                "password": secrets.token_urlsafe(18),
                "nickname": "另一名身份测试用户",
            },
        )
        self.assertEqual(201, other_status)
        self.assertNotEqual(registered_user_id, other["data"]["user"]["id"])
        after_second_registration = overview()
        self.assertEqual(before_second_registration["totalUsers"] + 1, after_second_registration["totalUsers"])
        self.assertEqual(before_second_registration["registeredUsers"] + 1, after_second_registration["registeredUsers"])
        self.assertEqual(before_second_registration["guestUsers"], after_second_registration["guestUsers"])
        self.assertEqual(before_second_registration["totalDevices"], after_second_registration["totalDevices"])
        self.assertEqual(before_second_registration["totalInstallations"], after_second_registration["totalInstallations"])
        self.assertEqual(before_second_registration["todayNewUsers"] + 1, after_second_registration["todayNewUsers"])
        self.assertEqual(before_second_registration["todayNewRegistrations"] + 1, after_second_registration["todayNewRegistrations"])

    def test_user_codes_are_readable_searchable_and_concurrent(self):
        _, _, _, user = self.phone_account()
        self.assertRegex(user["userCode"], r"^\d{17}$")
        admin = self.admin_token()
        status, listing = self.call(
            "GET",
            f"/api/v1/admin/users?q={user['userCode']}",
            token=admin,
        )
        self.assertEqual(200, status)
        self.assertTrue(any(row["user_code"] == user["userCode"] for row in listing["data"]))

        def register_one(_):
            return self.call(
                "POST",
                "/api/v1/auth/register",
                {
                    "phone": "19" + "".join(str(secrets.randbelow(10)) for _ in range(9)),
                    "password": secrets.token_urlsafe(18),
                    "nickname": "并发编号测试",
                },
            )

        server.RATE_LIMITER = server.RateLimiter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(register_one, range(8)))
        self.assertTrue(all(status == 201 for status, _ in results))
        codes = [response["data"]["user"]["userCode"] for _, response in results]
        self.assertEqual(8, len(set(codes)))
        self.assertTrue(all(code.startswith(user["userCode"][:12]) for code in codes))

    def phone_account(self, installation_id=None):
        phone = "19" + "".join(str(secrets.randbelow(10)) for _ in range(9))
        password = secrets.token_urlsafe(18)
        payload = {
            "phone": phone,
            "password": password,
            "nickname": "自动化测试用户",
        }
        if installation_id:
            payload["installationId"] = installation_id
        status, response = self.call("POST", "/api/v1/auth/register", payload)
        self.assertEqual(201, status)
        return phone, password, response["data"]["token"], response["data"]["user"]

    def admin_token(self):
        generated_secret = secrets.token_urlsafe(24)
        digest, salt = server.password_record(generated_secret)
        admin_id = "admin-" + secrets.token_hex(8)
        username = "admin-" + secrets.token_hex(8)
        with server.connection() as db:
            db.execute(
                """INSERT INTO users(
                      id,username,password_hash,password_salt,role,account_type,nickname,
                      admin_permissions_json,admin_role,admin_enabled,account_status,created_at
                  ) VALUES(?,?,?,?, 'ADMIN','ADMIN','测试管理员','["*"]','SUPER_ADMIN',1,'ACTIVE',?)""",
                (admin_id, username, digest, salt, server.now_ms()),
            )
        status, login = self.call(
            "POST",
            "/api/v1/auth/login",
            {"username": username, "password": generated_secret},
        )
        self.assertEqual(200, status)
        return login["data"]["token"]

    def publish_seed_release_for_version_tests(self):
        # The isolated test runtime seeds a draft row. Treat its seed artifact
        # as the prior formal release so minimum-version selection exercises
        # the same published-history contract as production.
        with server.connection() as db:
            db.execute(
                "UPDATE app_release_versions SET status='PUBLISHED',published_at=COALESCE(published_at,?) "
                "WHERE platform='android' AND version_code=31",
                (server.now_ms(),),
            )
            db.execute(
                "UPDATE app_update_policies SET minimum_supported_version_code=31 "
                "WHERE platform='android'"
            )

    def test_phone_account_gate_and_device_data_migration(self):
        installation_id = "migration-" + secrets.token_hex(8)
        device = self.device_token(installation_id)
        payload = {
            "clientLocalId": "migrated-" + secrets.token_hex(8),
            "type": "LOST",
            "itemName": "测试物品",
            "category": "OTHER",
            "location": "本地测试地点",
            "eventDate": date.today().isoformat(),
        }
        self.assertEqual(201, self.call("POST", "/api/v1/lost-found", payload, device)[0])
        phone, password, token, user = self.phone_account(installation_id)
        self.assertEqual("PHONE", user["accountType"])
        self.assertNotIn(phone, json.dumps(user, ensure_ascii=False))
        self.assertEqual(401, self.call("GET", "/api/v1/auth/me", token=device)[0])
        self.assertEqual(200, self.call("GET", "/api/v1/auth/me", token=token)[0])
        status, posts = self.call("GET", "/api/v1/lost-found?mine=true", token=token)
        self.assertEqual(200, status)
        self.assertTrue(any(row["itemName"] == "测试物品" for row in posts["data"]))
        self.assertEqual(409, self.call(
            "POST",
            "/api/v1/auth/register",
            {"phone": phone, "password": password, "nickname": "重复账号"},
        )[0])
        self.assertEqual(200, self.call(
            "POST",
            "/api/v1/auth/login",
            {"phone": phone, "password": password},
        )[0])

    def test_reservation_rules_maintenance_and_admin_override(self):
        _, _, user_token, user = self.phone_account()
        admin = self.admin_token()
        self.assertEqual(200, self.call(
            "PUT",
            "/api/v1/admin/reservation-rules",
            {
                "maxActivePerDay": 1,
                "maxUserCancellationsPerDay": 1,
                "cancelCutoffMinutes": 0,
                "restrictionHoursAfterExcessCancel": 1,
            },
            admin,
        )[0])
        booking_date = (date.today() + timedelta(days=1)).isoformat()
        request = {
            "venueId": "badminton",
            "date": booking_date,
            "timeSlot": "09:00-10:00",
            "courtNumber": 6,
        }
        status, created = self.call(
            "POST", "/api/v1/sports/reservations", request, user_token
        )
        self.assertEqual(201, status)
        request["courtNumber"] = 7
        self.assertEqual(429, self.call(
            "POST", "/api/v1/sports/reservations", request, user_token
        )[0])
        reservation_id = created["data"]["id"]
        self.assertEqual(200, self.call(
            "POST", f"/api/v1/sports/reservations/{reservation_id}/cancel", {}, user_token
        )[0])
        self.assertEqual(429, self.call(
            "POST", "/api/v1/sports/reservations", request, user_token
        )[0])
        self.assertEqual(200, self.call(
            "POST",
            f"/api/v1/admin/users/{user['id']}/clear-restrictions",
            {},
            admin,
        )[0])
        self.assertEqual(201, self.call(
            "POST", "/api/v1/sports/reservations", request, user_token
        )[0])
        self.assertEqual(200, self.call(
            "PUT",
            "/api/v1/admin/flags/sports_reservation",
            {
                "enabled": False,
                "payload": {
                    "status": "MAINTENANCE",
                    "maintenanceNote": "自动化维护测试",
                },
            },
            admin,
        )[0])
        status, error = self.call("GET", "/api/v1/sports/venues", token=user_token)
        self.assertEqual(503, status)
        self.assertEqual("FEATURE_MAINTENANCE", error["error"]["code"])
        self.assertEqual(200, self.call(
            "PUT",
            "/api/v1/admin/flags/sports_reservation",
            {"enabled": True, "payload": {"status": "OPEN"}},
            admin,
        )[0])

    def test_feedback_workflow_announcement_delete_and_audit(self):
        _, _, user_token, _ = self.phone_account()
        admin = self.admin_token()
        status, feedback = self.call(
            "POST",
            "/api/v1/feedback",
            {
                "category": "FEATURE_SUGGESTION",
                "title": "反馈闭环自动化",
                "content": "本地自动化反馈",
                "appVersion": "test",
            },
            user_token,
        )
        self.assertEqual(201, status)
        feedback_id = feedback["data"]["id"]
        self.assertEqual(200, self.call(
            "PATCH",
            f"/api/v1/admin/feedback/{feedback_id}",
            {
                "status": "RESOLVED",
                "publicReply": "问题已处理，请更新后重试。",
                "internalNote": "仅管理员可见的回归记录",
            },
            admin,
        )[0])
        status, mine = self.call("GET", "/api/v1/feedback", token=user_token)
        self.assertEqual(200, status)
        self.assertEqual("RESOLVED", mine["data"][0]["status"])
        self.assertEqual("问题已处理，请更新后重试。", mine["data"][0]["publicReply"])
        self.assertNotIn("internal", json.dumps(mine["data"], ensure_ascii=False).lower())
        self.assertNotIn("仅管理员", json.dumps(mine["data"], ensure_ascii=False))
        status, detail = self.call(
            "GET", f"/api/v1/admin/feedback/{feedback_id}", token=admin
        )
        self.assertEqual(200, status)
        self.assertEqual(
            "仅管理员可见的回归记录",
            detail["data"]["feedback"]["internal_note"],
        )
        status, announcement = self.call(
            "POST",
            "/api/v1/admin/announcements",
            {"title": "待删除公告", "content": "自动化测试", "enabled": True},
            admin,
        )
        self.assertEqual(201, status)
        announcement_id = announcement["data"]["id"]
        self.assertEqual(200, self.call(
            "DELETE", f"/api/v1/admin/announcements/{announcement_id}", token=admin
        )[0])
        status, bootstrap = self.call("GET", "/api/v1/public/bootstrap")
        self.assertEqual(200, status)
        self.assertFalse(any(
            row["id"] == announcement_id for row in bootstrap["data"]["announcements"]
        ))
        status, audits = self.call("GET", "/api/v1/admin/audit-logs", token=admin)
        self.assertEqual(200, status)
        self.assertTrue(any(
            row["action"] == "ANNOUNCEMENT_DELETED" for row in audits["data"]
        ))

    def test_service_announcement_management(self):
        admin = self.admin_token()
        buffer = io.BytesIO()
        Image.new("RGB", (16, 16), "green").save(buffer, "PNG")
        payload = {"title":"测试动态", "content":"测试正文", "category":"示例公告", "sortOrder":-100,
                   "enabled":False, "imageBase64":base64.b64encode(buffer.getvalue()).decode()}
        status, created = self.call("POST", "/api/v1/admin/announcements", payload, admin)
        self.assertEqual(201, status)
        identity = created["data"]["id"]
        route = "/api/v1/admin/announcements/" + identity
        def feed():
            return self.call("GET", "/api/v1/public/service-updates")[1]["data"]
        self.assertFalse(any(row.get("targetId") == identity for row in feed()))
        self.assertEqual(200,self.call("PATCH",route,{"enabled":True,"targetUrl":"https://www.sicau.edu.cn/"},admin)[0])
        row = next(row for row in feed() if row.get("targetId") == identity)
        self.assertEqual("示例公告",row["category"])
        self.assertEqual("URL",row["targetType"])
        self.assertTrue(row["imageUrl"].startswith("/uploads/"))
        self.assertEqual(-100,row["sortOrder"])
        self.assertEqual(identity,next(row for row in feed() if row["type"] == "ANNOUNCEMENT")["targetId"])
        self.assertEqual(400,self.call("PATCH",route,{"targetUrl":"javascript:alert(1)"},admin)[0])
        self.assertEqual(200,self.call("PATCH",route,{"title":"修改标题","content":"更新正文","targetUrl":"","removeImage":True},admin)[0])
        row = next(row for row in feed() if row.get("targetId") == identity)
        self.assertEqual("修改标题",row["title"])
        self.assertEqual("更新正文",row["summary"])
        self.assertEqual("ANNOUNCEMENT",row["targetType"])
        self.assertIsNone(row["imageUrl"])
        self.assertEqual(200,self.call("PATCH",route,{"enabled":False},admin)[0])
        self.assertFalse(any(row.get("targetId") == identity for row in feed()))
        self.assertEqual(200,self.call("DELETE",route,token=admin)[0])

    def test_announcement_notification_flags_are_explicit_and_edit_safe(self):
        admin = self.admin_token()
        status, created = self.call(
            "POST",
            "/api/v1/admin/announcements",
            {
                "title": "重要服务恢复",
                "content": "服务已经恢复，可以重新查询。",
                "enabled": True,
                "serviceVisible": False,
                "notificationEnabled": True,
                "popupEnabled": True,
            },
            admin,
        )
        self.assertEqual(201, status)
        announcement_id = created["data"]["id"]
        self.assertTrue(created["data"]["notificationEnabled"])
        self.assertTrue(created["data"]["popupEnabled"])

        status, bootstrap = self.call("GET", "/api/v1/public/bootstrap")
        self.assertEqual(200, status)
        current = next(
            row for row in bootstrap["data"]["announcements"] if row["id"] == announcement_id
        )
        self.assertIs(current["notificationEnabled"], True)
        self.assertIs(current["popupEnabled"], True)

        status, edited = self.call(
            "PATCH",
            f"/api/v1/admin/announcements/{announcement_id}",
            {
                "title": "重要服务恢复（文字修正）",
                "content": "服务已经恢复，可以重新查询。",
                "enabled": True,
                "serviceVisible": False,
                "notificationEnabled": False,
                "popupEnabled": False,
            },
            admin,
        )
        self.assertEqual(200, status)
        self.assertFalse(edited["data"]["notificationEnabled"])
        self.assertFalse(edited["data"]["popupEnabled"])
        status, bootstrap = self.call("GET", "/api/v1/public/bootstrap")
        self.assertEqual(200, status)
        current = next(
            row for row in bootstrap["data"]["announcements"] if row["id"] == announcement_id
        )
        self.assertFalse(current["notificationEnabled"])
        self.assertFalse(current["popupEnabled"])

    def test_reservation_profile_rules_snapshot_and_transparency(self):
        _, _, token, user = self.phone_account()
        admin = self.admin_token()
        status, context = self.call(
            "GET", "/api/v1/sports/context", token=token
        )
        self.assertEqual(200, status)
        self.assertFalse(context["data"]["rules"]["requireReservationStudentId"])
        self.assertEqual("OPTIONAL", context["data"]["rules"]["reservationProfileMode"])
        self.assertIsNone(context["data"]["profile"]["studentId"])
        self.assertEqual(200, self.call(
            "PUT",
            "/api/v1/admin/reservation-rules",
            {
                "maxActivePerDay": 2,
                "maxUserCancellationsPerDay": 2,
                "cancelCutoffMinutes": 30,
                "restrictionHoursAfterExcessCancel": 24,
                "reservationProfileMode": "REQUIRED",
                "reason": "验证预约资料必填流程",
            },
            admin,
        )[0])
        request = {
            "venueId": "table-tennis",
            "date": (date.today() + timedelta(days=2)).isoformat(),
            "timeSlot": "14:00-15:00",
            "courtNumber": 8,
        }
        self.assertEqual(409, self.call(
            "POST", "/api/v1/sports/reservations", request, token
        )[0])
        status, profile = self.call(
            "PUT",
            "/api/v1/auth/reservation-profile",
            {
                "studentId": "TEST" + secrets.token_hex(5),
                "name": "预约测试同学",
                "consented": True,
            },
            token,
        )
        self.assertEqual(200, status)
        saved_id = profile["data"]["profile"]["studentId"]
        status, created = self.call(
            "POST", "/api/v1/sports/reservations", request, token
        )
        self.assertEqual(201, status)
        self.assertEqual(
            saved_id, created["data"]["reservationStudentIdSnapshot"]
        )
        self.assertIn("canCancel", created["data"])
        status, users = self.call(
            "GET", f"/api/v1/admin/users?q={saved_id}", token=admin
        )
        self.assertEqual(200, status)
        self.assertEqual(user["id"], users["data"][0]["id"])
        self.assertEqual(saved_id, users["data"][0]["reservation_student_id"])
        self.assertEqual("预约测试同学", users["data"][0]["reservation_name"])
        status, by_name = self.call(
            "GET", "/api/v1/admin/users?q=预约测试同学", token=admin
        )
        self.assertEqual(200, status)
        self.assertEqual(user["id"], by_name["data"][0]["id"])
        status, revealed = self.call(
            "POST",
            f"/api/v1/admin/users/{user['id']}/reservation-profile",
            {"reason": "场馆人工核验预约资料"},
            admin,
        )
        self.assertEqual(200, status)
        self.assertEqual(saved_id, revealed["data"]["studentId"])
        self.assertEqual("预约测试同学", revealed["data"]["name"])
        status, snapshot = self.call(
            "POST",
            f"/api/v1/admin/reservations/{created['data']['id']}/profile",
            {"reason": "核验历史预约资料快照"},
            admin,
        )
        self.assertEqual(200, status)
        self.assertEqual(saved_id, snapshot["data"]["studentId"])
        self.assertEqual(200, self.call(
            "DELETE", "/api/v1/auth/reservation-profile", token=token
        )[0])
        status, reservations = self.call(
            "GET", "/api/v1/sports/reservations", token=token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            saved_id, reservations["data"][0]["reservationStudentIdSnapshot"]
        )
        self.assertEqual(
            "预约测试同学", reservations["data"][0]["reservationNameSnapshot"]
        )
        status, audits = self.call("GET", "/api/v1/admin/audit-logs", token=admin)
        rule_audit = next(
            row for row in audits["data"]
            if row["action"] == "RESERVATION_RULES_UPDATED"
        )
        self.assertIn("old", json.loads(rule_audit["details_json"]))
        self.assertNotIn(saved_id, rule_audit["details_json"])
        audit_json = json.dumps(audits["data"], ensure_ascii=False)
        self.assertIn("RESERVATION_PROFILE_REVEALED", audit_json)
        self.assertNotIn("预约测试同学", audit_json)
        self.assertNotIn(saved_id, audit_json)

    def test_feedback_validation_filters_and_account_isolation(self):
        phone_a, _, token_a, user_a = self.phone_account()
        _, _, token_b, _ = self.phone_account()
        admin = self.admin_token()
        status, created = self.call(
            "POST",
            "/api/v1/feedback",
            {
                "category": "ACCOUNT_SECURITY",
                "title": "账号登录问题",
                "content": "<script>alert('x')</script>\n第二行说明",
                "appVersion": "test",
            },
            token_a,
        )
        self.assertEqual(201, status)
        feedback_id = created["data"]["id"]
        self.assertEqual([], self.call("GET", "/api/v1/feedback", token=token_b)[1]["data"])
        self.assertEqual(
            404,
            self.call("GET", f"/api/v1/feedback/{feedback_id}", token=token_b)[0],
        )
        self.assertEqual(
            400,
            self.call(
                "POST",
                "/api/v1/feedback",
                {"category": "INVALID", "title": "无效", "content": "内容"},
                token_a,
            )[0],
        )
        self.assertEqual(
            400,
            self.call(
                "PATCH",
                f"/api/v1/admin/feedback/{feedback_id}",
                {"status": "RESOLVED", "publicReply": ""},
                admin,
            )[0],
        )
        status, rows = self.call(
            "GET",
            f"/api/v1/admin/feedback?status=OPEN&category=ACCOUNT_SECURITY&user={phone_a}&q=登录",
            token=admin,
        )
        self.assertEqual(200, status)
        self.assertEqual(feedback_id, rows["data"][0]["id"])
        self.assertNotIn(phone_a, json.dumps(rows["data"], ensure_ascii=False))
        self.assertEqual(user_a["id"], rows["data"][0]["user_id"])

    def test_exact_phone_search_controlled_reveal_and_user_details(self):
        phone, _, token, user = self.phone_account()
        self.assertEqual(201, self.call(
            "POST",
            "/api/v1/feedback",
            {
                "category": "OTHER",
                "title": "关联查询",
                "content": "关联查询测试",
                "appVersion": "test",
            },
            token,
        )[0])
        admin = self.admin_token()
        status, users = self.call(
            "GET", f"/api/v1/admin/users?q={phone}", token=admin
        )
        self.assertEqual(200, status)
        self.assertEqual(user["id"], users["data"][0]["id"])
        self.assertEqual(phone, users["data"][0]["phone"])
        status, revealed = self.call(
            "POST",
            f"/api/v1/admin/users/{user['id']}/phone",
            {"reason": "用户主动报号后定位问题"},
            admin,
        )
        self.assertEqual(200, status)
        self.assertEqual(phone, revealed["data"]["phone"])
        status, details = self.call(
            "GET", f"/api/v1/admin/users/{user['id']}/details", token=admin
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(details["data"]["feedback"]))
        status, audits = self.call("GET", "/api/v1/admin/audit-logs", token=admin)
        audit_json = json.dumps(audits["data"], ensure_ascii=False)
        self.assertIn("USER_PHONE_REVEALED", audit_json)
        self.assertNotIn(phone, audit_json)

    def test_registration_login_logout_and_account_status_enforcement(self):
        phone, password, token, user = self.phone_account()
        self.assertEqual(200, self.call("GET", "/api/v1/auth/me", token=token)[0])
        duplicate_status, duplicate = self.call(
            "POST", "/api/v1/auth/register",
            {"phone": phone, "password": password, "nickname": "重复用户"},
        )
        self.assertEqual(409, duplicate_status)
        self.assertEqual("PHONE_EXISTS", duplicate["error"]["code"])
        self.assertEqual("该账号已存在，请直接登录", duplicate["error"]["message"])
        self.assertEqual(401, self.call(
            "POST", "/api/v1/auth/login",
            {"phone": phone, "password": "definitely-wrong-password"},
        )[0])
        self.assertEqual(200, self.call(
            "POST", "/api/v1/auth/login", {"phone": phone, "password": password},
        )[0])
        self.assertEqual(200, self.call("POST", "/api/v1/auth/logout", {}, token)[0])
        self.assertEqual(401, self.call("GET", "/api/v1/auth/me", token=token)[0])
        self.assertEqual(401, self.call("GET", "/api/v1/auth/me", token="invalid-token")[0])

        admin = self.admin_token()
        self.assertEqual(200, self.call(
            "PATCH", f"/api/v1/admin/users/{user['id']}",
            {"accountStatus": "DISABLED", "reason": "自动化权限测试"}, admin,
        )[0])
        disabled_status, disabled = self.call(
            "POST", "/api/v1/auth/login", {"phone": phone, "password": password},
        )
        self.assertEqual(403, disabled_status)
        self.assertNotIn(password, json.dumps(disabled, ensure_ascii=False))
        delete_status, delete_error = self.call(
            "PATCH", f"/api/v1/admin/users/{user['id']}",
            {"accountStatus": "DELETED", "reason": "自动化注销测试"}, admin,
        )
        self.assertEqual(409, delete_status)
        self.assertEqual("USE_ACCOUNT_DELETE_API", delete_error["error"]["code"])
        self.assertEqual(200, self.call(
            "PATCH", f"/api/v1/admin/users/{user['id']}",
            {"accountStatus": "ACTIVE", "reason": "恢复自动化权限测试"}, admin,
        )[0])
        self.assertEqual(200, self.call(
            "POST", "/api/v1/auth/login", {"phone": phone, "password": password},
        )[0])

    def test_admin_role_workspaces_and_server_side_module_isolation(self):
        super_token = self.admin_token()
        cases = [
            ("SPORTS_OPERATOR", "/api/v1/admin/overview/sports", "/api/v1/admin/lost-found"),
            ("LOST_FOUND_OPERATOR", "/api/v1/admin/overview/lost-found", "/api/v1/admin/venues"),
            ("LIBRARY_OPERATOR", "/api/v1/admin/overview/library", "/api/v1/admin/reservations"),
            ("CONTENT_OPERATOR", "/api/v1/admin/overview/content", "/api/v1/admin/users"),
        ]
        for role, allowed, forbidden in cases:
            username = f"operator-{role.lower()}-{secrets.token_hex(3)}"
            password = "Safe-local-" + secrets.token_urlsafe(16)
            status, created = self.call(
                "POST", "/api/v1/admin/admins",
                {"username": username, "nickname": role, "password": password, "adminRole": role},
                super_token,
            )
            self.assertEqual(201, status)
            login_status, login = self.call(
                "POST", "/api/v1/auth/login", {"username": username, "password": password},
            )
            self.assertEqual(200, login_status)
            operator_token = login["data"]["token"]
            self.assertEqual(role, login["data"]["user"]["adminRole"])
            self.assertEqual(200, self.call("GET", allowed, token=operator_token)[0])
            if role == "CONTENT_OPERATOR":
                self.assertEqual(
                    200,
                    self.call(
                        "GET",
                        "/api/v1/admin/website-announcements",
                        token=operator_token,
                    )[0],
                )
                for protected_detail in (
                    "/api/v1/admin/canonical-users",
                    "/api/v1/admin/devices",
                    "/api/v1/admin/installations",
                ):
                    self.assertEqual(
                        403,
                        self.call("GET", protected_detail, token=operator_token)[0],
                    )
            self.assertEqual(403, self.call("GET", forbidden, token=operator_token)[0])
            self.assertEqual(403, self.call("GET", "/api/v1/admin/admins", token=operator_token)[0])
            self.assertEqual(200, self.call(
                "PATCH", f"/api/v1/admin/admins/{created['data']['id']}",
                {"adminRole": role, "enabled": False}, super_token,
            )[0])
            self.assertEqual(401, self.call("GET", allowed, token=operator_token)[0])
            self.assertEqual(403, self.call(
                "POST", "/api/v1/auth/login",
                {"username": username, "password": password},
            )[0])

        status, audits = self.call("GET", "/api/v1/admin/audit-logs", token=super_token)
        self.assertEqual(200, status)
        serialized = json.dumps(audits["data"], ensure_ascii=False)
        self.assertIn("ADMIN_ACCESS_DENIED", serialized)
        self.assertNotIn("Safe-local-", serialized)

    def test_sports_operator_receives_complete_reservation_profile_only_in_sports(self):
        phone, _, user_token, user = self.phone_account()
        student_id = "SPORT" + secrets.token_hex(5)
        name = "体育核验同学"
        self.assertEqual(200, self.call(
            "PUT", "/api/v1/auth/reservation-profile",
            {"name": name, "studentId": student_id, "consented": True}, user_token,
        )[0])
        booking_date = (date.today() + timedelta(days=2)).isoformat()
        status, created = self.call(
            "POST", "/api/v1/sports/reservations",
            {"venueId": "badminton", "date": booking_date,
             "timeSlot": "10:00-11:00", "courtNumber": 8}, user_token,
        )
        self.assertEqual(201, status)
        reservation_id = created["data"]["id"]

        super_token = self.admin_token()
        operator_password = secrets.token_urlsafe(18)
        operator_username = "sports-op-" + secrets.token_hex(4)
        status, operator = self.call(
            "POST", "/api/v1/admin/admins",
            {"username": operator_username, "nickname": "体育运营测试",
             "password": operator_password, "adminRole": "SPORTS_OPERATOR"},
            super_token,
        )
        self.assertEqual(201, status)
        status, login = self.call(
            "POST", "/api/v1/auth/login",
            {"username": operator_username, "password": operator_password},
        )
        self.assertEqual(200, status)
        token = login["data"]["token"]

        status, listing = self.call(
            "GET", f"/api/v1/admin/reservations?paged=true&user={phone}", token=token,
        )
        self.assertEqual(200, status)
        self.assertEqual(1, listing["data"]["total"])
        row = listing["data"]["items"][0]
        self.assertEqual(phone, row["phone"])
        self.assertEqual(name, row["reservation_name_snapshot"])
        self.assertEqual(student_id, row["reservation_student_id_snapshot"])
        self.assertEqual(user["id"], row["user_id"])
        self.assertEqual(reservation_id, row["id"])

        status, details = self.call(
            "GET", f"/api/v1/admin/reservations/{reservation_id}", token=token,
        )
        self.assertEqual(200, status)
        self.assertEqual(phone, details["data"]["reservation"]["phone"])
        self.assertTrue(details["data"]["sportsHistory"])
        self.assertNotIn("lostFound", details["data"])
        self.assertNotIn("feedback", details["data"])
        self.assertEqual(200, self.call(
            "POST", f"/api/v1/admin/reservations/{reservation_id}/profile", {}, token,
        )[0])
        self.assertEqual(200, self.call(
            "POST", f"/api/v1/admin/reservations/{reservation_id}/verify",
            {"status": "VERIFIED"}, token,
        )[0])
        self.assertEqual(200, self.call(
            "POST", f"/api/v1/admin/reservations/{reservation_id}/cancel", {}, token,
        )[0])
        for forbidden in (
            f"/api/v1/admin/users/{user['id']}/details",
            "/api/v1/admin/lost-found",
            "/api/v1/admin/resources",
            "/api/v1/admin/audit-logs",
        ):
            self.assertEqual(403, self.call("GET", forbidden, token=token)[0])

        status, audits = self.call("GET", "/api/v1/admin/audit-logs", token=super_token)
        self.assertEqual(200, status)
        text = json.dumps(audits["data"], ensure_ascii=False)
        self.assertIn("SPORTS_RESERVATIONS_VIEWED", text)
        self.assertIn("RESERVATION_PROFILE_VERIFIED", text)
        self.assertNotIn(phone, text)
        self.assertNotIn(student_id, text)

    def test_feature_states_persist_publish_and_block_user_apis(self):
        _, _, token, _ = self.phone_account()
        admin = self.admin_token()
        booking_status, booking = self.call(
            "POST", "/api/v1/sports/reservations",
            {"venueId": "table-tennis", "date": (date.today() + timedelta(days=3)).isoformat(),
             "timeSlot": "14:00-15:00", "courtNumber": 10}, token,
        )
        self.assertEqual(201, booking_status)
        cases = [
            ("sports_reservation", "/api/v1/sports/venues"),
            ("lost_found", "/api/v1/lost-found"),
            ("resource_library", "/api/v1/library"),
        ]
        for feature_key, user_path in cases:
            for feature_status, expected_code in (
                ("MAINTENANCE", "FEATURE_MAINTENANCE"),
                ("CLOSED", "FEATURE_CLOSED"),
            ):
                update_status, update = self.call(
                    "PUT", f"/api/v1/admin/flags/{feature_key}",
                    {"payload": {"status": feature_status,
                                 "maintenanceNote": f"{feature_key} local test",
                                 "expectedRestoreAt": server.now_ms() + 60000},
                     "changeNote": "automated state transition"}, admin,
                )
                self.assertEqual(200, update_status)
                self.assertEqual(feature_status, update["data"]["payload"]["status"])
                bootstrap_status, bootstrap = self.call("GET", "/api/v1/public/bootstrap")
                self.assertEqual(200, bootstrap_status)
                flag = next(item for item in bootstrap["data"]["flags"] if item["key"] == feature_key)
                self.assertEqual(feature_status, flag["payload"]["status"])
                self.assertIsNotNone(flag["updatedAt"])
                blocked_status, blocked = self.call("GET", user_path, token=token)
                self.assertEqual(503, blocked_status)
                self.assertEqual(expected_code, blocked["error"]["code"])
                if feature_key == "sports_reservation" and feature_status == "CLOSED":
                    self.assertEqual(200, self.call(
                        "GET", "/api/v1/sports/reservations", token=token,
                    )[0])
                    self.assertEqual(200, self.call(
                        "POST", f"/api/v1/sports/reservations/{booking['data']['id']}/cancel",
                        {}, token,
                    )[0])
            self.assertEqual(200, self.call(
                "PUT", f"/api/v1/admin/flags/{feature_key}",
                {"payload": {"status": "OPEN"}}, admin,
            )[0])
            self.assertEqual(200, self.call("GET", user_path, token=token)[0])

        operator_password = secrets.token_urlsafe(18)
        operator_username = "sports-flag-denied-" + secrets.token_hex(3)
        self.assertEqual(201, self.call(
            "POST", "/api/v1/admin/admins",
            {"username": operator_username, "nickname": "体育只读开关",
             "password": operator_password, "adminRole": "SPORTS_OPERATOR"}, admin,
        )[0])
        _, login = self.call("POST", "/api/v1/auth/login",
                             {"username": operator_username, "password": operator_password})
        self.assertEqual(403, self.call(
            "PUT", "/api/v1/admin/flags/sports_reservation",
            {"payload": {"status": "CLOSED"}}, login["data"]["token"],
        )[0])

    def test_published_version_check_is_local_and_drafts_are_hidden(self):
        self.publish_seed_release_for_version_tests()
        admin = self.admin_token()
        payload = {
            "latestVersionCode": 42,
            "minimumVersionCode": 31,
            "latestVersionName": "4.2-local",
            "updateMessage": "本地版本演示",
            "downloadUrl": "https://updates.invalid/my-chuannong.apk",
            "apkSha256": "a" * 64,
            "apkSize": 123456,
            "packageName": "com.mychuannong.app",
            "signingCertificateSha256": server.OFFICIAL_SIGNING_CERTIFICATE_SHA256,
            "metadataVerified": True,
            "forceUpdate": False,
            "status": "DRAFT",
        }
        status, response = self.call("PUT", "/api/v1/admin/version/android", payload, admin)
        self.assertEqual(200, status, response)
        hidden = self.call("GET", "/api/v1/public/version/android?currentVersionCode=1")[1]
        self.assertFalse(hidden["data"]["updateAvailable"])
        invalid_status, invalid_response = self.call(
            "PUT",
            "/api/v1/admin/version/android",
            dict(payload, status="PUBLISHED", metadataVerified=False),
            admin,
        )
        self.assertEqual(409, invalid_status)
        self.assertEqual("APK_METADATA_UNVERIFIED", invalid_response["error"]["code"])
        payload["status"] = "PUBLISHED"
        self.assertEqual(200, self.call("PUT", "/api/v1/admin/version/android", payload, admin)[0])
        disabled = self.call(
            "GET", "/api/v1/public/version/android?currentVersionCode=31"
        )[1]["data"]
        self.assertFalse(disabled["updateAvailable"])
        self.assertEqual("NONE", disabled["decision"])
        # V1.0.4 falls back to latest.json when latestVersion is null. The
        # request-scoped neutral code proves updateEnabled=false without
        # exposing stale website policy, while releaseMetadata stays factual.
        self.assertEqual(31, disabled["latestVersion"]["latest_version_code"])
        self.assertEqual(42, disabled["releaseMetadata"]["versionCode"])
        self.assertFalse(disabled["latestVersion"]["force_update"])
        self.assertIs(type(disabled["latestVersion"]["metadata_verified"]), bool)
        config = self.call("GET", "/api/v1/admin/config", token=admin)[1]["data"]
        policy = config["updatePolicy"]
        self.assertEqual(42, config["releaseMetadata"]["versionCode"])
        self.assertNotIn("forceUpdate", config["releaseMetadata"])
        self.assertNotIn("minimumSupportedVersionCode", config["releaseMetadata"])
        optional_policy = {
            "updateEnabled": True,
            "forceUpdateEnabled": False,
            "minimumSupportedVersionCode": 31,
            "expectedPolicyRevision": policy["policyRevision"],
        }
        status, saved_policy = self.call(
            "PUT", "/api/v1/admin/update-policy/android", optional_policy, admin
        )
        self.assertEqual(200, status, saved_policy)
        status, newer = self.call("GET", "/api/v1/public/version/android?currentVersionCode=31")
        self.assertEqual(200, status)
        self.assertTrue(newer["data"]["updateAvailable"])
        self.assertFalse(newer["data"]["minimumVersionViolated"])
        self.assertFalse(newer["data"]["mandatoryUpdate"])
        below_minimum = self.call(
            "GET", "/api/v1/public/version/android?currentVersionCode=30"
        )[1]
        self.assertTrue(below_minimum["data"]["minimumVersionViolated"])
        self.assertFalse(below_minimum["data"]["mandatoryUpdate"])
        self.assertEqual(42, newer["data"]["latestVersion"]["latest_version_code"])
        self.assertFalse(newer["data"]["forceUpdate"])
        self.assertEqual(42, newer["data"]["latestVersion"]["versionCode"])
        self.assertEqual(
            "https://updates.invalid/my-chuannong.apk",
            newer["data"]["latestVersion"]["downloadUrl"],
        )
        self.assertEqual("a" * 64, newer["data"]["latestVersion"]["apkSha256"])
        self.assertEqual(123456, newer["data"]["latestVersion"]["apkSize"])
        current_revision = saved_policy["data"]["policyRevision"]
        forced_payload = {
            "updateEnabled": True,
            "forceUpdateEnabled": True,
            "minimumSupportedVersionCode": 42,
            "expectedPolicyRevision": current_revision,
        }
        self.assertEqual(200, self.call(
            "PUT", "/api/v1/admin/update-policy/android", forced_payload, admin,
        )[0])
        forced = self.call(
            "GET", "/api/v1/public/version/android?currentVersionCode=31"
        )[1]
        self.assertTrue(forced["data"]["updateAvailable"])
        self.assertTrue(forced["data"]["forceUpdate"])
        self.assertTrue(forced["data"]["mandatoryUpdate"])
        self.assertTrue(forced["data"]["latestVersion"]["force_update"])
        config = self.call("GET", "/api/v1/admin/config", token=admin)[1]
        self.assertTrue(config["data"]["updatePolicy"]["forceUpdateEnabled"])
        self.assertEqual(
            forced["data"]["policyRevision"],
            config["data"]["updatePolicy"]["policyRevision"],
        )
        revoked_payload = {
            **forced_payload,
            "forceUpdateEnabled": False,
            "expectedPolicyRevision": forced["data"]["policyRevision"],
        }
        self.assertEqual(200, self.call(
            "PUT", "/api/v1/admin/update-policy/android", revoked_payload, admin,
        )[0])
        revoked = self.call(
            "GET", "/api/v1/public/version/android?currentVersionCode=31"
        )[1]["data"]
        self.assertTrue(revoked["updateAvailable"])
        self.assertFalse(revoked["mandatoryUpdate"])
        self.assertFalse(revoked["latestVersion"]["force_update"])
        self.assertEqual(1, revoked["latestVersion"]["minimum_version_code"])
        self.assertGreater(revoked["policyRevision"], forced["data"]["policyRevision"])
        conflict_status, conflict = self.call(
            "PUT",
            "/api/v1/admin/update-policy/android",
            {**forced_payload, "expectedPolicyRevision": forced["data"]["policyRevision"]},
            admin,
        )
        self.assertEqual(409, conflict_status)
        self.assertEqual("POLICY_REVISION_CONFLICT", conflict["error"]["code"])
        forced_again_payload = {
            **forced_payload,
            "expectedPolicyRevision": revoked["policyRevision"],
        }
        forced_again_status, forced_again = self.call(
            "PUT", "/api/v1/admin/update-policy/android", forced_again_payload, admin
        )
        self.assertEqual(200, forced_again_status)
        self.assertGreater(
            forced_again["data"]["policyRevision"], revoked["policyRevision"]
        )
        forced_again_check = self.call(
            "GET", "/api/v1/public/update-policy/android?currentVersionCode=31"
        )[1]["data"]
        self.assertTrue(forced_again_check["mandatoryUpdate"])
        self.assertEqual(
            forced_again["data"]["policyRevision"],
            forced_again_check["policyRevision"],
        )
        self.assertIsInstance(forced_again_check["requestTimestamp"], int)
        request = urllib.request.Request(
            self.base + "/api/v1/public/update-policy/android?currentVersionCode=31"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(
                "no-store, no-cache, must-revalidate, max-age=0",
                response.headers["Cache-Control"],
            )
            self.assertEqual("no-cache", response.headers["Pragma"])
            self.assertEqual("0", response.headers["Expires"])
        audits = self.call("GET", "/api/v1/admin/audit-logs", token=admin)[1]["data"]
        policy_audit = next(
            row for row in audits if row["action"] == "APP_UPDATE_POLICY_UPDATED"
        )
        audit_details = json.loads(policy_audit["details_json"])
        self.assertIn("oldPolicy", audit_details)
        self.assertIn("newPolicy", audit_details)
        self.assertEqual(
            audit_details["newPolicy"]["policyRevision"],
            audit_details["policyRevision"],
        )
        current = self.call("GET", "/api/v1/public/version/android?currentVersionCode=42")[1]
        self.assertFalse(current["data"]["updateAvailable"])
        self.assertFalse(current["data"]["forceUpdate"])
        self.assertFalse(current["data"]["mandatoryUpdate"])
        unsafe = dict(payload, downloadUrl="javascript:alert(1)")
        self.assertEqual(400, self.call("PUT", "/api/v1/admin/version/android", unsafe, admin)[0])
        invalid_size = dict(payload, apkSize="not-an-integer")
        self.assertEqual(400, self.call("PUT", "/api/v1/admin/version/android", invalid_size, admin)[0])

    def test_daily_first_login_metric_and_detail_are_distinct_and_consistent(self):
        admin = self.admin_token()
        before_status, before = self.call("GET", "/api/v1/admin/overview", token=admin)
        self.assertEqual(200, before_status)

        phone_a, password_a, _, user_a = self.phone_account()
        phone_b, password_b, _, user_b = self.phone_account()

        # B logged in during an earlier lifecycle day; today's successful login
        # must still count for the daily product metric.
        yesterday = server.now_ms() - 2 * 86400000
        with server.connection() as db:
            db.execute("DELETE FROM user_behavior_events WHERE user_id=?", (user_b["id"],))
            db.execute(
                "UPDATE users SET created_at=?,first_login_at=? WHERE id=?",
                (yesterday, yesterday, user_b["id"]),
            )
        self.assertEqual(200, self.call(
            "POST", "/api/v1/auth/login",
            {"phone": phone_b, "password": password_b},
        )[0])

        # Repeated success events must not increase the distinct-account count.
        for _ in range(5):
            self.assertEqual(200, self.call(
                "POST", "/api/v1/auth/login",
                {"phone": phone_a, "password": password_a},
            )[0])

        after_status, after = self.call("GET", "/api/v1/admin/overview", token=admin)
        self.assertEqual(200, after_status)
        self.assertEqual(
            before["data"]["todayFirstLogins"] + 2,
            after["data"]["todayFirstLogins"],
        )

        for user in (user_a, user_b):
            status, details = self.call(
                "GET",
                f"/api/v1/admin/user-events?user={user['id']}&eventType=LOGIN_SUCCESS&firstLoginToday=true",
                token=admin,
            )
            self.assertEqual(200, status)
            self.assertEqual(1, details["data"]["total"])
            self.assertEqual(1, len(details["data"]["items"]))
            self.assertEqual(user["id"], details["data"]["items"][0]["user_id"])
            self.assertEqual("LOGIN_SUCCESS", details["data"]["items"][0]["event_type"])
            self.assertEqual("Asia/Shanghai", details["data"]["businessTimezone"])
            self.assertIn("去重账号数", details["data"]["firstLoginDefinition"])

        empty_status, empty = self.call(
            "GET",
            "/api/v1/admin/user-events?user=not-a-real-account&firstLoginToday=true",
            token=admin,
        )
        self.assertEqual(200, empty_status)
        self.assertEqual(0, empty["data"]["total"])
        self.assertEqual([], empty["data"]["items"])

    def test_user_and_admin_audit_streams_are_actor_scoped(self):
        phone, password, _, user = self.phone_account()
        self.assertEqual(200, self.call(
            "POST", "/api/v1/auth/login",
            {"phone": phone, "password": password},
        )[0])
        admin = self.admin_token()

        status, registered_events = self.call(
            "GET",
            f"/api/v1/admin/user-events?user={user['id']}&eventType=USER_REGISTER",
            token=admin,
        )
        self.assertEqual(200, status)
        self.assertGreaterEqual(registered_events["data"]["total"], 1)
        status, login_events = self.call(
            "GET",
            f"/api/v1/admin/user-events?user={user['id']}&eventType=LOGIN_SUCCESS",
            token=admin,
        )
        self.assertEqual(200, status)
        self.assertGreaterEqual(login_events["data"]["total"], 1)

        # Preserve a legacy misclassified row in storage; the admin query must
        # filter it by Actor identity rather than deleting or action-blacklisting it.
        legacy_audit_id = "legacy-" + secrets.token_hex(8)
        with server.connection() as db:
            db.execute(
                """INSERT INTO admin_audit_logs(
                       id,actor_user_id,action,entity_type,entity_id,details_json,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    legacy_audit_id,
                    user["id"],
                    "USER_REGISTER",
                    "USER",
                    user["id"],
                    "{\"legacy\":true}",
                    server.now_ms(),
                ),
            )

        status, audits = self.call("GET", "/api/v1/admin/audit-logs", token=admin)
        self.assertEqual(200, status)
        self.assertTrue(audits["data"])
        self.assertTrue(all(row["actor_type"] == "ADMIN" for row in audits["data"]))
        self.assertTrue(all(row["actor_role"] in server.ADMIN_ROLES for row in audits["data"]))
        self.assertFalse(any(row["actor_user_id"] == user["id"] for row in audits["data"]))
        self.assertFalse(any(row["action"] == "USER_REGISTER" for row in audits["data"]))
        self.assertTrue(any(row["action"] == "ADMIN_LOGIN_SUCCEEDED" for row in audits["data"]))

        with server.connection() as db:
            self.assertEqual(
                1,
                db.execute(
                    "SELECT COUNT(*) FROM admin_audit_logs WHERE id=?",
                    (legacy_audit_id,),
                ).fetchone()[0],
            )

        version_payload = {
            "latestVersionCode": 90,
            "minimumVersionCode": 80,
            "latestVersionName": "9.0-audit-test",
            "updateMessage": "审计隔离测试",
            "downloadUrl": "https://updates.invalid/audit-test.apk",
            "apkSha256": "b" * 64,
            "apkSize": 456789,
            "forceUpdate": True,
            "status": "DRAFT",
        }
        self.assertEqual(200, self.call(
            "PUT", "/api/v1/admin/version/android", version_payload, admin,
        )[0])
        status, audits_after_update = self.call(
            "GET", "/api/v1/admin/audit-logs", token=admin,
        )
        self.assertEqual(200, status)
        self.assertTrue(any(
            row["action"] == "APP_VERSION_UPDATED"
            and row["actor_type"] == "ADMIN"
            for row in audits_after_update["data"]
        ))

        serialized = json.dumps(
            {"events": registered_events["data"], "audits": audits_after_update["data"]},
            ensure_ascii=False,
        )
        self.assertNotIn(password, serialized)

    def test_resource_library_admin_publish_user_activity_and_complaint(self):
        _, _, token_a, _ = self.phone_account()
        _, _, token_b, _ = self.phone_account()
        admin = self.admin_token()
        payload = {
            "title": "自动化测试资料",
            "summary": "仅用于本地接口测试",
            "category": "学习资料",
            "tags": ["测试", "公开链接"],
            "audienceScope": "测试用户",
            "sourceLabel": "自动化测试",
            "usageNote": "请遵守版权与使用规则",
            "externalUrl": "https://example.org/resource",
            "publishStatus": "PUBLISHED",
            "visibility": "MEMBERS",
            "isFree": True,
            "sortOrder": 99,
        }
        status, created = self.call(
            "POST", "/api/v1/admin/resources", payload, admin
        )
        self.assertEqual(201, status)
        resource_id = created["data"]["id"]
        self.assertEqual(401, self.call("GET", "/api/v1/library?q=自动化")[0])
        public_payload = {
            **payload,
            "title": "公开浏览测试资料",
            "visibility": "PUBLIC",
        }
        status, public_created = self.call(
            "POST", "/api/v1/admin/resources", public_payload, admin
        )
        self.assertEqual(201, status)
        public_id = public_created["data"]["id"]
        self.assertEqual(200, self.call(
            "PUT",
            "/api/v1/admin/flags/resource_library",
            {
                "enabled": True,
                "payload": {
                    "status": "OPEN",
                    "allowGuestBrowsing": True,
                },
            },
            admin,
        )[0])
        status, guest_resources = self.call(
            "GET", "/api/v1/library?q=公开浏览"
        )
        self.assertEqual(200, status)
        self.assertEqual([public_id], [item["id"] for item in guest_resources["data"]])
        self.assertEqual(401, self.call(
            "POST", f"/api/v1/library/{public_id}/favorite", {}
        )[0])
        status, resources = self.call(
            "GET", "/api/v1/library?q=自动化", token=token_a
        )
        self.assertEqual(200, status)
        self.assertEqual(resource_id, resources["data"][0]["id"])
        self.assertEqual(200, self.call(
            "POST", f"/api/v1/library/{resource_id}/view", {}, token_a
        )[0])
        self.assertEqual(200, self.call(
            "POST", f"/api/v1/library/{resource_id}/favorite", {}, token_a
        )[0])
        status, favorites = self.call(
            "GET", "/api/v1/library?favorites=true", token=token_a
        )
        self.assertEqual(resource_id, favorites["data"][0]["id"])
        status, other_favorites = self.call(
            "GET", "/api/v1/library?favorites=true", token=token_b
        )
        self.assertEqual([], other_favorites["data"])
        status, complaint = self.call(
            "POST",
            f"/api/v1/library/{resource_id}/complaints",
            {"reason": "版权问题", "detail": "本地自动化投诉"},
            token_a,
        )
        self.assertEqual(201, status)
        complaint_id = complaint["data"]["id"]
        self.assertEqual(200, self.call(
            "PATCH",
            f"/api/v1/admin/resource-complaints/{complaint_id}",
            {"status": "RESOLVED", "resolutionNote": "已核查"},
            admin,
        )[0])
        self.assertEqual(200, self.call(
            "PATCH",
            f"/api/v1/admin/resources/{resource_id}",
            {"publishStatus": "UNLISTED"},
            admin,
        )[0])
        self.assertEqual(404, self.call(
            "GET", f"/api/v1/library/{resource_id}", token=token_a
        )[0])
        self.assertEqual(200, self.call(
            "DELETE", f"/api/v1/admin/resources/{resource_id}", token=admin
        )[0])
        self.assertEqual(200, self.call(
            "DELETE", f"/api/v1/admin/resources/{public_id}", token=admin
        )[0])

    def test_health_and_bootstrap(self):
        self.assertEqual(200, self.call("GET", "/health")[0])
        status, body = self.call("GET", "/api/v1/public/bootstrap")
        self.assertEqual(200, status)
        self.assertTrue(any(flag["key"] == "sports_reservation" for flag in body["data"]["flags"]))
        if body["data"].get("version"):
            self.assertIsInstance(body["data"]["version"]["force_update"], bool)

    def test_website_announcements_are_independent_from_app_announcements(self):
        admin = self.admin_token()
        app_status, app_response = self.call(
            "POST",
            "/api/v1/admin/announcements",
            {"title": "仅 App 公告", "content": "App 内容", "enabled": True},
            admin,
        )
        self.assertEqual(201, app_status)
        app_id = app_response["data"]["id"]

        website_status, website_response = self.call(
            "POST",
            "/api/v1/admin/website-announcements",
            {"title": "仅网站公告", "content": "官网内容", "enabled": True},
            admin,
        )
        self.assertEqual(201, website_status)
        website_id = website_response["data"]["id"]

        status, listing = self.call(
            "GET", "/api/v1/admin/website-announcements", token=admin
        )
        self.assertEqual(200, status)
        listed = next(row for row in listing["data"] if row["id"] == website_id)
        self.assertTrue(listed["enabled"])

        status, public = self.call("GET", "/api/v1/public/website-announcements")
        self.assertEqual(200, status)
        public_rows = public["data"]["websiteAnnouncements"]
        public_row = next(row for row in public_rows if row["id"] == website_id)
        self.assertEqual(
            {"id", "title", "content", "publishedAt", "updatedAt"},
            set(public_row),
        )
        self.assertNotIn(app_id, {row["id"] for row in public_rows})

        status, bootstrap = self.call("GET", "/api/v1/public/bootstrap")
        self.assertEqual(200, status)
        bootstrap_ids = {row["id"] for row in bootstrap["data"]["announcements"]}
        self.assertIn(app_id, bootstrap_ids)
        self.assertNotIn(website_id, bootstrap_ids)

        status, updates = self.call("GET", "/api/v1/public/service-updates")
        self.assertEqual(200, status)
        self.assertFalse(any(item["targetId"] == website_id for item in updates["data"]))

        self.assertEqual(
            200,
            self.call(
                "PATCH",
                f"/api/v1/admin/website-announcements/{website_id}",
                {"title": "网站公告已更新", "content": "更新后的官网内容", "enabled": False},
                admin,
            )[0],
        )
        status, public_after_disable = self.call(
            "GET", "/api/v1/public/website-announcements"
        )
        self.assertEqual(200, status)
        self.assertFalse(
            any(row["id"] == website_id for row in public_after_disable["data"]["websiteAnnouncements"])
        )

        self.assertEqual(
            200,
            self.call(
                "DELETE",
                f"/api/v1/admin/website-announcements/{website_id}",
                token=admin,
            )[0],
        )

    def test_service_updates_aggregation_and_visibility(self):
        # This class shares a synthetic database. Restore flags even if assertions
        # fail so its maintenance scenario cannot disable later reservation tests.
        with server.connection() as db:
            flags_before = [dict(row) for row in db.execute(
                "SELECT * FROM remote_flags WHERE key IN ('sports_reservation','lost_found','resource_library')"
            )]

        def restore_flags():
            with server.connection() as db:
                for row in flags_before:
                    db.execute(
                        "UPDATE remote_flags SET enabled=?,payload_json=?,updated_at=?,updated_by=?,change_note=? WHERE key=?",
                        (row["enabled"], row["payload_json"], row["updated_at"],
                         row["updated_by"], row["change_note"], row["key"]),
                    )

        self.addCleanup(restore_flags)
        self.publish_seed_release_for_version_tests()
        admin = self.admin_token()
        for feature_key in ("sports_reservation", "lost_found", "resource_library"):
            self.assertEqual(
                200,
                self.call(
                    "PUT",
                    f"/api/v1/admin/flags/{feature_key}",
                    {"payload": {"status": "OPEN", "maintenanceNote": ""}},
                    admin,
                )[0],
            )

        version_payload = {
            "latestVersionCode": 42,
            "minimumVersionCode": 31,
            "latestVersionName": "4.2-local",
            "updateMessage": "服务动态聚合测试版本",
            "downloadUrl": "https://updates.invalid/my-chuannong.apk",
            "apkSha256": "a" * 64,
            "apkSize": 123456,
            "packageName": "com.mychuannong.app",
            "signingCertificateSha256": server.OFFICIAL_SIGNING_CERTIFICATE_SHA256,
            "metadataVerified": True,
            "forceUpdate": False,
            "status": "DRAFT",
        }
        self.assertEqual(
            200,
            self.call("PUT", "/api/v1/admin/version/android", version_payload, admin)[0],
        )
        empty_status, empty = self.call("GET", "/api/v1/public/service-updates")
        self.assertEqual(200, empty_status)
        self.assertEqual([], empty["data"])
        version_payload["status"] = "PUBLISHED"
        self.assertEqual(
            200,
            self.call("PUT", "/api/v1/admin/version/android", version_payload, admin)[0],
        )
        policy = self.call("GET", "/api/v1/admin/config", token=admin)[1]["data"]["updatePolicy"]
        self.assertEqual(200, self.call(
            "PUT",
            "/api/v1/admin/update-policy/android",
            {
                "updateEnabled": True,
                "forceUpdateEnabled": False,
                "minimumSupportedVersionCode": 31,
                "expectedPolicyRevision": policy["policyRevision"],
            },
            admin,
        )[0])

        visible = self.call(
            "POST",
            "/api/v1/admin/announcements",
            {"title": "服务页可见公告", "content": "公告正文", "serviceVisible": True},
            admin,
        )
        hidden = self.call(
            "POST",
            "/api/v1/admin/announcements",
            {"title": "服务页隐藏公告", "content": "不应出现", "serviceVisible": False},
            admin,
        )
        disabled = self.call(
            "POST",
            "/api/v1/admin/announcements",
            {"title": "撤回公告", "content": "不应出现", "serviceVisible": True, "enabled": False},
            admin,
        )
        self.assertEqual(201, visible[0])
        self.assertEqual(201, hidden[0])
        self.assertEqual(201, disabled[0])

        status, updates = self.call("GET", "/api/v1/public/service-updates")
        self.assertEqual(200, status)
        items = updates["data"]
        self.assertEqual(["UPDATE"], [item["type"] for item in items if item["type"] == "UPDATE"])
        update_item = next(item for item in items if item["type"] == "UPDATE")
        self.assertEqual("ABOUT_UPDATE", update_item["targetType"])
        self.assertEqual(42, update_item["versionCode"])
        self.assertIn("announcement-", items[-1]["id"])
        self.assertEqual("服务页可见公告", items[-1]["title"])
        self.assertEqual("ANNOUNCEMENT", items[-1]["targetType"])
        self.assertIsNotNone(items[-1]["targetId"])
        self.assertTrue(all("maintenanceNote" not in item for item in items))
        self.assertTrue(all("changeNote" not in item for item in items))
        self.assertFalse(any(item["title"] == "服务页隐藏公告" for item in items))
        self.assertFalse(any(item["title"] == "撤回公告" for item in items))

        for sports_status in ("CLOSED", "MAINTENANCE"):
            self.assertEqual(200, self.call(
                "PUT", "/api/v1/admin/flags/sports_reservation",
                {"payload": {"status": sports_status}}, admin,
            )[0])
            _, updates = self.call("GET", "/api/v1/public/service-updates")
            self.assertEqual(items, updates["data"])

        self.assertEqual(
            200,
            self.call(
                "PUT",
                "/api/v1/admin/flags/lost_found",
                {"payload": {"status": "MAINTENANCE", "maintenanceNote": "失物招领维护"}},
                admin,
            )[0],
        )
        _, updates = self.call("GET", "/api/v1/public/service-updates")
        maintenance = [item for item in updates["data"] if item["type"] == "MAINTENANCE"]
        self.assertEqual(1, len(maintenance))
        self.assertEqual("lost_found", maintenance[0]["moduleKey"])
        self.assertEqual("失物招领维护中", maintenance[0]["title"])
        self.assertEqual("FEATURE", maintenance[0]["targetType"])
        self.assertEqual("失物招领维护", maintenance[0]["summary"])

        self.assertEqual(
            200,
            self.call(
                "PUT",
                "/api/v1/admin/flags/lost_found",
                {"payload": {"status": "CLOSED", "maintenanceNote": "暂不开放"}},
                admin,
            )[0],
        )
        _, updates = self.call("GET", "/api/v1/public/service-updates")
        closed = [item for item in updates["data"] if item["type"] == "CLOSED"]
        self.assertEqual(1, len(closed))
        self.assertEqual("lost_found", closed[0]["moduleKey"])
        self.assertEqual(90, closed[0]["priority"])
        self.assertEqual("FEATURE", closed[0]["targetType"])
        self.assertEqual("暂不开放", closed[0]["summary"])

        self.assertEqual(
            200,
            self.call(
                "PUT",
                "/api/v1/admin/flags/lost_found",
                {"payload": {"status": "OPEN"}},
                admin,
            )[0],
        )
        _, updates = self.call("GET", "/api/v1/public/service-updates")
        self.assertFalse(any(item["type"] == "MAINTENANCE" for item in updates["data"]))
        self.assertFalse(any(item["type"] == "CLOSED" for item in updates["data"]))

    def test_core_pagination_and_error_model(self):
        self.assertEqual((50, 0), core.parse_pagination({}))
        self.assertEqual((25, 10), core.parse_pagination({"limit": "25", "offset": "10"}))
        self.assertEqual((100, 10000), core.parse_pagination({"limit": "999", "offset": "99999"}))
        with self.assertRaises(core.ApiError) as ctx:
            core.parse_pagination({"limit": "abc"})
        self.assertEqual(422, ctx.exception.status)
        self.assertEqual("VALIDATION_ERROR", ctx.exception.code)
        response = core.paginated_response(["a"], 1, 50, 0)
        self.assertEqual({"items": ["a"], "total": 1, "limit": 50, "offset": 0}, response)

    def test_core_test_runtime_refuses_default_database(self):
        default_path = core.ROOT / "data" / "my-chuannong.db"
        with self.assertRaises(RuntimeError):
            core.configure_test_runtime(default_path)

    def test_reservation_is_atomic_and_cancel_restores_inventory(self):
        token_a = self.device_token("reservation-device-a")
        token_b = self.device_token("reservation-device-b")
        booking_date = (date.today() + timedelta(days=1)).isoformat()
        request = {
            "venueId": "badminton",
            "date": booking_date,
            "timeSlot": "09:00-10:00",
            "courtNumber": 3,
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda token: self.call("POST", "/api/v1/sports/reservations", request, token),
                [token_a, token_b],
            ))
        self.assertEqual([201, 409], sorted(result[0] for result in results))
        winner = next(result for result in results if result[0] == 201)
        winner_token = token_a if results[0][0] == 201 else token_b
        reservation_id = winner[1]["data"]["id"]
        self.assertEqual(200, self.call(
            "POST", f"/api/v1/sports/reservations/{reservation_id}/cancel", {}, winner_token
        )[0])
        loser_token = token_b if winner_token == token_a else token_a
        self.assertEqual(201, self.call(
            "POST", "/api/v1/sports/reservations", request, loser_token
        )[0])

    def test_lost_found_crud_search_favorite_and_ownership(self):
        owner = self.device_token("lost-owner")
        viewer = self.device_token("lost-viewer")
        payload = {
            "clientLocalId": "legacy-local-id",
            "type": "FOUND",
            "itemName": "蓝色雨伞",
            "category": "UMBRELLA",
            "location": "图书馆",
            "eventDate": date.today().isoformat(),
            "description": "伞柄有白色贴纸",
            "claimNote": "请说明特征",
        }
        status, created = self.call("POST", "/api/v1/lost-found", payload, owner)
        self.assertEqual(201, status)
        post_id = created["data"]["id"]
        status, search = self.call("GET", "/api/v1/lost-found?q=雨伞", token=viewer)
        self.assertEqual(200, status)
        self.assertEqual(post_id, search["data"][0]["id"])
        self.assertEqual(200, self.call("POST", f"/api/v1/lost-found/{post_id}/favorite", {}, viewer)[0])
        self.assertEqual(200, self.call("POST", f"/api/v1/lost-found/{post_id}/claim", {}, viewer)[0])
        self.assertEqual(201, self.call(
            "POST",
            f"/api/v1/lost-found/{post_id}/report",
            {"reason": "测试无效信息"},
            viewer,
        )[0])
        self.assertEqual(403, self.call("DELETE", f"/api/v1/lost-found/{post_id}", token=viewer)[0])
        self.assertEqual(200, self.call(
            "PATCH", f"/api/v1/lost-found/{post_id}", {"status": "RETURNED"}, owner
        )[0])
        self.assertEqual(200, self.call("DELETE", f"/api/v1/lost-found/{post_id}", token=owner)[0])
        self.assertEqual(404, self.call("GET", f"/api/v1/lost-found/{post_id}", token=owner)[0])

    def test_lost_found_image_admin_detail_delete_tombstone_and_public_access(self):
        _, _, owner, _ = self.phone_account()
        _, _, viewer, _ = self.phone_account()
        admin = self.admin_token()
        image_buffer = io.BytesIO()
        Image.new("RGB", (48, 32), (47, 111, 214)).save(image_buffer, "PNG")
        client_local_id = "image-tombstone-" + secrets.token_hex(8)
        payload = {
            "clientLocalId": client_local_id,
            "type": "FOUND",
            "itemName": "图片闭环测试",
            "category": "ELECTRONIC",
            "location": "测试地点",
            "eventDate": date.today().isoformat(),
            "description": "管理员应能查看图片与详情",
            "imageBase64": base64.b64encode(image_buffer.getvalue()).decode(),
            "status": "WAITING",
        }
        status, created = self.call("POST", "/api/v1/lost-found", payload, owner)
        self.assertEqual(201, status)
        post_id = created["data"]["id"]
        image_url = created["data"]["imageUrl"]
        self.assertTrue(image_url.startswith("/uploads/"))
        with urllib.request.urlopen(self.base + image_url, timeout=5) as response:
            self.assertEqual(200, response.status)
            self.assertEqual("image/png", response.headers.get_content_type())
            self.assertGreater(len(response.read()), 0)
        status, detail = self.call(
            "GET", f"/api/v1/admin/lost-found/{post_id}", token=admin
        )
        self.assertEqual(200, status)
        self.assertEqual("图片闭环测试", detail["data"]["item_name"])
        self.assertTrue(detail["data"]["image_name"].endswith(".png"))
        self.assertEqual(200, self.call(
            "PATCH", f"/api/v1/admin/lost-found/{post_id}",
            {"status": "CLAIMED"}, admin,
        )[0])
        self.assertEqual("CLAIMED", self.call(
            "GET", f"/api/v1/lost-found/{post_id}", token=viewer
        )[1]["data"]["status"])
        self.assertEqual(200, self.call(
            "DELETE", f"/api/v1/admin/lost-found/{post_id}", token=admin
        )[0])
        self.assertEqual(404, self.call(
            "GET", f"/api/v1/lost-found/{post_id}", token=viewer
        )[0])
        retry_status, retry = self.call("POST", "/api/v1/lost-found", payload, owner)
        self.assertEqual(410, retry_status)
        self.assertEqual("POST_DELETED", retry["error"]["code"])
        with server.connection() as db:
            self.assertIsNotNone(db.execute(
                """SELECT 1 FROM lost_found_deletion_tombstones
                   WHERE client_local_id=?""",
                (client_local_id,),
            ).fetchone())

    def test_admin_bulk_lost_found_delete_requires_exact_confirmation(self):
        _, _, owner, _ = self.phone_account()
        admin = self.admin_token()
        ids = []
        for index in range(2):
            status, created = self.call(
                "POST",
                "/api/v1/lost-found",
                {
                    "clientLocalId": f"bulk-delete-{index}-{secrets.token_hex(5)}",
                    "type": "FOUND",
                    "itemName": f"批量治理测试 {index}",
                    "category": "OTHER",
                    "location": "测试地点",
                    "eventDate": date.today().isoformat(),
                },
                owner,
            )
            self.assertEqual(201, status)
            ids.append(created["data"]["id"])
        self.assertEqual(400, self.call(
            "POST",
            "/api/v1/admin/lost-found/bulk-delete",
            {"ids": ids, "reason": "自动化违规内容清理", "confirmation": "DELETE"},
            admin,
        )[0])
        status, deleted = self.call(
            "POST",
            "/api/v1/admin/lost-found/bulk-delete",
            {
                "ids": ids,
                "reason": "自动化违规内容清理",
                "confirmation": "DELETE 2 ITEMS",
            },
            admin,
        )
        self.assertEqual(200, status)
        self.assertEqual(2, deleted["data"]["deleted"])
        for post_id in ids:
            self.assertEqual(404, self.call(
                "GET", f"/api/v1/lost-found/{post_id}", token=owner
            )[0])

    def test_timed_account_limit_blocks_writes_and_automatically_restores(self):
        phone, password, token, user = self.phone_account()
        admin = self.admin_token()
        status, limited = self.call(
            "PATCH",
            f"/api/v1/admin/users/{user['id']}",
            {
                "accountStatus": "LIMITED",
                "suspensionDuration": "7_DAYS",
                "reason": "自动化七天暂停测试",
            },
            admin,
        )
        self.assertEqual(200, status)
        self.assertGreater(limited["data"]["account_status_expires_at"], server.now_ms())
        login_status, limited_login = self.call(
            "POST", "/api/v1/auth/login", {"phone": phone, "password": password},
        )
        self.assertEqual(200, login_status)
        self.assertEqual("LIMITED", limited_login["data"]["user"]["accountStatus"])
        self.assertGreater(
            limited_login["data"]["user"]["accountStatusExpiresAt"], server.now_ms()
        )
        duplicate_status, duplicate = self.call(
            "POST", "/api/v1/auth/register",
            {"phone": phone, "password": password, "nickname": "绕过限制测试"},
        )
        self.assertEqual(409, duplicate_status)
        self.assertEqual("该账号已存在，请直接登录", duplicate["error"]["message"])
        self.assertEqual(200, self.call("GET", "/api/v1/auth/me", token=token)[0])
        self.assertEqual(403, self.call("GET", "/api/v1/lost-found", token=token)[0])
        self.assertEqual(403, self.call("GET", "/api/v1/library", token=token)[0])
        self.assertEqual(403, self.call(
            "POST",
            "/api/v1/lost-found",
            {
                "type": "FOUND",
                "itemName": "受限发布",
                "category": "OTHER",
                "location": "测试地点",
                "eventDate": date.today().isoformat(),
            },
            token,
        )[0])
        with server.connection() as db:
            db.execute(
                "UPDATE users SET account_status_expires_at=? WHERE id=?",
                (server.now_ms() - 1, user["id"]),
            )
        self.assertEqual(200, self.call("GET", "/api/v1/auth/me", token=token)[0])
        with server.connection() as db:
            restored = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            self.assertEqual("ACTIVE", restored["account_status"])
            self.assertIsNone(restored["account_status_expires_at"])
        self.assertEqual(201, self.call(
            "POST",
            "/api/v1/lost-found",
            {
                "type": "FOUND",
                "itemName": "恢复后发布",
                "category": "OTHER",
                "location": "测试地点",
                "eventDate": date.today().isoformat(),
            },
            token,
        )[0])
        self.assertEqual(200, self.call(
            "PATCH",
            f"/api/v1/admin/users/{user['id']}",
            {
                "accountStatus": "LIMITED",
                "suspensionDuration": "30_DAYS",
                "reason": "自动化三十天暂停测试",
            },
            admin,
        )[0])
        self.assertEqual(200, self.call(
            "PATCH",
            f"/api/v1/admin/users/{user['id']}",
            {"accountStatus": "ACTIVE", "reason": "管理员手动恢复账号"},
            admin,
        )[0])
        self.assertEqual(200, self.call("GET", "/api/v1/lost-found", token=token)[0])

    def test_bulk_user_governance_is_user_only_and_requires_second_confirmation(self):
        _, _, token_a, user_a = self.phone_account()
        _, _, token_b, user_b = self.phone_account()
        admin = self.admin_token()
        ids = [user_a["id"], user_b["id"]]
        self.assertEqual(400, self.call(
            "POST",
            "/api/v1/admin/users/bulk-status",
            {
                "ids": ids,
                "accountStatus": "DELETED",
                "reason": "自动化测试账号清理",
                "confirmation": "UPDATE USERS",
            },
            admin,
        )[0])
        status, updated = self.call(
            "POST",
            "/api/v1/admin/users/bulk-status",
            {
                "ids": ids,
                "accountStatus": "DELETED",
                "reason": "自动化测试账号清理",
                "confirmation": "DELETE 2 USERS",
            },
            admin,
        )
        self.assertEqual(200, status)
        self.assertEqual(2, updated["data"]["updated"])
        self.assertEqual(401, self.call("GET", "/api/v1/auth/me", token=token_a)[0])
        self.assertEqual(401, self.call("GET", "/api/v1/auth/me", token=token_b)[0])
        self.assertEqual(200, self.call("GET", "/api/v1/admin/overview", token=admin)[0])
        with server.connection() as db:
            for user_id in ids:
                deleted_user = db.execute(
                    "SELECT * FROM users WHERE id=?", (user_id,)
                ).fetchone()
                self.assertEqual("DELETED", deleted_user["account_status"])
                self.assertTrue(deleted_user["username"].startswith("deleted_"))
                self.assertIsNone(deleted_user["phone_hash"])
            self.assertGreaterEqual(
                db.execute("SELECT COUNT(*) FROM users WHERE role='ADMIN'").fetchone()[0],
                1,
            )

    def test_deleted_user_purge_is_super_admin_only_and_foreign_key_safe(self):
        phone, _, _, user = self.phone_account()
        super_token = self.admin_token()
        self.assertEqual(200, self.call(
            "DELETE",
            f"/api/v1/admin/users/{user['id']}",
            {"reason": "永久清理接口测试", "confirmation": f"DELETE USER {user['id']}"},
            super_token,
        )[0])

        operator_password = secrets.token_urlsafe(18)
        operator_username = "purge-denied-" + secrets.token_hex(4)
        self.assertEqual(201, self.call(
            "POST",
            "/api/v1/admin/admins",
            {"username": operator_username, "nickname": "清理权限测试", "password": operator_password,
             "adminRole": "CONTENT_OPERATOR"},
            super_token,
        )[0])
        _, operator_login = self.call(
            "POST", "/api/v1/auth/login",
            {"username": operator_username, "password": operator_password},
        )
        purge_body = {
            "ids": [user["id"]],
            "confirmation": "PURGE 1 DELETED USERS",
        }
        denied_status, denied = self.call(
            "POST", "/api/v1/admin/users/purge", purge_body, operator_login["data"]["token"]
        )
        self.assertEqual(403, denied_status)
        self.assertIn(denied["error"]["code"], {"ADMIN_MODULE_FORBIDDEN", "ADMIN_PERMISSION_REQUIRED", "SUPER_ADMIN_REQUIRED"})

        purged_status, purged = self.call(
            "POST", "/api/v1/admin/users/purge", purge_body, super_token
        )
        self.assertEqual(200, purged_status)
        self.assertEqual(1, purged["data"]["purged"])
        self.assertEqual(user["userCode"], purged["data"]["userCodes"][0])
        with server.connection() as db:
            self.assertIsNone(db.execute("SELECT id FROM users WHERE id=?", (user["id"],)).fetchone())
            self.assertEqual([], db.execute("PRAGMA foreign_key_check").fetchall())
            audit = db.execute(
                "SELECT details_json FROM admin_audit_logs WHERE action='USER_PURGE_COMPLETED' AND entity_id=?",
                (purged["data"]["operationId"],),
            ).fetchone()
        self.assertIsNotNone(audit)
        self.assertNotIn(phone, audit["details_json"])

        _, _, _, active_user = self.phone_account()
        active_status, active_error = self.call(
            "POST",
            "/api/v1/admin/users/purge",
            {"ids": [active_user["id"]], "confirmation": "PURGE 1 DELETED USERS"},
            super_token,
        )
        self.assertEqual(409, active_status)
        self.assertEqual("PURGE_TARGET_CHANGED", active_error["error"]["code"])

    def test_admin_delete_user_scrubs_personal_data_and_allows_phone_reregistration(self):
        phone, _, token, user = self.phone_account()
        admin = self.admin_token()
        image_buffer = io.BytesIO()
        Image.new("RGB", (32, 24), (40, 120, 220)).save(image_buffer, "PNG")
        status, post = self.call(
            "POST",
            "/api/v1/lost-found",
            {
                "clientLocalId": "admin-account-delete-" + secrets.token_hex(6),
                "type": "FOUND",
                "itemName": "待随账号清理的内容",
                "category": "OTHER",
                "location": "测试地点",
                "eventDate": date.today().isoformat(),
                "imageBase64": base64.b64encode(image_buffer.getvalue()).decode(),
            },
            token,
        )
        self.assertEqual(201, status)
        image_name = post["data"]["imageUrl"].rsplit("/", 1)[-1]
        self.assertTrue((server.UPLOADS / image_name).is_file())
        self.assertEqual(201, self.call(
            "POST",
            "/api/v1/feedback",
            {"category": "OTHER", "title": "待清理反馈", "content": "用户个人反馈内容"},
            token,
        )[0])
        with server.connection() as db:
            announcement_count = db.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
            resource_count = db.execute("SELECT COUNT(*) FROM resource_items").fetchone()[0]

        endpoint = f"/api/v1/admin/users/{user['id']}"
        self.assertEqual(400, self.call(
            "DELETE",
            endpoint,
            {"reason": "发布前测试账号清理", "confirmation": "DELETE USER"},
            admin,
        )[0])
        status, deleted = self.call(
            "DELETE",
            endpoint,
            {
                "reason": "发布前测试账号清理",
                "confirmation": f"DELETE USER {user['id']}",
            },
            admin,
        )
        self.assertEqual(200, status)
        self.assertTrue(deleted["data"]["deleted"])
        self.assertEqual(401, self.call("GET", "/api/v1/auth/me", token=token)[0])
        self.assertFalse((server.UPLOADS / image_name).exists())
        default_status, default_users = self.call(
            "GET", "/api/v1/admin/users", token=admin
        )
        self.assertEqual(200, default_status)
        self.assertNotIn(user["id"], {row["id"] for row in default_users["data"]})
        deleted_status, deleted_users = self.call(
            "GET", "/api/v1/admin/users?status=DELETED", token=admin
        )
        self.assertEqual(200, deleted_status)
        self.assertIn(user["id"], {row["id"] for row in deleted_users["data"]})
        restore_status, restore_error = self.call(
            "PATCH",
            endpoint,
            {"accountStatus": "ACTIVE", "reason": "不应恢复已清理账号"},
            admin,
        )
        self.assertEqual(409, restore_status)
        self.assertEqual("ACCOUNT_DELETION_FINAL", restore_error["error"]["code"])
        with server.connection() as db:
            deleted_user = db.execute(
                "SELECT * FROM users WHERE id=?", (user["id"],)
            ).fetchone()
            self.assertEqual("DELETED", deleted_user["account_status"])
            self.assertTrue(deleted_user["username"].startswith("deleted_"))
            self.assertIsNone(deleted_user["phone_hash"])
            self.assertIsNone(deleted_user["phone_encrypted"])
            self.assertEqual(0, db.execute(
                "SELECT COUNT(*) FROM lost_found_posts WHERE owner_user_id=?", (user["id"],)
            ).fetchone()[0])
            self.assertEqual(0, db.execute(
                "SELECT COUNT(*) FROM feedback WHERE user_id=?", (user["id"],)
            ).fetchone()[0])
            self.assertEqual(announcement_count, db.execute(
                "SELECT COUNT(*) FROM announcements"
            ).fetchone()[0])
            self.assertEqual(resource_count, db.execute(
                "SELECT COUNT(*) FROM resource_items"
            ).fetchone()[0])

        new_password = secrets.token_urlsafe(18)
        status, registered = self.call(
            "POST",
            "/api/v1/auth/register",
            {"phone": phone, "password": new_password, "nickname": "重新注册用户"},
        )
        self.assertEqual(201, status)
        self.assertNotEqual(user["id"], registered["data"]["user"]["id"])

    def test_client_local_id_is_idempotent(self):
        token = self.device_token("migration-device")
        payload = {
            "clientLocalId": "same-local-id",
            "type": "LOST",
            "itemName": "校园卡",
            "category": "CARD",
            "location": "教学楼",
            "eventDate": date.today().isoformat(),
        }
        first = self.call("POST", "/api/v1/lost-found", payload, token)
        second = self.call("POST", "/api/v1/lost-found", payload, token)
        self.assertEqual(first[1]["data"]["id"], second[1]["data"]["id"])

    def test_admin_foundation_is_protected_and_configurable(self):
        ordinary = self.device_token("ordinary-admin-check")
        self.assertEqual(403, self.call("GET", "/api/v1/admin/venues", token=ordinary)[0])
        generated_secret = secrets.token_urlsafe(24)
        digest, salt = server.password_record(generated_secret)
        admin_id = "test-admin-id"
        with server.connection() as db:
            db.execute(
                """INSERT OR REPLACE INTO users(
                       id,username,password_hash,password_salt,role,account_type,nickname,
                       admin_permissions_json,admin_role,admin_enabled,account_status,created_at
                   ) VALUES(?,?,?,?, 'ADMIN','ADMIN','测试管理员','["*"]',
                            'SUPER_ADMIN',1,'ACTIVE',?)""",
                (admin_id, "test-admin", digest, salt, server.now_ms()),
            )
        status, login = self.call(
            "POST",
            "/api/v1/auth/login",
            {"username": "test-admin", "password": generated_secret},
        )
        self.assertEqual(200, status)
        token = login["data"]["token"]
        self.assertEqual(200, self.call(
            "PATCH",
            "/api/v1/admin/venues/badminton",
            {"priceYuan": 22, "courtCount": 8, "enabled": True},
            token,
        )[0])
        self.assertEqual(200, self.call(
            "PUT",
            "/api/v1/admin/flags/lost_found",
            {"enabled": True, "payload": {"maintenance": False}},
            token,
        )[0])

        self.assertEqual(200, self.call(
            "PATCH",
            "/api/v1/admin/venues/badminton",
            {
                "name": "羽毛球馆",
                "priceYuan": 22,
                "courtCount": 8,
                "enabled": True,
                "operatingStatus": "MAINTENANCE",
                "maintenanceNote": "测试维护",
            },
            token,
        )[0])
        status, venues = self.call("GET", "/api/v1/admin/venues", token=token)
        self.assertEqual(200, status)
        badminton = next(item for item in venues["data"] if item["id"] == "badminton")
        self.assertEqual("MAINTENANCE", badminton["operating_status"])
        self.assertTrue(badminton["timeSlots"])

        booking_date = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(200, self.call(
            "POST",
            "/api/v1/admin/inventory/block",
            {
                "venueId": "badminton",
                "date": booking_date,
                "timeSlot": "09:00-10:00",
                "courtNumber": 1,
                "reason": "单元测试",
            },
            token,
        )[0])
        status, inventory = self.call(
            "GET",
            f"/api/v1/admin/inventory?venueId=badminton&date={booking_date}",
            token=token,
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(inventory["data"]))
        block_id = inventory["data"][0]["id"]
        self.assertEqual(200, self.call(
            "DELETE", f"/api/v1/admin/inventory/block/{block_id}", token=token
        )[0])

        status, announcement = self.call(
            "POST",
            "/api/v1/admin/announcements",
            {"title": "测试公告", "content": "本地后台测试", "enabled": True},
            token,
        )
        self.assertEqual(201, status)
        announcement_id = announcement["data"]["id"]
        self.assertEqual(200, self.call(
            "PATCH",
            f"/api/v1/admin/announcements/{announcement_id}",
            {"enabled": False},
            token,
        )[0])
        status, config = self.call("GET", "/api/v1/admin/config", token=token)
        self.assertEqual(200, status)
        self.assertIn("flags", config["data"])
        self.assertIn("version", config["data"])
        self.assertEqual(200, self.call(
            "PATCH",
            "/api/v1/admin/venues/badminton",
            {"operatingStatus": "OPEN", "maintenanceNote": ""},
            token,
        )[0])

        lost_payload = {
            "type": "FOUND",
            "itemName": "后台测试物品",
            "category": "OTHER",
            "location": "测试地点",
            "eventDate": date.today().isoformat(),
            "status": "WAITING",
        }
        status, created = self.call(
            "POST", "/api/v1/lost-found", lost_payload, ordinary
        )
        self.assertEqual(201, status)
        lost_id = created["data"]["id"]
        self.assertEqual(200, self.call(
            "PATCH",
            f"/api/v1/admin/lost-found/{lost_id}",
            {"status": "RETURNED"},
            token,
        )[0])
        self.assertEqual(200, self.call(
            "DELETE", f"/api/v1/admin/lost-found/{lost_id}", token=token
        )[0])
        self.assertEqual(404, self.call(
            "GET", f"/api/v1/lost-found/{lost_id}", token=ordinary
        )[0])

        self.assertEqual(200, self.call("POST", "/api/v1/auth/logout", {}, token)[0])
        self.assertEqual(401, self.call("GET", "/api/v1/admin/overview", token=token)[0])

    def test_admin_edit_permissions_and_last_super_admin_protection(self):
        admin_token = self.admin_token()
        with server.connection() as db:
            admin_id = db.execute(
                "SELECT id FROM users WHERE username LIKE 'admin-%' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()["id"]

        second_id = "super-two-" + secrets.token_hex(8)
        second_username = "super-two-" + secrets.token_hex(6)
        second_digest, second_salt = server.password_record(secrets.token_urlsafe(24))
        with server.connection() as db:
            db.execute(
                """INSERT INTO users(
                       id,username,password_hash,password_salt,role,account_type,nickname,
                       admin_permissions_json,admin_role,admin_enabled,account_status,created_at
                   ) VALUES(?,?,?,?, 'ADMIN','ADMIN','第二超级管理员','["*"]','SUPER_ADMIN',1,'ACTIVE',?)""",
                (second_id, second_username, second_digest, second_salt, server.now_ms()),
            )

        self.assertEqual(
            200,
            self.call(
                "PATCH",
                f"/api/v1/admin/admins/{second_id}",
                {"nickname": "第二超级管理员-已编辑", "adminRole": "SUPER_ADMIN", "enabled": True},
                admin_token,
            )[0],
        )
        self.assertEqual(
            409,
            self.call(
                "PATCH",
                f"/api/v1/admin/admins/{admin_id}",
                {"adminRole": "SPORTS_OPERATOR", "enabled": True},
                admin_token,
            )[0],
        )
        self.assertEqual(
            409,
            self.call(
                "PATCH",
                f"/api/v1/admin/admins/{admin_id}",
                {"adminRole": "SUPER_ADMIN", "enabled": False},
                admin_token,
            )[0],
        )
        self.assertEqual(
            409,
            self.call(
                "PATCH",
                f"/api/v1/admin/admins/{admin_id}",
                {"nickname": "不应成功", "adminRole": "SUPER_ADMIN", "enabled": True},
                admin_token,
            )[0],
        )
        self.assertEqual(
            200,
            self.call(
                "PATCH",
                f"/api/v1/admin/admins/{second_id}",
                {"adminRole": "SUPER_ADMIN", "enabled": False},
                admin_token,
            )[0],
        )
        self.assertEqual(
            200,
            self.call(
                "PATCH",
                f"/api/v1/admin/admins/{second_id}",
                {"adminRole": "SUPER_ADMIN", "enabled": True},
                admin_token,
            )[0],
        )

        operator_password = secrets.token_urlsafe(18)
        operator_username = "operator-edit-" + secrets.token_hex(3)
        self.assertEqual(
            201,
            self.call(
                "POST",
                "/api/v1/admin/admins",
                {
                    "username": operator_username,
                    "nickname": "体育运营编辑测试",
                    "password": operator_password,
                    "adminRole": "SPORTS_OPERATOR",
                },
                admin_token,
            )[0],
        )
        _, operator_login = self.call(
            "POST",
            "/api/v1/auth/login",
            {"username": operator_username, "password": operator_password},
        )
        self.assertEqual(
            403,
            self.call("GET", "/api/v1/admin/admins", token=operator_login["data"]["token"])[0],
        )
        self.assertEqual(
            403,
            self.call(
                "PATCH",
                f"/api/v1/admin/admins/{second_id}",
                {"adminRole": "SUPER_ADMIN", "enabled": True},
                operator_login["data"]["token"],
            )[0],
        )

    def test_admin_web_assets_are_served_locally(self):
        for path, expected in (
            ("/admin/", "校园运营后台"),
            ("/admin/styles.css", "--blue"),
            ("/admin/app.js", "sessionStorage"),
        ):
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                self.assertEqual(200, response.status)
                self.assertIn(expected, response.read().decode("utf-8"))

        with urllib.request.urlopen(self.base + "/admin/", timeout=5) as response:
            html = response.read().decode("utf-8")
            self.assertIn("MY CHUANNONG / CAMPUS CONSOLE", html)
            self.assertIn("本地联调", html)
            self.assertIn("/admin/assets/my-chuannong-app-icon.png", html)
            self.assertNotIn("LOCAL ADMIN", html)
            self.assertNotIn("仅本机", html)
        with urllib.request.urlopen(
            self.base + "/admin/assets/my-chuannong-app-icon.png", timeout=5
        ) as response:
            self.assertEqual("image/png", response.headers.get_content_type())
            self.assertEqual(b"\x89PNG\r\n\x1a\n", response.read(8))
        with urllib.request.urlopen(self.base + "/admin/styles.css", timeout=5) as response:
            stylesheet = response.read().decode("utf-8")
            self.assertIn("#user-bulk-actions[hidden]", stylesheet)
        with urllib.request.urlopen(self.base + "/admin/app.js", timeout=5) as response:
            javascript = response.read().decode("utf-8")
            self.assertIn("生产运行说明", javascript)
            self.assertIn('DEVICE: "未登录用户"', javascript)
            self.assertNotIn('DEVICE: "测试设备"', javascript)
            self.assertIn("lost-details", javascript)
            self.assertIn("bulk-delete-lost", javascript)
            self.assertIn("suspensionDuration", javascript)
            self.assertIn("bulk-delete-users", javascript)
            self.assertIn("批量限制访问", javascript)
            self.assertIn("批量恢复权限", javascript)
            self.assertIn("delete-user-account", javascript)
            self.assertIn("默认隐藏已注销用户", javascript)
            self.assertIn("恢复账号", javascript)
            self.assertIn("已注销，可重新注册", javascript)
            self.assertIn("网站公告", javascript)
            self.assertIn("website-announcements", javascript)
            self.assertNotIn("本地运行说明", javascript)
            self.assertIn("用户概况", javascript)
            self.assertIn("累计用户量", javascript)
            self.assertIn("我的川农注册用户", javascript)
            self.assertIn("未注册 / 游客用户", javascript)
            self.assertIn("今日首次使用用户", javascript)
            for removed_note in (
                "累计用户量按 canonical identity 去重",
                "DAU / WAU / MAU 按 canonical identity 去重",
                "这里只是账号生命周期与权限治理数据",
                "设备数 ≠ 用户数",
            ):
                self.assertNotIn(removed_note, javascript)
            self.assertNotIn(", null, {}", javascript)
            self.assertIn('"canonicalUsers"', javascript)
            self.assertIn('"devices"', javascript)
            self.assertIn('"installations"', javascript)
            self.assertIn('data-action="back-overview"', javascript)
            self.assertNotIn("账号概况（仅 PHONE 注册账号）", javascript)

    def test_dashboard_drilldown_filters_counts_privacy_and_permissions(self):
        admin = self.admin_token()
        status, overview_response = self.call(
            "GET", "/api/v1/admin/overview", token=admin
        )
        self.assertEqual(200, status)
        overview = overview_response["data"]
        as_of = overview["asOf"]

        canonical_cases = (
            ("totalUsers", "cohort=all"),
            ("registeredUsers", "cohort=registered"),
            ("guestUsers", "cohort=guest"),
            ("todayNewUsers", "firstSeen=today"),
            ("todayFirstUseUsers", "firstSeen=today"),
            ("active5m", f"activity=5m&asOf={as_of}"),
            ("dau", f"activity=today&asOf={as_of}"),
            ("wau", f"activity=7d&asOf={as_of}"),
            ("mau", f"activity=30d&asOf={as_of}"),
        )
        for metric, query in canonical_cases:
            detail_status, detail_response = self.call(
                "GET", f"/api/v1/admin/canonical-users?{query}", token=admin
            )
            self.assertEqual(200, detail_status)
            detail = detail_response["data"]
            self.assertEqual(overview[metric], detail["total"], metric)
            for row in detail["items"]:
                self.assertRegex(row["identityCode"], r"^(?:\d{17}|GUEST-[0-9A-F]{10})$")
                self.assertNotIn("identityKey", row)
                self.assertNotIn("deviceId", row)
                self.assertNotIn("userId", row)

        user_status, user_response = self.call(
            "GET",
            "/api/v1/admin/users?accountType=PHONE&includeDeleted=true&paged=true&limit=100",
            token=admin,
        )
        self.assertEqual(200, user_status)
        self.assertEqual(overview["registeredUsers"], user_response["data"]["total"])
        today_registration_status, today_registration_response = self.call(
            "GET",
            "/api/v1/admin/users?accountType=PHONE&includeDeleted=true&createdToday=true&paged=true&limit=100",
            token=admin,
        )
        self.assertEqual(200, today_registration_status)
        self.assertEqual(
            overview["todayNewRegistrations"],
            today_registration_response["data"]["total"],
        )

        device_cases = (
            ("totalDevices", "window=all"),
            ("todayNewDevices", "window=today"),
            ("activeDevices", f"window=5m&asOf={as_of}"),
            ("activeDevices7d", f"window=7d&asOf={as_of}"),
            ("activeDevices30d", f"window=30d&asOf={as_of}"),
        )
        for metric, query in device_cases:
            detail_status, detail_response = self.call(
                "GET", f"/api/v1/admin/devices?{query}", token=admin
            )
            self.assertEqual(200, detail_status)
            detail = detail_response["data"]
            self.assertEqual(overview[metric], detail["total"], metric)
            for row in detail["items"]:
                self.assertRegex(row["deviceCode"], r"^DEVICE-[0-9A-F]{10}$")
                self.assertNotIn("deviceId", row)
                self.assertNotIn("deviceScopedId", row)

        installation_cases = (
            ("totalInstallations", "window=all"),
            ("todayNewInstallations", "window=today"),
            ("reinstallations", "reinstallation=true"),
        )
        for metric, query in installation_cases:
            detail_status, detail_response = self.call(
                "GET", f"/api/v1/admin/installations?{query}", token=admin
            )
            self.assertEqual(200, detail_status)
            detail = detail_response["data"]
            self.assertEqual(overview[metric], detail["total"], metric)
            for row in detail["items"]:
                self.assertRegex(row["installationCode"], r"^INSTALL-[0-9A-F]{10}$")
                self.assertNotIn("installationId", row)

        for endpoint in (
            "/api/v1/admin/canonical-users",
            "/api/v1/admin/devices",
            "/api/v1/admin/installations",
        ):
            self.assertEqual(401, self.call("GET", endpoint)[0])

    def test_platform_metrics_user_events_status_and_risk_are_separated(self):
        phone, password, user_token, user = self.phone_account()
        admin = self.admin_token()
        self.assertEqual(200, self.call("GET", "/api/v1/auth/me", token=user_token)[0])
        for _ in range(5):
            self.assertEqual(401, self.call(
                "POST", "/api/v1/auth/login", {"phone": phone, "password": "wrong-password"}
            )[0])

        status, overview = self.call("GET", "/api/v1/admin/overview", token=admin)
        self.assertEqual(200, status)
        self.assertGreaterEqual(overview["data"]["registeredAccounts"], 1)
        self.assertGreaterEqual(overview["data"]["dau"], 1)
        self.assertGreaterEqual(overview["data"]["loginFailures24h"], 5)
        self.assertGreaterEqual(overview["data"]["riskCandidates"], 1)

        status, events = self.call(
            "GET", f"/api/v1/admin/user-events?user={user['id']}&eventType=LOGIN_FAILURE",
            token=admin,
        )
        self.assertEqual(200, status)
        self.assertGreaterEqual(events["data"]["total"], 5)
        serialized = json.dumps(events["data"], ensure_ascii=False)
        self.assertNotIn(password, serialized)
        self.assertNotIn("wrong-password", serialized)

        status, candidates = self.call("GET", "/api/v1/admin/risk-candidates?status=OPEN", token=admin)
        self.assertEqual(200, status)
        candidate = next(row for row in candidates["data"] if row["user_id"] == user["id"])
        self.assertEqual(200, self.call(
            "PATCH", f"/api/v1/admin/risk-candidates/{candidate['id']}",
            {"status": "WATCHING", "reason": "本地自动化复核"}, admin,
        )[0])

        self.assertEqual(200, self.call(
            "PATCH", f"/api/v1/admin/users/{user['id']}",
            {"accountStatus": "LIMITED", "reason": "本地测试限制"}, admin,
        )[0])
        status, rows = self.call("GET", "/api/v1/admin/users?status=LIMITED", token=admin)
        self.assertEqual(200, status)
        self.assertTrue(any(row["id"] == user["id"] for row in rows["data"]))
        self.assertEqual(200, self.call("GET", "/api/v1/auth/me", token=user_token)[0])
        self.assertEqual(403, self.call(
            "POST", "/api/v1/feedback",
            {"title": "受限验证", "category": "OTHER", "content": "该请求应被服务端拒绝"},
            user_token,
        )[0])

        status, audits = self.call("GET", "/api/v1/admin/audit-logs", token=admin)
        self.assertEqual(200, status)
        self.assertTrue(any(row["action"] == "RISK_CANDIDATE_REVIEWED" for row in audits["data"]))
        self.assertTrue(any(row["action"] == "USER_STATUS_UPDATED" for row in audits["data"]))


class EmptyDailyLoginContractTest(unittest.TestCase):
    """Keep the zero-user dashboard/detail contract explicit and isolated."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.database = Path(cls.temp.name) / "empty.db"
        core.configure_test_runtime(cls.database)
        server.DB_PATH = cls.database
        server.UPLOADS = Path(cls.temp.name) / "uploads"
        server.initialize_database()
        cls.admin_id = "empty-admin"
        digest, salt = server.password_record(secrets.token_urlsafe(24))
        with server.connection() as db:
            db.execute(
                """INSERT INTO users(
                       id,username,password_hash,password_salt,role,account_type,nickname,
                       admin_permissions_json,admin_role,admin_enabled,account_status,created_at
                   ) VALUES(?,?,?,?, 'ADMIN','ADMIN','空数据管理员','[\"*\"]','SUPER_ADMIN',1,'ACTIVE',?)""",
                (cls.admin_id, "empty-admin", digest, salt, server.now_ms()),
            )
            cls.admin_token = server.issue_token(db, cls.admin_id)
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)
        with server.connection() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        cls.temp.cleanup()

    def call(self, method, path, token=None):
        parts = urllib.parse.urlsplit(path)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        target = urllib.parse.quote(parts.path)
        if parts.query:
            target += "?" + urllib.parse.quote(parts.query, safe="=&")
        request = urllib.request.Request(self.base + target, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_zero_users_produce_zero_dashboard_and_first_login_detail(self):
        status, overview = self.call("GET", "/api/v1/admin/overview", self.admin_token)
        self.assertEqual(200, status)
        for metric in (
            "totalUsers",
            "registeredUsers",
            "guestUsers",
            "todayNewUsers",
            "todayNewRegistrations",
            "todayFirstUseUsers",
            "active5m",
            "dau",
            "wau",
            "mau",
        ):
            self.assertEqual(0, overview["data"][metric])
        self.assertEqual(0, overview["data"]["todayFirstLogins"])

        status, details = self.call(
            "GET", "/api/v1/admin/user-events?firstLoginToday=true", self.admin_token
        )
        self.assertEqual(200, status)
        self.assertEqual(0, details["data"]["total"])
        self.assertEqual([], details["data"]["items"])


class AppConfigContractTest(unittest.TestCase):
    """Exercise the local Config V1 -> V2 -> rollback control plane."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.database = Path(cls.temp.name) / "app-config.db"
        core.configure_test_runtime(cls.database)
        server.DB_PATH = cls.database
        server.UPLOADS = Path(cls.temp.name) / "uploads"
        server.initialize_database()
        cls.admin_id = "config-admin"
        digest, salt = server.password_record(secrets.token_urlsafe(24))
        with server.connection() as db:
            db.execute(
                """INSERT INTO users(
                       id,username,password_hash,password_salt,role,account_type,nickname,
                       admin_permissions_json,admin_role,admin_enabled,account_status,created_at
                   ) VALUES(?,?,?,?, 'ADMIN','ADMIN','配置管理员','[\"*\"]','SUPER_ADMIN',1,'ACTIVE',?)""",
                (cls.admin_id, "config-admin", digest, salt, server.now_ms()),
            )
            cls.admin_token = server.issue_token(db, cls.admin_id)
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)
        with server.connection() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        cls.temp.cleanup()

    def call(self, method, path, data=None, token=None):
        body = json.dumps(data).encode() if data is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        parts = urllib.parse.urlsplit(path)
        target = urllib.parse.quote(parts.path)
        if parts.query:
            target += "?" + urllib.parse.quote(parts.query, safe="=&")
        request = urllib.request.Request(
            self.base + target,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def public_config(self):
        status, payload = self.call(
            "GET",
            "/api/v1/public/bootstrap?schemaVersion=1&appVersionCode=37",
        )
        self.assertEqual(200, status)
        return payload["data"]

    def test_config_v1_v2_rollback_is_monotonic_and_legacy_safe(self):
        legacy = self.call("GET", "/api/v1/public/bootstrap")
        self.assertEqual(200, legacy[0])
        self.assertNotIn("config", legacy[1]["data"])

        first = self.public_config()
        self.assertEqual(1, first["schemaVersion"])
        self.assertEqual(1, first["configVersion"])
        v1_title = first["config"]["navigation"][0]["title"]
        v2 = json.loads(json.dumps(first["config"], ensure_ascii=False))
        v2["navigation"][0]["title"] = "课表（动态 V2）"
        v2["navigation"][0]["order"] = 999
        v2["navigation"][-1]["visible"] = False
        v2["navigation"][8]["title"] = "资料库（配置测试）"

        status, validation = self.call(
            "POST",
            "/api/v1/admin/app-config/validate",
            {"config": v2},
            self.admin_token,
        )
        self.assertEqual(200, status)
        self.assertNotEqual(first["configHash"], validation["data"]["configHash"])

        status, draft = self.call(
            "POST",
            "/api/v1/admin/app-config/revisions",
            {"config": v2, "reason": "P0 local V2"},
            self.admin_token,
        )
        self.assertEqual(201, status)
        self.assertEqual(2, draft["data"]["configVersion"])

        status, activated = self.call(
            "POST",
            "/api/v1/admin/app-config/activate",
            {
                "revision": draft["data"]["revision"],
                "expectedActiveRevision": first["configVersion"],
            },
            self.admin_token,
        )
        self.assertEqual(200, status)
        self.assertEqual(2, activated["data"]["configVersion"])

        second = self.public_config()
        self.assertEqual(2, second["configVersion"])
        self.assertEqual("课表（动态 V2）", second["config"]["navigation"][0]["title"])
        self.assertFalse(second["config"]["navigation"][-1]["visible"])
        self.assertEqual("资料库（配置测试）", second["config"]["navigation"][8]["title"])

        status, rolled_back = self.call(
            "POST",
            "/api/v1/admin/app-config/rollback",
            {
                "targetRevision": first["configVersion"],
                "expectedActiveRevision": activated["data"]["activeRevision"],
            },
            self.admin_token,
        )
        self.assertEqual(200, status)
        self.assertEqual(3, rolled_back["data"]["configVersion"])
        self.assertEqual(first["configHash"], rolled_back["data"]["active"]["configHash"])

        restored = self.public_config()
        self.assertEqual(3, restored["configVersion"])
        self.assertEqual(v1_title, restored["config"]["navigation"][0]["title"])
        self.assertEqual(first["configHash"], restored["configHash"])

    def test_invalid_or_stale_config_cannot_be_published(self):
        current = self.public_config()
        bad = json.loads(json.dumps(current["config"], ensure_ascii=False))
        bad["navigation"][0]["actionId"] = "execute_arbitrary_intent"
        status, response = self.call(
            "POST",
            "/api/v1/admin/app-config/revisions",
            {"config": bad},
            self.admin_token,
        )
        self.assertEqual(422, status)
        self.assertEqual("INVALID_APP_CONFIG", response["error"]["code"])
        self.assertEqual(current["configVersion"], self.public_config()["configVersion"])

        status, response = self.call(
            "POST",
            "/api/v1/admin/app-config/activate",
            {"revision": 999999, "expectedActiveRevision": 1},
            self.admin_token,
        )
        self.assertIn(status, (404, 409))


if __name__ == "__main__":
    unittest.main(verbosity=2)
