"""Account activity for MyChuannong; never infer people from anonymous devices.

Adapted service-event allowlist and UTC+8 calendar windows from
HEltDeng/my-changli-backend identity_statistics.py (d069aeb814f041414e5c7da76d414acb93416e9b).
Daily rows bound storage and make retries / multiple devices idempotent.
"""
from datetime import datetime, timedelta
import logging
import re
import threading
import phone_auth

from core import ApiError, now_ms, BUSINESS_TIMEZONE, business_day_bounds_ms

DAY = 86400000
RETENTION_DAYS = 180
CONSENT_VERSION = 3  # Android and older iOS remain compatible.
IOS_CONSENT_VERSION = 4
POLICY_VERSION = "2026-10-23"
SERVICE_EVENTS = (
    'LOGIN_SUCCESS', 'USER_REGISTER', 'RESERVATION_CREATED', 'RESERVATION_CANCELLED',
    'LOST_FOUND_PUBLISHED', 'LOST_FOUND_EDITED', 'LOST_FOUND_DELETED',
    'LOST_FOUND_FAVORITED', 'LOST_FOUND_UNFAVORITED', 'LOST_FOUND_CLAIMED',
    'LOST_FOUND_REPORTED', 'RESOURCE_VIEWED', 'RESOURCE_FAVORITED',
    'RESOURCE_UNFAVORITED', 'RESOURCE_COMPLAINT_SUBMITTED', 'FEEDBACK_SUBMITTED',
)
ELIGIBLE = "u.role='USER' AND u.account_type='PHONE' AND u.account_status IN ('ACTIVE','LIMITED')"


