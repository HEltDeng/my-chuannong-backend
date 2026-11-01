"""Synthetic iOS/Android compatibility tests; no school data or production services."""
import unittest
import urllib.request

import server
import test_campus_hub as fixtures
import test_account_analytics as analytics_fixtures


class IOSContractTest(unittest.TestCase):
    setUp = fixtures.CampusHubTest.setUp
    tearDown = fixtures.CampusHubTest.tearDown
    identity = fixtures.CampusHubTest.identity
    call = fixtures.CampusHubTest.call
    account = analytics_fixtures.AccountAnalyticsTest.account

    def test_ios_admin_entry_loads_its_script(self):
        with urllib.request.urlopen(self.base + '/admin/') as response:
            self.assertIn(b'/admin/ios-release.js', response.read())
        with urllib.request.urlopen(self.base + '/admin/ios-release.js') as response:
            self.assertEqual(200, response.status)
            self.assertIn('javascript', response.headers['Content-Type'])
            self.assertIn(b'renderers.iosRelease', response.read())

    def release(self, **changes):
        data = dict(published=True, bundleId='com.mychuannong.app', versionName='1.0.0',
                    buildNumber=12, minimumBuildNumber=1,
                    storeUrl='https://apps.apple.com/cn/app/id1234567890', releaseNotes='Synthetic release')
        data.update(changes)
        return data

    def publish(self, **changes):
        return self.call('PUT', '/api/v1/admin/ios-release', self.release(**changes), self.admin)

    def ios_device(self, **changes):
        data = dict(installationId='synthetic-ios', deviceScopedId='b'*64,
                    versionName='1.0.0', versionCode=12, consentVersion=3,
                    platform='ios', manufacturer='Apple', model='Synthetic iPhone', osVersion='26.5')
        data.update(changes)
        return data

    def test_draft_metadata_is_private_and_requires_super_admin(self):
        expected = dict(platform='ios', published=False, bundleId='com.mychuannong.app')
        self.assertEqual(expected, self.call('GET', '/api/v1/public/ios-release')[1]['data'])
        self.assertEqual(200, self.publish(published=False)[0])
        self.assertEqual(expected, self.call('GET', '/api/v1/public/ios-release')[1]['data'])
        self.assertEqual(401, self.call('GET', '/api/v1/admin/ios-release')[0])
        for token in [self.owner, self.identity('ADMIN', 'CONTENT_OPERATOR')]:
            self.assertEqual(403, self.call('GET', '/api/v1/admin/ios-release', token=token)[0])
            self.assertEqual(403, self.call('PUT', '/api/v1/admin/ios-release', self.release(), token)[0])

    def test_ios_publication_is_separate_from_android_bootstrap(self):
        before = self.call('GET', '/api/v1/public/bootstrap')[1]['data']
        status, response = self.publish()
        self.assertEqual(200, status, response)
        release = self.call('GET', '/api/v1/public/ios-release')[1]['data']
        ios = self.call('GET', '/api/v1/public/bootstrap?platform=ios')[1]['data']
        self.assertEqual(release, ios['updatePolicy'])
        self.assertIsNone(ios['version'])
        self.assertIsNone(ios['releaseMetadata'])
        after = self.call('GET', '/api/v1/public/bootstrap?platform=android')[1]['data']
        for field in ['version', 'releaseMetadata', 'updatePolicy']:
            self.assertEqual(before[field], after[field])
        self.assertEqual(422, self.call('GET', '/api/v1/public/bootstrap?platform=other')[0])
        server.initialize_database()
        self.assertEqual(release, self.call('GET', '/api/v1/public/ios-release')[1]['data'])

    def test_release_rejects_unsafe_links_invalid_builds_and_rollback(self):
        invalid = [dict(storeUrl=url) for url in [
            'https://example.invalid/app.apk', 'http://apps.apple.com/cn/app/id1',
            'https://apps.apple.com.evil.invalid/id1', 'https://user:pass@apps.apple.com/id1',
            'https://apps.apple.com/id1?redirect=evil', 'https://apps.apple.com/id1#fragment',
            'https://apps.apple.com:8443/id1', 'https://apps.apple.com/id1/']]
        invalid += [dict(buildNumber=True), dict(minimumBuildNumber=13), dict(buildNumber=0),
                    dict(bundleId='com.other.app'), dict(versionName='1.0'), dict(published=1)]
        for change in invalid:
            with self.subTest(change=change):
                self.assertEqual(422, self.publish(**change)[0])
        self.assertEqual(200, self.publish()[0])
        self.assertEqual(409, self.publish(buildNumber=11)[0])
        self.assertEqual(12, self.call('GET', '/api/v1/public/ios-release')[1]['data']['buildNumber'])

    def test_ios_foreground_and_legacy_android_profiles_survive_reinitialization(self):
        self.account(self.owner)
        self.account(self.other)
        android = analytics_fixtures.AccountAnalyticsTest.payload(self)
        for token, payload in [(self.owner, self.ios_device()), (self.other, android)]:
            status, response = self.call('POST', '/api/v1/activity/foreground', payload, token)
            self.assertEqual(200, status, response)
        server.initialize_database()
        server.initialize_database()
        rows = self.call('GET', '/api/v1/admin/account-analytics/accounts', token=self.admin)[1]['data']['items']
        profiles = {row['deviceProfile']['platform']: row['deviceProfile'] for row in rows}
        self.assertEqual({'android', 'ios'}, set(profiles))
        self.assertEqual('26.5', profiles['ios']['os_version'])
        self.assertEqual('', profiles['ios']['android_version'])
        self.assertEqual('Apple', profiles['ios']['manufacturer'])
        self.assertEqual('15', profiles['android']['os_version'])
        summary = self.call('GET', '/api/v1/admin/account-analytics', token=self.admin)[1]['data']
        self.assertEqual(2, summary['metrics']['reportedInstallations'])
        self.assertEqual(2, summary['metrics']['dau'])

    def test_ios_rejects_mixed_platform_fields_and_school_data(self):
        self.account(self.owner)
        for change in [dict(androidVersion='15'), dict(sdkInt=35), dict(manufacturer='Other'),
                       dict(osVersion=''), dict(platform='other'), dict(platform=[]),
                       dict(schoolPassword='synthetic-only'), dict(cookies='synthetic-only')]:
            with self.subTest(change=change):
                status, response = self.call('POST', '/api/v1/activity/foreground', self.ios_device(**change), self.owner)
                self.assertEqual(422, status, response)
        with server.connection() as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM account_device_profiles').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
