"""Opt-in module notices. Public bootstrap never authorizes notification delivery."""
from core import ApiError, now_ms

MODULES = {
    'general': '综合公告', 'secondhand': '二手好物', 'lost-found': '失物招领',
    'resources': '资料库', 'driving_school': '驾校', 'ielts_toefl': '雅思托福',
    'civil_postgrad': '考公考研', 'gym': '健身房', 'errand': '跑腿代取',
    'logistics_repair': '后勤报修', 'electric': '电费查询指南',
}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS notification_follows(
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        module_id TEXT NOT NULL,
        followed_at INTEGER NOT NULL,
        PRIMARY KEY(user_id,module_id)
    )''')


def route(handler, method, path, connection):
    with connection() as db:
        user = handler.current_user(db, 'USER')
        if method == 'GET' and path == '/api/v1/notifications/follows':
            followed = dict(db.execute('SELECT module_id,followed_at FROM notification_follows WHERE user_id=?', (user['id'],)))
            return {'accountId': user['id'], 'items': [
                {'module': key, 'title': title, 'following': key in followed, 'followedAt': followed.get(key)}
                for key, title in MODULES.items()
            ]}, 200
        if method == 'PUT' and path.startswith('/api/v1/notifications/follows/'):
            module = path.removeprefix('/api/v1/notifications/follows/')
            if module not in MODULES:
                raise ApiError(422, 'VALIDATION_ERROR', '关注板块无效')
            data = handler.body_json()
            if set(data) != {'following'} or type(data['following']) is not bool:
                raise ApiError(422, 'VALIDATION_ERROR', '请明确选择是否关注')
            handler.enforce_rate_limit('notification-follow', user['id'], 120, 3600)
            if data['following']:
                # Retrying a successful follow does not reset its start time.
                db.execute('INSERT OR IGNORE INTO notification_follows VALUES(?,?,?)', (user['id'], module, now_ms()))
            else:
                db.execute('DELETE FROM notification_follows WHERE user_id=? AND module_id=?', (user['id'], module))
            row = db.execute('SELECT followed_at FROM notification_follows WHERE user_id=? AND module_id=?', (user['id'], module)).fetchone()
            return {'accountId': user['id'], 'module': module, 'title': MODULES[module],
                    'following': bool(row), 'followedAt': row['followed_at'] if row else None}, 200
        if method == 'GET' and path == '/api/v1/notifications/feed':
            rows = db.execute('''SELECT a.* FROM announcements a
                JOIN notification_follows f ON f.module_id=a.module_id AND f.user_id=?
                WHERE a.enabled=1 AND a.published_at>=f.followed_at
                  AND (a.notification_enabled=1 OR a.popup_enabled=1)
                ORDER BY a.published_at DESC,a.id LIMIT 50''', (user['id'],))
            return {'accountId': user['id'], 'items': [
                {'id': r['id'], 'module': r['module_id'], 'title': r['title'], 'content': r['content'],
                 'publishedAt': r['published_at'], 'notificationEnabled': bool(r['notification_enabled']),
                 'popupEnabled': bool(r['popup_enabled'])}
                for r in rows
            ]}, 200
    raise ApiError(404, 'NOT_FOUND', '接口不存在')
