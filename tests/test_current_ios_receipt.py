"""Current iOS Release receipts against the real HTTP/admin contract; synthetic data only."""
import unittest

import server
import test_campus_hub as fixtures
import test_account_analytics as analytics_fixtures


class CurrentIOSReceiptTest(unittest.TestCase):
    setUp = fixtures.CampusHubTest.setUp
    tearDown = fixtures.CampusHubTest.tearDown
    identity = fixtures.CampusHubTest.identity
    call = fixtures.CampusHubTest.call
    account = analytics_fixtures.AccountAnalyticsTest.account

    def receipt(self, **changes):
        data = dict(installationId='synthetic-current-ios', versionName='0.1.0',
                    versionCode=1, consentVersion=5, policyVersion='2026-10-23.2',
                    consentAcceptedAt=server.now_ms(), platform='ios',
                    manufacturer='Apple', model='Synthetic iPhone', osVersion='26.5')
        data.update(changes)
        return data

    def test_current_receipt_is_visible_in_admin_and_retries_are_idempotent(self):
        self.account(self.owner)
        payload = self.receipt()
        for _ in range(2):
            status, result = self.call('POST', '/api/v1/activity/foreground', payload, self.owner)
            self.assertEqual(200, status, result)
            self.assertTrue(result['data']['accepted'])
        status, result = self.call('GET', '/api/v1/admin/account-analytics/devices?platform=ios', token=self.admin)
        self.assertEqual(200, status, result)
        self.assertEqual(1, result['data']['total'])
        self.assertEqual('2026-10-23.3', result['data']['policyVersion'])
        row = result['data']['items'][0]
        for key, value in [('consent_version', 5), ('policy_version', '2026-10-23.2'),
                           ('app_build', 1), ('platform', 'ios'),
                           ('consent_accepted_at', payload['consentAcceptedAt'])]:
            self.assertEqual(value, row[key])
        self.assertNotIn('installation_id', row)
        summary = self.call('GET', '/api/v1/admin/account-analytics', token=self.admin)[1]['data']
        self.assertEqual(1, summary['metrics']['todayForegroundAccounts'])
        self.assertEqual(1, summary['metrics']['reportedInstallations'])

    def test_mismatched_missing_and_future_receipts_do_not_create_profiles(self):
        self.account(self.owner)
        for changes in [dict(policyVersion='2026-10-23'), dict(consentVersion=4),
                        dict(consentVersion=6), dict(consentVersion=True),
                        dict(consentAcceptedAt=None), dict(consentAcceptedAt=True),
                        dict(consentAcceptedAt=server.now_ms() + 600000),
                        dict(versionCode=True), dict(schoolPassword='synthetic-only')]:
            with self.subTest(changes=changes):
                status, result = self.call('POST', '/api/v1/activity/foreground', self.receipt(**changes), self.owner)
                self.assertEqual(422, status, result)
        with server.connection() as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM account_device_profiles').fetchone()[0])

    def test_legacy_ios_and_android_remain_compatible(self):
        self.account(self.owner)
        ios = self.receipt(consentVersion=4, policyVersion='2026-10-23')
        android = analytics_fixtures.AccountAnalyticsTest.payload(self)
        for payload in [ios, android]:
            status, result = self.call('POST', '/api/v1/activity/foreground', payload, self.owner)
            self.assertEqual(200, status, result)


if __name__ == '__main__':
    unittest.main()
