"""Three-state service availability, API enforcement and operator isolation."""
import unittest
from datetime import timedelta
import server
from module_access import ANNOUNCEMENT_ROLES
import test_campus_hub as fixtures

class ServiceAvailabilityTest(unittest.TestCase):
    setUp = fixtures.CampusHubTest.setUp
    tearDown = fixtures.CampusHubTest.tearDown
    identity = fixtures.CampusHubTest.identity
    call = fixtures.CampusHubTest.call
    provider = fixtures.CampusHubTest.provider

    def flag(self, key, status, token=None, **extra):
        return self.call('PUT', '/api/v1/admin/flags/' + key,
            {'payload': {'status': status, 'maintenanceNote': '合成维护提示', 'expectedRestoreAt': 1900000000000, **extra}}, token or self.admin)

    def test_all_ten_states_reach_both_platforms_and_block_service_apis(self):
        endpoints = {'resource_library':'/library', 'secondhand':'/community/secondhand', 'lost_found':'/community/lost-found'}
        for key in server.SERVICE_FEATURES:
            endpoint = endpoints.get(key, '/services/guides/' + key if key in {'errand','electric','logistics_repair'} else '/services/providers?category=' + key)
            for state in ('MAINTENANCE', 'COMING_SOON', 'OPEN'):
                with self.subTest(key=key, state=state):
                    code, result = self.flag(key, state)
                    self.assertEqual(200, code, result)
                    for platform in ('ios', 'android'):
                        code, result = self.call('GET', '/api/v1/public/bootstrap?platform=' + platform)
                        row = next(f for f in result['data']['flags'] if f['key'] == key)
                        self.assertEqual(state, row['payload']['status'])
                        self.assertEqual('合成维护提示', row['payload']['maintenanceNote'])
                        self.assertEqual(1900000000000, row['payload']['expectedRestoreAt'])
                    code, result = self.call('GET', '/api/v1' + endpoint, token=self.owner)
                    if state == 'OPEN': self.assertIn(code, (200,404), result)
                    else:
                        self.assertEqual(503, code, result)
                        self.assertEqual('FEATURE_' + state, result['error']['code'])

    def test_only_super_admin_can_change_service_states(self):
        for role, module in ANNOUNCEMENT_ROLES.items():
            key = {'resources':'resource_library','lost-found':'lost_found'}.get(module,module)
            token = self.identity('ADMIN',role)
            code, data = self.call('GET','/api/v1/admin/service-availability',token=token)
            self.assertEqual(200,code,data)
            self.assertEqual([key],[x['key'] for x in data['data']['items']])
            self.assertFalse(data['data']['canManage'])
            self.assertEqual(403,self.flag(key,'MAINTENANCE',token)[0])
            self.assertEqual(200,self.flag(key,'MAINTENANCE')[0])
            other = next(k for k in server.SERVICE_FEATURES if k != key)
            self.assertEqual(403,self.flag(other,'MAINTENANCE',token)[0])
            self.assertEqual(403,self.call('GET','/api/v1/admin/config',token=token)[0])
        self.assertEqual(403,self.flag('errand','OPEN',self.owner)[0])

    def test_provider_deep_links_booking_and_aggregate_lists(self):
        status, data = self.provider(status='PUBLISHED',bookingEnabled=True)
        self.assertEqual(201,status,data)
        provider_id = data['data']['id']
        body = {'providerId':provider_id,'clientId':'availability-booking','course':'合成课程',
                'preferredDate':str(server.business_today()+timedelta(days=1)), 'name':'合成联系人','phone':'19900000000','consented':True}
        code,data = self.call('POST','/api/v1/services/bookings',body,self.owner)
        self.assertEqual(201,code,data)
        booking = data['data']['id']
        self.flag('driving_school','COMING_SOON')
        self.assertEqual(503,self.call('GET','/api/v1/services/providers/'+provider_id,token=self.owner)[0])
        self.assertEqual([],self.call('GET','/api/v1/services/providers',token=self.owner)[1]['data']['items'])
        self.assertEqual(503,self.call('POST','/api/v1/services/bookings',{**body,'clientId':'blocked-booking'},self.owner)[0])
        # Preserve viewing and cancelling an already-owned booking during maintenance.
        self.assertEqual(200,self.call('GET','/api/v1/services/bookings',token=self.owner)[0])
        self.assertEqual(200,self.call('PATCH','/api/v1/services/bookings/'+booking,{'status':'CANCELLED'},self.owner)[0])
        self.assertEqual(200,self.call('GET','/api/v1/admin/campus/providers',token=self.admin)[0])

    def test_validate_metadata_preserve_guest_setting_and_audit(self):
        for expected in (-1, True, 'tomorrow', 1.2, 253402300800000):
            self.assertEqual(400,self.flag('errand','MAINTENANCE',expectedRestoreAt=expected)[0])
        self.assertEqual(400,self.flag('errand','OTHER')[0])
        self.assertEqual(400,self.flag('errand','OPEN',maintenanceNote='字'*301)[0])
        self.assertEqual(200,self.flag('resource_library','OPEN',allowGuestBrowsing=True)[0])
        self.assertEqual(200,self.flag('resource_library','MAINTENANCE')[0])
        with server.connection() as db:
            import json
            payload=json.loads(db.execute("SELECT payload_json FROM remote_flags WHERE key='resource_library'").fetchone()[0])
            self.assertTrue(payload['allowGuestBrowsing'])
            self.assertTrue(db.execute("SELECT COUNT(*) FROM admin_audit_logs WHERE action='FEATURE_FLAG_UPDATED'").fetchone()[0])
        # The optional timestamp can be cleared; reaching a date never reopens a service.
        self.assertEqual(200,self.flag('errand','COMING_SOON',expectedRestoreAt=None)[0])

    def test_closed_modules_freeze_all_operator_routes_and_reopen_immediately(self):
        endpoints = {
            'LIBRARY_OPERATOR': '/api/v1/admin/resources',
            'LOST_FOUND_OPERATOR': '/api/v1/admin/campus/lost-found',
            'SECONDHAND_OPERATOR': '/api/v1/admin/campus/secondhand',
            'DRIVING_SCHOOL_OPERATOR': '/api/v1/admin/campus/providers?category=driving_school',
            'IELTS_TOEFL_OPERATOR': '/api/v1/admin/campus/providers?category=ielts_toefl',
            'CIVIL_POSTGRAD_OPERATOR': '/api/v1/admin/campus/providers?category=civil_postgrad',
            'GYM_OPERATOR': '/api/v1/admin/campus/providers?category=gym',
            'ERRAND_OPERATOR': '/api/v1/admin/campus/guides',
            'REPAIR_OPERATOR': '/api/v1/admin/campus/guides',
            'ELECTRIC_OPERATOR': '/api/v1/admin/campus/guides',
        }
        for role, endpoint in endpoints.items():
            key = next(iter(server.service_features_for_role(role)))
            token = self.identity('ADMIN', role)
            for state in ('CLOSED', 'COMING_SOON', 'MAINTENANCE'):
                with self.subTest(role=role, state=state):
                    self.assertEqual(200, self.flag(key,state)[0])
                    for method, path in [('GET',endpoint),('POST',endpoint),
                                         ('GET','/api/v1/admin/announcements'),
                                         ('POST','/api/v1/admin/announcements')]:
                        code, data = self.call(method,path,{} if method == 'POST' else None,token)
                        self.assertEqual(403,code,data)
                        self.assertEqual('ADMIN_SERVICE_CLOSED',data['error']['code'])
                    self.assertEqual(200,self.call('GET','/api/v1/admin/service-availability',token=token)[0])
                    self.assertEqual(403,self.flag(key,'OPEN',token)[0])
                    self.assertEqual(200,self.call('GET',endpoint,token=self.admin)[0])
            self.assertEqual(200,self.flag(key,'OPEN')[0])
            self.assertEqual(200,self.call('GET',endpoint,token=token)[0])
        content = self.identity('ADMIN','CONTENT_OPERATOR')
        self.assertEqual(403,self.flag('errand','OPEN',content)[0])

    def test_legacy_content_role_cannot_bypass_closed_modules(self):
        token = self.identity('ADMIN','CONTENT_OPERATOR')
        code,data = self.provider(status='PUBLISHED',bookingEnabled=True)
        self.assertEqual(201,code,data)
        provider = data['data']['id']
        self.flag('driving_school','CLOSED')
        self.flag('secondhand','CLOSED')
        self.flag('errand','CLOSED')
        for method,path,body in [
            ('GET','/api/v1/admin/campus/providers?category=driving_school',None),
            ('PUT','/api/v1/admin/campus/providers/'+provider,{'category':'gym'}),
            ('GET','/api/v1/admin/campus/providers/'+provider,None),
            ('POST','/api/v1/admin/campus/secondhand',{}),
            ('GET','/api/v1/admin/campus/secondhand',None),
            ('PUT','/api/v1/admin/campus/guides/errand',{}),
            ('POST','/api/v1/admin/announcements',{'module':'errand'}),
            ('GET','/api/v1/admin/announcements?module=errand',None),
        ]:
            code,data=self.call(method,path,body,token)
            self.assertEqual(403,code,data)
            self.assertEqual('ADMIN_SERVICE_CLOSED',data['error']['code'])
        self.assertEqual([],self.call('GET','/api/v1/admin/campus/providers',token=token)[1]['data']['items'])
        self.assertEqual(200,self.call('GET','/api/v1/admin/campus/providers?category=gym',token=token)[0])
