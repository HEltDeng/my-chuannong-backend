import hashlib
import secrets
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import core
import server
import account_analytics as analytics
import test_campus_hub as fixtures


class AccountAnalyticsTest(unittest.TestCase):
    setUp = fixtures.CampusHubTest.setUp
    tearDown = fixtures.CampusHubTest.tearDown
    identity = fixtures.CampusHubTest.identity
    call = fixtures.CampusHubTest.call
    post = fixtures.CampusHubTest.post

    def account(self, token):
        with server.connection() as db:
            ident = db.execute('SELECT user_id FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()[0]
            db.execute("UPDATE users SET account_type='PHONE',phone_masked='138****0000' WHERE id=?",(ident,))
            return ident

    def payload(self, **changes):
        data = {'installationId':'synthetic-install','deviceScopedId':'a'*64,'versionName':'1.0.0','versionCode':9,
                'consentVersion':3,'manufacturer':'Synthetic','model':'Test phone','androidVersion':'15','sdkInt':35}
        data.update(changes)
        return data

    def overview(self):
        status, response = self.call('GET','/api/v1/admin/account-analytics',token=self.admin)
        self.assertEqual(200,status,response)
        return response['data']

    def test_zero_baseline_and_no_guest_or_background_inflation(self):
        self.account(self.owner)
        self.call('GET','/api/v1/auth/me',token=self.owner)
        self.call('GET','/api/v1/auth/me',token=self.other)
        data = self.overview()
        self.assertEqual(0,data['metrics']['dau'])
        self.assertTrue(all(row['dau'] is None for row in data['trend']))
        self.assertIsNone(data['coverage']['firstForegroundAt'])
        self.assertEqual(1,data['metrics']['registeredAccounts'])

    def test_foreground_retries_multiple_devices_and_account_switch(self):
        self.account(self.owner); self.account(self.other)
        for token, payload in [(self.owner,self.payload()),(self.owner,self.payload()),
                               (self.owner,self.payload(installationId='second',deviceScopedId='b'*64)),
                               (self.other,self.payload())]:
            status,response=self.call('POST','/api/v1/activity/foreground',payload,token)
            self.assertEqual(200,status,response)
            self.assertEqual({'accepted','day'},set(response['data']))
        metrics=self.overview()['metrics']
        self.assertEqual(2,metrics['dau']); self.assertEqual(2,metrics['mau'])
        self.assertEqual(2,metrics['reportedInstallations'])
        detail=self.call('GET','/api/v1/admin/account-analytics/accounts',token=self.admin)[1]['data']
        self.assertEqual(2,detail['total'])
        self.assertEqual('Test phone',detail['items'][0]['deviceProfile']['model'])
        self.assertNotIn('device_key_hash',str(detail)); self.assertNotIn('installation_id',str(detail))
        self.assertEqual('138****0000',detail['items'][0]['phoneMasked'])

    def test_consent_validation_authentication_and_roles(self):
        self.account(self.owner)
        self.assertEqual(401,self.call('POST','/api/v1/activity/foreground',self.payload())[0])
        self.assertEqual(403,self.call('POST','/api/v1/activity/foreground',self.payload(),self.other)[0])
        self.assertEqual(403,self.call('POST','/api/v1/activity/foreground',self.payload(),self.admin)[0])
        for changes in [{'consentVersion':2},{'consentVersion':True},{'model':'x'*101},{'sdkInt':True},{'createdAt':0},{'phone':'13800000000'}]:
            self.assertEqual(422,self.call('POST','/api/v1/activity/foreground',self.payload(**changes),self.owner)[0])
        for token in [self.owner,self.identity('ADMIN','CONTENT_OPERATOR'),self.identity('ADMIN','DRIVING_SCHOOL_OPERATOR')]:
            for path in ['/api/v1/admin/account-analytics','/api/v1/admin/account-analytics/accounts']:
                self.assertEqual(403,self.call('GET',path,token=token)[0])
        self.assertEqual(0,self.overview()['metrics']['dau'])

    def test_beijing_midnight_rolling_and_natural_month_windows(self):
        user=self.account(self.owner)
        at=int(datetime(2026,11,1,0,0,1,tzinfo=core.BUSINESS_TIMEZONE).timestamp()*1000)
        with patch.object(analytics,'now_ms',return_value=at), server.connection() as db:
            for timestamp in [at-2000,at,at-29*analytics.DAY,at-30*analytics.DAY]:
                analytics.record(db,user,timestamp=timestamp)
            result=analytics.summary(db,{'days':'7'})
            self.assertEqual(1,result['metrics']['dau']);self.assertEqual(1,result['metrics']['calendarMonth'])
            self.assertEqual(1,result['metrics']['mau'])
            self.assertEqual('2026-11-01',result['trend'][-1]['day'])
            self.assertEqual(1,result['trend'][-2]['dau'])
            self.assertEqual(('2026-10-03','2026-11-01'),analytics.window({'window':'30d'},at))
            self.assertEqual(1,analytics.accounts(db,{'window':'day','day':'2026-10-31'},server.serialize_user)['total'])

    def test_service_allowlist_failed_events_and_repeated_initialization(self):
        user=self.account(self.owner)
        with server.connection() as db:
            for event,result in [('APP_ACTIVE','SUCCESS'),('LOGIN_FAILURE','FAILURE'),('LOGIN_SUCCESS','FAILURE')]:
                server.write_user_event(db,user,'PLATFORM',event,result)
        self.assertEqual(0,self.overview()['metrics']['dau'])
        with server.connection() as db:
            server.write_user_event(db,user,'PLATFORM','LOGIN_SUCCESS')
        self.assertEqual(1,self.overview()['metrics']['dau'])
        server.initialize_database();server.initialize_database()
        self.assertEqual(1,self.overview()['metrics']['dau'])

    def test_backfill_never_attributes_anonymous_history_to_current_owner(self):
        user=self.account(self.owner)
        with server.connection() as db:
            server.write_user_event(db,user,'PLATFORM','LOGIN_SUCCESS',identity_kind='DEVICE')
            db.execute("DELETE FROM account_analytics_settings WHERE key='backfill_v1'")
            analytics.initialize(db)
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM account_activity_daily').fetchone()[0])
            server.write_user_event(db,user,'PLATFORM','LOGIN_SUCCESS')
            db.execute('DELETE FROM account_activity_daily')
            db.execute("DELETE FROM account_analytics_settings WHERE key='backfill_v1'")
            analytics.initialize(db)
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM account_activity_daily').fetchone()[0])

    def test_account_deletion_status_and_retention(self):
        user=self.account(self.owner)
        self.assertEqual(200,self.call('POST','/api/v1/activity/foreground',self.payload(),self.owner)[0])
        with server.connection() as db:
            db.execute("UPDATE users SET account_status='DISABLED' WHERE id=?",(user,))
        self.assertEqual(0,self.overview()['metrics']['dau'])
        self.assertEqual(403,self.call('POST','/api/v1/activity/foreground',self.payload(),self.owner)[0])
        with server.connection() as db:
            db.execute("UPDATE users SET account_status='ACTIVE' WHERE id=?",(user,))
            old=analytics.now_ms()-181*analytics.DAY
            analytics.record(db,user,timestamp=old)
            analytics.prune(db)
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM account_activity_daily').fetchone()[0])
            row=db.execute('SELECT * FROM users WHERE id=?',(user,)).fetchone()
            server.delete_user_account_data(db,row,reason='synthetic deletion')
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM account_activity_daily').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM account_device_profiles').fetchone()[0])

    def test_active_account_pagination_matches_count(self):
        with server.connection() as db:
            for n in range(53):
                ident='synthetic-page-'+str(n)
                db.execute("INSERT INTO users(id,username,password_hash,password_salt,role,account_type,created_at) VALUES(?,?,?,?,'USER','PHONE',?)",(ident,ident,'unused','unused',server.now_ms()))
                analytics.record(db,ident)
        first=self.call('GET','/api/v1/admin/account-analytics/accounts',token=self.admin)[1]['data']
        second=self.call('GET','/api/v1/admin/account-analytics/accounts?offset=50',token=self.admin)[1]['data']
        self.assertEqual(53,first['total']);self.assertEqual(50,len(first['items']));self.assertEqual(3,len(second['items']))
        self.assertEqual(53,len({r['id'] for r in first['items']+second['items']}))

    def test_real_post_operation_is_account_activity(self):
        self.account(self.owner)
        status,response=self.post()
        self.assertEqual(201,status,response)
        self.assertEqual(1,self.overview()['metrics']['dau'])
        self.assertEqual(0,self.overview()['metrics']['todayForegroundAccounts'])

    def test_range_validation_and_assets(self):
        for suffix in ['?days=1000','/accounts?window=all','/accounts?offset=-1','/accounts?window=day&day=not-a-date']:
            self.assertEqual(422,self.call('GET','/api/v1/admin/account-analytics'+suffix,token=self.admin)[0])
        import urllib.request
        with urllib.request.urlopen(self.base+'/admin/account-analytics.js') as response:
            self.assertEqual(200,response.status)
            self.assertIn(b'renderers.analytics',response.read())

    def test_ios_current_consent_metadata_validation_and_admin_device_view(self):
        user = self.account(self.owner)
        payload = {'installationId':'synthetic-ios-current','versionName':'1.0.0','versionCode':12,
                   'consentVersion':4,'policyVersion':'2026-10-23','consentAcceptedAt':server.now_ms()-1000,
                   'platform':'ios','manufacturer':'Apple','model':'iPhone17,3','osVersion':'26.0'}
        for changes in [{'consentAcceptedAt':True},{'consentAcceptedAt':server.now_ms()+600000},
                        {'policyVersion':'old'},{'versionCode':True},{'deviceName':'Private phone'},
                        {'imei':'forbidden'},{'schoolPassword':'forbidden'}]:
            self.assertEqual(422,self.call('POST','/api/v1/activity/foreground',{**payload,**changes},self.owner)[0])
        for _ in range(2):
            code,data = self.call('POST','/api/v1/activity/foreground',payload,self.owner)
            self.assertEqual(200,code,data)
        path = '/api/v1/admin/account-analytics/devices?platform=ios'
        code,data = self.call('GET',path,token=self.admin)
        self.assertEqual(200,code,data)
        self.assertEqual(1,data['data']['total'])
        row = data['data']['items'][0]
        self.assertEqual('iPhone17,3',row['model'])
        self.assertEqual(12,row['app_build'])
        self.assertEqual(payload['consentAcceptedAt'],row['consent_accepted_at'])
        self.assertEqual('2026-10-23',row['policy_version'])
        self.assertGreaterEqual(row['consent_received_at'],row['consent_accepted_at'])
        self.assertNotIn('installation_id',row)
        self.assertNotIn('deviceScopedId',row)
        for token in [self.owner,self.identity('ADMIN','LIBRARY_OPERATOR'),self.identity('ADMIN','CONTENT_OPERATOR')]:
            self.assertEqual(403,self.call('GET',path,token=token)[0])
        with server.connection() as db:
            analytics.forget(db,user)
        self.assertEqual(0,self.call('GET',path,token=self.admin)[1]['data']['total'])
