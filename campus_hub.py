"""MyChuannong community and off-campus operations; no school credentials/data."""
from __future__ import annotations

import json
import re
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from core import ApiError, now_ms, parse_pagination, paginated_response
from module_access import PROVIDER_ROLES, GUIDE_ROLES

CAMPUSES = ('成都校区', '雅安校区', '都江堰校区')
CATEGORIES = {'driving_school': '驾校', 'ielts_toefl': '雅思托福', 'civil_postgrad': '考公考研', 'gym': '健身房'}
GUIDES = {'errand': '跑腿代取', 'logistics_repair': '后勤报修', 'electric': '电费查询'}
POST_MODULES = {'secondhand', 'lost-found'}
REMOVED = {'campus_activities', 'campus_activity', 'sports_reservation', 'campus_channel', 'buddy_club'}


def fail(message, status=422, code='VALIDATION_ERROR'):
    raise ApiError(status, code, message)


def text(data, key, maximum, required=False):
    value = data.get(key, '')
    if not isinstance(value, str):
        fail(f'{key} 必须为文本')
    value = value.strip()
    if (required and not value) or len(value) > maximum:
        fail(f'请检查 {key}，最多 {maximum} 字')
    return value


def choice(value, options, label):
    if value not in options:
        fail(f'{label} 无效')
    return value


def boolean(data, key, default=False):
    value = data.get(key, default)
    if not isinstance(value, bool):
        fail(f'{key} 必须为布尔值')
    return value