def day_at(timestamp):
    return datetime.fromtimestamp(timestamp / 1000, BUSINESS_TIMEZONE).date()


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS account_activity_daily(
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        day TEXT NOT NULL, first_at INTEGER NOT NULL, last_at INTEGER NOT NULL,
        foreground INTEGER NOT NULL DEFAULT 0, service INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(user_id,day))''')
    db.execute('CREATE INDEX IF NOT EXISTS account_activity_day ON account_activity_daily(day,user_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS account_device_profiles(
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        installation_id TEXT NOT NULL, device_id TEXT,
        manufacturer TEXT NOT NULL, model TEXT NOT NULL, android_version TEXT NOT NULL,
        sdk_int INTEGER NOT NULL, app_version TEXT NOT NULL, consent_version INTEGER NOT NULL,
        first_at INTEGER NOT NULL, last_at INTEGER NOT NULL,
        PRIMARY KEY(user_id,installation_id))''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(account_device_profiles)')}
    if 'platform' not in columns:
        db.execute("ALTER TABLE account_device_profiles ADD COLUMN platform TEXT NOT NULL DEFAULT 'android'")
    if 'os_version' not in columns:
        db.execute("ALTER TABLE account_device_profiles ADD COLUMN os_version TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE account_device_profiles SET os_version=android_version WHERE platform='android'")
    for name, definition in [('app_build', 'INTEGER'), ('policy_version', "TEXT NOT NULL DEFAULT ''"),
                             ('consent_accepted_at', 'INTEGER'), ('consent_received_at', 'INTEGER')]:
        if name not in columns:
            db.execute(f'ALTER TABLE account_device_profiles ADD COLUMN {name} {definition}')
    db.execute('''CREATE TABLE IF NOT EXISTS account_analytics_settings(
        key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
    # Only events attributed to a PHONE account at event time can be backfilled.
    # APP_ACTIVE includes background polling and is deliberately excluded.
    if not db.execute("SELECT 1 FROM account_analytics_settings WHERE key='backfill_v1'").fetchone():
        at = now_ms()
        slots = ','.join('?' for _ in SERVICE_EVENTS)
        db.execute(f'''INSERT INTO account_activity_daily(user_id,day,first_at,last_at,foreground,service)
            SELECT e.user_id,date(e.created_at/1000,'unixepoch','+8 hours'),
                   MIN(e.created_at),MAX(e.created_at),0,1
            FROM user_behavior_events e JOIN users u ON u.id=e.user_id
            WHERE {ELIGIBLE} AND e.identity_kind='USER' AND e.result='SUCCESS'
              AND e.client_source='APP' AND e.event_type IN ({slots})
              AND e.created_at>=? AND e.created_at<=?
            GROUP BY e.user_id,date(e.created_at/1000,'unixepoch','+8 hours')
            ON CONFLICT(user_id,day) DO UPDATE SET
              first_at=MIN(first_at,excluded.first_at),last_at=MAX(last_at,excluded.last_at),service=1''',
            (*SERVICE_EVENTS, at-RETENTION_DAYS*DAY, at))
        db.execute("INSERT INTO account_analytics_settings VALUES('backfill_v1',?)", (str(at),))
    prune(db)


def prune(db):
    cutoff = now_ms() - RETENTION_DAYS * DAY
    db.execute('DELETE FROM account_activity_daily WHERE day<?', ((day_at(now_ms())-timedelta(days=RETENTION_DAYS-1)).isoformat(),))
    db.execute('DELETE FROM account_device_profiles WHERE last_at<?', (cutoff,))
    db.execute('DELETE FROM phone_challenges WHERE created_at<?', (now_ms() - 30 * DAY,))
    db.execute('DELETE FROM password_resets WHERE day<?', ((day_at(now_ms())-timedelta(days=30)).isoformat(),))


def start_retention_worker(connection):
    """Production also expires observations when there is no incoming traffic."""
    stop = threading.Event()
    def work():
        while not stop.wait(3600):
            try:
                with connection() as db:
                    prune(db)
                    import student_presence
                    student_presence.prune(db)
            except Exception:
                logging.getLogger('mychuannong.runtime').error('Account statistics retention failed; retrying next hour')
    threading.Thread(target=work, name='account-statistics-retention', daemon=True).start()
    return stop


def record(db, user_id, *, foreground=False, timestamp=None):
    at = now_ms() if timestamp is None else timestamp
    if not db.execute(f'SELECT 1 FROM users u WHERE u.id=? AND {ELIGIBLE}', (user_id,)).fetchone():
        return
    db.execute('''INSERT INTO account_activity_daily VALUES(?,?,?,?,?,?)
        ON CONFLICT(user_id,day) DO UPDATE SET
          first_at=MIN(first_at,excluded.first_at),last_at=MAX(last_at,excluded.last_at),
          foreground=MAX(foreground,excluded.foreground),service=MAX(service,excluded.service)''',
        (user_id, day_at(at).isoformat(), at, at, int(foreground), int(not foreground)))


def receive(handler, connection, record_device_identity):
    data = handler.body_json()
    allowed = {'installationId','deviceScopedId','versionName','versionCode','consentVersion',
               'manufacturer','model','androidVersion','sdkInt','platform','osVersion','policyVersion','consentAcceptedAt'}
    if set(data) - allowed:
        raise ApiError(422, 'INVALID_ACTIVITY', '不支持的统计字段')
    if type(data.get('consentVersion')) is not int or data['consentVersion'] not in {CONSENT_VERSION, IOS_CONSENT_VERSION, 5}:
        raise ApiError(422, 'CONSENT_REQUIRED', '请先阅读并同意当前隐私政策')
    platform = data.get('platform', 'android')
    if not isinstance(platform, str) or platform not in {'android', 'ios'}:
        raise ApiError(422, 'INVALID_DEVICE_PROFILE', '客户端平台无效')
    if data['consentVersion'] == 5:
        phone_auth.consent(data)
        if type(data.get('versionCode')) is not int or not 1 <= data['versionCode'] <= 2147483647:
            raise ApiError(422, 'INVALID_DEVICE_PROFILE', '应用构建版本无效')
    elif data['consentVersion'] == IOS_CONSENT_VERSION:
        accepted = data.get('consentAcceptedAt')
        if (platform != 'ios' or data.get('policyVersion') != POLICY_VERSION
                or type(accepted) is not int or not 1792684800000 <= accepted <= now_ms() + 300000):
            raise ApiError(422, 'CONSENT_REQUIRED', '请先阅读并同意当前隐私政策')
        if type(data.get('versionCode')) is not int or not 1 <= data['versionCode'] <= 2147483647:
            raise ApiError(422, 'INVALID_DEVICE_PROFILE', '应用构建版本无效')
    elif 'policyVersion' in data or 'consentAcceptedAt' in data:
        raise ApiError(422, 'CONSENT_REQUIRED', '协议记录版本不匹配')
    os_field = 'androidVersion' if platform == 'android' else 'osVersion'
    if platform == 'ios' and ('androidVersion' in data or 'sdkInt' in data or data.get('manufacturer') != 'Apple'):
        raise ApiError(422, 'INVALID_DEVICE_PROFILE', 'iOS 设备资料格式无效')
    if platform == 'android' and 'osVersion' in data and data['osVersion'] != data.get('androidVersion'):
        raise ApiError(422, 'INVALID_DEVICE_PROFILE', '系统版本不一致')
    for key, limit in [('manufacturer',80),('model',100),(os_field,40),('versionName',40)]:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c)<32 for c in value):
            raise ApiError(422, 'INVALID_DEVICE_PROFILE', '设备资料格式无效')
    if platform == 'android' and (type(data.get('sdkInt')) is not int or not 21 <= data['sdkInt'] <= 100):
        raise ApiError(422, 'INVALID_DEVICE_PROFILE', '系统版本无效')
    with connection() as db:
        user = handler.current_user(db, 'USER')
        if user['account_type'] != 'PHONE':
            raise ApiError(403, 'ACCOUNT_REQUIRED', '请先登录我的川农账号')
        if data['consentVersion'] == 5:
            phone_auth.save_consent(db, user['id'], data)
        handler.enforce_rate_limit('account-foreground', user['id'], 240, 3600)
        # current_user may update its legacy last-active timestamp.
        db.commit()
        identity = record_device_identity(db, data, user_id=user['id'])
        at = now_ms()
        db.execute('''INSERT INTO account_device_profiles(user_id,installation_id,device_id,manufacturer,model,android_version,sdk_int,app_version,consent_version,first_at,last_at,platform,os_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id,installation_id) DO UPDATE SET
              device_id=excluded.device_id,manufacturer=excluded.manufacturer,model=excluded.model,
              android_version=excluded.android_version,sdk_int=excluded.sdk_int,
              app_version=excluded.app_version,consent_version=excluded.consent_version,last_at=excluded.last_at,
              platform=excluded.platform,os_version=excluded.os_version''',
            (user['id'],identity['installation_id'],identity['device_id'],data['manufacturer'].strip(),
             data['model'].strip(),data.get('androidVersion','').strip(),data.get('sdkInt',0),data['versionName'].strip(),
             data['consentVersion'],at,at,platform,data[os_field].strip()))
        db.execute("""UPDATE account_device_profiles SET app_build=?,policy_version=?,consent_accepted_at=?,
            consent_received_at=CASE WHEN policy_version=? AND consent_accepted_at=?
              THEN COALESCE(consent_received_at,?) ELSE ? END
            WHERE user_id=? AND installation_id=?""",
            (data.get('versionCode'), data.get('policyVersion',''), data.get('consentAcceptedAt'),
             data.get('policyVersion',''), data.get('consentAcceptedAt'), at, at,
             user['id'], identity['installation_id']))
        record(db, user['id'], foreground=True, timestamp=at)
        prune(db)
        return {'accepted': True, 'day': day_at(at).isoformat()}, 200


