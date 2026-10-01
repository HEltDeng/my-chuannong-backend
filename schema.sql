PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'USER' CHECK (role IN ('USER', 'ADMIN')),
    account_type TEXT NOT NULL DEFAULT 'DEVICE',
    user_code TEXT,
    nickname TEXT NOT NULL DEFAULT '川农同学',
    phone_hash TEXT UNIQUE,
    phone_masked TEXT,
    phone_encrypted TEXT,
    student_id TEXT,
    real_name TEXT,
    campus_verified INTEGER NOT NULL DEFAULT 0,
    campus_verified_at INTEGER,
    reservation_student_id TEXT,
    reservation_name TEXT,
    reservation_profile_consented_at INTEGER,
    reservation_student_id_hash TEXT,
    reservation_name_hash TEXT,
    reservation_profile_version INTEGER NOT NULL DEFAULT 0,
    reservation_profile_updated_at INTEGER,
    admin_permissions_json TEXT NOT NULL DEFAULT '[]',
    admin_role TEXT,
    admin_enabled INTEGER NOT NULL DEFAULT 1,
    account_status TEXT NOT NULL DEFAULT 'ACTIVE',
    account_status_reason TEXT NOT NULL DEFAULT '',
    account_status_changed_at INTEGER,
    account_status_expires_at INTEGER,
    last_active_at INTEGER,
    first_login_at INTEGER,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY,
    sport TEXT NOT NULL UNIQUE CHECK (sport IN ('BADMINTON', 'TABLE_TENNIS')),
    name TEXT NOT NULL,
    unit_label TEXT NOT NULL,
    court_count INTEGER NOT NULL CHECK (court_count > 0),
    price_yuan INTEGER NOT NULL CHECK (price_yuan >= 0),
    enabled INTEGER NOT NULL DEFAULT 1,
    operating_status TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (operating_status IN ('OPEN', 'CLOSED', 'MAINTENANCE')),
    maintenance_note TEXT NOT NULL DEFAULT '',
    expected_restore_at INTEGER,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS venue_time_slots (
    id TEXT PRIMARY KEY,
    venue_id TEXT NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE (venue_id, start_time, end_time)
);

CREATE TABLE IF NOT EXISTS blocked_inventory (
    id TEXT PRIMARY KEY,
    venue_id TEXT NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
    booking_date TEXT NOT NULL,
    time_slot TEXT NOT NULL,
    court_number INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '场馆暂不可用',
    UNIQUE (venue_id, booking_date, time_slot, court_number)
);

CREATE TABLE IF NOT EXISTS reservations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    venue_id TEXT NOT NULL REFERENCES venues(id),
    booking_date TEXT NOT NULL,
    time_slot TEXT NOT NULL,
    court_number INTEGER NOT NULL,
    price_yuan INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CANCELLED')),
    payment_status TEXT NOT NULL DEFAULT 'SIMULATED' CHECK (payment_status IN ('SIMULATED')),
    refund_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
    reservation_code TEXT,
    verification_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED',
    reservation_student_id_snapshot TEXT,
    reservation_name_snapshot TEXT,
    cancelled_at INTEGER,
    cancelled_by TEXT,
    cancel_reason TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS reservations_unique_active_inventory
ON reservations(venue_id, booking_date, time_slot, court_number)
WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS reservations_user_created
ON reservations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS reservations_admin_filter
ON reservations(booking_date, venue_id, status, verification_status, created_at DESC);
CREATE INDEX IF NOT EXISTS users_reservation_student_id_hash
ON users(reservation_student_id_hash) WHERE reservation_student_id_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS users_reservation_name_hash
ON users(reservation_name_hash) WHERE reservation_name_hash IS NOT NULL;

-- A trusted, app-scoped device identity. The raw Android ID and the client
-- derived value are never stored here; only the server HMAC is retained.
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    device_key_hash TEXT NOT NULL UNIQUE,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    first_app_version_name TEXT,
    first_app_version_code INTEGER,
    last_app_version_name TEXT,
    last_app_version_code INTEGER,
    install_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS devices_last_seen
