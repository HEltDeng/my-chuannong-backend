import os
import secrets
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import server
from core import ApiError, parse_pagination
from security import RateLimiter


class SecurityRegressionTests(unittest.TestCase):
    def test_parallel_local_key_creation_preserves_one_owner_only_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic-key"
            barrier = threading.Barrier(4)

            def generate():
                barrier.wait(timeout=5)
                return secrets.token_bytes(48)

            with ThreadPoolExecutor(max_workers=4) as workers:
                jobs = [workers.submit(server._local_key, path, generate) for _ in range(4)]
                results = [job.result() for job in jobs]
            self.assertTrue(all(result == path.read_bytes() for result in results))
            self.assertEqual(list(path.parent.iterdir()), [path])
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(server._local_key(path, lambda: self.fail("key regenerated")), results[0])

    def test_pre_auth_write_limits_use_only_the_peer_address(self):
        handler = object.__new__(server.ApiHandler)
        handler.client_address = ("127.0.0.1", 0)
        with patch.object(handler, "enforce_rate_limit") as limit:
            for path in ("/api/v1/feedback", "/api/v1/lost-found"):
                handler.enforce_route_rate_limit("POST", path)
            self.assertEqual([call.args[1] for call in limit.call_args_list], ["127.0.0.1"] * 2)

    def test_expired_identities_are_reclaimed_without_resetting_live_limits(self):
        limiter = RateLimiter()
        with patch('security.time.monotonic', return_value=100):
            for number in range(100):
                self.assertIsNone(limiter.check(f'expired-{number}', 1, 5))
            self.assertIsNone(limiter.check('active', 1, 3600))
        with patch('security.time.monotonic', return_value=161):
            self.assertIsNone(limiter.check('new', 1, 60))
            self.assertIsNotNone(limiter.check('active', 1, 3600))
        self.assertEqual(set(limiter._events), {'active', 'new'})

    def test_window_expiry_and_reset_keep_existing_behavior(self):
        limiter = RateLimiter()
        with patch('security.time.monotonic', return_value=10):
            self.assertIsNone(limiter.check('login', 1, 60))
            self.assertEqual(limiter.check('login', 1, 60), 60)
        with patch('security.time.monotonic', return_value=70):
            self.assertIsNone(limiter.check('login', 1, 60))
            limiter.reset()
            self.assertIsNone(limiter.check('login', 1, 60))

    def test_malformed_pagination_produces_validation_errors(self):
        for field in ('limit', 'offset'):
            for value in ('²', '9' * 5000, '-1', 'NaN'):
                with self.subTest(field=field, value=value[:20]):
                    with self.assertRaises(ApiError) as result:
                        parse_pagination({field: value})
                    self.assertEqual(result.exception.status, 422)

    def test_pagination_retains_defaults_and_bounds(self):
        self.assertEqual(parse_pagination({}), (50, 0))
        self.assertEqual(parse_pagination({'limit': ' 00020 ', 'offset': '3'}), (20, 3))
        self.assertEqual(parse_pagination({'limit': '999', 'offset': '999999'}), (100, 10000))