def window(query, at):
    today = day_at(at)
    value = query.get('window', 'today')
    if value == 'day':
        try:
            text = query.get('day', '')
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
                raise ValueError()
            start = datetime.strptime(text, '%Y-%m-%d').date()
        except ValueError:
            raise ApiError(422, 'INVALID_DAY', '日期格式无效')
        if not today-timedelta(days=89) <= start <= today:
            raise ApiError(422, 'INVALID_DAY', '请选择最近90天内的日期')
        return start.isoformat(), start.isoformat()
    if value not in {'today','7d','30d','month'}:
        raise ApiError(422, 'INVALID_WINDOW', '统计范围无效')
    start = today.replace(day=1) if value == 'month' else today-timedelta(days={'today':0,'7d':6,'30d':29}[value])
    return start.isoformat(), today.isoformat()


def summary(db, query):
    if query.get('days', '30') not in {'7','30','90'}:
        raise ApiError(422, 'INVALID_RANGE', '请选择7、30或90天')
    prune(db)
    at = now_ms()
    today = day_at(at)
    days = int(query.get('days', '30'))
    start = today-timedelta(days=days-1)
    def active(kind):
        begin, end = window({'window':kind},at)
        return db.execute(f'''SELECT COUNT(DISTINCT a.user_id) FROM account_activity_daily a
            JOIN users u ON u.id=a.user_id WHERE {ELIGIBLE} AND a.day BETWEEN ? AND ?''',(begin,end)).fetchone()[0]
    metrics = {k:active(v) for k,v in {'dau':'today','wau':'7d','mau':'30d','calendarMonth':'month'}.items()}
    day_start, day_end = business_day_bounds_ms(today)
    metrics.update({
        'phoneVerifiedAccounts':db.execute(f'SELECT COUNT(*) FROM users u WHERE {ELIGIBLE} AND phone_verified_at IS NOT NULL').fetchone()[0],
        'studentBoundAccounts':db.execute(f'SELECT COUNT(*) FROM users u WHERE {ELIGIBLE} AND student_identity_hash IS NOT NULL').fetchone()[0],
        'verifiedStudents':db.execute(f"SELECT COUNT(*) FROM users u WHERE {ELIGIBLE} AND identity_status='VERIFIED'").fetchone()[0],
        'verifiedStudentDau':db.execute(f"SELECT COUNT(*) FROM account_activity_daily a JOIN users u ON u.id=a.user_id WHERE {ELIGIBLE} AND a.day=? AND u.identity_status='VERIFIED'",(today.isoformat(),)).fetchone()[0],
        'registeredAccounts':db.execute("SELECT COUNT(*) FROM users WHERE role='USER' AND account_type='PHONE' AND account_status!='DELETED'").fetchone()[0],
        'activeAccounts':db.execute(f'SELECT COUNT(*) FROM users u WHERE {ELIGIBLE}').fetchone()[0],
        'todayRegistrations':db.execute("SELECT COUNT(*) FROM users WHERE role='USER' AND account_type='PHONE' AND account_status!='DELETED' AND created_at>=? AND created_at<?",(day_start,day_end)).fetchone()[0],
        'todayForegroundAccounts':db.execute(f'SELECT COUNT(*) FROM account_activity_daily a JOIN users u ON u.id=a.user_id WHERE {ELIGIBLE} AND a.day=? AND a.foreground=1',(today.isoformat(),)).fetchone()[0],
        'reportedInstallations':db.execute(f'SELECT COUNT(DISTINCT p.installation_id) FROM account_device_profiles p JOIN users u ON u.id=p.user_id WHERE {ELIGIBLE}').fetchone()[0],
    })
    activity = {r['day']:dict(r) for r in db.execute(f'''SELECT a.day,COUNT(*) dau,SUM(a.foreground) foreground
        FROM account_activity_daily a JOIN users u ON u.id=a.user_id
        WHERE {ELIGIBLE} AND a.day BETWEEN ? AND ? GROUP BY a.day''',(start.isoformat(),today.isoformat()))}
    registrations = dict(db.execute('''SELECT date(created_at/1000,'unixepoch','+8 hours'),COUNT(*)
        FROM users WHERE role='USER' AND account_type='PHONE' AND account_status!='DELETED'
        AND created_at>=? AND created_at<? GROUP BY 1''',(business_day_bounds_ms(start)[0], day_end)))
    coverage = (
        db.execute(f'SELECT MIN(a.first_at) FROM account_activity_daily a JOIN users u ON u.id=a.user_id WHERE {ELIGIBLE}').fetchone()[0],
        db.execute(f'SELECT MIN(p.first_at) FROM account_device_profiles p JOIN users u ON u.id=p.user_id WHERE {ELIGIBLE}').fetchone()[0],
    )
    first_day = day_at(coverage[0]).isoformat() if coverage[0] else None
    foreground_day = day_at(coverage[1]).isoformat() if coverage[1] else None
    trend = []
    for i in range(days):
        day = (start+timedelta(days=i)).isoformat()
        row = activity.get(day,{})
        trend.append({'day':day,'dau':row.get('dau',0) if first_day and day>=first_day else None,
                      'foreground':row.get('foreground',0) if foreground_day and day>=foreground_day else None,
                      'registrations':registrations.get(day,0)})
    return {'asOf':at,'timezone':'Asia/Shanghai','metrics':metrics,'trend':trend,
            'coverage':{'firstAccountActivityAt':coverage[0], 'firstForegroundAt':coverage[1]},
            'definition':'成功注册、登录、主动业务操作或授权前台上报；同一账号每天只计一次，跨设备去重。排除游客、管理员、后台轮询及当前已禁用/已注销账号。近30日含今日，按北京时间自然日。',
            'retentionDays':RETENTION_DAYS}


