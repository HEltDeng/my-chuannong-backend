import unittest
from unittest.mock import patch

import phone_auth
import server
import test_campus_hub as fixtures


class PasswordRegistrationTest(unittest.TestCase):
    setUp = fixtures.CampusHubTest.setUp
    tearDown = fixtures.CampusHubTest.tearDown
    identity = fixtures.CampusHubTest.identity
    call = fixtures.CampusHubTest.call

    def payload(self, phone='13900000101'):
        return {'phone': phone, 'nickname': '合成测试账号',
                'password': 'SyntheticPassword123!', 'confirmPassword': 'SyntheticPassword123!',
                'registrationMethod': 'password', 'policyVersion': '2026-11-17.1',
                'consentAcceptedAt': server.now_ms()}

    def test_password_account_can_register_login_and_delete_without_sms(self):
        body = self.payload()
        with patch.object(phone_auth, 'send_tencent', side_effect=AssertionError('SMS must not be called')):
            status, response = self.call('POST', '/api/v1/auth/register', body)
        self.assertEqual(201, status, response)
        account = response['data']
        self.assertFalse(account['user']['phoneVerified'])
        with server.connection() as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM phone_challenges').fetchone()[0])
            self.assertIsNone(db.execute('SELECT phone_verified_at FROM users WHERE id=?', (account['user']['id'],)).fetchone()[0])
            self.assertEqual('2026-11-17.1', db.execute('SELECT policy_version FROM account_consents WHERE user_id=?', (account['user']['id'],)).fetchone()[0])
        status, logged = self.call('POST', '/api/v1/auth/login', {'phone': body['phone'], 'password': body['password']})
        self.assertEqual(200, status, logged)
        token = logged['data']['token']
        self.assertEqual(200, self.call('DELETE', '/api/v1/auth/account', {'password': body['password']}, token)[0])
        self.assertEqual(401, self.call('GET', '/api/v1/auth/me', token=token)[0])
        self.assertEqual(401, self.call('POST', '/api/v1/auth/login', {'phone': body['phone'], 'password': body['password']})[0])

    def test_confirmation_consent_and_existing_sms_contract_are_enforced(self):
        body = self.payload()
        for changes in [{'confirmPassword': 'DifferentPassword123!'}, {'policyVersion': 'invalid'}, {'registrationMethod': 'unknown'}]:
            server.RATE_LIMITER.reset()
            self.assertEqual(422, self.call('POST', '/api/v1/auth/register', {**body, **changes})[0])
        server.RATE_LIMITER.reset()
        original = {k: v for k, v in body.items() if k != 'registrationMethod'}
        self.assertEqual(422, self.call('POST', '/api/v1/auth/register', original)[0])

    def test_unverified_number_cannot_replace_an_account_or_bypass_identity_review(self):
        body = self.payload()
        status, response = self.call('POST', '/api/v1/auth/register', body)
        self.assertEqual(201, status, response)
        self.assertEqual(409, self.call('POST', '/api/v1/auth/register', {**body, 'password': 'OtherPassword123!', 'confirmPassword': 'OtherPassword123!'})[0])
        self.assertEqual(200, self.call('POST', '/api/v1/auth/login', {'phone': body['phone'], 'password': body['password']})[0])
        status, _ = self.call('PUT', '/api/v1/auth/student-identity',
            {'studentId': '2026000101', 'realName': '合成同学', 'schoolCode': 'SICAU',
             'policyVersion': body['policyVersion'], 'consentAcceptedAt': server.now_ms()}, response['data']['token'])
        self.assertEqual(403, status)


if __name__ == '__main__':
    unittest.main()
