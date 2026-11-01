"""Member logins, business writes and two-client delivery against an isolated DB."""
import secrets
import unittest
from datetime import timedelta

import server
import test_campus_hub as fixtures
from synthetic_fixtures import sms_registration
from module_access import ANNOUNCEMENT_ROLES, PROVIDER_ROLES, GUIDE_ROLES


class OperatorDeliveryTest(unittest.TestCase):
    setUp = fixtures.CampusHubTest.setUp
    tearDown = fixtures.CampusHubTest.tearDown
    identity = fixtures.CampusHubTest.identity
    call = fixtures.CampusHubTest.call

    def request(self, method, path, body=None, token=None, expected=200):
        if method == 'POST' and path == '/auth/register':
            body = sms_registration(server, body)
        status, result = self.call(method, '/api/v1' + path, body, token)
        self.assertEqual(expected, status, result)
        return result.get('data', result)

    def operator(self, role):
        credentials = dict(username='qa_' + secrets.token_hex(8), password=secrets.token_urlsafe(24))
        admin = self.request('POST', '/admin/admins',
                             dict(**credentials, nickname='合成板块验收', adminRole=role), self.admin, 201)
        login = self.request('POST', '/auth/login', credentials)
        self.assertEqual(role, login['user']['adminRole'])
        return admin['id'], login['token'], credentials

    def clients(self):
        credentials = dict(phone='199' + str(secrets.randbelow(10**8)).zfill(8), password=secrets.token_urlsafe(24))
        android = self.request('POST', '/auth/register', dict(**credentials, nickname='合成双端账号', installationId='android-qa'), expected=201)
        ios = self.request('POST', '/auth/login', dict(**credentials, installationId='ios-qa'))
        self.assertEqual(android['user']['id'], ios['user']['id'])
        return android['token'], ios['token']

    def test_every_member_login_notice_delivery_isolation_and_disable(self):
        android, ios = self.clients()
        for role, module in ANNOUNCEMENT_ROLES.items():
            with self.subTest(role=role):
                ident, operator, credentials = self.operator(role)
                self.request('GET', '/admin/admins', token=operator, expected=403)
                self.request('GET', '/admin/ios-release', token=operator, expected=403)
                self.request('PUT', '/notifications/follows/' + module, {'following': True}, android)
                follows = self.request('GET', '/notifications/follows', token=ios)['items']
                self.assertTrue(next(x for x in follows if x['module'] == module)['following'])
                notice = self.request('POST', '/admin/announcements', {
                    'title': '合成板块提醒', 'content': '仅用于隔离验收', 'enabled': True,
                    'serviceVisible': True, 'notificationEnabled': True, 'popupEnabled': True,
                }, operator, 201)['id']
                for client in (android, ios):
                    rows = self.request('GET', '/notifications/feed', token=client)['items']
                    self.assertIn(notice, [x['id'] for x in rows])
                foreign = next(x for x in ANNOUNCEMENT_ROLES.values() if x != module)
                self.request('GET', '/admin/announcements?module=' + foreign, token=operator, expected=403)
                self.request('POST', '/admin/announcements', {'title': '越权', 'content': '不得发布', 'module': foreign}, operator, 403)
                self.assertEqual([], self.request('GET', '/notifications/feed', token=self.other)['items'])
                self.request('PATCH', '/admin/announcements/' + notice, {'enabled': False}, operator)
                self.assertNotIn(notice, [x['id'] for x in self.request('GET', '/notifications/feed', token=ios)['items']])
                self.request('PUT', '/notifications/follows/' + module, {'following': False}, ios)
                self.request('PATCH', '/admin/admins/' + ident, {'enabled': False}, self.admin)
                self.request('POST', '/auth/login', credentials, expected=403)
                self.request('GET', '/admin/announcements', token=operator, expected=401)

    def test_members_publish_review_and_process_cross_client_business(self):
        android, ios = self.clients()
        for role, category in PROVIDER_ROLES.items():
            with self.subTest(provider=category):
                _, operator, _ = self.operator(role)
                provider = self.request('POST', '/admin/campus/providers', {
                    'category': category, 'name': '合成验收机构', 'campus': '成都校区',
                    'status': 'PUBLISHED', 'isDemo': False, 'bookingEnabled': True,
                    'details': {'courses': [{'id': 'test', 'title': '合成课程'}]},
                }, operator, 201)
                for client in (android, ios):
                    self.assertIn(provider['id'], [x['id'] for x in self.request('GET', '/services/providers?category=' + category, token=client)['items']])
                booking = self.request('POST', '/services/bookings', {
                    'providerId': provider['id'], 'clientId': secrets.token_hex(12),
                    'course': '合成课程', 'preferredDate': (server.business_today() + timedelta(days=1)).isoformat(),
                    'name': '合成用户', 'phone': '12345678900', 'consented': True,
                }, ios, 201)
                self.request('PATCH', '/admin/campus/bookings/' + booking['id'], {'status': 'CONFIRMED', 'reply': '验收确认'}, operator)
                rows = self.request('GET', '/services/bookings', token=android)['items']
                self.assertEqual('CONFIRMED', next(x for x in rows if x['id'] == booking['id'])['status'])
                self.request('PATCH', '/services/bookings/' + booking['id'], {'status': 'CANCELLED'}, android)
                self.request('PATCH', '/admin/campus/providers/' + provider['id'], {'status': 'UNLISTED'}, operator)
                self.request('GET', '/services/providers/' + provider['id'], token=ios, expected=404)
        for role, guide in GUIDE_ROLES.items():
            with self.subTest(guide=guide):
                _, operator, _ = self.operator(role)
                self.request('PUT', '/admin/campus/guides/' + guide, {'title': '合成指南', 'body': '验收内容', 'status': 'PUBLISHED'}, operator)
                for client in (android, ios):
                    self.assertEqual('验收内容', self.request('GET', '/services/guides/' + guide, token=client)['body'])
                draft = self.request('PUT', '/admin/campus/guides/' + guide, {'body': '', 'status': 'DRAFT'}, operator)
                self.assertEqual('', draft['body'])
                self.request('GET', '/services/guides/' + guide, token=ios, expected=404)
                self.request('PUT', '/admin/campus/guides/' + guide, {'status': 'PUBLISHED'}, operator, expected=422)
        for role, module in [('SECONDHAND_OPERATOR', 'secondhand'), ('LOST_FOUND_OPERATOR', 'lost-found')]:
            with self.subTest(community=module):
                _, operator, _ = self.operator(role)
                data = {'clientId': secrets.token_hex(12), 'title': '合成验收物品', 'description': '隔离验收', 'campus': '成都校区'}
                data.update({'type': 'sell', 'category': '书籍', 'wechat': 'synthetic', 'images': []} if module == 'secondhand'
                            else {'kind': 'found', 'location': '合成地点', 'date': server.business_today().isoformat(), 'contact': 'synthetic', 'image': ''})
                post = self.request('POST', '/community/' + module, data, ios, 201)
                self.request('PATCH', '/admin/campus/' + module + '/' + post['id'], {'action': 'approve'}, operator)
                self.request('GET', '/community/' + module + '/' + post['id'], token=android)
                if module == 'secondhand':
                    # Android's marketplace requires edits to pass review again.
                    edited = self.request('PATCH', '/community/secondhand/' + post['id'], dict(data, title='合成修改后物品'), android)
                    self.assertEqual('PENDING', edited['status'])
                    self.request('PATCH', '/admin/campus/secondhand/' + post['id'], {'action': 'approve'}, operator)
                    self.assertEqual('合成修改后物品', self.request('GET', '/community/secondhand/' + post['id'], token=ios)['title'])
                else:
                    self.request('PATCH', '/community/' + module + '/' + post['id'], {'done': True}, android)
                    self.assertTrue(self.request('GET', '/community/' + module + '/' + post['id'], token=ios)['done'])
        _, librarian, _ = self.operator('LIBRARY_OPERATOR')
        resource = self.request('POST', '/admin/resources', {'title': '合成验收资料', 'category': '学习资料',
                                'externalUrl': 'https://example.com/qa', 'publishStatus': 'PUBLISHED', 'visibility': 'MEMBERS'}, librarian, 201)
        for client in (android, ios):
            self.request('GET', '/library/' + resource['id'], token=client)
        self.request('POST', '/library/' + resource['id'] + '/favorite', {}, ios)
        self.request('PATCH', '/admin/resources/' + resource['id'], {'publishStatus': 'UNLISTED'}, librarian)
        self.request('GET', '/library/' + resource['id'], token=android, expected=404)
