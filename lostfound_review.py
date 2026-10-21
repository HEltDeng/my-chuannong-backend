"""Review workflow for lost and found posts; completion is separate from review."""
import json
import re
import uuid
from core import now_ms, parse_pagination, paginated_response
from campus_hub import fail, text, choice, post_payload, post_view, dump, encrypted, CAMPUSES

STATES = ('PENDING', 'PUBLISHED', 'VIOLATION')


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS campus_module_migrations(name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS lostfound_review_deleted_posts(
        owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        client_id TEXT NOT NULL, post_id TEXT NOT NULL UNIQUE, deleted_at INTEGER NOT NULL,
        PRIMARY KEY(owner_id,client_id));
      CREATE TABLE IF NOT EXISTS lostfound_review_notifications(
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        post_id TEXT NOT NULL, title TEXT NOT NULL, post_type TEXT NOT NULL,
        action TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, read_at INTEGER);
      CREATE INDEX IF NOT EXISTS lostfound_review_notification_owner ON lostfound_review_notifications(owner_id,created_at DESC);
    ''')
    if db.execute("SELECT 1 FROM campus_module_migrations WHERE name='lostfound-review-v1'").fetchone():
        return
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute("INSERT OR IGNORE INTO lostfound_review_deleted_posts SELECT owner_id,client_id,id,? FROM campus_posts WHERE module='lost-found' AND status='DELETED'", (now_ms(),))
        db.execute("DELETE FROM campus_posts WHERE module='lost-found' AND status='DELETED'")
        db.execute("UPDATE campus_posts SET payload_json=json_set(payload_json,'$.done',json(CASE WHEN status='COMPLETED' THEN 'true' ELSE 'false' END)) WHERE module='lost-found'")
        db.execute("UPDATE campus_posts SET status=CASE WHEN status='HIDDEN' THEN 'VIOLATION' ELSE 'PENDING' END, updated_at=? WHERE module='lost-found'", (now_ms(),))
        db.execute("INSERT INTO campus_module_migrations VALUES('lostfound-review-v1',?)", (now_ms(),))
        db.commit()
    except Exception:
        db.rollback()
        raise


def notices(handler, method, db, user):
    if method == 'GET':
        limit,offset=parse_pagination(handler.market_query,default_limit=100)
        total=db.execute('SELECT COUNT(*) FROM lostfound_review_notifications WHERE owner_id=?',(user['id'],)).fetchone()[0]
        rows=db.execute('SELECT * FROM lostfound_review_notifications WHERE owner_id=? ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (user['id'],limit,offset)).fetchall()
        return paginated_response([dict(r) for r in rows],total,limit,offset),200
    if method == 'PATCH':
        data=handler.body_json(); ids=data.get('ids')
        if not isinstance(ids,list) or not 1 <= len(ids) <= 100 or any(not isinstance(v,str) or len(v)>100 for v in ids):
            fail('请选择需要标记已读的通知')
        db.execute('UPDATE lostfound_review_notifications SET read_at=COALESCE(read_at,?) WHERE owner_id=? AND id IN ('+','.join('?' for _ in ids)+')',(now_ms(),user['id'],*ids))
        return {'read':True},200
    fail('不支持的通知操作',405,'METHOD_NOT_ALLOWED')


def notify(db,row,action,status,reason):
    payload=json.loads(row['payload_json'])
    db.execute('INSERT INTO lostfound_review_notifications(id,owner_id,post_id,title,post_type,action,status,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
               (str(uuid.uuid4()),row['owner_id'],row['id'],row['title'],payload['kind'],action,status,reason,now_ms()))


def handle(handler,method,ident,query,db,user,s,admin):
    if ident == 'notifications' and not admin:
        handler.market_query=query
        return notices(handler,method,db,user)
    if method == 'GET' and not ident:
        clauses=["module='lost-found'"]; args=[]
        mine=not admin and query.get('mine')=='true'
        if mine:
            clauses.append('owner_id=?');args.append(user['id'])
        elif not admin:
            clauses.append("status='PUBLISHED' AND COALESCE(json_extract(payload_json,'$.done'),0)=0")
        if query.get('status'):
            clauses.append('status=?');args.append(choice(query['status'],STATES,'审核状态'))
        if query.get('campus'):
            clauses.append('campus=?');args.append(choice(query['campus'],CAMPUSES,'校区'))
        if query.get('kind'):
            clauses.append("json_extract(payload_json,'$.kind')=?");args.append(choice(query['kind'],('lost','found'),'分类'))
        if query.get('q'):
            clauses.append("(instr(lower(title),lower(?))>0 OR instr(lower(json_extract(payload_json,'$.description')),lower(?))>0 OR instr(lower(json_extract(payload_json,'$.location')),lower(?))>0)")
            args.extend([query['q'].strip()[:100]]*3)
        limit,offset=parse_pagination(query,default_limit=100);where=' AND '.join(clauses)
        total=db.execute('SELECT COUNT(*) FROM campus_posts WHERE '+where,args).fetchone()[0]
        rows=db.execute('SELECT * FROM campus_posts WHERE '+where+' ORDER BY created_at DESC,id LIMIT ? OFFSET ?',[*args,limit,offset]).fetchall()
        return paginated_response([post_view(r,user,s,admin=admin) for r in rows],total,limit,offset),200

    if method != 'GET':
        if not admin:s.require_account_operation(user)
        db.execute('BEGIN IMMEDIATE')
    row=db.execute("SELECT * FROM campus_posts WHERE module='lost-found' AND id=?",(ident,)).fetchone() if ident else None
    if ident and not row:fail('信息已删除或不存在',404,'NOT_FOUND')
    if row and not admin and row['owner_id']!=user['id']:
        if method != 'GET':fail('只能管理自己的发布',403,'FORBIDDEN')
        if row['status']!='PUBLISHED':fail('信息未发布或已下架',404,'NOT_FOUND')
    if method=='GET' and row:return post_view(row,user,s,detail=True,admin=admin),200
    if method=='DELETE' and row:
        db.execute('INSERT OR IGNORE INTO lostfound_review_deleted_posts VALUES(?,?,?,?)',(row['owner_id'],row['client_id'],row['id'],now_ms()))
        db.execute('DELETE FROM lostfound_review_notifications WHERE post_id=?',(ident,))
        db.execute('DELETE FROM campus_posts WHERE id=?',(ident,))
        if admin:s.write_audit(db,user['id'],'LOST_FOUND_DELETE','lost-found',ident,{'previousStatus':row['status']})
        db.commit()
        for image in [json.loads(row['payload_json']).get('image','')]:
            if re.fullmatch(r'/uploads/[0-9a-f-]+\.(png|jpg|webp)',image):s.run_safely_delete_upload(image.rsplit('/',1)[1])
        return {'id':ident,'deleted':True},200
    if admin:
        if method!='PATCH' or not row:fail('请选择审核或删除操作',405,'METHOD_NOT_ALLOWED')
        data=handler.body_json();action=choice(data.get('action'),('approve','reject','take_down'),'管理操作')
        if 'expectedUpdatedAt' in data and data['expectedUpdatedAt']!=row['updated_at']:
            fail('信息已更新，请刷新详情后重新处理',409,'STALE_RECORD')
        required='PUBLISHED' if action=='take_down' else 'PENDING'
        if row['status']!=required:fail('状态已改变，请刷新后操作',409,'INVALID_TRANSITION')
        reason=text(data,'reason',500,action!='approve')
        status='PUBLISHED' if action=='approve' else 'VIOLATION'
        db.execute('UPDATE campus_posts SET status=?,moderation_note=?,updated_at=? WHERE id=?',(status,reason,max(now_ms(),row['updated_at']+1),ident))
        notify(db,row,action,status,reason)
        s.write_audit(db,user['id'],'LOST_FOUND_REVIEW','lost-found',ident,{'action':action,'status':status,'reason':reason})
        db.commit()
        return {'id':ident,'status':status,'moderationNote':reason},200
    if method=='POST' and not ident:
        data=handler.body_json();client=text(data,'clientId',100,True)
        if db.execute('SELECT 1 FROM lostfound_review_deleted_posts WHERE owner_id=? AND client_id=?',(user['id'],client)).fetchone():
            fail('这条提交已被删除，请重新创建信息',409,'POST_DELETED')
        existing=db.execute("SELECT * FROM campus_posts WHERE owner_id=? AND module='lost-found' AND client_id=?",(user['id'],client)).fetchone()
        if existing:
            db.commit();return post_view(existing,user,s,detail=True),200
        value,contact=post_payload(handler,data,'lost-found');value['ownerName']=user['nickname'];value['done']=False
        ident=str(uuid.uuid4());timestamp=now_ms()
        db.execute("INSERT INTO campus_posts(id,module,owner_id,client_id,title,campus,payload_json,contact_encrypted,status,created_at,updated_at) VALUES(?,'lost-found',?,?,?,?,?,?,'PENDING',?,?)",(ident,user['id'],client,value['title'],value['campus'],dump(value),encrypted(s,contact),timestamp,timestamp))
        db.commit()
        return post_view(db.execute('SELECT * FROM campus_posts WHERE id=?',(ident,)).fetchone(),user,s,detail=True),201
    if method=='PATCH' and row:
        data=handler.body_json()
        old=json.loads(row['payload_json'])
        if set(data)=={'restore'}:fail('已删除记录不能恢复',409,'POST_DELETED')
        if set(data)=={'done'}:
            if row['status']!='PUBLISHED':fail('仅审核通过的信息可标记完成或重新开放',409,'REVIEW_REQUIRED')
            if not isinstance(data['done'],bool):fail('完成状态无效')
            old['done']=data['done']
            db.execute('UPDATE campus_posts SET payload_json=?,updated_at=? WHERE id=?',(dump(old),max(now_ms(),row['updated_at']+1),ident))
            db.commit()
            return post_view(db.execute('SELECT * FROM campus_posts WHERE id=?',(ident,)).fetchone(),user,s,detail=True),200
        value,contact=post_payload(handler,data,'lost-found',row)
        value['ownerName']=user['nickname'];value['done']=bool(old.get('done',False))
        db.execute("UPDATE campus_posts SET title=?,campus=?,payload_json=?,contact_encrypted=?,status='PENDING',moderation_note='',updated_at=? WHERE id=?",(value['title'],value['campus'],dump(value),encrypted(s,contact),max(now_ms(),row['updated_at']+1),ident))
        db.commit()
        return post_view(db.execute('SELECT * FROM campus_posts WHERE id=?',(ident,)).fetchone(),user,s,detail=True),200
    fail('不支持的操作',405,'METHOD_NOT_ALLOWED')