def accounts(db, query, serialize_user):
    prune(db)
    begin, end = window(query, now_ms())
    try:
        offset = int(query.get('offset',0))
        if not 0 <= offset <= 1000000: raise ValueError()
    except (ValueError,TypeError):
        raise ApiError(422,'INVALID_PAGE','页码无效')
    predicate = f'''FROM users u WHERE {ELIGIBLE} AND EXISTS(
        SELECT 1 FROM account_activity_daily a WHERE a.user_id=u.id AND a.day BETWEEN ? AND ?)'''
    total = db.execute('SELECT COUNT(*) '+predicate,(begin,end)).fetchone()[0]
    rows = db.execute('SELECT u.* '+predicate+' ORDER BY u.created_at DESC,u.id LIMIT 50 OFFSET ?',(begin,end,offset)).fetchall()
    items = []
    for row in rows:
        user = serialize_user(row)
        profile = db.execute('''SELECT manufacturer,model,android_version,platform,os_version,app_version,app_build,policy_version,consent_accepted_at,consent_received_at,last_at
            FROM account_device_profiles WHERE user_id=? ORDER BY last_at DESC LIMIT 1''',(row['id'],)).fetchone()
        activity = db.execute('SELECT MIN(first_at),MAX(last_at) FROM account_activity_daily WHERE user_id=? AND day BETWEEN ? AND ?',(row['id'],begin,end)).fetchone()
        items.append({'id':row['id'],'userCode':user.get('userCode'), 'nickname':row['nickname'],
                      'studentId':user.get('studentId'), 'realName':user.get('realName'),
                      'phoneVerified':user.get('phoneVerified'), 'identityStatus':user.get('identityStatus'),
                      'phoneMasked':user.get('phoneMasked'), 'accountStatus':row['account_status'],
                      'firstActiveAt':activity[0],'lastActiveAt':activity[1],
                      'deviceProfile':dict(profile) if profile else None})
    return {'items':items,'total':total,'limit':50,'offset':offset,'from':begin,'to':end}


