"""Super-admin managed QQ group entry. No school/session data is accepted here."""
import json
import re
from urllib.parse import parse_qs, urlsplit

from core import ApiError, now_ms


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS qq_group_settings(
        id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, updated_at INTEGER NOT NULL)''')


def state(db):
    row = db.execute('SELECT payload,updated_at FROM qq_group_settings WHERE id=1').fetchone()
    if not row:
        return dict(enabled=False, groupNumber='', joinUrl='', updatedAt=None)
    return {**json.loads(row['payload']), 'updatedAt': row['updated_at']}


def public_state(db):
    value = state(db)
    return value if value['enabled'] else {'enabled': False}


def valid_join_url(raw, group):
    if not raw or len(raw) > 2048 or any(c.isspace() or ord(c) < 32 for c in raw):
        return False
    try:
        url = urlsplit(raw)
        if url.username or url.password or url.fragment or url.port not in {None, 443}:
            return False
        if url.scheme == 'https':
            return url.hostname in {'qun.qq.com', 'qm.qq.com'} and url.path not in {'', '/'}
        if url.scheme == 'mqqapi' and url.netloc == 'card' and url.path == '/show_pslcard':
            query = parse_qs(url.query, keep_blank_values=True)
            return (query.get('card_type') == ['group'] and query.get('uin') == [group]
                    and all(len(values) == 1 for values in query.values())
                    and set(query) <= {'src_type', 'version', 'uin', 'card_type', 'source', 'auth', 'key'})
    except ValueError:
        pass
    return False


def route(handler, method, path, connection, audit):
    with connection() as db:
        if path == '/api/v1/public/qq-group':
            if method != 'GET':
                raise ApiError(405, 'METHOD_NOT_ALLOWED', '不支持的操作')
            return public_state(db), 200
        admin = handler.current_user(db, 'ADMIN')
        if admin['admin_role'] != 'SUPER_ADMIN':
            raise ApiError(403, 'ADMIN_MODULE_FORBIDDEN', '当前管理员无权管理 QQ 聊天群')
        if method == 'GET':
            return state(db), 200
        if method != 'PUT':
            raise ApiError(405, 'METHOD_NOT_ALLOWED', '不支持的操作')
        data = handler.body_json()
        if (set(data) != {'enabled', 'groupNumber', 'joinUrl'} or type(data['enabled']) is not bool
                or not isinstance(data['groupNumber'], str) or not isinstance(data['joinUrl'], str)):
            raise ApiError(422, 'INVALID_QQ_GROUP', 'QQ群配置字段无效')
        data = {**data, 'groupNumber': data['groupNumber'].strip(), 'joinUrl': data['joinUrl'].strip()}
        if ((data['enabled'] or data['groupNumber']) and not re.fullmatch(r'[1-9][0-9]{4,11}', data['groupNumber'])):
            raise ApiError(422, 'INVALID_QQ_GROUP', '请填写有效的 QQ 群号')
        if ((data['enabled'] or data['joinUrl']) and not valid_join_url(data['joinUrl'], data['groupNumber'])):
            raise ApiError(422, 'INVALID_QQ_GROUP', '请填写 QQ 官方加群链接，唤起链接的群号须与配置一致')
        previous = state(db)
        handler.enforce_rate_limit('qq-group-update', admin['id'], 30, 3600)
        db.execute('INSERT INTO qq_group_settings VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',
                   (json.dumps(data, ensure_ascii=False), now_ms()))
        audit(db, admin['id'], 'QQ_GROUP_UPDATED', 'qq_group', '1', {'before': previous, 'after': state(db)})
        return state(db), 200
