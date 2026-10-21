"""Both marketplace types exercise the actual HTTP handlers and durable SQLite state."""
import json
import base64
import io
import sqlite3
import urllib.parse
from pathlib import Path
from PIL import Image
import server
import marketplace
import test_campus_hub as fixtures
import unittest


class MarketplaceReviewTest(unittest.TestCase):
    setUp=fixtures.CampusHubTest.setUp
    tearDown=fixtures.CampusHubTest.tearDown
    identity=fixtures.CampusHubTest.identity
    call=fixtures.CampusHubTest.call
    post=fixtures.CampusHubTest.post
    review=fixtures.CampusHubTest.review

    def test_both_types_pending_private_then_approved_public(self):
        for kind in ('sell','want'):
            with self.subTest(kind=kind):
                item=self.post(type=kind)[1]['data'];self.assertEqual('PENDING',item['status'])
                path='/api/v1/community/secondhand/'+item['id']
                self.assertEqual(404,self.call('GET',path,token=self.other)[0])
                self.assertEqual('PENDING',self.call('GET',path,token=self.owner)[1]['data']['status'])
                rows=self.call('GET','/api/v1/community/secondhand?type='+kind,token=self.other)[1]['data']['items'];self.assertEqual([],rows)
                self.assertEqual(200,self.review(item['id'])[0])
                self.assertEqual('PUBLISHED',self.call('GET',path,token=self.other)[1]['data']['status'])
                self.assertEqual(409,self.review(item['id'])[0])

    def test_required_reasons_notices_and_state_machine(self):
        for kind in ('sell','want'):
            for action in ('reject','take_down'):
                item=self.post(type=kind)[1]['data'];ident=item['id']
                if action=='take_down':self.review(ident)
                self.assertEqual(422,self.review(ident,action,'  ')[0])
                self.assertEqual(200,self.review(ident,action,'描述与图片不符，请修改后重新提交')[0])
                owner=self.call('GET','/api/v1/community/secondhand/'+ident,token=self.owner)[1]['data']
                self.assertEqual('VIOLATION',owner['status']);self.assertIn('图片不符',owner['moderationNote'])
                self.assertEqual(404,self.call('GET','/api/v1/community/secondhand/'+ident,token=self.other)[0])
                notices=self.call('GET','/api/v1/community/secondhand/notifications',token=self.owner)[1]['data']['items']
                note=next(n for n in notices if n['post_id']==ident and n['action']==action)
                self.assertEqual(item['title'],note['title']);self.assertEqual(kind,note['post_type']);self.assertEqual(owner['moderationNote'],note['reason'])
                self.assertEqual(0,self.call('GET','/api/v1/community/secondhand/notifications',token=self.other)[1]['data']['total'])
                self.assertEqual(200,self.call('PATCH','/api/v1/community/secondhand/notifications',{'ids':[note['id']]},self.owner)[0])
                with server.connection() as db:self.assertIsNotNone(db.execute('SELECT read_at FROM marketplace_notifications WHERE id=?',(note['id'],)).fetchone()[0])

    def test_delete_all_states_removes_every_surface_and_prevents_retry_resurrection(self):
        for kind in ('sell','want'):
            for status in ('PENDING','PUBLISHED','VIOLATION'):
                client=kind+status;item=self.post(type=kind,clientId=client)[1]['data'];ident=item['id']
                if status=='PUBLISHED':self.review(ident)
                if status=='VIOLATION':self.review(ident,'reject','测试原因')
                self.assertEqual(200,self.call('DELETE','/api/v1/admin/campus/secondhand/'+ident,token=self.admin)[0])
                for token,path in ((self.admin,'/api/v1/admin/campus/secondhand'),(self.owner,'/api/v1/community/secondhand?mine=true'),(self.other,'/api/v1/community/secondhand')):
                    self.assertNotIn(ident,[r['id'] for r in self.call('GET',path,token=token)[1]['data']['items']])
                self.assertEqual(404,self.call('GET','/api/v1/admin/campus/secondhand/'+ident,token=self.admin)[0])
                self.assertEqual(409,self.post(type=kind,clientId=client)[0])
                self.assertEqual(404,self.call('PATCH','/api/v1/community/secondhand/'+ident,{'restore':True},self.owner)[0])
                server.initialize_database()
                with server.connection() as db:
                    self.assertEqual(0,db.execute('SELECT COUNT(*) FROM campus_posts WHERE id=?',(ident,)).fetchone()[0])
                    self.assertEqual(0,db.execute('SELECT COUNT(*) FROM marketplace_notifications WHERE post_id=?',(ident,)).fetchone()[0])

    def test_combined_keyword_campus_status_and_type_filters(self):
        target=self.post(type='want',title='审核筛选测试',description='正文特征组合',campus='雅安校区')[1]['data'];self.review(target['id'],'reject','资料不足')
        self.post(type='sell',description='正文特征组合',campus='雅安校区')
        self.post(type='want',description='正文特征组合',campus='成都校区')
        q=urllib.parse.urlencode({'q':'正文特征组合','campus':'雅安校区','status':'VIOLATION','type':'want'})
        data=self.call('GET','/api/v1/admin/campus/secondhand?'+q,token=self.admin)[1]['data']
        self.assertEqual(1,data['total']);self.assertEqual(target['id'],data['items'][0]['id'])
        self.assertEqual(422,self.call('GET','/api/v1/admin/campus/secondhand?status=DELETED',token=self.admin)[0])

    def test_edit_requires_new_review_and_stale_admin_decisions_fail(self):
        for kind in ('sell','want'):
            item=self.post(type=kind)[1]['data'];ident=item['id'];path='/api/v1/community/secondhand/'+ident
            self.review(ident)
            item['title']='编辑后的标题';updated=self.call('PATCH',path,item,self.owner)[1]['data']
            self.assertEqual('PENDING',updated['status']);self.assertEqual(404,self.call('GET',path,token=self.other)[0])
            self.assertEqual(409,self.call('PATCH','/api/v1/admin/campus/secondhand/'+ident,{'action':'approve','expectedUpdatedAt':item['updatedAt']},self.admin)[0])
            self.assertEqual(200,self.review(ident)[0])

    def test_full_details_preserve_all_images_long_text_and_contacts(self):
        pictures=[]
        for dimensions in ((180,1800),(1200,100),(400,400)):
            output=io.BytesIO();Image.new('RGB',dimensions,'green').save(output,format='PNG');pictures.append('data:image/png;base64,'+base64.b64encode(output.getvalue()).decode())
        for kind in ('sell','want'):
            item=self.post(type=kind,description='完整描述\n'*180,images=pictures,phone='00000000000')[1]['data'];ident=item['id']
            for state in ('PENDING','PUBLISHED','VIOLATION'):
                if state=='PUBLISHED':self.review(ident)
                if state=='VIOLATION':self.review(ident,'take_down','图片核验测试')
                detail=self.call('GET','/api/v1/admin/campus/secondhand/'+ident,token=self.admin)[1]['data']
                self.assertEqual(state,detail['status']);self.assertEqual(3,len(detail['images']));self.assertEqual('完整描述\n'*179+'完整描述',detail['description']);self.assertEqual('00000000000',detail['phone'])
                for image in detail['images']:self.assertTrue((server.UPLOADS/image.rsplit('/',1)[1]).exists())

    def test_legacy_schema_migration_is_atomic_idempotent_and_keeps_lost_found(self):
        path=Path(self.temp.name)/'legacy.db';db=sqlite3.connect(path,isolation_level=None);db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE users(id TEXT PRIMARY KEY)');db.execute("INSERT INTO users VALUES('owner')")
        sql=(Path(server.ROOT)/'campus_schema.sql').read_text(encoding='utf-8').replace("'PENDING','PUBLISHED','VIOLATION','COMPLETED','HIDDEN','DELETED'","'PUBLISHED','COMPLETED','HIDDEN','DELETED'")
        db.executescript(sql)
        for ident,module,state in [('active','secondhand','PUBLISHED'),('hidden','secondhand','HIDDEN'),('deleted','secondhand','DELETED'),('lost','lost-found','COMPLETED')]:
            db.execute('INSERT INTO campus_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(ident,module,'owner',ident,ident,'雅安校区','{}','cipher',state,'旧原因',1,1))
        marketplace.initialize(db);marketplace.initialize(db)
        rows={r['id']:r['status'] for r in db.execute('SELECT * FROM campus_posts')}
        self.assertEqual({'active':'PENDING','hidden':'VIOLATION','lost':'COMPLETED'},rows)
        self.assertEqual(1,db.execute('SELECT COUNT(*) FROM marketplace_deleted_posts').fetchone()[0]);db.close()

if __name__=='__main__':unittest.main()