ON devices(last_seen_at DESC);

CREATE TABLE IF NOT EXISTS installations (
    installation_id TEXT PRIMARY KEY,
    device_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    first_app_version_name TEXT,
    first_app_version_code INTEGER,
    last_app_version_name TEXT,
    last_app_version_code INTEGER,
    legacy INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS installations_device_seen
ON installations(device_id, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS user_devices (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    PRIMARY KEY(user_id, device_id)
);

CREATE INDEX IF NOT EXISTS user_devices_device
ON user_devices(device_id, last_seen_at DESC);

-- Registration minutes are deliberately never decremented. A failed insert
-- may leave a gap, but a business number is never reused.
CREATE TABLE IF NOT EXISTS user_code_sequences (
    registration_minute TEXT PRIMARY KEY,
    next_sequence INTEGER NOT NULL CHECK (next_sequence BETWEEN 1 AND 100000)
);

CREATE TABLE IF NOT EXISTS lost_found_posts (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(id),
    client_local_id TEXT,
    type TEXT NOT NULL CHECK (type IN ('LOST', 'FOUND')),
    item_name TEXT NOT NULL,
    category TEXT NOT NULL,
    location TEXT NOT NULL,
    event_date TEXT NOT NULL,
    approximate_time TEXT,
    description TEXT NOT NULL DEFAULT '',
    claim_note TEXT NOT NULL DEFAULT '',
    image_name TEXT,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(owner_user_id, client_local_id)
);

CREATE INDEX IF NOT EXISTS lost_found_updated ON lost_found_posts(updated_at DESC);
CREATE INDEX IF NOT EXISTS lost_found_owner ON lost_found_posts(owner_user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS lost_found_deletion_tombstones (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_local_id TEXT NOT NULL,
    deleted_by_user_id TEXT,
    reason TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    UNIQUE(owner_user_id, client_local_id)
);

CREATE INDEX IF NOT EXISTS lost_found_tombstone_owner
ON lost_found_deletion_tombstones(owner_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS lost_found_favorites (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    post_id TEXT NOT NULL REFERENCES lost_found_posts(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(user_id, post_id)
);

CREATE TABLE IF NOT EXISTS lost_found_claims (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    post_id TEXT NOT NULL REFERENCES lost_found_posts(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(user_id, post_id)
);

CREATE TABLE IF NOT EXISTS lost_found_reports (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    post_id TEXT NOT NULL REFERENCES lost_found_posts(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'REVIEWED', 'CLOSED')),
    created_at INTEGER NOT NULL,
    UNIQUE(user_id, post_id)
);

CREATE TABLE IF NOT EXISTS announcements (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    service_visible INTEGER NOT NULL DEFAULT 1,
    notification_enabled INTEGER NOT NULL DEFAULT 0,
    popup_enabled INTEGER NOT NULL DEFAULT 0,
    published_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Website announcements are intentionally isolated from App announcements.
-- `enabled` controls publication on the static website only; App's
-- `service_visible` flag remains scoped to the App service-updates feed.
CREATE TABLE IF NOT EXISTS website_announcements (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    published_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS website_announcements_listing
ON website_announcements(enabled, published_at DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS app_versions (
    platform TEXT PRIMARY KEY,
    latest_version_code INTEGER NOT NULL,
    minimum_version_code INTEGER NOT NULL,
    latest_version_name TEXT NOT NULL,
    update_message TEXT NOT NULL DEFAULT '',
    download_url TEXT,
    apk_sha256 TEXT,
    apk_size INTEGER,
    package_name TEXT,
    signing_certificate_sha256 TEXT,
    metadata_verified INTEGER NOT NULL DEFAULT 0,
    force_update INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'PUBLISHED', 'DISABLED')),
    published_at INTEGER,
    created_at INTEGER,
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_at INTEGER NOT NULL
);

-- Release metadata above is an immutable publication fact.  Live update
-- controls live here so an operator can enable, disable, or revoke a forced
-- update without rewriting the release record.
CREATE TABLE IF NOT EXISTS app_update_policies (
    platform TEXT PRIMARY KEY,
    update_enabled INTEGER NOT NULL DEFAULT 0,
    force_update_enabled INTEGER NOT NULL DEFAULT 0,
    minimum_supported_version_code INTEGER NOT NULL DEFAULT 1,
    policy_revision INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL,
    updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    CHECK (minimum_supported_version_code >= 1),
    CHECK (policy_revision >= 1)
);

-- Dynamic App configuration is deliberately separate from release metadata and
-- update policy.  A configuration revision can change the existing UI
-- catalogue without changing the APK bytes or publishing a new release.
CREATE TABLE IF NOT EXISTS app_config_revisions (
    revision INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL DEFAULT 'android',
    schema_version INTEGER NOT NULL,
    config_version INTEGER NOT NULL,
    min_app_version_code INTEGER NOT NULL DEFAULT 0,
    max_app_version_code INTEGER,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')),
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL,
    updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(platform, config_version),
    CHECK (schema_version >= 1),
    CHECK (config_version >= 1),
    CHECK (min_app_version_code >= 0),
    CHECK (max_app_version_code IS NULL OR max_app_version_code >= min_app_version_code)
);

CREATE INDEX IF NOT EXISTS app_config_revisions_listing
ON app_config_revisions(platform, status, config_version DESC);

CREATE TABLE IF NOT EXISTS app_config_state (
    platform TEXT PRIMARY KEY,
    active_revision INTEGER,
    config_version INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY(active_revision) REFERENCES app_config_revisions(revision)
);

-- Published release history feeds the admin minimum-version selector. The
-- app_versions row remains the effective policy for backward compatibility.
CREATE TABLE IF NOT EXISTS app_release_versions (
    platform TEXT NOT NULL,
    version_code INTEGER NOT NULL,
    version_name TEXT NOT NULL,
    update_message TEXT NOT NULL DEFAULT '',
    download_url TEXT,
    apk_sha256 TEXT,
    apk_size INTEGER,
    package_name TEXT,
    signing_certificate_sha256 TEXT,
    metadata_verified INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'PUBLISHED', 'DISABLED')),
    published_at INTEGER,
    created_at INTEGER NOT NULL,
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(platform, version_code),
    UNIQUE(platform, version_name)
);

CREATE INDEX IF NOT EXISTS app_release_versions_listing
ON app_release_versions(platform, status, version_code DESC);

CREATE TABLE IF NOT EXISTS remote_flags (
    key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    change_note TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS remote_flags_updated
ON remote_flags(updated_at DESC);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id),
    category TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    app_version TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'PROCESSING', 'CLOSED')),
    workflow_status TEXT NOT NULL DEFAULT 'OPEN',
    public_reply TEXT NOT NULL DEFAULT '',
    internal_note TEXT NOT NULL DEFAULT '',
    handled_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    resolved_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS feedback_user_created
ON feedback(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS feedback_events (
    id TEXT PRIMARY KEY,
    feedback_id TEXT NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,
    actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT,
    public_visible INTEGER NOT NULL DEFAULT 0,
    public_message TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS feedback_events_feedback
ON feedback_events(feedback_id, created_at ASC);

CREATE TABLE IF NOT EXISTS reservation_rules (
    id TEXT PRIMARY KEY CHECK (id = 'default'),
    max_active_per_day INTEGER NOT NULL DEFAULT 2,
    max_user_cancellations_per_day INTEGER NOT NULL DEFAULT 2,
    cancel_cutoff_minutes INTEGER NOT NULL DEFAULT 30,
    restriction_hours_after_excess_cancel INTEGER NOT NULL DEFAULT 24,
    require_reservation_student_id INTEGER NOT NULL DEFAULT 0,
    reservation_profile_mode TEXT NOT NULL DEFAULT 'OPTIONAL'
        CHECK (reservation_profile_mode IN ('OFF', 'OPTIONAL', 'REQUIRED')),
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_restrictions (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    reservation_blocked_until INTEGER,
    cancellation_blocked_until INTEGER,
    reason TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reservation_events (
    id TEXT PRIMARY KEY,
    reservation_id TEXT REFERENCES reservations(id) ON DELETE SET NULL,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS reservation_events_reservation
ON reservation_events(reservation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id TEXT PRIMARY KEY,
    actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS admin_audit_created
ON admin_audit_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS user_behavior_events (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    module TEXT NOT NULL,
    event_type TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('SUCCESS', 'FAILURE', 'DENIED')),
    target_type TEXT,
    target_id TEXT,
    client_source TEXT NOT NULL DEFAULT 'APP',
    client_version TEXT,
    risk_flag TEXT,
    device_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
    installation_id TEXT REFERENCES installations(installation_id) ON DELETE SET NULL,
    identity_kind TEXT NOT NULL DEFAULT 'LEGACY'
        CHECK (identity_kind IN ('USER', 'DEVICE', 'LEGACY')),
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS user_behavior_events_created
ON user_behavior_events(created_at DESC);
CREATE INDEX IF NOT EXISTS user_behavior_events_user_created
ON user_behavior_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS user_behavior_events_filter
ON user_behavior_events(module, event_type, result, created_at DESC);

CREATE TABLE IF NOT EXISTS risk_candidates (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rule_key TEXT NOT NULL,
    rule_label TEXT NOT NULL,
    trigger_count INTEGER NOT NULL DEFAULT 1,
    window_started_at INTEGER NOT NULL,
    window_ended_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'WATCHING', 'IGNORED', 'CLOSED')),
    admin_note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id, rule_key, window_started_at)
);

CREATE INDEX IF NOT EXISTS risk_candidates_status_updated
ON risk_candidates(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS resource_items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    audience_scope TEXT NOT NULL DEFAULT '',
    publisher_label TEXT NOT NULL DEFAULT '我的川农资料库',
    source_label TEXT NOT NULL DEFAULT '',
    usage_note TEXT NOT NULL DEFAULT '',
    resource_type TEXT NOT NULL DEFAULT 'EXTERNAL_LINK',
    external_url TEXT NOT NULL,
    cover_url TEXT,
    publish_status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (publish_status IN ('DRAFT', 'PUBLISHED', 'UNLISTED')),
    visibility TEXT NOT NULL DEFAULT 'MEMBERS'
        CHECK (visibility IN ('MEMBERS', 'PUBLIC')),
    is_free INTEGER NOT NULL DEFAULT 1,
    future_price_fen INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    published_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS resource_items_listing
ON resource_items(publish_status, sort_order DESC, published_at DESC);

CREATE TABLE IF NOT EXISTS resource_favorites (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id TEXT NOT NULL REFERENCES resource_items(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(user_id, resource_id)
);

CREATE TABLE IF NOT EXISTS resource_views (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id TEXT NOT NULL REFERENCES resource_items(id) ON DELETE CASCADE,
    view_count INTEGER NOT NULL DEFAULT 1,
    last_viewed_at INTEGER NOT NULL,
    PRIMARY KEY(user_id, resource_id)
);

CREATE TABLE IF NOT EXISTS resource_complaints (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id TEXT NOT NULL REFERENCES resource_items(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'PROCESSING', 'RESOLVED', 'CLOSED')),
    resolution_note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS resource_complaints_status
ON resource_complaints(status, created_at DESC);

CREATE TABLE IF NOT EXISTS resource_unlocks (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id TEXT NOT NULL REFERENCES resource_items(id) ON DELETE CASCADE,
    unlock_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED',
    price_fen INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(user_id, resource_id)
);