def forget(db, user_id):
    db.execute('DELETE FROM account_activity_daily WHERE user_id=?',(user_id,))
    db.execute('DELETE FROM account_device_profiles WHERE user_id=?',(user_id,))


def devices(db, query, serialize_user):
    """Super-admin-only paginated device/consent view; no installation keys or secrets."""
    prune(db)
    try:
        offset = int(query.get('offset', 0))
        if not 0 <= offset <= 1000000: raise ValueError()
    except (ValueError, TypeError):
        raise ApiError(422, 'INVALID_PAGE', '页码无效')
    platform = query.get('platform', '')
    if platform not in {'', 'ios', 'android'}:
        raise ApiError(422, 'INVALID_DEVICE_PROFILE', '客户端平台无效')
    predicate = "FROM account_device_profiles p JOIN users u ON u.id=p.user_id WHERE " + ELIGIBLE
    args = []
    if platform:
        predicate += ' AND p.platform=?'; args.append(platform)
    total = db.execute('SELECT COUNT(*) ' + predicate, args).fetchone()[0]
    rows = db.execute("""SELECT u.id,u.nickname,u.phone_masked,p.manufacturer,p.model,p.platform,p.os_version,
        p.app_version,p.app_build,p.policy_version,p.consent_version,p.consent_accepted_at,
        p.consent_received_at,p.first_at,p.last_at """ + predicate +
        ' ORDER BY p.last_at DESC,u.id,p.installation_id LIMIT 50 OFFSET ?', (*args,offset)).fetchall()
    return {'items': [dict(row) for row in rows], 'total': total, 'limit': 50, 'offset': offset,
            'retentionDays': RETENTION_DAYS, 'policyVersion': phone_auth.POLICY_VERSION}
