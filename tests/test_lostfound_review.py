"""Both lostfound_review types exercise the actual HTTP handlers and durable SQLite state."""
import json
import base64
import io
import sqlite3
import urllib.parse
from pathlib import Path
from PIL import Image
import server
import lostfound_review
import test_campus_hub as fixtures
import unittest


class LostFoundReviewTest(unittest.TestCase):
    setUp=fixtures.CampusHubTest.setUp
    tearDown=fixtures.CampusHubTest.tearDown
    identity=fixtures.CampusHubTest.identity
    call=fixtures.CampusHubTest.call
    def post(self, **changes):
        return fixtures.CampusHubTest.post(self,'lost-found',**changes)
    def review(self,ident,action='approve',reason=''):
        return self.call('PATCH','/api/v1/admin/campus/lost-found/'+ident,{'action':action,'reason':reason},self.admin)

    def test_both_types_pending_private_then_approved_public(self):
        for kind in ('lost','found'):
            with self.subTest(kind=kind):
                item=self.post(kind=kind)[1]['data'];self.assertEqual('PENDING',item['status'])
                path='/api/v1/community/lost-found/'+item['id']
                self.assertEqual(404,self.call('GET',path,token=self.other)[0])
                self.assertEqual('PENDING',self.call('GET',path,token=self.owner)[1]['data']['status'])
                rows=self.call('GET','/api/v1/community/lost-found?kind='+kind,token=self.other)[1]['data']['items'];self.assertEqual([],rows)
                self.assertEqual(200,self.review(item['id'])[0])
                self.assertEqual('PUBLISHED',self.call('GET',path,token=self.other)[1]['data']['status'])
                self.assertEqual(409,self.review(item['id'])[0])

    def test_required_reasons_notices_and_state_machine(self):
        for kind in ('lost','found'):
            for action in ('reject','take_down'):
                item=self.post(kind=kind)[1]['data'];ident=item['id']
                if action=='take_down':self.review(ident)
                self.assertEqual(422,self.review(ident,action,'  ')[0])
                self.assertEqual(200,self.review(ident,action,'描述与图片不符，请修改后重新提交')[0])
                owner=self.call('GET','/api/v1/community/lost-found/'+ident,token=self.owner)[1]['data']
                self.assertEqual('VIOLATION',owner['status']);self.assertIn('图片不符',owner['moderationNote'])
                self.assertEqual(404,self.call('GET','/api/v1/community/lost-found/'+ident,token=self.other)[0])
                notices=self.call('GET','/api/v1/community/lost-found/notifications',token=self.owner)[1]['data']['items']
                note=next(n for n in notices if n['post_id']==ident and n['action']==action)
                self.assertEqual(item['title'],note['title']);self.assertEqual(kind,note['post_type']);self.assertEqual(owner['moderationNote'],note['reason'])
                self.assertEqual(0,self.call('GET','/api/v1/community/lost-found/notifications',token=self.other)[1]['data']['total'])
                self.assertEqual(200,self.call('PATCH','/api/v1/community/lost-found/notifications',{'ids':[note['id']]},self.owner)[0])
                with server.connection() as db:self.assertIsNotNone(db.execute('SELECT read_at FROM lostfound_review_notifications WHERE id=?',(note['id'],)).fetchone()[0])

    def test_delete_all_states_removes_every_surface_and_prevents_retry_resurrection(self):
        for kind in ('lost','found'):
            for status in ('PENDING','PUBLISHED','VIOLATION'):
                client=kind+status;item=self.post(kind=kind,clientId=client)[1]['data'];ident=item['id']
                if status=='PUBLISHED':self.review(ident)
                if status=='VIOLATION':self.review(ident,'reject','测试原因')
                self.assertEqual(200,self.call('DELETE','/api/v1/admin/campus/lost-found/'+ident,token=self.admin)[0])
                for token,path in ((self.admin,'/api/v1/admin/campus/lost-found'),(self.owner,'/api/v1/community/lost-found?mine=true'),(self.other,'/api/v1/community/lost-found')):
                    self.assertNotIn(ident,[r['id'] for r in self.call('GET',path,token=token)[1]['data']['items']])
                self.assertEqual(404,self.call('GET','/api/v1/admin/campus/lost-found/'+ident,token=self.admin)[0])
                self.assertEqual(409,self.post(kind=kind,clientId=client)[0])
                self.assertEqual(404,self.call('PATCH','/api/v1/community/lost-found/'+ident,{'restore':True},self.owner)[0])
                server.initialize_database()
                with server.connection() as db:
                    self.assertEqual(0,db.execute('SELECT COUNT(*) FROM campus_posts WHERE id=?',(ident,)).fetchone()[0])
                    self.assertEqual(0,db.execute('SELECT COUNT(*) FROM lostfound_review_notifications WHERE post_id=?',(ident,)).fetchone()[0])

    def test_combined_keyword_campus_status_and_type_filters(self):
        target=self.post(kind='lost',title='审核筛选测试',description='正文特征组合',campus='雅安校区')[1]['data'];self.review(target['id'],'reject','资料不足')
        self.post(kind='found',description='正文特征组合',campus='雅安校区')
        self.post(kind='lost',description='正文特征组合',campus='成都校区')
        q=urllib.parse.urlencode({'q':'正文特征组合','campus':'雅安校区','status':'VIOLATION','kind':'lost'})
        data=self.call('GET','/api/v1/admin/campus/lost-found?'+q,token=self.admin)[1]['data']
        self.assertEqual(1,data['total']);self.assertEqual(target['id'],data['items'][0]['id'])
        self.assertEqual(422,self.call('GET','/api/v1/admin/campus/lost-found?status=DELETED',token=self.admin)[0])

    def test_edit_requires_new_review_and_stale_admin_decisions_fail(self):
        for kind in ('lost','found'):
            item=self.post(kind=kind)[1]['data'];ident=item['id'];path='/api/v1/community/lost-found/'+ident
            self.review(ident)
            item['title']='编辑后的标题';updated=self.call('PATCH',path,item,self.owner)[1]['data']
            self.assertEqual('PENDING',updated['status']);self.assertEqual(404,self.call('GET',path,token=self.other)[0])
            self.assertEqual(409,self.call('PATCH','/api/v1/admin/campus/lost-found/'+ident,{'action':'approve','expectedUpdatedAt':item['updatedAt']},self.admin)[0])
            self.assertEqual(200,self.review(ident)[0])

    def test_detail_preserves_real_lost_found_fields_and_long_image(self):
        output=io.BytesIO();Image.new('RGB',(180,1800),'green').save(output,format='PNG')
        photo='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()
        for kind in ('lost','found'):
            item=self.post(kind=kind,description='完整描述\n'*45,image=photo,location='图书馆二楼',date='2026-10-18',contact='synthetic_contact')[1]['data']
            for state in ('PENDING','PUBLISHED','VIOLATION'):
                if state=='PUBLISHED':self.review(item['id'])
                if state=='VIOLATION':self.review(item['id'],'take_down','补充物品特征')
                detail=self.call('GET','/api/v1/admin/campus/lost-found/'+item['id'],token=self.admin)[1]['data']
                self.assertEqual(state,detail['status']);self.assertEqual('图书馆二楼',detail['location'])
                self.assertEqual('2026-10-18',detail['date']);self.assertEqual('synthetic_contact',detail['contact'])
                self.assertEqual(item['description'],detail['description'])
                with Image.open(server.UPLOADS/detail['image'].rsplit('/',1)[1]) as image:self.assertEqual(10,image.height/image.width)

    def test_completion_is_separate_and_cannot_bypass_review(self):
        for kind in ('lost','found'):
            item=self.post(kind=kind,done=True)[1]['data'];path='/api/v1/community/lost-found/'+item['id']
            self.assertFalse(item['done']);self.assertEqual(409,self.call('PATCH',path,{'done':False},self.owner)[0])
            self.review(item['id'])
            done=self.call('PATCH',path,{'done':True},self.owner)[1]['data']
            self.assertTrue(done['done']);self.assertEqual('PUBLISHED',done['status'])
            self.assertNotIn(item['id'],[r['id'] for r in self.call('GET','/api/v1/community/lost-found',token=self.other)[1]['data']['items']])
            self.assertEqual(200,self.call('PATCH',path,{'done':False},self.owner)[0])
            self.review(item['id'],'take_down','核验物品信息')
            self.assertEqual(409,self.call('PATCH',path,{'done':False},self.owner)[0])
            self.assertEqual(409,self.call('PATCH',path,{'restore':True},self.owner)[0])
            item['description']='已补充物品特征'
            edited=self.call('PATCH',path,item,self.owner)[1]['data']
            self.assertEqual('PENDING',edited['status']);self.assertEqual('',edited['moderationNote'])

    def test_migration_preserves_completion_and_marketplace_and_is_idempotent(self):
        path=Path(self.temp.name)/'legacy.db';db=sqlite3.connect(path,isolation_level=None);db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE users(id TEXT PRIMARY KEY)');db.execute("INSERT INTO users VALUES('owner')")
        db.executescript((Path(server.ROOT)/'campus_schema.sql').read_text(encoding='utf-8'))
        for ident,module,state in [('active','lost-found','PUBLISHED'),('done','lost-found','COMPLETED'),('hidden','lost-found','HIDDEN'),('deleted','lost-found','DELETED'),('market','secondhand','PUBLISHED')]:
            db.execute('INSERT INTO campus_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(ident,module,'owner',ident,ident,'雅安校区','{}','cipher',state,'旧原因',1,1))
        lostfound_review.initialize(db);lostfound_review.initialize(db)
        rows={r['id']:r['status'] for r in db.execute('SELECT * FROM campus_posts')}
        self.assertEqual({'active':'PENDING','done':'PENDING','hidden':'VIOLATION','market':'PUBLISHED'},rows)
        self.assertTrue(json.loads(db.execute("SELECT payload_json FROM campus_posts WHERE id='done'").fetchone()[0])['done'])
        self.assertEqual(1,db.execute('SELECT COUNT(*) FROM lostfound_review_deleted_posts').fetchone()[0]);db.close()

if __name__=='__main__':unittest.main()
