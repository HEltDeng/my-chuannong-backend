import unittest
from unittest.mock import patch

from core import ApiError, parse_pagination
from security import RateLimiter


class SecurityRegressionTests(unittest.TestCase):
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
