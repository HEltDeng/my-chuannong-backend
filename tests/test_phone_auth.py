import concurrent.futures
import hashlib
import secrets
import unittest
from unittest.mock import patch

import server
import phone_auth
import test_campus_hub as fixtures


class PhoneAuthTest(unittest.TestCase):
    setUp = fixtures.CampusHubTest.setUp
    tearDown = fixtures.CampusHubTest.tearDown
    identity = fixtures.CampusHubTest.identity
    call = fixtures.CampusHubTest.call

    def receipt(self):
        return {'policyVersion': phone_auth.POLICY_VERSION, 'consentAcceptedAt': server.now_ms()}

    def challenge(self, phone, purpose='register'):
        sent = []
        def provider(number, code, kind):
            sent.append((number, code, kind))
            return {'ok': True, 'code': 'Ok', 'requestId': 'synthetic-request', 'fee': 1}
        with patch.object(phone_auth, 'send_tencent', side_effect=provider):
            status, result = self.call('POST', '/api/v1/auth/sms-code', {'phone': phone, 'purpose': purpose, **self.receipt()})
        self.assertEqual(200, status, result)
        self.assertNotIn('code', result['data'])
        self.assertTrue(sent)
        return {'challengeId': result['data']['challengeId'], 'code': sent[0][1]}

    def register(self, phone='13900000001'):
        payload = {'phone': phone, 'password': 'SyntheticPass123!', 'confirmPassword': 'SyntheticPass123!',
                   **self.receipt(), **self.challenge(phone)}
        status, response = self.call('POST', '/api/v1/auth/register', payload)
        self.assertEqual(201, status, response)
        return response['data'], payload

    def cooldown(self):
        with server.connection() as db:
            db.execute('UPDATE phone_challenges SET created_at=created_at-61000')

    def test_registration_requires_otp_confirmation_and_current_consent(self):
        payload = {'phone': '13900000001', 'password': 'SyntheticPass123!', **self.receipt()}
        self.assertEqual(422, self.call('POST','/api/v1/auth/register',payload)[0])
        payload['confirmPassword'] = payload['password']
        self.assertEqual(422, self.call('POST','/api/v1/auth/register',payload)[0])
        account, payload = self.register()
        self.assertTrue(account['user']['phoneVerified'])
        server.RATE_LIMITER.reset()
        self.assertEqual(422, self.call('POST','/api/v1/auth/register',payload)[0])
        with server.connection() as db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM account_consents').fetchone()[0])
            record = db.execute('SELECT * FROM phone_challenges').fetchone()
            self.assertNotEqual(payload['code'], record['code_hash'])
            self.assertNotIn(payload['phone'], str(dict(record)))

    def test_otp_attempt_limit_and_purpose_separation(self):
        otp = self.challenge('13900000002')
        payload = {'phone':'13900000002','password':'SyntheticPass123!','confirmPassword':'SyntheticPass123!',**self.receipt(),**otp}
        wrong = '000000' if otp['code'] != '000000' else '999999'
        for _ in range(5):
            server.RATE_LIMITER.reset()
            self.assertEqual(422,self.call('POST','/api/v1/auth/register',{**payload,'code':wrong})[0])
        server.RATE_LIMITER.reset()
        self.assertEqual(422,self.call('POST','/api/v1/auth/register',payload)[0])
        with server.connection() as db:
            self.assertEqual(5,db.execute('SELECT attempts FROM phone_challenges').fetchone()[0])

    def test_sms_quotas_persist_failure_and_never_return_fake_success(self):
        with patch.object(phone_auth,'send_tencent',side_effect=server.ApiError(503,'SMS_DELIVERY_UNKNOWN','unavailable')):
            body={'phone':'13900000003','purpose':'register',**self.receipt()}
            self.assertEqual(503,self.call('POST','/api/v1/auth/sms-code',body)[0])
            server.RATE_LIMITER.reset()
            self.assertEqual(429,self.call('POST','/api/v1/auth/sms-code',body)[0])
        with server.connection() as db:
            self.assertEqual('UNKNOWN',db.execute('SELECT state FROM phone_challenges').fetchone()[0])

    def test_reset_two_per_day_revokes_sessions_and_preserves_user(self):
        account, original = self.register()
        for index in range(2):
            self.cooldown()
            otp=self.challenge(original['phone'],'reset')
            reset={**original,**otp,'password':'NewSyntheticPass123!','confirmPassword':'NewSyntheticPass123!'}
            code,response=self.call('POST','/api/v1/auth/reset-password',reset)
            self.assertEqual(200,code,response)
            self.assertNotIn('token',response['data'])
            self.assertEqual(1-index,response['data']['remainingToday'])
        self.assertEqual(401,self.call('GET','/api/v1/auth/me',token=account['token'])[0])
        self.cooldown()
        self.assertEqual(429,self.call('POST','/api/v1/auth/sms-code',{'phone':original['phone'],'purpose':'reset',**self.receipt()})[0])
        status, logged = self.call('POST','/api/v1/auth/login',{'phone':original['phone'],'password':'NewSyntheticPass123!'})
        self.assertEqual(200,status,logged)
        self.assertEqual(account['user']['id'],logged['data']['user']['id'])

    def test_binding_encrypted_unique_immutable_and_admin_full_fields(self):
        a,_=self.register('13900000004');b,_=self.register('13900000005')
        data={'studentId':'2026000001','realName':'合成同学','schoolCode':'SICAU',**self.receipt()}
        code,response=self.call('PUT','/api/v1/auth/student-identity',data,a['token'])
        self.assertEqual(200,code,response)
        self.assertEqual('PENDING',response['data']['identityStatus'])
        self.assertEqual(409,self.call('PUT','/api/v1/auth/student-identity',data,b['token'])[0])
        self.assertEqual(409,self.call('PUT','/api/v1/auth/student-identity',{**data,'studentId':'2026000002'},a['token'])[0])
        self.assertEqual(422,self.call('PUT','/api/v1/auth/student-identity',{**data,'schoolPassword':'forbidden'},a['token'])[0])
        with server.connection() as db:
            row=db.execute('SELECT * FROM users WHERE id=?',(a['user']['id'],)).fetchone()
            self.assertIsNone(row['student_id'])
            self.assertNotIn(data['studentId'],row['identity_encrypted'])
            self.assertNotIn(data['realName'],row['identity_encrypted'])
        details=self.call('GET','/api/v1/admin/users/'+a['user']['id']+'/details',token=self.admin)[1]['data']['user']
        self.assertEqual(data['studentId'],details['student_id'])
        self.assertEqual(data['realName'],details['real_name'])
        self.assertNotIn('identity_encrypted',details)
        restricted=self.identity('ADMIN','CONTENT_OPERATOR')
        self.assertEqual(403,self.call('GET','/api/v1/admin/users/'+a['user']['id']+'/details',token=restricted)[0])
        self.assertEqual(0,self.call('GET','/api/v1/admin/account-analytics',token=self.admin)[1]['data']['metrics']['verifiedStudents'])

    def test_concurrent_registration_consumes_code_once(self):
        phone='13900000006'
        payload={'phone':phone,'password':'SyntheticPass123!','confirmPassword':'SyntheticPass123!',**self.receipt(),**self.challenge(phone)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.call('POST','/api/v1/auth/register',payload)[0],range(2)))
        self.assertEqual([201,422],sorted(results))

    def test_identity_review_requires_superadmin_and_independent_evidence(self):
        account,_=self.register('13900000007')
        identity={'studentId':'2026000007','realName':'核验同学','schoolCode':'SICAU',**self.receipt()}
        self.assertEqual(200,self.call('PUT','/api/v1/auth/student-identity',identity,account['token'])[0])
        path='/api/v1/admin/users/'+account['user']['id']+'/student-review'
        body={'action':'approve','reason':'合成凭证核验通过'}
        restricted=self.identity('ADMIN','CONTENT_OPERATOR')
        self.assertEqual(403,self.call('POST',path,body,restricted)[0])
        self.assertEqual(422,self.call('POST',path,{'action':'approve'},self.admin)[0])
        status,response=self.call('POST',path,body,self.admin)
        self.assertEqual(200,status,response)
        self.assertEqual('VERIFIED',response['data']['identityStatus'])
        self.assertEqual(409,self.call('POST',path,body,self.admin)[0])
        with server.connection() as db:
            row=db.execute('SELECT * FROM student_identity_reviews').fetchone()
            self.assertNotIn(body['reason'],row['evidence_encrypted'])
        self.assertEqual(1,self.call('GET','/api/v1/admin/account-analytics',token=self.admin)[1]['data']['metrics']['verifiedStudents'])

    def test_provider_missing_config_fails_closed(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(server.ApiError) as error:
                phone_auth.send_tencent('13900000000','123456','register')
            self.assertEqual('SMS_NOT_CONFIGURED',error.exception.code)

    def test_unconfigured_api_does_not_consume_quota_or_claim_sms_available(self):
        with patch.dict('os.environ', {}, clear=True):
            status,response=self.call('GET','/api/v1/auth/capabilities')
            self.assertEqual(200,status,response)
            self.assertEqual([],response['data']['availablePurposes'])
            for _ in range(3):
                status,response=self.call('POST','/api/v1/auth/sms-code',{'phone':'13900000008','purpose':'register',**self.receipt()})
                self.assertEqual(503,status,response)
                self.assertEqual('SMS_NOT_CONFIGURED',response['error']['code'])
            with server.connection() as db:
                self.assertEqual(0,db.execute('SELECT COUNT(*) FROM phone_challenges').fetchone()[0])

    def test_existing_phone_verification_is_bound_to_logged_in_owner(self):
        a,_=self.register('13900000009');b,_=self.register('13900000010')
        self.cooldown()
        data={'phone':'13900000009','purpose':'verify_phone',**self.receipt()}
        self.assertEqual(422,self.call('POST','/api/v1/auth/sms-code',data,b['token'])[0])
        sent=[]
        def provider(phone,code,purpose):
            sent.append((code,purpose));return {'ok':True,'code':'Ok','requestId':'synthetic','fee':1}
        with patch.object(phone_auth,'send_tencent',side_effect=provider):
            status,response=self.call('POST','/api/v1/auth/sms-code',data,a['token'])
        self.assertEqual(200,status,response)
        self.assertEqual('verify_phone',sent[0][1])
        proof={'challengeId':response['data']['challengeId'],'code':sent[0][0],**self.receipt()}
        self.assertEqual(422,self.call('POST','/api/v1/auth/verify-phone',proof,b['token'])[0])
        self.assertEqual(200,self.call('POST','/api/v1/auth/verify-phone',proof,a['token'])[0])
        self.assertEqual(422,self.call('POST','/api/v1/auth/verify-phone',proof,a['token'])[0])
