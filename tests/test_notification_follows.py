import unittest
import server
import test_campus_hub as fixtures


class NotificationFollowsTest(unittest.TestCase):
    setUp = fixtures.CampusHubTest.setUp
    tearDown = fixtures.CampusHubTest.tearDown
    identity = fixtures.CampusHubTest.identity
    call = fixtures.CampusHubTest.call

    def follows(self, token=None):
        return self.call('GET', '/api/v1/notifications/follows', token=token or self.owner)[1]['data']

    def follow(self, module='driving_school', following=True, token=None):
        return self.call('PUT', '/api/v1/notifications/follows/'+module, {'following': following}, token or self.owner)

    def feed(self, token=None):
        return self.call('GET', '/api/v1/notifications/feed', token=token or self.owner)[1]['data']

    def notice(self, module='driving_school', **changes):
        body = dict(title='合成板块动态', content='仅用于测试', module=module,
                    notificationEnabled=True, popupEnabled=True, enabled=True)
        body.update(changes)
        status, data = self.call('POST', '/api/v1/admin/announcements', body, self.admin)
        self.assertEqual(201, status, data)
        return data['data']['id']

    def test_default_off_and_public_content_never_authorizes_delivery(self):
        self.notice()
        self.assertTrue(self.follows()['items'])
        self.assertFalse(any(item['following'] for item in self.follows()['items']))
        self.assertEqual([], self.feed()['items'])
        public = self.call('GET', '/api/v1/public/bootstrap')[1]['data']['announcements']
        self.assertTrue(public)
        self.assertTrue(all(not x['notificationEnabled'] and not x['popupEnabled'] for x in public))
        self.assertEqual(401, self.call('GET', '/api/v1/notifications/feed')[0])
        self.assertEqual(403, self.call('GET', '/api/v1/notifications/follows', token=self.admin)[0])

    def test_only_followed_new_enabled_notices_and_unfollow_immediately_removes_access(self):
        old = self.notice()
        self.assertEqual(200, self.follow()[0])
        account = self.follows()['accountId']
        with server.connection() as db:
            # Explicit ordering avoids a test relying on clock resolution or sleeps.
            db.execute('UPDATE announcements SET published_at=1 WHERE id=?', (old,))
            db.execute('UPDATE notification_follows SET followed_at=2 WHERE user_id=?', (account,))
        new = self.notice()
        self.notice(module='gym')
        self.notice(enabled=False)
        self.notice(notificationEnabled=False, popupEnabled=False)
        result = self.feed()
        self.assertEqual(account, result['accountId'])
        self.assertEqual([new], [x['id'] for x in result['items']])
        self.assertEqual([], self.feed(self.other)['items'])
        self.assertEqual(200, self.follow(following=False)[0])
        self.assertEqual([], self.feed()['items'])
        self.assertFalse(any(x['following'] for x in self.follows()['items']))

    def test_follow_retry_keeps_timestamp_and_cannot_set_someone_elses_subscription(self):
        first = self.follow()[1]['data']
        second = self.follow()[1]['data']
        self.assertEqual(first['followedAt'], second['followedAt'])
        self.assertEqual(422, self.call('PUT', '/api/v1/notifications/follows/driving_school',
            {'following': True, 'accountId': self.follows(self.other)['accountId']}, self.owner)[0])
        self.assertEqual(422, self.follow(following='true')[0])
        self.assertEqual(422, self.follow(module='all')[0])
        self.assertEqual([], self.feed(self.other)['items'])

    def test_account_anonymization_clears_follow_preferences(self):
        self.follow()
        account = self.follows()['accountId']
        with server.connection() as db:
            user = db.execute('SELECT * FROM users WHERE id=?', (account,)).fetchone()
            server.delete_user_account_data(db, user, reason='合成测试注销')
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM notification_follows WHERE user_id=?', (account,)).fetchone()[0])
