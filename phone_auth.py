"""Phone ownership, password recovery and campus identity for one platform account.

No school credentials enter this module. SMS delivery never returns a code to a client.
All quotas and one-time consumption are persisted under SQLite write transactions.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

from core import ApiError, now_ms, business_day_bounds_ms, BUSINESS_TIMEZONE

POLICY_VERSION = '2026-10-23.2'
CODE_TTL_SECONDS = 300
RESET_LIMIT = 2

TEMPLATES = {'register': 'REGISTER_TEMPLATE_ID', 'reset': 'RESET_TEMPLATE_ID',
             'verify_phone': 'VERIFY_TEMPLATE_ID'}


def capabilities():
    common = all(os.getenv('MY_CHUANNONG_SMS_' + key, '').strip()
                 for key in ('SECRET_ID', 'SECRET_KEY', 'APP_ID', 'SIGN_NAME'))
    purposes = [purpose for purpose, field in TEMPLATES.items()
                if common and os.getenv('MY_CHUANNONG_SMS_' + field, '').strip()]
    return {'availablePurposes': purposes, 'codeLength': 6, 'expiresIn': CODE_TTL_SECONDS,
            'retryAfter': 60, 'resetLimit': RESET_LIMIT, 'policyVersion': POLICY_VERSION}


def initialize(db):
    columns = {r[1] for r in db.execute('PRAGMA table_info(users)')}
    for key, declaration in [('phone_verified_at', 'INTEGER'), ('identity_encrypted', 'TEXT'),
                             ('student_identity_hash', 'TEXT'), ('identity_name_hash', 'TEXT'),
                             ('identity_status', "TEXT NOT NULL DEFAULT 'UNBOUND'"),
                             ('identity_updated_at', 'INTEGER'), ('identity_policy_version', 'TEXT')]:
        if key not in columns:
            db.execute(f'ALTER TABLE users ADD COLUMN {key} {declaration}')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS unique_student_identity ON users(student_identity_hash) WHERE student_identity_hash IS NOT NULL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS phone_challenges(
          id TEXT PRIMARY KEY, phone_hash TEXT NOT NULL, purpose TEXT NOT NULL,
          code_hash TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
          ip_hash TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
          consumed_at INTEGER, provider_request_id TEXT, provider_code TEXT,
          fee INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS phone_challenges_phone_time ON phone_challenges(phone_hash,created_at);
        CREATE INDEX IF NOT EXISTS phone_challenges_time ON phone_challenges(created_at);
        CREATE TABLE IF NOT EXISTS password_resets(
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          day TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,day));
        CREATE TABLE IF NOT EXISTS account_consents(
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          policy_version TEXT NOT NULL, accepted_at INTEGER NOT NULL, received_at INTEGER NOT NULL,
          PRIMARY KEY(user_id,policy_version));
        CREATE TABLE IF NOT EXISTS student_identity_reviews(
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          admin_id TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL,
          evidence_encrypted TEXT NOT NULL, created_at INTEGER NOT NULL);
    ''')


def consent(data):
    accepted = data.get('consentAcceptedAt')
    if (data.get('policyVersion') not in {POLICY_VERSION, '2026-10-23.3', '2026-10-26.1', '2026-11-08.1', '2026-11-16.1', '2026-11-17.1'} or type(accepted) is not int
            or not 1792684800000 <= accepted <= now_ms() + 300000):
        raise ApiError(422, 'CONSENT_REQUIRED', '请先阅读并同意当前用户协议与隐私政策')
    return accepted


def save_consent(db, user_id, data):
    db.execute('''INSERT INTO account_consents VALUES(?,?,?,?)
        ON CONFLICT(user_id,policy_version) DO NOTHING''',
        (user_id, data['policyVersion'], consent(data), now_ms()))


def confirmed_password(data):
    password = data.get('password')
    if not isinstance(password, str) or not 8 <= len(password) <= 200 or not password.strip():
        raise ApiError(422, 'WEAK_PASSWORD', '密码须为 8 至 200 位')
    if data.get('confirmPassword') != password:
        raise ApiError(422, 'PASSWORD_MISMATCH', '两次输入的密码不一致')
    return password


def keyed(api, kind, value):
    return hmac.new(api['phone_hash_secret'](), (kind + ':' + value).encode(), hashlib.sha256).hexdigest()


def send_tencent(phone, code, purpose):
    """Tencent SendSms 2021-01-11 with TC3; no retries after ambiguous outcomes.

    The approved template has two variables: code and validity in minutes.
    Only +86 destination, template variables and application metadata are sent.
    """
    if purpose not in TEMPLATES:
        raise ApiError(422, 'INVALID_SMS_PURPOSE', '验证码用途无效')
    names = ['SECRET_ID', 'SECRET_KEY', 'APP_ID', 'SIGN_NAME', TEMPLATES[purpose]]
    values = [os.getenv('MY_CHUANNONG_SMS_' + name, '').strip() for name in names]
    if not all(values):
        raise ApiError(503, 'SMS_NOT_CONFIGURED', '短信服务尚未配置完成')
    secret_id, secret_key, app_id, sign, template = values
    host = 'sms.tencentcloudapi.com'
    payload = json.dumps({'PhoneNumberSet': ['+86' + phone], 'SmsSdkAppId': app_id,
                          'SignName': sign, 'TemplateId': template,
                          'TemplateParamSet': [code, str(CODE_TTL_SECONDS // 60)]},
                         ensure_ascii=False, separators=(',', ':')).encode()
    timestamp = int(time.time())
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y-%m-%d')
    canonical_headers = f'content-type:application/json; charset=utf-8\nhost:{host}\n'
    signed_headers = 'content-type;host'
    canonical = '\n'.join(['POST', '/', '', canonical_headers, signed_headers, hashlib.sha256(payload).hexdigest()])
    scope = f'{date}/sms/tc3_request'
    to_sign = '\n'.join(['TC3-HMAC-SHA256', str(timestamp), scope, hashlib.sha256(canonical.encode()).hexdigest()])
    def sign_bytes(key, value):
        return hmac.new(key, value.encode(), hashlib.sha256).digest()
    signing_key = sign_bytes(sign_bytes(sign_bytes(('TC3' + secret_key).encode(), date), 'sms'), 'tc3_request')
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    request = urllib.request.Request('https://' + host, payload, headers={
        'Content-Type': 'application/json; charset=utf-8', 'Host': host,
        'Authorization': f'TC3-HMAC-SHA256 Credential={secret_id}/{scope}, SignedHeaders={signed_headers}, Signature={signature}',
        'X-TC-Action': 'SendSms', 'X-TC-Version': '2021-01-11', 'X-TC-Timestamp': str(timestamp),
        'X-TC-Region': os.getenv('MY_CHUANNONG_SMS_REGION', 'ap-guangzhou'),
    }, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read(65536))['Response']
        status = (result.get('SendStatusSet') or [{}])[0]
        provider_code = status.get('Code') or result.get('Error', {}).get('Code') or 'Unknown'
        # Never persist the provider Message or PhoneNumber: both may contain PII.
        return {'ok': provider_code == 'Ok', 'code': str(provider_code)[:100],
                'requestId': str(result.get('RequestId', ''))[:100], 'fee': int(status.get('Fee') or 0)}
    except Exception:
        # A timeout may still have delivered a billable message; do not resend.
        raise ApiError(503, 'SMS_DELIVERY_UNKNOWN', '短信发送结果暂未确认，请稍后重试') from None


def request_code(handler, api):
    data = handler.body_json()
    if set(data) - {'phone', 'purpose', 'policyVersion', 'consentAcceptedAt'}:
        raise ApiError(422, 'INVALID_SMS_REQUEST', '验证码请求字段无效')
    consent(data)
    phone = api['normalize_phone'](data.get('phone'))
    purpose = data.get('purpose')
    if purpose not in {'register', 'reset', 'verify_phone'}:
        raise ApiError(422, 'INVALID_SMS_PURPOSE', '验证码用途无效')
    phone_hash = api['phone_digest'](phone)
    ip_hash = keyed(api, 'sms-ip', handler.request_identity())
    at = now_ms()
    begin, _ = business_day_bounds_ms(datetime.fromtimestamp(at / 1000, BUSINESS_TIMEZONE).date())
    ident = secrets.token_urlsafe(24)
    code = f'{secrets.randbelow(1000000):06d}'
    handler.enforce_rate_limit('sms-request-ip', handler.request_identity(), 30, 3600)
    with api['connection']() as db:
        db.execute('BEGIN IMMEDIATE')
        user = db.execute("SELECT * FROM users WHERE phone_hash=? AND role='USER' AND account_type='PHONE'", (phone_hash,)).fetchone()
        if purpose == 'verify_phone':
            current = handler.current_user(db, 'USER')
            if not user or current['id'] != user['id']:
                raise ApiError(422, 'PHONE_MISMATCH', '请输入当前账号的注册手机号')
        recent = db.execute('SELECT MAX(created_at) FROM phone_challenges WHERE phone_hash=?', (phone_hash,)).fetchone()[0]
        if recent and at - recent < 60000:
            raise ApiError(429, 'SMS_COOLDOWN', '请在 60 秒后重新获取验证码')
        limit = RESET_LIMIT if purpose == 'reset' else 5
        count = db.execute('SELECT COUNT(*) FROM phone_challenges WHERE phone_hash=? AND purpose=? AND created_at>=?', (phone_hash, purpose, begin)).fetchone()[0]
        if count >= limit:
            raise ApiError(429, 'SMS_DAILY_LIMIT', '今日验证码次数已用完，请明天再试')
        ip_count = db.execute('SELECT COUNT(*) FROM phone_challenges WHERE ip_hash=? AND created_at>=?', (ip_hash, begin)).fetchone()[0]
        total = db.execute('SELECT COUNT(*) FROM phone_challenges WHERE created_at>=?', (begin,)).fetchone()[0]
        daily_cap = max(1, int(os.getenv('MY_CHUANNONG_SMS_DAILY_LIMIT', '500')))
        if ip_count >= 50 or total >= daily_cap:
            raise ApiError(429, 'SMS_SEND_LIMIT', '短信发送繁忙，请稍后再试')
        eligible = (not user if purpose == 'register' else bool(user and user['account_status'] in {'ACTIVE', 'LIMITED'}))
        db.execute('INSERT INTO phone_challenges(id,phone_hash,purpose,code_hash,created_at,expires_at,ip_hash,state) VALUES(?,?,?,?,?,?,?,?)',
                   (ident, phone_hash, purpose, keyed(api, 'sms-code', ident + ':' + code), at,
                    at + CODE_TTL_SECONDS * 1000, ip_hash, 'PENDING' if eligible else 'UNAVAILABLE'))
        db.commit()
    if eligible:
        try:
            result = send_tencent(phone, code, purpose)
        except ApiError as error:
            with api['connection']() as db:
                if error.code == 'SMS_NOT_CONFIGURED':
                    # No external request was made; do not spend the user's daily quota.
                    db.execute('DELETE FROM phone_challenges WHERE id=?', (ident,))
                else:
                    db.execute("UPDATE phone_challenges SET state='UNKNOWN' WHERE id=?", (ident,))
            raise
        with api['connection']() as db:
            db.execute('UPDATE phone_challenges SET state=?,provider_request_id=?,provider_code=?,fee=? WHERE id=?',
                       ('SENT' if result['ok'] else 'FAILED', result['requestId'], result['code'], result['fee'], ident))
        if not result['ok']:
            raise ApiError(503, 'SMS_SEND_FAILED', '验证码发送失败，请稍后重试')
    return {'challengeId': ident, 'expiresIn': CODE_TTL_SECONDS, 'retryAfter': 60}


def consume(db, data, phone_hash, purpose, api):
    """Caller holds BEGIN IMMEDIATE. Wrong-attempt updates survive the error."""
    ident, code = data.get('challengeId'), data.get('code')
    if not isinstance(ident, str) or not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
        raise ApiError(422, 'SMS_CODE_REQUIRED', '请输入手机收到的 6 位验证码')
    row = db.execute('SELECT * FROM phone_challenges WHERE id=? AND phone_hash=? AND purpose=?', (ident, phone_hash, purpose)).fetchone()
    if not row or row['state'] != 'SENT' or row['consumed_at'] or row['expires_at'] <= now_ms() or row['attempts'] >= 5:
        raise ApiError(422, 'SMS_CODE_INVALID', '验证码已失效，请重新获取')
    if not hmac.compare_digest(row['code_hash'], keyed(api, 'sms-code', ident + ':' + code)):
        db.execute('UPDATE phone_challenges SET attempts=attempts+1 WHERE id=?', (ident,))
        db.commit()
        raise ApiError(422, 'SMS_CODE_INVALID', '验证码不正确')
    db.execute("UPDATE phone_challenges SET consumed_at=?,state='CONSUMED' WHERE id=?", (now_ms(), ident))


def reset_password(handler, api):
    data = handler.body_json()
    consent(data)
    password = confirmed_password(data)
    phone = api['normalize_phone'](data.get('phone'))
    handler.enforce_rate_limit('reset-ip', handler.request_identity(), 20, 300)
    digest, salt = api['password_record'](password)
    at = now_ms()
    day = datetime.fromtimestamp(at / 1000, BUSINESS_TIMEZONE).date().isoformat()
    with api['connection']() as db:
        db.execute('BEGIN IMMEDIATE')
        user = db.execute("SELECT * FROM users WHERE phone_hash=? AND role='USER' AND account_type='PHONE' AND account_status IN ('ACTIVE','LIMITED')", (api['phone_digest'](phone),)).fetchone()
        if not user:
            raise ApiError(422, 'SMS_CODE_INVALID', '验证码无效或账号不可用')
        used = db.execute('SELECT count FROM password_resets WHERE user_id=? AND day=?', (user['id'], day)).fetchone()
        if used and used[0] >= RESET_LIMIT:
            raise ApiError(429, 'RESET_DAILY_LIMIT', '今日已找回两次，请明天再试，账号仍可正常登录')
        consume(db, data, user['phone_hash'], 'reset', api)
        db.execute('UPDATE users SET password_hash=?,password_salt=?,phone_verified_at=COALESCE(phone_verified_at,?) WHERE id=?', (digest, salt, at, user['id']))
        db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
        db.execute('INSERT INTO password_resets VALUES(?,?,1) ON CONFLICT(user_id,day) DO UPDATE SET count=count+1', (user['id'], day))
        save_consent(db, user['id'], data)
        api['write_user_event'](db, user['id'], 'ACCOUNT', 'PASSWORD_RESET')
    return {'reset': True, 'loginRequired': True, 'remainingToday': RESET_LIMIT - (used[0] if used else 0) - 1}


def verify_phone(handler, api):
    data = handler.body_json()
    with api['connection']() as db:
        db.execute('BEGIN IMMEDIATE')
        user = handler.current_user(db, 'USER')
        if user['account_type'] != 'PHONE':
            raise ApiError(403, 'ACCOUNT_REQUIRED', '请先登录平台账号')
        consume(db, data, user['phone_hash'], 'verify_phone', api)
        db.execute('UPDATE users SET phone_verified_at=COALESCE(phone_verified_at,?) WHERE id=?', (now_ms(), user['id']))
        save_consent(db, user['id'], data)
        return api['serialize_user'](db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone())


def identity_fields(row, api):
    data = dict(row)
    result = {'studentId': None, 'realName': None, 'identityStatus': data.get('identity_status', 'UNBOUND'),
              'phoneVerified': bool(data.get('phone_verified_at'))}
    if data.get('identity_encrypted'):
        try:
            result.update(json.loads(api['pii_cipher']().decrypt(data['identity_encrypted'].encode())))
        except Exception:
            raise ApiError(503, 'IDENTITY_READ_FAILED', '身份资料暂时无法读取') from None
    return result


def bind_identity(handler, api):
    data = handler.body_json()
    if set(data) - {'studentId', 'realName', 'schoolCode', 'policyVersion', 'consentAcceptedAt'}:
        raise ApiError(422, 'INVALID_IDENTITY', '仅可提交姓名与学号，不可提交学校凭据')
    consent(data)
    student = str(data.get('studentId', '')).strip().upper()
    name = str(data.get('realName', '')).strip()
    if data.get('schoolCode') != 'SICAU' or not re.fullmatch(r'[A-Z0-9]{6,20}', student) or not 2 <= len(name) <= 40 or any(ord(c) < 32 for c in name):
        raise ApiError(422, 'INVALID_IDENTITY', '请核对姓名和四川农业大学学号')
    identity_hash = keyed(api, 'student:SICAU', student)
    encrypted = api['pii_cipher']().encrypt(json.dumps({'studentId': student, 'realName': name}, ensure_ascii=False).encode()).decode()
    with api['connection']() as db:
        db.execute('BEGIN IMMEDIATE')
        user = handler.current_user(db, 'USER')
        if user['account_type'] != 'PHONE' or not user['phone_verified_at']:
            raise ApiError(403, 'PHONE_VERIFICATION_REQUIRED', '请先验证当前账号的手机号')
        if user['student_identity_hash']:
            existing = identity_fields(user, api)
            if user['student_identity_hash'] == identity_hash and existing['realName'] == name:
                return existing
            raise ApiError(409, 'IDENTITY_ALREADY_BOUND', '账号已绑定学号，如需更正请联系管理员')
        try:
            db.execute("""UPDATE users SET identity_encrypted=?,student_identity_hash=?,identity_name_hash=?,
                identity_status='PENDING',identity_updated_at=?,identity_policy_version=? WHERE id=?""",
                (encrypted, identity_hash, keyed(api, 'student-name', name), now_ms(), data['policyVersion'], user['id']))
        except sqlite3.IntegrityError:
            raise ApiError(409, 'STUDENT_ALREADY_BOUND', '该学号已绑定其他账号，请使用原账号或找回密码') from None
        save_consent(db, user['id'], data)
        api['write_user_event'](db, user['id'], 'ACCOUNT', 'STUDENT_IDENTITY_SUBMITTED')
        return identity_fields(db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone(), api)


def stats(db):
    begin, _ = business_day_bounds_ms()
    rows = [dict(r) for r in db.execute('''SELECT purpose,COUNT(*) requests,
        SUM(state IN ('SENT','CONSUMED')) submitted,SUM(fee) billedSegments,
        SUM(consumed_at IS NOT NULL) completed FROM phone_challenges WHERE created_at>=? GROUP BY purpose''', (begin,))]
    configured = all(os.getenv('MY_CHUANNONG_SMS_' + key, '').strip() for key in
                     ['SECRET_ID','SECRET_KEY','APP_ID','SIGN_NAME','REGISTER_TEMPLATE_ID','RESET_TEMPLATE_ID','VERIFY_TEMPLATE_ID'])
    return {'configured': configured, 'today': rows, 'dailyLimit': int(os.getenv('MY_CHUANNONG_SMS_DAILY_LIMIT', '500')),
            'resetLimit': RESET_LIMIT, 'policyVersion': POLICY_VERSION}


def review_identity(db, user_id, admin_id, data, api):
    """An operator must verify independent evidence before approving a binding."""
    action = data.get('action')
    reason = str(data.get('reason', '')).strip()
    if action not in {'approve', 'reject'} or not 4 <= len(reason) <= 300:
        raise ApiError(422, 'IDENTITY_REVIEW_REQUIRED', '请选择核验结果并填写核验依据或驳回原因')
    db.execute('BEGIN IMMEDIATE')
    user = db.execute("SELECT * FROM users WHERE id=? AND role='USER' AND account_status IN ('ACTIVE','LIMITED')", (user_id,)).fetchone()
    if not user or user['identity_status'] != 'PENDING':
        raise ApiError(409, 'IDENTITY_REVIEW_CONFLICT', '身份资料已变化，请刷新后重试')
    if action == 'approve':
        db.execute("UPDATE users SET identity_status='VERIFIED',identity_updated_at=? WHERE id=?", (now_ms(), user_id))
    else:
        db.execute("""UPDATE users SET identity_status='UNBOUND',identity_encrypted=NULL,
            student_identity_hash=NULL,identity_name_hash=NULL,identity_updated_at=? WHERE id=?""", (now_ms(),user_id))
    review_id = secrets.token_hex(16)
    db.execute('INSERT INTO student_identity_reviews VALUES(?,?,?,?,?,?)',
               (review_id,user_id,admin_id,action,api['pii_cipher']().encrypt(reason.encode()).decode(),now_ms()))
    # Only outcome and an opaque audit reference; identity originals are not logged.
    api['write_audit'](db,admin_id,'STUDENT_IDENTITY_REVIEW','USER',user_id,{'action':action,'reviewId':review_id})
    return {'reviewed': True, 'identityStatus': 'VERIFIED' if action == 'approve' else 'UNBOUND'}


def forget(db, user):
    db.execute('DELETE FROM student_identity_reviews WHERE user_id=?', (user['id'],))
    db.execute('DELETE FROM phone_challenges WHERE phone_hash=?', (user['phone_hash'],))
    db.execute('DELETE FROM password_resets WHERE user_id=?', (user['id'],))
    db.execute('DELETE FROM account_consents WHERE user_id=?', (user['id'],))
    db.execute("""UPDATE users SET identity_encrypted=NULL,student_identity_hash=NULL,identity_name_hash=NULL,
        identity_status='UNBOUND',identity_updated_at=NULL,identity_policy_version=NULL,phone_verified_at=NULL WHERE id=?""", (user['id'],))
