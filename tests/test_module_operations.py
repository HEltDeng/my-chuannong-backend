import secrets
import base64
import io
import unittest
import urllib.request
from datetime import timedelta
import server
from module_access import PROVIDER_ROLES, GUIDE_ROLES
from PIL import Image
import test_campus_hub as fixtures


class ModuleOperationsTest(unittest.TestCase):
    setUp = fixtures.CampusHubTest.setUp
    tearDown = fixtures.CampusHubTest.tearDown
    identity = fixtures.CampusHubTest.identity
    call = fixtures.CampusHubTest.call
    provider = fixtures.CampusHubTest.provider

    def test_module_workspace_script_is_served(self):
        with urllib.request.urlopen(self.base + '/admin/module-operations.js', timeout=5) as response:
            self.assertEqual(200, response.status)
            self.assertIn('javascript', response.headers['Content-Type'])
            self.assertIn(b'renderers[key]', response.read())

    def test_provider_scope_lists_and_mutations(self):
        for role, category in PROVIDER_ROLES.items():
            token = self.identity('ADMIN', role)
            own = self.provider(category=category)[1]['data']
            foreign_category = next(v for v in PROVIDER_ROLES.values() if v != category)
            foreign = self.provider(category=foreign_category)[1]['data']
            base = '/api/v1/admin/campus/providers'
            rows = self.call('GET', base, token=token)[1]['data']['items']
            self.assertTrue(rows)
            self.assertTrue(all(r['category'] == category for r in rows))
            self.assertEqual(403, self.call('GET', base+'?category='+foreign_category, token=token)[0])
            self.assertEqual(404, self.call('GET', base+'/'+foreign['id'], token=token)[0])
            self.assertEqual(403, self.call('PATCH', base+'/'+foreign['id'], {'name':'越权'}, token)[0])
            self.assertEqual(403, self.call('PATCH', base+'/'+own['id'], {'category':foreign_category}, token)[0])
            self.assertEqual(200, self.call('PATCH', base+'/'+own['id'], {'name':'合法修改'}, token)[0])
            self.assertEqual(403, self.call('GET','/api/v1/admin/campus/summary',token=token)[0])
            self.assertEqual(403, self.call('GET','/api/v1/admin/admins',token=token)[0])

    def test_guide_scopes(self):
        for role, guide in GUIDE_ROLES.items():
            token = self.identity('ADMIN', role)
            base = '/api/v1/admin/campus/guides'
            rows = self.call('GET',base,token=token)[1]['data']['items']
            self.assertEqual([guide], [r['id'] for r in rows])
            data = {'title':'合成指南','body':'合成内容','status':'PUBLISHED'}
            self.assertEqual(200,self.call('PUT',base+'/'+guide,data,token)[0])
            other = next(v for v in GUIDE_ROLES.values() if v != guide)
            self.assertEqual(403,self.call('PUT',base+'/'+other,data,token)[0])
            self.assertEqual(403,self.call('GET',base+'/'+other,token=token)[0])
            self.assertEqual(403,self.call('GET','/api/v1/admin/campus/providers',token=token)[0])

    def test_booking_scope_hides_contacts_and_denies_changes(self):
        created=[]
        for category in ['driving_school','gym']:
            provider=self.provider(category=category,status='PUBLISHED',bookingEnabled=True)[1]['data']
            body={'providerId':provider['id'],'clientId':secrets.token_hex(12),'course':'测试课程',
                  'preferredDate':(server.business_today()+timedelta(days=1)).isoformat(),
                  'name':'合成姓名','phone':'12345678900','consented':True}
            status,data=self.call('POST','/api/v1/services/bookings',body,self.owner)
            self.assertEqual(201,status,data);created.append(data['data']['id'])
        token=self.identity('ADMIN','DRIVING_SCHOOL_OPERATOR')
        base='/api/v1/admin/campus/bookings'
        status,data=self.call('GET',base,token=token)
        self.assertEqual(200,status,data)
        self.assertEqual(1,data['data']['total'])
        self.assertEqual([created[0]],[r['id'] for r in data['data']['items']])
        self.assertEqual(403,self.call('PATCH',base+'/'+created[1],{'status':'CONFIRMED'},token)[0])
        self.assertEqual(200,self.call('PATCH',base+'/'+created[0],{'status':'CONFIRMED'},token)[0])

    def test_admin_publish_visible_idempotent_and_delete(self):
        for module,role in [('secondhand','SECONDHAND_OPERATOR'),('lost-found','LOST_FOUND_OPERATOR')]:
            token=self.identity('ADMIN',role)
            body={'clientId':secrets.token_hex(12),'title':'合成管理发布','description':'测试内容','campus':'成都校区'}
            body.update({'type':'sell','category':'书籍','wechat':'synthetic','images':[]} if module=='secondhand'
                        else {'kind':'found','location':'测试地点','date':'2026-10-22','time':'','contact':'synthetic','image':''})
            base='/api/v1/admin/campus/'+module
            status,data=self.call('POST',base,body,token)
            self.assertEqual(201,status,data);ident=data['data']['id']
            self.assertEqual('PUBLISHED',data['data']['status'])
            self.assertEqual(200,self.call('POST',base,body,token)[0])
            self.assertEqual(200,self.call('GET','/api/v1/community/'+module+'/'+ident,token=self.owner)[0])
            self.assertEqual(200,self.call('DELETE',base+'/'+ident,token=token)[0])
            self.assertEqual(409,self.call('POST',base,body,token)[0])
            self.assertEqual(404,self.call('GET','/api/v1/community/'+module+'/'+ident,token=self.owner)[0])
            wrong=self.identity('ADMIN','LIBRARY_OPERATOR')
            self.assertEqual(403,self.call('POST',base,body,wrong)[0])

    def test_super_admin_can_create_each_scoped_operator(self):
        for role in set(PROVIDER_ROLES)|set(GUIDE_ROLES)|{'SECONDHAND_OPERATOR'}:
            status,data=self.call('POST','/api/v1/admin/admins',{'username':'test_'+secrets.token_hex(6),
                'nickname':'合成管理员','password':secrets.token_urlsafe(24),'adminRole':role},self.admin)
            self.assertEqual(201,status,data)
            self.assertEqual(role,data['data']['adminRole'])

    def test_module_notices_images_and_cross_module_protection(self):
        base = '/api/v1/admin/announcements'
        driving = self.identity('ADMIN', 'DRIVING_SCHOOL_OPERATOR')
        gym = self.identity('ADMIN', 'GYM_OPERATOR')
        output = io.BytesIO()
        Image.new('RGB', (5, 5)).save(output, format='PNG')
        body = {'title': '合成驾校动态', 'content': '仅用于测试', 'enabled': True,
                'serviceVisible': True, 'imageBase64': base64.b64encode(output.getvalue()).decode()}
        status, data = self.call('POST', base, body, driving)
        self.assertEqual(201, status, data)
        ident = data['data']['id']
        rows = self.call('GET', base, token=driving)[1]['data']
        self.assertEqual([ident], [r['id'] for r in rows])
        self.assertEqual('driving_school', rows[0]['module_id'])
        self.assertTrue((server.UPLOADS / rows[0]['image_name']).is_file())
        self.assertEqual([], self.call('GET', base, token=gym)[1]['data'])
        self.assertEqual(403, self.call('GET', base+'?module=gym', token=driving)[0])
        self.assertEqual(403, self.call('POST', base, {**body, 'module':'gym'}, driving)[0])
        self.assertEqual(403, self.call('PATCH', base+'/'+ident, {'title':'越权'}, gym)[0])
        self.assertEqual(403, self.call('DELETE', base+'/'+ident, token=gym)[0])
        self.assertEqual(422, self.call('PATCH', base+'/'+ident, {'module':'gym'}, driving)[0])
        self.assertEqual(422, self.call('POST', base, {**body, 'module':[]}, driving)[0])
        self.assertEqual(200, self.call('PATCH', base+'/'+ident, {'title':'合法修改'}, driving)[0])
        self.assertEqual(1, len(self.call('GET', base, token=self.admin)[1]['data']))
        self.assertEqual(200, self.call('DELETE', base+'/'+ident, token=driving)[0])
        self.assertEqual([], self.call('GET', base, token=driving)[1]['data'])
