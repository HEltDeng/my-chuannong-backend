"""Native iOS release metadata. APK rows and Android force policies never enter this contract."""
import json
import re
from urllib.parse import urlsplit
from core import ApiError, now_ms


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS ios_release_settings(
        id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, updated_at INTEGER NOT NULL)''')


def state(db):
    row = db.execute('SELECT payload,updated_at FROM ios_release_settings WHERE id=1').fetchone()
    if not row:
        return {'platform': 'ios', 'published': False, 'bundleId': 'com.mychuannong.app',
                'versionName': None, 'buildNumber': None, 'minimumBuildNumber': None,
                'storeUrl': None, 'releaseNotes': '', 'updatedAt': None}
    return {**json.loads(row['payload']), 'platform': 'ios', 'updatedAt': row['updated_at']}


def public_state(db):
    value = state(db)
    return value if value['published'] else {
        'platform': 'ios', 'published': False, 'bundleId': value['bundleId']}


def route(handler, method, path, connection, audit):
    with connection() as db:
        if method == 'GET' and path == '/api/v1/public/ios-release':
            return public_state(db), 200
        admin = handler.current_user(db, 'ADMIN')
        if admin['admin_role'] != 'SUPER_ADMIN':
            raise ApiError(403, 'ADMIN_MODULE_FORBIDDEN', '当前管理员无权管理 iOS 版本')
        if method == 'GET':
            return state(db), 200
        if method != 'PUT':
            raise ApiError(405, 'METHOD_NOT_ALLOWED', '不支持的操作')
        data = handler.body_json()
        required = {'published', 'bundleId', 'versionName', 'buildNumber', 'minimumBuildNumber', 'storeUrl', 'releaseNotes'}
        if set(data) != required or type(data['published']) is not bool:
            raise ApiError(422, 'INVALID_IOS_RELEASE', 'iOS 版本字段不完整')
        if data['bundleId'] != 'com.mychuannong.app':
            raise ApiError(422, 'INVALID_IOS_RELEASE', '请选择我的川农正式 iOS 应用')
        if not isinstance(data['versionName'], str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}', data['versionName']):
            raise ApiError(422, 'INVALID_IOS_RELEASE', '版本名称格式无效')
        if any(type(data[k]) is not int for k in ['buildNumber', 'minimumBuildNumber']) or not 1 <= data['minimumBuildNumber'] <= data['buildNumber'] <= 2147483647:
            raise ApiError(422, 'INVALID_IOS_RELEASE', 'iOS 构建号范围无效')
        if not isinstance(data['releaseNotes'], str) or len(data['releaseNotes']) > 4000:
            raise ApiError(422, 'INVALID_IOS_RELEASE', '版本说明最多 4000 字')
        if not isinstance(data['storeUrl'], str):
            raise ApiError(422, 'INVALID_IOS_RELEASE', 'App Store 地址无效')
        try:
            target = urlsplit(data['storeUrl'])
            valid = (target.scheme == 'https' and target.hostname == 'apps.apple.com' and
                     target.port in {None, 443} and not target.username and not target.password and
                     not target.query and not target.fragment and re.search(r'/id\d+$', target.path))
        except ValueError:
            valid = False
        if not valid:
            raise ApiError(422, 'INVALID_IOS_RELEASE', '请使用 Apple App Store 正式应用地址')
        previous = state(db)
        if previous.get('buildNumber') and data['buildNumber'] < previous['buildNumber']:
            raise ApiError(409, 'IOS_BUILD_ROLLBACK', 'iOS 构建号不可回退')
        handler.enforce_rate_limit('ios-release-update', admin['id'], 30, 3600)
        db.execute('INSERT INTO ios_release_settings VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',
                   (json.dumps(data, ensure_ascii=False), now_ms()))
        audit(db, admin['id'], 'IOS_RELEASE_UPDATED', 'ios_release', '1', {'before': previous, 'after': state(db)})
        return state(db), 200
