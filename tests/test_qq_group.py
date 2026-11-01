import unittest
import urllib.request

import server
import test_campus_hub as fixtures


class QQGroupTest(unittest.TestCase):
    setUp = fixtures.CampusHubTest.setUp
    tearDown = fixtures.CampusHubTest.tearDown
    identity = fixtures.CampusHubTest.identity
    call = fixtures.CampusHubTest.call

    def config(self, **changes):
        value = dict(enabled=True, groupNumber='123456789', joinUrl='https://qm.qq.com/q/synthetic-only')
        value.update(changes)
        return value

    def save(self, **changes):
        return self.call('PUT', '/api/v1/admin/qq-group', self.config(**changes), self.admin)

    def public(self):
        return self.call('GET', '/api/v1/public/qq-group')[1]['data']

    def test_draft_and_admin_boundaries(self):
        self.assertEqual({'enabled': False}, self.public())
        self.assertEqual(200, self.save(enabled=False, groupNumber='', joinUrl='')[0])
        self.assertEqual(200, self.save(enabled=False)[0])
        self.assertEqual({'enabled': False}, self.public())
        self.assertEqual(401, self.call('GET', '/api/v1/admin/qq-group')[0])
        for token in [self.owner, self.identity('ADMIN', 'CONTENT_OPERATOR')]:
            for method in ['GET', 'PUT']:
                self.assertEqual(403, self.call(method, '/api/v1/admin/qq-group', self.config() if method == 'PUT' else None, token)[0])
        self.assertEqual(405, self.call('POST', '/api/v1/public/qq-group', self.config())[0])
        self.assertEqual({'enabled': False}, self.public())

    def test_publish_replace_disable_persist_and_audit(self):
        before = self.call('GET', '/api/v1/public/bootstrap')[1]['data']
        self.assertEqual(200, self.save()[0])
        self.assertEqual(self.config(), {k: self.public()[k] for k in self.config()})
        for platform in ['ios', 'android']:
            self.assertEqual(self.public(), self.call('GET', '/api/v1/public/bootstrap?platform=' + platform)[1]['data']['qqGroup'])
        after = self.call('GET', '/api/v1/public/bootstrap')[1]['data']
        for key in ['flags', 'version', 'releaseMetadata', 'updatePolicy']:
            self.assertEqual(before[key], after[key])
        replacement = 'mqqapi://card/show_pslcard?src_type=internal&version=1&uin=987654321&card_type=group&source=qrcode'
        self.assertEqual(200, self.save(groupNumber='987654321', joinUrl=replacement)[0])
        server.initialize_database()
        self.assertEqual(replacement, self.public()['joinUrl'])
        self.assertEqual(200, self.save(enabled=False, groupNumber='987654321', joinUrl=replacement)[0])
        self.assertEqual({'enabled': False}, self.public())
        with server.connection() as db:
            self.assertEqual(3, db.execute("SELECT COUNT(*) FROM admin_audit_logs WHERE action='QQ_GROUP_UPDATED'").fetchone()[0])

    def test_rejects_invalid_links_numbers_and_extra_fields(self):
        invalid = [dict(joinUrl=url) for url in [
            'https://evil.invalid/join', 'http://qm.qq.com/q/x', 'https://qm.qq.com.evil.invalid/q/x',
            'https://user:pass@qm.qq.com/q/x', 'https://qm.qq.com:444/q/x', 'https://qm.qq.com/',
            'https://qm.qq.com/q/x#fragment', 'https://qm.qq.com/q/\nx', 'javascript:alert(1)',
            'mqqapi://card/show_pslcard?uin=999999&card_type=group',
            'mqqapi://card/show_pslcard?uin=123456789&card_type=friend',
            'mqqapi://card/show_pslcard?uin=123456789&uin=999999&card_type=group',
            'mqqapi://card/show_pslcard?uin=123456789&card_type=group&redirect=https://evil.invalid']]
        invalid += [dict(groupNumber=''), dict(groupNumber='１２３４５６'), dict(groupNumber='012345'), dict(groupNumber=123456), dict(enabled=1), dict(schoolPassword='synthetic')]
        for change in invalid:
            with self.subTest(change=change):
                self.assertEqual(422, self.save(**change)[0])
        self.assertEqual({'enabled': False}, self.public())

    def test_admin_page_includes_configuration_script(self):
        with urllib.request.urlopen(self.base + '/admin/') as response:
            html = response.read()
            self.assertIn(b'data-page="qqGroup" data-roles="SUPER_ADMIN"', html)
            self.assertIn(b'/admin/qq-group.js', html)
        with urllib.request.urlopen(self.base + '/admin/qq-group.js') as response:
            self.assertIn(b'renderers.qqGroup', response.read())