def dump(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def initialize(db):
    db.executescript(Path(__file__).with_name('campus_schema.sql').read_text(encoding='utf-8'))
    from marketplace import initialize as initialize_marketplace
    initialize_marketplace(db)
    from lostfound_review import initialize as initialize_lostfound
    initialize_lostfound(db)
    for key, title in GUIDES.items():
        db.execute('INSERT OR IGNORE INTO campus_guides(id,title,updated_at) VALUES(?,?,?)', (key, title, now_ms()))


def authorize_admin(handler, db, module, s):
    user = handler.current_user(db, 'ADMIN')
    role = s.admin_role(user)
    if role == 'SUPER_ADMIN':
        return user
    if role == 'CONTENT_OPERATOR' and module in {'summary', 'providers', 'bookings', 'guides', 'secondhand'}:
        return user
    if role == 'LOST_FOUND_OPERATOR' and module == 'lost-found':
        return user
    if role == 'SECONDHAND_OPERATOR' and module == 'secondhand':
        return user
    if role in PROVIDER_ROLES and module in {'providers', 'bookings'}:
        return user
    if role in GUIDE_ROLES and module == 'guides':
        return user
    fail('当前管理员无权访问该业务模块', 403, 'ADMIN_MODULE_FORBIDDEN')


def require_scope(actual, allowed):
    if allowed and actual != allowed:
        fail('当前管理员无权访问该板块', 403, 'ADMIN_MODULE_FORBIDDEN')


def encrypted(s, data):
    return s.pii_cipher().encrypt(dump(data).encode()).decode()


def decrypted(s, value):
    return json.loads(s.pii_cipher().decrypt(value.encode()).decode())


def image_value(handler, value, previous=()):
    if not value:
        return ''
    if not isinstance(value, str):
        fail('图片格式无效')
    if value in previous and value.startswith('/uploads/'):
        return value
    match = re.fullmatch(r'data:image/(?:png|jpeg|webp);base64,([A-Za-z0-9+/=]+)', value)
    if not match:
        fail('请上传 JPEG、PNG 或 WebP 图片')
    return '/uploads/' + handler.save_image(match.group(1), str(uuid.uuid4()))


def post_view(row, user, s, detail=False, admin=False):
    payload = json.loads(row['payload_json'])
    mine = bool(user and user['id'] == row['owner_id'])
    payload.update(id=row['id'], ownerId=row['owner_id'], mine=mine,
                   done=bool(payload.get('done', row['status'] == 'COMPLETED')), status=row['status'],
                   createdAt=row['created_at'], updatedAt=row['updated_at'])
    if detail or mine:
        payload.update(decrypted(s, row['contact_encrypted']))
    else:
        payload.update(contact='', wechat='', phone='')
    if admin or mine:
        payload['moderationNote'] = row['moderation_note']
    return payload


def post_payload(handler, data, module, previous=None):
    old = json.loads(previous['payload_json']) if previous else {}
    value = {
        'title': text(data, 'title', 50 if module == 'secondhand' else 30, True),
        'description': text(data, 'description', 1000 if module == 'secondhand' else 300, module == 'secondhand'),
        'campus': choice(data.get('campus'), CAMPUSES, '校区'),
    }
    if module == 'secondhand':
        value['type'] = choice(data.get('type'), ('sell', 'want'), '信息类型')
        value['category'] = choice(data.get('category'), ('自行车', '书籍', '电动车', '高速材料', '四六级耳机', '宿舍用品', '数码产品', '其他'), '分类')
        contact = {'wechat': text(data, 'wechat', 64), 'phone': text(data, 'phone', 32)}
        if not any(contact.values()):
            fail('微信号或联系电话至少填写一项')
        images = data.get('images', [])
        if not isinstance(images, list) or len(images) > 4:
            fail('最多上传 4 张图片')
        value['images'] = [image_value(handler, x, old.get('images', [])) for x in images]
    else:
        value['kind'] = choice(data.get('kind'), ('lost', 'found'), '信息类型')
        value['location'] = text(data, 'location', 40, True)
        value['date'] = text(data, 'date', 10, True)
        try:
            if date.fromisoformat(value['date']).isoformat() != value['date']:
                fail('日期格式无效')
        except ValueError:
            fail('日期格式无效')
        value['time'] = text(data, 'time', 30)
        contact = {'contact': text(data, 'contact', 60, True)}
        value['image'] = image_value(handler, data.get('image', ''), [old.get('image')])
    return value, contact


def posts(handler, method, module, ident, query, db, user, s, admin):
    if method == 'GET' and not ident:
        clauses, args = ['module=?'], [module]
        if admin:
            if query.get('status'):
                clauses.append('status=?'); args.append(choice(query['status'], ('PUBLISHED','COMPLETED','HIDDEN','DELETED'), '状态'))
        else:
            clauses.append("status IN ('PUBLISHED','COMPLETED','HIDDEN')" if query.get("mine") == "true" else "status IN ('PUBLISHED','COMPLETED')")
        if query.get('mine') == 'true':
            clauses.append('owner_id=?'); args.append(user['id'])
        if query.get('campus'):
            clauses.append('campus=?'); args.append(choice(query['campus'], CAMPUSES, '校区'))
        if query.get('q'):
            clauses.append('(title LIKE ? OR payload_json LIKE ?)'); args.extend(['%' + query['q'][:100] + '%'] * 2)
        where = ' AND '.join(clauses)
        limit, offset = parse_pagination(query, default_limit=100)
        total = db.execute('SELECT COUNT(*) FROM campus_posts WHERE ' + where, args).fetchone()[0]
        rows = db.execute('SELECT * FROM campus_posts WHERE ' + where + ' ORDER BY created_at DESC,id LIMIT ? OFFSET ?', [*args,limit,offset]).fetchall()
        return paginated_response([post_view(r,user,s,admin=admin) for r in rows], total, limit, offset), 200
    row = db.execute('SELECT * FROM campus_posts WHERE module=? AND id=?', (module,ident)).fetchone() if ident else None
    if ident and (not row or (not admin and (row['status'] in ('DELETED','HIDDEN') and row['owner_id'] != user['id']))):
        fail('信息不存在或已下架',404,'NOT_FOUND')
    if method == 'GET' and row:
        return post_view(row,user,s,detail=True,admin=admin),200
    if admin:
        if method not in ('PATCH','DELETE') or not row:
            fail('管理端仅处理已提交内容',405,'METHOD_NOT_ALLOWED')
        data = handler.body_json() if method == 'PATCH' else {}
        status = 'DELETED' if method == 'DELETE' else choice(data.get('status'), ('PUBLISHED','COMPLETED','HIDDEN'), '状态')
        reason = text(data,'reason',500,method == 'PATCH') or '管理员删除'
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE campus_posts SET status=?,moderation_note=?,updated_at=? WHERE id=?', (status,reason,now_ms(),ident))
        s.write_audit(db,user['id'],'CAMPUS_POST_MODERATE',module,ident,{'status':status,'reason':reason})
        db.commit()
        return {'id':ident,'status':status},200
    s.require_account_operation(user)
    if row and row['owner_id'] != user['id']:
        fail('只能管理自己的发布',403,'FORBIDDEN')
    if method == 'DELETE' and row:
        db.execute("UPDATE campus_posts SET status='DELETED',updated_at=? WHERE id=?", (now_ms(),ident))
        return {'id':ident,'deleted':True},200
    if method == 'PATCH' and row:
        data = handler.body_json()
        if set(data) == {'done'}:
            if row['status'] not in ('PUBLISHED','COMPLETED'):
                fail('该信息已由管理员处理',409,'MODERATED')
            done = boolean(data,'done')
            db.execute('UPDATE campus_posts SET status=?,updated_at=? WHERE id=?',('COMPLETED' if done else 'PUBLISHED',now_ms(),ident))
        elif set(data) == {'restore'} and boolean(data,'restore'):
            if row['moderation_note'] or row['status'] != 'DELETED':
                fail('该信息不能撤销删除',409,'MODERATED')
            db.execute("UPDATE campus_posts SET status='PUBLISHED',updated_at=? WHERE id=?",(now_ms(),ident))
        else:
            if row['status'] in ('HIDDEN','DELETED'):
                fail('已下架的信息不能通过编辑重新发布',409,'MODERATED')
            value, contact = post_payload(handler,data,module,row)
            value['ownerName'] = user['nickname']
            db.execute('UPDATE campus_posts SET title=?,campus=?,payload_json=?,contact_encrypted=?,updated_at=? WHERE id=?',
                       (value['title'],value['campus'],dump(value),encrypted(s,contact),now_ms(),ident))
        return post_view(db.execute('SELECT * FROM campus_posts WHERE id=?',(ident,)).fetchone(),user,s,True),200
    if method == 'POST' and not ident:
        data = handler.body_json()
        client_id = text(data,'clientId',100,True)
        existing = db.execute('SELECT * FROM campus_posts WHERE owner_id=? AND module=? AND client_id=?',(user['id'],module,client_id)).fetchone()
        if existing:
            return post_view(existing,user,s,True),200
        value, contact = post_payload(handler,data,module)
        value['ownerName'] = user['nickname']
        ident = str(uuid.uuid4()); timestamp = now_ms()
        db.execute('INSERT OR IGNORE INTO campus_posts(id,module,owner_id,client_id,title,campus,payload_json,contact_encrypted,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (ident,module,user['id'],client_id,value['title'],value['campus'],dump(value),encrypted(s,contact),timestamp,timestamp))
        row = db.execute('SELECT * FROM campus_posts WHERE owner_id=? AND module=? AND client_id=?',(user['id'],module,client_id)).fetchone()
        return post_view(row,user,s,True),201
    fail('不支持的操作',405,'METHOD_NOT_ALLOWED')


def provider_view(row):
    result = dict(row)
    result['details'] = json.loads(result.pop('details_json'))
    result['bookingEnabled'] = bool(result.pop('booking_enabled'))
    result['isDemo'] = bool(result.pop('is_demo'))
    result['sortOrder'] = result.pop('sort_order')
    return result


def providers(handler,method,ident,query,db,user,s,admin):
    scope = PROVIDER_ROLES.get(s.admin_role(user)) if admin else None
    if query.get('category'):
        require_scope(query['category'], scope)
    if method == 'GET':
        where, args = ['1=1'], []
        if scope:
            where.append('category=?'); args.append(scope)
        if not admin:
            where.append("status='PUBLISHED'")
        if ident:
            where.append('id=?'); args.append(ident)
        if query.get('category'):
            where.append('category=?'); args.append(choice(query['category'],CATEGORIES,'机构类别'))
        rows = db.execute('SELECT * FROM campus_providers WHERE '+' AND '.join(where)+' ORDER BY sort_order,created_at,id',args).fetchall()
        if ident:
            if not rows: fail('机构不存在或未发布',404,'NOT_FOUND')
            return provider_view(rows[0]),200
        return {'items':[provider_view(r) for r in rows]},200
    if not admin:
        fail('需要管理员权限',403,'FORBIDDEN')
    old = db.execute('SELECT * FROM campus_providers WHERE id=?',(ident,)).fetchone() if ident else None
    if ident and not old: fail('机构不存在',404,'NOT_FOUND')
    if old:
        require_scope(old['category'], scope)
    if method not in ('POST','PUT','PATCH'):
        fail('请使用下架保留预约记录',405,'METHOD_NOT_ALLOWED')
    data = handler.body_json()
    if old:
        data = {**provider_view(old),**data}
    category = choice(data.get('category'),CATEGORIES,'机构类别')
    require_scope(category, scope)
    status = choice(data.get('status','DRAFT'),('DRAFT','PUBLISHED','UNLISTED'),'状态')
    details = data.get('details',{})
    if not isinstance(details,dict) or len(dump(details).encode()) > 64*1024:
        fail('机构详情必须是小于 64KB 的对象')
    # Only a finite app template is editable; no HTML, scripts or endpoints.
    if set(details) - {'courses','facilities','hours','pickup','description','bookingSlots'}:
        fail('详情字段仅支持 courses、facilities、hours、pickup、description、bookingSlots')
    for key in ('courses','facilities','bookingSlots'):
        if key in details and (not isinstance(details[key],list) or len(details[key]) > 100):
            fail(f'{key} 必须是最多 100 项的列表')
    for key in ('hours','pickup','description'):
        if key in details and (not isinstance(details[key],str) or len(details[key]) > 4000):
            fail(f'{key} 格式或长度无效')
    demo = boolean(data,'isDemo')
    booking = boolean(data,'bookingEnabled')
    if demo and booking:
        fail('演示机构不能开启真实预约')
    sort = data.get('sortOrder',0)
    if type(sort) is not int or not -100000 <= sort <= 100000:
        fail('排序范围为 -100000 至 100000')
    image = image_value(handler,data.get('image',''),[old['image']] if old else [])
    ident = ident or str(uuid.uuid4()); timestamp=now_ms()
    values=(ident,category,text(data,'name',60,True),choice(data.get('campus'),CAMPUSES,'校区'),text(data,'summary',300),text(data,'address',200),text(data,'phone',32),image,dump(details),sort,status,int(demo),int(booking),timestamp,timestamp)
    db.execute('BEGIN IMMEDIATE')
    db.execute('''INSERT INTO campus_providers(id,category,name,campus,summary,address,phone,image,details_json,sort_order,status,is_demo,booking_enabled,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET category=excluded.category,name=excluded.name,campus=excluded.campus,summary=excluded.summary,address=excluded.address,phone=excluded.phone,image=excluded.image,details_json=excluded.details_json,sort_order=excluded.sort_order,status=excluded.status,is_demo=excluded.is_demo,booking_enabled=excluded.booking_enabled,updated_at=excluded.updated_at''',values)
    s.write_audit(db,user['id'],'PROVIDER_SAVE','PROVIDER',ident,{'status':status,'category':category})
    db.commit()
    return provider_view(db.execute('SELECT * FROM campus_providers WHERE id=?',(ident,)).fetchone()),200 if old else 201


def booking_view(row,user,s,admin):
    result=dict(row)
    contact=result.pop('contact_encrypted')
    result.update(decrypted(s,contact))
    return result


def bookings(handler,method,ident,query,db,user,s,admin):
    scope = PROVIDER_ROLES.get(s.admin_role(user)) if admin else None
    category = query.get('category')
    if category:
        choice(category, CATEGORIES, '机构类别')
        require_scope(category, scope)
    if method=='GET':
        where,args=['1=1'],[]
        if not admin: where.append('b.owner_id=?');args.append(user['id'])
        if scope or category:
            where.append('p.category=?');args.append(scope or category)
        if ident:
            where.append('b.id=?');args.append(ident)
        if query.get('status'): where.append('b.status=?');args.append(choice(query['status'],('PENDING','CONFIRMED','COMPLETED','CANCELLED','REJECTED'),'状态'))
        limit,offset=parse_pagination(query)
        condition=' AND '.join(where)
        total=db.execute('SELECT COUNT(*) FROM campus_bookings b JOIN campus_providers p ON p.id=b.provider_id WHERE '+condition,args).fetchone()[0]
        rows=db.execute('SELECT b.*,p.name AS provider_name,p.category FROM campus_bookings b JOIN campus_providers p ON p.id=b.provider_id WHERE '+condition+' ORDER BY b.created_at DESC LIMIT ? OFFSET ?',[*args,limit,offset]).fetchall()
        return paginated_response([booking_view(r,user,s,admin) for r in rows],total,limit,offset),200
    if method=='POST' and not ident and not admin:
        s.require_account_operation(user)
        data=handler.body_json()
        if not boolean(data,'consented'): fail('请同意将联系方式用于本次预约')
        client=text(data,'clientId',100,True)
        existing=db.execute('SELECT * FROM campus_bookings WHERE owner_id=? AND client_id=?',(user['id'],client)).fetchone()
        if existing: return booking_view(existing,user,s,admin),200
        provider=text(data,'providerId',100,True)
        course=text(data,'course',100,True)
        day=text(data,'preferredDate',10,True)
        try:
            if date.fromisoformat(day)<s.business_today(): fail('预约日期不能早于今天')
        except ValueError: fail('预约日期无效')
        contact={'name':text(data,'name',40,True),'phone':text(data,'phone',32,True)}
        note=text(data,'note',500)
        db.execute('BEGIN IMMEDIATE')
        merchant=db.execute("SELECT * FROM campus_providers WHERE id=? AND status='PUBLISHED' AND booking_enabled=1 AND is_demo=0",(provider,)).fetchone()
        if not merchant: fail('此机构尚未开放预约',409,'BOOKING_UNAVAILABLE')
        ident=str(uuid.uuid4()); timestamp=now_ms()
        db.execute('INSERT OR IGNORE INTO campus_bookings(id,owner_id,provider_id,client_id,contact_encrypted,course,preferred_date,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (ident,user['id'],provider,client,encrypted(s,contact),course,day,note,timestamp,timestamp))
        db.commit()
        row=db.execute('SELECT * FROM campus_bookings WHERE owner_id=? AND client_id=?',(user['id'],client)).fetchone()
        return booking_view(row,user,s,False),201
    if method=='PATCH' and ident:
        data=handler.body_json()
        status=choice(data.get('status'),('CONFIRMED','COMPLETED','CANCELLED','REJECTED'),'状态')
        reply=text(data,'reply',500)
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM campus_bookings WHERE id=?',(ident,)).fetchone()
        if not row: fail('预约不存在',404,'NOT_FOUND')
        if scope:
            provider = db.execute('SELECT category FROM campus_providers WHERE id=?', (row['provider_id'],)).fetchone()
            require_scope(provider['category'], scope)
        if not admin and (row['owner_id']!=user['id'] or status!='CANCELLED'):
            fail('只能取消自己的预约',403,'FORBIDDEN')
        transitions={'PENDING':{'CONFIRMED','CANCELLED','REJECTED'},'CONFIRMED':{'COMPLETED','CANCELLED'}}
        if status not in transitions.get(row['status'],set()): fail('当前状态不能进行此操作',409,'INVALID_TRANSITION')
        db.execute('UPDATE campus_bookings SET status=?,reply=?,updated_at=? WHERE id=?',(status,reply if admin else row['reply'],now_ms(),ident))
        if admin: s.write_audit(db,user['id'],'BOOKING_UPDATE','BOOKING',ident,{'status':status})
        db.commit()
        return {'id':ident,'status':status},200
    fail('不支持的操作',405,'METHOD_NOT_ALLOWED')


def guides(handler,method,ident,db,user,s,admin):
    scope = GUIDE_ROLES.get(s.admin_role(user)) if admin else None
    if ident:
        require_scope(ident, scope)
    if method=='GET':
        rows=db.execute('SELECT * FROM campus_guides'+('' if admin else " WHERE status='PUBLISHED'")).fetchall()
        if scope:
            rows = [r for r in rows if r['id'] == scope]
        if ident:
            row=next((r for r in rows if r['id']==ident),None)
            if not row: fail('指南尚未发布',404,'NOT_FOUND')
            return dict(row),200
        return {'items':[dict(r) for r in rows]},200
    if admin and method in ('PUT','PATCH') and ident in GUIDES:
        row=db.execute('SELECT * FROM campus_guides WHERE id=?',(ident,)).fetchone()
        data={**dict(row),**handler.body_json()}
        url=text(data,'url',500)
        if url:
            p=urlsplit(url)
            if p.scheme!='https' or not p.hostname or p.username or p.password: fail('链接必须为 HTTPS 网页')
        title=text(data,'title',60,True);body=text(data,'body',4000,True)
        image=image_value(handler,data.get('image',''),[row['image']])
        status=choice(data.get('status'),('DRAFT','PUBLISHED','UNLISTED'),'状态')
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE campus_guides SET title=?,body=?,image=?,url=?,status=?,updated_at=? WHERE id=?',(title,body,image,url,status,now_ms(),ident))
        s.write_audit(db,user['id'],'GUIDE_SAVE','GUIDE',ident,{'status':status})
        db.commit()
        return dict(db.execute('SELECT * FROM campus_guides WHERE id=?',(ident,)).fetchone()),200
    fail('不支持的操作',405,'METHOD_NOT_ALLOWED')


def create_admin_post(handler, module, db, user, s):
    data = handler.body_json()
    client = text(data, 'clientId', 100, True)
    db.execute('BEGIN IMMEDIATE')
    existing = db.execute('SELECT * FROM campus_posts WHERE module=? AND owner_id=? AND client_id=?',
                          (module, user['id'], client)).fetchone()
    if existing:
        return post_view(existing, user, s, detail=True, admin=True), 200
    deleted_table = 'marketplace_deleted_posts' if module == 'secondhand' else 'lostfound_review_deleted_posts'
    if db.execute(f'SELECT 1 FROM {deleted_table} WHERE owner_id=? AND client_id=?', (user['id'], client)).fetchone():
        fail('这条提交已被删除，请重新创建信息', 409, 'POST_DELETED')
    value, contact = post_payload(handler, data, module)
    value['ownerName'] = user['nickname'] or '校园运营'
    if module == 'lost-found':
        value['done'] = False
    ident, timestamp = str(uuid.uuid4()), now_ms()
    db.execute('''INSERT INTO campus_posts(id,module,owner_id,client_id,title,campus,payload_json,
                  contact_encrypted,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'PUBLISHED',?,?)''',
               (ident,module,user['id'],client,value['title'],value['campus'],dump(value),encrypted(s,contact),timestamp,timestamp))
    s.write_audit(db,user['id'],'CAMPUS_POST_PUBLISH',module,ident,{'status':'PUBLISHED'})
    db.commit()
    return post_view(db.execute('SELECT * FROM campus_posts WHERE id=?',(ident,)).fetchone(),user,s,detail=True,admin=True),201


def route(handler,method,path,query,environment):
    s=SimpleNamespace(**environment)
    admin=path.startswith('/api/v1/admin/campus/')
    prefix='/api/v1/admin/campus/' if admin else '/api/v1/community/' if path.startswith('/api/v1/community/') else '/api/v1/services/'
    parts=path.removeprefix(prefix).split('/')
    module=parts[0]; ident=parts[1] if len(parts)==2 else None
    if len(parts)>2: fail('接口不存在',404,'NOT_FOUND')
    with s.connection() as db:
        user=authorize_admin(handler,db,module,s) if admin else handler.current_user(db,'USER')
        if method!='GET':
            handler.enforce_rate_limit('campus-write',user['id'],120,3600)
        if admin and method=='GET' and module=='summary':
            counts={key:db.execute("SELECT COUNT(*) FROM campus_posts WHERE module=? AND status!='DELETED'",(key,)).fetchone()[0] for key in POST_MODULES}
            counts.update(providers=db.execute("SELECT COUNT(*) FROM campus_providers WHERE status='PUBLISHED'").fetchone()[0],bookings=db.execute("SELECT COUNT(*) FROM campus_bookings WHERE status='PENDING'").fetchone()[0])
            return {'counts':counts,'campuses':CAMPUSES,'categories':CATEGORIES,'removed':sorted(REMOVED)},200
        if admin and method=='GET' and module=='catalogue':
            return {'navigation':s.default_app_config()['navigation'],'schoolData':'device-only','removed':sorted(REMOVED)},200
        if module in POST_MODULES:
            if admin and method == 'POST' and not ident:
                return create_admin_post(handler, module, db, user, s)
            s.ensure_feature_open(db,'lost_found' if module=='lost-found' else 'secondhand','失物招领' if module=='lost-found' else '二手好物') if not admin else None
            if module == 'secondhand':
                from marketplace import handle
                return handle(handler,method,ident,query,db,user,s,admin)
            from lostfound_review import handle
            return handle(handler,method,ident,query,db,user,s,admin)
        if module=='providers': return providers(handler,method,ident,query,db,user,s,admin)
        if module=='bookings': return bookings(handler,method,ident,query,db,user,s,admin)
        if module=='guides': return guides(handler,method,ident,db,user,s,admin)
    fail('接口不存在',404,'NOT_FOUND')
