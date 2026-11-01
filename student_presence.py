"""Consented school profile observations, independent of platform authentication.

Only this small projection leaves the device. A client observation is not a
server-verified student credential. School sessions and learning data are rejected.
"""
import hashlib
import hmac
import json
import re
from datetime import datetime

from core import ApiError, now_ms, BUSINESS_TIMEZONE, business_day_bounds_ms
import account_analytics

POLICY_VERSION = '2026-10-23.3'
CONSENT_VERSION = 6
ACCEPTED_POLICIES = {6: POLICY_VERSION, 7: "2026-10-26.1"}
RETENTION_DAYS = 180


def keyed(api, kind, value):
    return hmac.new(api['phone_hash_secret'](), (kind + ':' + value).encode(), hashlib.sha256).hexdigest()


def initialize(db):
    # Additive migration also supports the deployed backend before optional SMS onboarding.
    columns={r[1] for r in db.execute('PRAGMA table_info(users)')}
    for name, definition in [('phone_verified_at','INTEGER'),('identity_encrypted','TEXT'),
            ('student_identity_hash','TEXT'),('identity_name_hash','TEXT'),('identity_status',"TEXT NOT NULL DEFAULT 'UNBOUND'")]:
        if name not in columns: db.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS student_presence(
        installation_id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL,
        user_id TEXT REFERENCES users(id) ON DELETE SET NULL, session_hash TEXT,
        student_hash TEXT, name_hash TEXT, identity_encrypted TEXT,
        platform TEXT NOT NULL, manufacturer TEXT NOT NULL, model TEXT NOT NULL,
        os_version TEXT NOT NULL, app_version TEXT NOT NULL, app_build INTEGER NOT NULL,
        policy_version TEXT NOT NULL, accepted_at INTEGER NOT NULL, received_at INTEGER NOT NULL,
        first_at INTEGER NOT NULL, last_at INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS student_presence_user ON student_presence(user_id,last_at);
      CREATE INDEX IF NOT EXISTS student_presence_student ON student_presence(student_hash,last_at);
      CREATE TABLE IF NOT EXISTS student_presence_cursors(installation_id TEXT PRIMARY KEY,secret_hash TEXT NOT NULL,sequence INTEGER NOT NULL,last_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS student_presence_daily(
        subject TEXT NOT NULL, day TEXT NOT NULL, first_at INTEGER NOT NULL, last_at INTEGER NOT NULL,
        PRIMARY KEY(subject,day));
    ''')


def prune(db):
    cutoff = now_ms() - RETENTION_DAYS * 86400000
    db.execute('DELETE FROM student_presence WHERE last_at<?', (cutoff,))
    db.execute('DELETE FROM student_presence_cursors WHERE last_at<?', (cutoff,))
    db.execute('DELETE FROM student_presence_daily WHERE last_at<?', (cutoff,))


def forget(db, user_id):
    # Remove only observations belonging to this platform account, never another
    # user's school profile on a shared installation.
    subjects = [r[0] for r in db.execute('SELECT student_hash FROM student_presence WHERE user_id=? AND student_hash IS NOT NULL', (user_id,))]
    db.execute('DELETE FROM student_presence WHERE user_id=?', (user_id,))
    db.execute('DELETE FROM student_presence_daily WHERE subject=?', ('u:' + user_id,))
    for subject in subjects:
        if not db.execute('SELECT 1 FROM student_presence WHERE student_hash=?', (subject,)).fetchone():
            db.execute('DELETE FROM student_presence_daily WHERE subject=?', (subject,))


def text_field(data, key, limit):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ApiError(422, 'INVALID_PROFILE', '资料格式无效')
    return value.strip()


def validate(data):
    allowed = {'installationId','reportToken','reportSequence','versionName','versionCode','consentVersion',
               'policyVersion','consentAcceptedAt','platform','manufacturer','model','osVersion','schoolIdentity'}
    if set(data) - allowed:
        raise ApiError(422, 'INVALID_PROFILE', '不支持的字段；请勿提交密码、学校会话或学习数据')
    accepted = data.get('consentAcceptedAt')
    if (type(data.get('consentVersion')) is not int or data['consentVersion'] not in ACCEPTED_POLICIES
            or data.get('policyVersion') != ACCEPTED_POLICIES.get(data['consentVersion']) or type(accepted) is not int
            or not 1792684800000 <= accepted <= now_ms() + 300000):
        raise ApiError(422, 'CONSENT_REQUIRED', '请先阅读并同意当前隐私政策')
    if type(data.get('reportSequence')) is not int or not 1 <= data['reportSequence'] <= 9007199254740991:
        raise ApiError(422, 'INVALID_SEQUENCE', '请求序号无效')
    installation = text_field(data, 'installationId', 128)
    secret = text_field(data, 'reportToken', 128)
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', installation) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', secret):
        raise ApiError(422, 'INVALID_INSTALLATION', '安装凭据无效')
    if data.get('platform') not in {'ios','android'}:
        raise ApiError(422, 'INVALID_PROFILE', '平台无效')
    for key, limit in [('manufacturer',80),('model',100),('osVersion',40),('versionName',40)]:
        text_field(data,key,limit)
    if type(data.get('versionCode')) is not int or not 1 <= data['versionCode'] <= 2147483647:
        raise ApiError(422, 'INVALID_PROFILE', '应用版本无效')
    if data['platform'] == 'ios' and data['manufacturer'] != 'Apple':
        raise ApiError(422, 'INVALID_PROFILE', '设备品牌无效')
    school = data.get('schoolIdentity')
    if school is not None:
        if not isinstance(school,dict) or set(school) - {'studentId','realName','grade','admissionYear'}:
            raise ApiError(422, 'INVALID_PROFILE', '仅可提交姓名、学号与学校返回的年级')
        student = text_field(school,'studentId',32)
        if not re.fullmatch(r'[A-Za-z0-9_-]{4,32}', student):
            raise ApiError(422,'INVALID_PROFILE','学号格式无效')
        name = school.get('realName','')
        if not isinstance(name,str) or len(name) > 40 or any(ord(c) < 32 for c in name):
            raise ApiError(422,'INVALID_PROFILE','姓名格式无效')
        grade = school.get('grade','')
        if grade not in {'','大一','大二','大三','大四','大五','大六','研一','研二','研三','研四'}:
            raise ApiError(422,'INVALID_PROFILE','年级格式无效')
        year = school.get('admissionYear')
        if year is not None and (type(year) is not int or not 2000 <= year <= datetime.now(BUSINESS_TIMEZONE).year):
            raise ApiError(422,'INVALID_PROFILE','入学年份无效')
        school = {'studentId':student,'realName':name.strip(),'grade':grade,'admissionYear':year}
    return installation, hashlib.sha256(secret.encode()).hexdigest(), school


def receive(handler, api):
    data = handler.body_json()
    installation, secret_hash, school = validate(data)
    handler.enforce_rate_limit('student-presence-ip', handler.request_identity(), 600, 3600)
    handler.enforce_rate_limit('student-presence-install', installation, 240, 3600)
    with api['connection']() as db:
        db.execute('BEGIN IMMEDIATE')
        user = handler.optional_user(db)
        if user is not None and (user['role'] != 'USER' or user['account_type'] != 'PHONE'):
            raise ApiError(403, 'ACCOUNT_REQUIRED', '平台账号无效')
        cursor=db.execute('SELECT * FROM student_presence_cursors WHERE installation_id=?',(installation,)).fetchone()
        if cursor:
            if not hmac.compare_digest(cursor['secret_hash'],secret_hash):
                raise ApiError(403,'INSTALLATION_MISMATCH','安装凭据不匹配')
            if data['reportSequence'] <= cursor['sequence']: return {'accepted':True}, 200
        db.execute('INSERT INTO student_presence_cursors VALUES(?,?,?,?) ON CONFLICT(installation_id) DO UPDATE SET sequence=excluded.sequence,last_at=excluded.last_at',(installation,secret_hash,data['reportSequence'],now_ms()))
        existing = db.execute('SELECT secret_hash,student_hash FROM student_presence WHERE installation_id=?', (installation,)).fetchone()
        if existing and not hmac.compare_digest(existing['secret_hash'], secret_hash):
            raise ApiError(403, 'INSTALLATION_MISMATCH', '安装凭据不匹配')
        if user is None and school is None:
            # Explicit logout/unbind clears the last association; no anonymous
            # background traffic is turned into a student or an account.
            if existing:
                db.execute('DELETE FROM student_presence WHERE installation_id=?',(installation,))
                if existing['student_hash'] and not db.execute('SELECT 1 FROM student_presence WHERE student_hash=?',(existing['student_hash'],)).fetchone():
                    db.execute('DELETE FROM student_presence_daily WHERE subject=?',(existing['student_hash'],))
            return {'accepted':True}, 200
        at = now_ms()
        student_hash = keyed(api,'observed-student:SICAU',school['studentId']) if school else None
        name_hash = keyed(api,'observed-name',school['realName']) if school else None
        encrypted = api['pii_cipher']().encrypt(json.dumps(school,ensure_ascii=False).encode()).decode() if school else None
        user_id = user['id'] if user is not None else None
        db.execute('''INSERT INTO student_presence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(installation_id) DO UPDATE SET user_id=excluded.user_id,session_hash=excluded.session_hash,
          student_hash=excluded.student_hash,name_hash=excluded.name_hash,identity_encrypted=excluded.identity_encrypted,
          platform=excluded.platform,manufacturer=excluded.manufacturer,model=excluded.model,os_version=excluded.os_version,
          app_version=excluded.app_version,app_build=excluded.app_build,policy_version=excluded.policy_version,
          received_at=CASE WHEN policy_version=excluded.policy_version AND accepted_at=excluded.accepted_at THEN received_at ELSE excluded.received_at END,
          accepted_at=excluded.accepted_at,last_at=excluded.last_at''',
          (installation,secret_hash,user_id,hashlib.sha256(handler.headers.get("Authorization", "")[7:].strip().encode()).hexdigest() if user_id else None,student_hash,name_hash,encrypted,data['platform'],data['manufacturer'],data['model'],
           data['osVersion'],data['versionName'],data['versionCode'],data['policyVersion'],data['consentAcceptedAt'],at,at,at))
        if existing and existing['student_hash'] and existing['student_hash'] != student_hash and not db.execute('SELECT 1 FROM student_presence WHERE student_hash=?',(existing['student_hash'],)).fetchone():
            db.execute('DELETE FROM student_presence_daily WHERE subject=?',(existing['student_hash'],))
        subject = student_hash or 'u:' + user_id
        db.execute('''INSERT INTO student_presence_daily VALUES(?,?,?,?) ON CONFLICT(subject,day)
          DO UPDATE SET last_at=excluded.last_at''',(subject,account_analytics.day_at(at).isoformat(),at,at))
        if user is not None:
            account_analytics.record(db,user_id,foreground=True,timestamp=at)
        prune(db)
        return {'accepted':True}, 200


# A platform registration remains visible even before its first device report.
# Anonymous school observations are deduplicated by school identity and not joined
# to a phone merely because that phone previously used the same installation.
DIRECTORY = '''WITH current_presence AS (
 SELECT p.*,CASE WHEN EXISTS(SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id
 WHERE s.token_hash=p.session_hash AND s.user_id=p.user_id AND s.expires_at>CAST(strftime('%s','now') AS INTEGER)*1000
 AND u.account_status IN ('ACTIVE','LIMITED')) THEN p.user_id ELSE NULL END current_user_id FROM student_presence p
), latest AS (
 SELECT p.*,ROW_NUMBER() OVER(PARTITION BY current_user_id ORDER BY last_at DESC,installation_id) rn FROM current_presence p WHERE current_user_id IS NOT NULL
), guests AS (
 SELECT p.*,ROW_NUMBER() OVER(PARTITION BY student_hash ORDER BY last_at DESC,installation_id) rn FROM current_presence p
 WHERE current_user_id IS NULL AND student_hash IS NOT NULL AND NOT EXISTS(SELECT 1 FROM current_presence q JOIN users a ON a.id=q.current_user_id
 WHERE q.student_hash=p.student_hash AND a.account_status IN ('ACTIVE','LIMITED'))
), directory AS (
 SELECT u.id account_id,u.nickname,u.phone_encrypted,u.phone_hash,u.phone_verified_at,u.identity_status,
 u.identity_encrypted manual_identity,u.student_identity_hash,u.identity_name_hash,u.created_at,u.account_status,
 p.installation_id,p.student_hash,p.name_hash,p.identity_encrypted,p.platform,p.manufacturer,p.model,p.os_version,p.app_version,p.app_build,
 p.policy_version,p.accepted_at,p.received_at,p.last_at,
 (SELECT MAX(last_at) FROM account_activity_daily WHERE user_id=u.id) account_last_at
 FROM users u LEFT JOIN latest p ON p.current_user_id=u.id AND p.rn=1
 WHERE u.role='USER' AND u.account_type='PHONE' AND u.account_status!='DELETED'
 UNION ALL
 SELECT NULL,'',NULL,NULL,NULL,'UNBOUND',NULL,NULL,NULL,NULL,'ACTIVE',
 p.installation_id,p.student_hash,p.name_hash,p.identity_encrypted,p.platform,p.manufacturer,p.model,p.os_version,p.app_version,p.app_build,
 p.policy_version,p.accepted_at,p.received_at,p.last_at,NULL FROM guests p WHERE rn=1
) '''


def predicate(query, api):
    scope = query.get('scope','all')
    clauses = {'all':'1=1','registered':'account_id IS NOT NULL','school':'student_hash IS NOT NULL',
        'platform':'account_id IS NOT NULL AND installation_id IS NOT NULL',
        'schoolOnly':'account_id IS NULL AND student_hash IS NOT NULL',
        'phoneVerified':'phone_verified_at IS NOT NULL','verified':"identity_status='VERIFIED'",
        'todayRegistrations':'created_at>=? AND created_at<?'}
    if scope not in clauses:
        raise ApiError(422,'INVALID_FILTER','账号筛选无效')
    sql = clauses[scope]
    args = list(business_day_bounds_ms()) if scope == 'todayRegistrations' else []
    if query.get('window'):
        begin,end = account_analytics.window(query,now_ms())
        sql += ''' AND ((account_id IS NOT NULL AND EXISTS(SELECT 1 FROM account_activity_daily a WHERE a.user_id=account_id AND a.day BETWEEN ? AND ?))
          OR (account_id IS NULL AND EXISTS(SELECT 1 FROM student_presence_daily a WHERE a.subject=student_hash AND a.day BETWEEN ? AND ?)))'''
        args.extend([begin,end,begin,end])
    q = query.get('q','').strip()
    if q:
        if len(q)>80: raise ApiError(422,'INVALID_FILTER','搜索内容过长')
        sql += ''' AND (nickname LIKE ? ESCAPE '\\' OR student_hash=? OR name_hash=? OR phone_hash=? OR student_identity_hash=? OR identity_name_hash=?)'''
        escaped=q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        args.extend(['%'+escaped+'%',keyed(api,'observed-student:SICAU',q),keyed(api,'observed-name',q),
                     api['phone_digest'](q),keyed(api,'student:SICAU',q.upper()),keyed(api,'student-name',q)])
    return sql,args


def grade_label(identity):
    if identity.get('grade'): return identity['grade']
    year = identity.get('admissionYear')
    if not year: return ''
    today = datetime.now(BUSINESS_TIMEZONE)
    level = today.year - (1 if today.month < 9 else 0) - year + 1
    return ['大一','大二','大三','大四','大五','大六'][level-1] if 1 <= level <= 6 else str(year)+'级'


def directory(db, query, api):
    prune(db)
    try:
        offset=int(query.get('offset',0))
        if not 0 <= offset <= 1000000: raise ValueError()
    except (ValueError,TypeError): raise ApiError(422,'INVALID_PAGE','页码无效')
    where,args=predicate(query,api)
    base=DIRECTORY+'SELECT {fields} FROM directory WHERE '+where
    total=db.execute(base.format(fields='COUNT(*)'),args).fetchone()[0]
    rows=db.execute(base.format(fields='*')+' ORDER BY COALESCE(last_at,account_last_at,created_at) DESC,COALESCE(account_id,installation_id) LIMIT 50 OFFSET ?',(*args,offset)).fetchall()
    items=[]
    for row in rows:
        identity={}
        encrypted=row['identity_encrypted'] or row['manual_identity']
        if encrypted:
            try: identity=json.loads(api['pii_cipher']().decrypt(encrypted.encode()))
            except Exception: raise ApiError(503,'IDENTITY_READ_FAILED','学生资料暂时无法读取') from None
        device={key:row[key] for key in ['platform','manufacturer','model','os_version','app_version','app_build']} if row['installation_id'] else None
        if not device and row['account_id']:
            old=db.execute('SELECT platform,manufacturer,model,os_version,app_version,app_build FROM account_device_profiles WHERE user_id=? ORDER BY last_at DESC LIMIT 1',(row['account_id'],)).fetchone()
            if old: device=dict(old)
        items.append({'accountId':row['account_id'],'realName':identity.get('realName',''),'nickname':row['nickname'],
          'studentId':identity.get('studentId',''),'grade':grade_label(identity),
          'phone':api['decrypt_phone'](row['phone_encrypted']) or '',
          'platformSignedIn':bool(row['account_id'] and row['installation_id']), 'schoolSignedIn':bool(row['student_hash']),
          'identitySource':'学校资料（客户端读取）' if row['identity_encrypted'] else '主动填写' if row['manual_identity'] else '',
          'deviceProfile':device,'lastActiveAt':max(row['last_at'] or 0,row['account_last_at'] or 0) or None,
          'registeredAt':row['created_at'],'accountStatus':row['account_status'],
          'policyVersion':row['policy_version'],'consentAcceptedAt':row['accepted_at'],'consentReceivedAt':row['received_at']})
    return {'items':items,'total':total,'offset':offset,'limit':50}


def metrics(db, api):
    prune(db)
    result={}
    for scope in ['registered','school','platform','schoolOnly','phoneVerified','verified','todayRegistrations']:
        where,args=predicate({'scope':scope},api)
        result[scope]=db.execute(DIRECTORY+'SELECT COUNT(*) FROM directory WHERE '+where,args).fetchone()[0]
    return result


def account_fields(db, user_id, api):
    row=db.execute("SELECT identity_encrypted FROM student_presence WHERE user_id=? AND identity_encrypted IS NOT NULL ORDER BY last_at DESC LIMIT 1",(user_id,)).fetchone()
    if not row: return {}
    try: identity=json.loads(api['pii_cipher']().decrypt(row[0].encode()))
    except Exception: raise ApiError(503,'IDENTITY_READ_FAILED','学生资料暂时无法读取') from None
    return {'observed_real_name':identity.get('realName',''),'observed_student_id':identity.get('studentId',''),'observed_grade':grade_label(identity)}


def devices(db, query, api):
    prune(db)
    try:
        offset=int(query.get('offset',0))
        if not 0 <= offset <= 1000000: raise ValueError()
    except (ValueError,TypeError): raise ApiError(422,'INVALID_PAGE','页码无效')
    platform=query.get('platform','')
    if platform not in {'','ios','android'}: raise ApiError(422,'INVALID_FILTER','平台无效')
    sql=DIRECTORY.split(', latest AS (')[0]+      ''', device_rows AS (
       SELECT u.id,u.nickname,u.phone_encrypted,u.phone_masked,p.identity_encrypted,p.platform,p.manufacturer,p.model,p.os_version,
       p.app_version,p.app_build,p.policy_version,CASE WHEN p.policy_version='2026-10-26.1' THEN 7 ELSE 6 END consent_version,p.accepted_at consent_accepted_at,p.received_at consent_received_at,
       p.first_at,p.last_at,p.installation_id FROM current_presence p LEFT JOIN users u ON u.id=p.current_user_id
       UNION ALL
       SELECT u.id,u.nickname,u.phone_encrypted,u.phone_masked,NULL,p.platform,p.manufacturer,p.model,p.os_version,
       p.app_version,p.app_build,p.policy_version,p.consent_version,p.consent_accepted_at,p.consent_received_at,p.first_at,p.last_at,p.installation_id
       FROM account_device_profiles p JOIN users u ON u.id=p.user_id WHERE u.role='USER' AND u.account_type='PHONE'
       AND u.account_status IN ('ACTIVE','LIMITED') AND NOT EXISTS(SELECT 1 FROM student_presence q WHERE q.installation_id=p.installation_id)
      ) SELECT {fields} FROM device_rows WHERE (?='' OR platform=?)'''
    total=db.execute(sql.format(fields='COUNT(*)'),(platform,platform)).fetchone()[0]
    rows=db.execute(sql.format(fields='*')+' ORDER BY last_at DESC,installation_id LIMIT 50 OFFSET ?',(platform,platform,offset)).fetchall()
    items=[]
    for row in rows:
        item=dict(row); item.pop('installation_id')
        item['phone']=api['decrypt_phone'](item.pop('phone_encrypted')) or ''
        encrypted=item.pop('identity_encrypted')
        if encrypted:
            try: item['realName']=json.loads(api['pii_cipher']().decrypt(encrypted.encode())).get('realName','')
            except Exception: raise ApiError(503,'IDENTITY_READ_FAILED','学生资料暂时无法读取') from None
        items.append(item)
    return {'items':items,'total':total,'offset':offset,'limit':50,'retentionDays':RETENTION_DAYS,'policyVersion':POLICY_VERSION}
