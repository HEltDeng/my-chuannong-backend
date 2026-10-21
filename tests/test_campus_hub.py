import base64
import io
import json
import secrets
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path
from PIL import Image
import core
import server


class CampusHubTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        core.configure_test_runtime(Path(self.temp.name)/'test.db')
        server.DB_PATH=core.DB_PATH
        server.UPLOADS=Path(self.temp.name)/'uploads'
        server.RATE_LIMITER.reset()
        server.initialize_database()
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.ApiHandler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.http.server_port}'
        self.admin=self.identity('ADMIN','SUPER_ADMIN')
        self.owner=self.identity();self.other=self.identity()

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join()
        self.temp.cleanup()

    def identity(self,role='USER',admin_role=None):
        ident=secrets.token_hex(12)
        digest,salt=server.password_record(secrets.token_urlsafe(24))
        with server.connection() as db:
            db.execute('INSERT INTO users(id,username,password_hash,password_salt,role,admin_role,admin_permissions_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
                       (ident,ident,digest,salt,role,admin_role,'["*"]' if admin_role=='SUPER_ADMIN' else '[]',server.now_ms()))
            return server.issue_token(db,ident)

    def call(self,method,path,body=None,token=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        request=urllib.request.Request(self.base+path,json.dumps(body).encode() if body is not None else None,headers,method=method)
        try:
            with urllib.request.urlopen(request,timeout=5) as r:return r.status,json.loads(r.read())
        except urllib.error.HTTPError as r:return r.code,json.loads(r.read())

    def post(self,module='secondhand',**changes):
        data={'clientId':secrets.token_hex(12),'title':'合成测试信息','description':'仅用于测试','campus':'成都校区'}
        data.update({'type':'sell','category':'书籍','wechat':'synthetic_contact','images':[]} if module=='secondhand' else {'kind':'found','location':'测试图书馆','date':'2026-10-19','time':'12:00','image':'','contact':'synthetic_contact'})
        data.update(changes)
        return self.call('POST','/api/v1/community/'+module,data,self.owner)

    def provider(self,**changes):
        data={'category':'driving_school','name':'合成机构','campus':'成都校区','status':'DRAFT','summary':'仅用于测试','details':{'courses':[{'id':'course-1','title':'合成课程'}]},'isDemo':False,'bookingEnabled':False}
        data.update(changes)
        return self.call('POST','/api/v1/admin/campus/providers',data,self.admin)

    def review(self, ident, action='approve', reason=''):
        return self.call('PATCH','/api/v1/admin/campus/secondhand/'+ident,{'action':action,'reason':reason},self.admin)

    def test_unauthenticated_and_operator_boundaries(self):
        self.assertEqual(401,self.call('GET','/api/v1/community/secondhand')[0])
        self.assertEqual(403,self.call('GET','/api/v1/admin/campus/secondhand',token=self.owner)[0])
        lost=self.identity('ADMIN','LOST_FOUND_OPERATOR')
        content=self.identity('ADMIN','CONTENT_OPERATOR')
        self.assertEqual(200,self.call('GET','/api/v1/admin/campus/lost-found',token=lost)[0])
        self.assertEqual(403,self.call('GET','/api/v1/admin/campus/providers',token=lost)[0])
        self.assertEqual(200,self.call('GET','/api/v1/admin/campus/providers',token=content)[0])
        self.assertEqual(403,self.call('GET','/api/v1/admin/campus/lost-found',token=content)[0])

    def test_posts_are_owned_encrypted_and_contacts_are_detail_only(self):
        status,data=self.post();self.assertEqual(201,status,data);ident=data['data']['id']
        self.assertEqual(200,self.review(ident)[0])
        listing=self.call('GET','/api/v1/community/secondhand',token=self.other)[1]['data']['items'][0]
        self.assertEqual('',listing['wechat']);self.assertFalse(listing['mine'])
        detail=self.call('GET','/api/v1/community/secondhand/'+ident,token=self.other)[1]['data']
        self.assertEqual('synthetic_contact',detail['wechat'])
        self.assertEqual(403,self.call('DELETE','/api/v1/community/secondhand/'+ident,token=self.other)[0])
        self.assertEqual(403,self.call('PATCH','/api/v1/community/secondhand/'+ident,{'done':True},self.other)[0])
        with server.connection() as db:
            row=db.execute('SELECT * FROM campus_posts WHERE id=?',(ident,)).fetchone()
            self.assertNotIn('synthetic_contact',row['contact_encrypted']+row['payload_json'])
        self.assertEqual(200,self.call('DELETE','/api/v1/community/secondhand/'+ident,token=self.owner)[0])
        self.assertEqual(404,self.call('GET','/api/v1/community/secondhand/'+ident,token=self.other)[0])

    def test_post_idempotency_and_validation(self):
        a=self.post(clientId='stable-id');b=self.post(clientId='stable-id')
        self.assertEqual(a[1]['data']['id'],b[1]['data']['id'])
        self.assertEqual(422,self.post(campus='长理校区')[0])
        self.assertEqual(422,self.post(wechat='',phone='')[0])
        self.assertEqual(422,self.post(type='chat')[0])
        self.assertEqual(422,self.post(images=['javascript:alert(1)'])[0])
        self.assertEqual(422,self.post(module='lost-found',date='2026-02-30')[0])

    def test_normalized_images_and_cross_owner_image_reuse(self):
        output=io.BytesIO();Image.new('RGB',(5,5)).save(output,format='PNG')
        uri='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()
        status,data=self.post(images=[uri]);self.assertEqual(201,status,data)
        image=data['data']['images'][0]
        self.assertTrue(image.startswith('/uploads/'))
        self.assertTrue((server.UPLOADS/image.rsplit('/',1)[1]).exists())
        self.assertEqual(422,self.post(images=[image])[0])

    def test_lost_completion_review_and_permanent_delete(self):
        status,data=self.post('lost-found');self.assertEqual(201,status,data);ident=data['data']['id'];path='/api/v1/community/lost-found/'+ident
        self.assertEqual(200,self.call('PATCH','/api/v1/admin/campus/lost-found/'+ident,{'action':'approve'},self.admin)[0])
        self.assertTrue(self.call('PATCH',path,{'done':True},self.owner)[1]['data']['done'])
        self.assertFalse(self.call('PATCH',path,{'done':False},self.owner)[1]['data']['done'])
        self.assertEqual(200,self.call('DELETE',path,token=self.owner)[0])
        self.assertEqual(404,self.call('PATCH',path,{'restore':True},self.owner)[0])

    def test_moderation_is_audited_and_cannot_be_bypassed(self):
        ident=self.post()[1]['data']['id'];path='/api/v1/community/secondhand/'+ident
        self.assertEqual(200,self.review(ident,'reject','测试驳回原因')[0])
        self.assertEqual(404,self.call('GET',path,token=self.other)[0])
        self.assertEqual(409,self.call('PATCH',path,{'done':False},self.owner)[0])
        with server.connection() as db:self.assertEqual(1,db.execute("SELECT COUNT(*) FROM admin_audit_logs WHERE action='MARKETPLACE_REVIEW'").fetchone()[0])
        values=self.call('GET',path,token=self.owner)[1]['data']
        self.assertEqual('测试驳回原因',values['moderationNote'])
        self.assertEqual('PENDING',self.call('PATCH',path,values,self.owner)[1]['data']['status'])
        self.assertEqual(200,self.review(ident)[0])
        self.assertEqual(200,self.call('GET',path,token=self.other)[0])

    def test_pagination_and_campus_filters(self):
        self.review(self.post(campus='成都校区')[1]['data']['id']);self.review(self.post(campus='雅安校区')[1]['data']['id'])
        data=self.call('GET','/api/v1/community/secondhand?limit=1',token=self.other)[1]['data']
        self.assertEqual(2,data['total']);self.assertEqual(1,len(data['items']))
        campus=urllib.parse.quote('雅安校区')
        data=self.call('GET','/api/v1/community/secondhand?campus='+campus,token=self.other)[1]['data']
        self.assertEqual(1,data['total'])
        self.assertEqual(422,self.call('GET','/api/v1/community/secondhand?offset=-1',token=self.other)[0])

    def test_draft_publish_and_unlist_provider(self):
        status,data=self.provider();self.assertEqual(201,status,data);ident=data['data']['id']
        self.assertEqual([],self.call('GET','/api/v1/services/providers',token=self.owner)[1]['data']['items'])
        self.assertEqual(200,self.call('PATCH','/api/v1/admin/campus/providers/'+ident,{'status':'PUBLISHED'},self.admin)[0])
        self.assertEqual('合成机构',self.call('GET','/api/v1/services/providers/'+ident,token=self.owner)[1]['data']['name'])
        self.call('PATCH','/api/v1/admin/campus/providers/'+ident,{'status':'UNLISTED'},self.admin)
        self.assertEqual(404,self.call('GET','/api/v1/services/providers/'+ident,token=self.owner)[0])
        self.assertEqual(422,self.provider(details={'script':'alert(1)'})[0])
        self.assertEqual(422,self.provider(isDemo=True,bookingEnabled=True)[0])

    def booking(self,provider,client='same-client'):
        return self.call('POST','/api/v1/services/bookings',{'providerId':provider,'clientId':client,'course':'测试课程','preferredDate':(server.business_today()+timedelta(days=1)).isoformat(),'name':'合成同学','phone':'00000000000','consented':True},self.owner)

    def test_booking_state_machine_ownership_and_idempotency(self):
        ident=self.provider(status='PUBLISHED',bookingEnabled=True)[1]['data']['id']
        status,data=self.booking(ident);self.assertEqual(201,status,data);bid=data['data']['id']
        self.assertEqual('PENDING',data['data']['status'])
        self.assertEqual(bid,self.booking(ident)[1]['data']['id'])
        path='/api/v1/services/bookings/'+bid;adminpath='/api/v1/admin/campus/bookings/'+bid
        self.assertEqual(403,self.call('PATCH',path,{'status':'CANCELLED'},self.other)[0])
        self.assertEqual(403,self.call('PATCH',path,{'status':'CONFIRMED'},self.owner)[0])
        self.assertEqual(200,self.call('PATCH',adminpath,{'status':'CONFIRMED','reply':'测试确认'},self.admin)[0])
        self.assertEqual(200,self.call('PATCH',path,{'status':'CANCELLED'},self.owner)[0])
        self.assertEqual(409,self.call('PATCH',adminpath,{'status':'COMPLETED'},self.admin)[0])
        self.assertEqual(0,self.call('GET','/api/v1/services/bookings',token=self.other)[1]['data']['total'])

    def test_unpublished_demo_and_closed_providers_do_not_accept_bookings(self):
        for changes in ({},{'status':'PUBLISHED','isDemo':True},{'status':'PUBLISHED','bookingEnabled':False}):
            ident=self.provider(**changes)[1]['data']['id']
            self.assertEqual(409,self.booking(ident,ident)[0])

    def test_guides_roundtrip_and_unsafe_urls(self):
        self.assertEqual(404,self.call('GET','/api/v1/services/guides/errand',token=self.owner)[0])
        data={'title':'跑腿指南','body':'测试指南内容','status':'PUBLISHED','url':'javascript:alert(1)'}
        self.assertEqual(422,self.call('PUT','/api/v1/admin/campus/guides/errand',data,self.admin)[0])
        data['url']=''
        self.assertEqual(200,self.call('PUT','/api/v1/admin/campus/guides/errand',data,self.admin)[0])
        self.assertEqual('测试指南内容',self.call('GET','/api/v1/services/guides/errand',token=self.owner)[1]['data']['body'])

    def test_account_deletion_removes_posts_photos_and_booking_contacts(self):
        output=io.BytesIO();Image.new('RGB',(5,5)).save(output,format='PNG')
        uri='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()
        post=self.post(images=[uri])[1]['data']
        provider=self.provider(status='PUBLISHED',bookingEnabled=True)[1]['data']['id']
        self.assertEqual(201,self.booking(provider)[0])
        image=server.UPLOADS/post['images'][0].rsplit('/',1)[1]
        self.assertTrue(image.exists())
        with server.connection() as db:
            user=db.execute('SELECT * FROM users WHERE id=?',(post['ownerId'],)).fetchone()
            server.delete_user_account_data(db,user,reason='合成注销测试')
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM campus_posts WHERE owner_id=?',(user['id'],)).fetchone()[0])
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM campus_bookings WHERE owner_id=?',(user['id'],)).fetchone()[0])
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM campus_providers WHERE id=?',(provider,)).fetchone()[0])
        self.assertFalse(image.exists())
        self.assertEqual(401,self.call('GET','/api/v1/community/secondhand',token=self.owner)[0])

    def test_catalogue_matches_current_app_and_activities_are_removed(self):
        config=server.validate_app_config(server.default_app_config())
        actions={r['actionId'] for r in config['navigation']}
        self.assertTrue({'secondhand','errand','logistics_repair','driving_school','ielts_toefl','civil_postgrad','gym','ncre','weekly_attendance','holiday_destination'}<=actions)
        self.assertNotIn('campus_activity',actions)
        self.assertEqual(410,self.call('POST','/api/v1/admin/campus-activities',{},self.admin)[0])
        android=Path(server.ROOT).parent/'my-chuannong-android/app/src/main/assets/app-config/default-v1.json'
        if android.exists():
            self.assertEqual(actions,{r['actionId'] for r in json.loads(android.read_text(encoding='utf-8'))['config']['navigation']})


if __name__=='__main__':unittest.main()
