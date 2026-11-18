import hashlib
import secrets
import unittest
from unittest.mock import patch

import server
import student_presence as presence
import test_campus_hub as fixtures


class StudentPresenceTest(unittest.TestCase):
    setUp = fixtures.CampusHubTest.setUp
    tearDown = fixtures.CampusHubTest.tearDown
    identity = fixtures.CampusHubTest.identity
    call = fixtures.CampusHubTest.call

    def payload(self, **changes):
        self.sequence = getattr(self,'sequence',0) + 1
        data = dict(reportSequence=self.sequence,installationId='test-installation-0001', reportToken='a'*64,
                    versionName='1.0.0', versionCode=14, consentVersion=6,
                    policyVersion=presence.POLICY_VERSION, consentAcceptedAt=server.now_ms(),
                    platform='ios', manufacturer='Apple', model='iPhone17,3', osVersion='26.5',
                    schoolIdentity=dict(studentId='20990001',realName='测试学生',grade='大二'))
        data.update(changes)
        return data

    def account(self, token, phone='13800000001'):
        with server.connection() as db:
            ident=db.execute('SELECT user_id FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()[0]
            db.execute("UPDATE users SET account_type='PHONE',phone_encrypted=?,phone_hash=?,phone_masked=? WHERE id=?",
                       (server.encrypt_phone(phone),server.phone_digest(phone),server.phone_mask(phone),ident))
            return ident

    def send(self, data=None, token=None):
        return self.call('POST','/api/v1/activity/student-session',data or self.payload(),token)

    def directory(self, query=''):
        status,result=self.call('GET','/api/v1/admin/account-directory'+query,token=self.admin)
        self.assertEqual(200,status,result)
        return result['data']

    def test_updated_local_password_policy_preserves_reporting_contract(self):
        for version, policy in [(6, '2026-10-23.3'), (7, '2026-10-26.1'), (8, '2026-11-08.1'), (9, '2026-11-16.1'), (10, '2026-11-17.1')]:
            self.assertEqual(200, self.send(self.payload(consentVersion=version, policyVersion=policy))[0])
            status, result = self.call('GET', '/api/v1/admin/account-analytics/devices?platform=ios', token=self.admin)
            self.assertEqual(200, status)
            self.assertEqual(version, result['data']['items'][0]['consent_version'])
            self.assertEqual(policy, result['data']['items'][0]['policy_version'])
        for version, policy in [(6, '2026-10-26.1'), (7, '2026-10-23.3'), (8, '2026-10-26.1'), (9, '2026-11-08.1'), (10, '2026-11-16.1')]:
            self.assertEqual(422, self.send(self.payload(consentVersion=version, policyVersion=policy))[0])
        for key in ['password', 'schoolPassword', 'unifiedCredential', 'cookies']:
            self.assertEqual(422, self.send(self.payload(consentVersion=7, policyVersion='2026-10-26.1', **{key:'synthetic-forbidden'}))[0])

    def test_school_only_full_profile_and_encrypted_storage(self):
        self.assertEqual(200,self.send()[0])
        row=self.directory()['items'][0]
        self.assertEqual(('测试学生','20990001','大二',''),(row['realName'],row['studentId'],row['grade'],row['phone']))
        self.assertFalse(row['platformSignedIn'])
        with server.connection() as db:
            record=dict(db.execute('SELECT * FROM student_presence').fetchone())
            self.assertNotIn('测试学生',str(record)); self.assertNotIn('20990001',str(record));self.assertNotIn('a'*64,str(record))
        self.assertNotIn('reportToken',str(row));self.assertNotIn('installation',str(row))

    def test_login_logout_switch_and_unbind_never_retain_phone(self):
        self.account(self.owner)
        self.assertEqual(200,self.send(token=self.owner)[0])
        row=self.directory('?scope=platform')['items'][0]
        self.assertEqual('13800000001',row['phone']);self.assertTrue(row['platformSignedIn'])
        self.assertEqual(200,self.send()[0])
        guest=self.directory('?scope=schoolOnly')['items'][0]
        self.assertEqual('',guest['phone']);self.assertEqual(0,self.directory('?scope=platform')['total'])
        self.account(self.other,'13800000002')
        other=self.payload(schoolIdentity=dict(studentId='20990002',realName='另一学生'))
        self.assertEqual(200,self.send(other,self.other)[0])
        row=self.directory('?scope=school')['items'][0]
        self.assertEqual('另一学生',row['realName']);self.assertEqual('13800000002',row['phone'])
        self.assertEqual(200,self.send(self.payload(schoolIdentity=None),self.other)[0])
        self.assertEqual(0,self.directory('?scope=school')['total'])
        self.assertEqual(200,self.send(self.payload(schoolIdentity=None))[0])
        self.assertEqual(0,self.directory('?scope=platform')['total'])

    def test_current_consent_and_strict_allowlist(self):
        for changes in [dict(consentVersion=5),dict(policyVersion='2026-10-23.2'),dict(consentAcceptedAt=True),
                        dict(password='never'),dict(cookie='never'),dict(phone='13800000000'),
                        dict(schoolIdentity=dict(studentId='20990001',password='never')),
                        dict(schoolIdentity=dict(studentId='20990001',admissionYear=True))]:
            self.assertEqual(422,self.send(self.payload(**changes))[0],changes)
        self.assertEqual(0,self.directory()['total'])

    def test_installation_capability_and_role_guards(self):
        self.assertEqual(200,self.send()[0])
        self.assertEqual(403,self.send(self.payload(reportToken='b'*64))[0])
        self.assertEqual(401,self.send(token='invalid')[0])
        self.assertEqual(403,self.send(token=self.admin)[0])
        for token in [None,self.owner,self.identity('ADMIN','CONTENT_OPERATOR')]:
            self.assertIn(self.call('GET','/api/v1/admin/account-directory',token=token)[0],[401,403])
            self.assertIn(self.call('GET','/api/v1/admin/account-directory/metrics',token=token)[0],[401,403])

    def test_dedup_filters_search_and_metrics(self):
        self.account(self.owner)
        self.send(token=self.owner)
        self.send(self.payload(installationId='test-installation-0002',reportToken='b'*64))
        self.assertEqual(1,self.directory('?scope=school')['total'])
        self.assertEqual(0,self.directory('?scope=schoolOnly')['total'])
        for q in ['20990001','13800000001','%E6%B5%8B%E8%AF%95%E5%AD%A6%E7%94%9F']:
            self.assertEqual(1,self.directory('?q='+q)['total'])
        self.assertEqual(0,self.directory('?q=20990002')['total'])
        status,result=self.call('GET','/api/v1/admin/account-directory/metrics',token=self.admin)
        self.assertEqual(200,status)
        for scope,count in result['data'].items():
            self.assertEqual(count,self.directory('?scope='+scope)['total'])
        self.assertEqual(1,self.directory('?window=today')['total'])

    def test_unknown_grade_registration_without_report_and_deletion(self):
        ident=self.account(self.owner)
        self.assertEqual(1,self.directory('?scope=registered')['total'])
        self.send(self.payload(schoolIdentity=dict(studentId='20990001',realName='测试学生')),self.owner)
        self.assertEqual('',self.directory()['items'][0]['grade'])
        with server.connection() as db:
            server.delete_user_account_data(db,db.execute('SELECT * FROM users WHERE id=?',(ident,)).fetchone(),reason='test')
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM student_presence').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM student_presence_daily').fetchone()[0])
        self.assertEqual(0,self.directory()['total'])

    def test_reordered_reports_and_revoked_sessions(self):
        self.account(self.owner)
        old=self.payload()
        self.send(old,self.owner)
        self.send(self.payload(schoolIdentity=None))
        self.send(old,self.owner)
        self.assertEqual(0,self.directory('?scope=school')['total'])
        self.send(token=self.owner)
        self.call('POST','/api/v1/auth/logout',{},self.owner)
        self.assertEqual(0,self.directory('?scope=platform')['total'])
        self.assertEqual('',self.directory('?scope=schoolOnly')['items'][0]['phone'])

    def test_device_list_reads_new_observation_with_full_phone(self):
        self.account(self.owner);self.send(token=self.owner)
        status,response=self.call('GET','/api/v1/admin/account-analytics/devices?platform=ios',token=self.admin)
        self.assertEqual(200,status,response)
        row=response['data']['items'][0]
        self.assertEqual('13800000001',row['phone']);self.assertEqual('测试学生',row['realName'])
        self.assertNotIn('installation_id',row)

    def test_retention_and_invalid_filters(self):
        self.send()
        with server.connection() as db:
            db.execute('UPDATE student_presence SET last_at=1');db.execute('UPDATE student_presence_daily SET last_at=1')
            presence.prune(db)
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM student_presence').fetchone()[0])
        for query in ['?scope=bad','?offset=-1','?window=no']:
            self.assertEqual(422,self.call('GET','/api/v1/admin/account-directory'+query,token=self.admin)[0])

if __name__ == '__main__': unittest.main()
