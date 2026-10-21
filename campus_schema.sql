CREATE TABLE IF NOT EXISTS campus_posts (
 id TEXT PRIMARY KEY, module TEXT NOT NULL CHECK(module IN ('secondhand','lost-found')),
 owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 client_id TEXT NOT NULL, title TEXT NOT NULL, campus TEXT NOT NULL,
 payload_json TEXT NOT NULL, contact_encrypted TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PUBLISHED' CHECK(status IN ('PENDING','PUBLISHED','VIOLATION','COMPLETED','HIDDEN','DELETED')),
 moderation_note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(owner_id,module,client_id)
);
CREATE INDEX IF NOT EXISTS campus_posts_feed ON campus_posts(module,status,created_at DESC);
CREATE TABLE IF NOT EXISTS campus_providers (
 id TEXT PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL,
 campus TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
 phone TEXT NOT NULL DEFAULT '', image TEXT NOT NULL DEFAULT '',
 details_json TEXT NOT NULL DEFAULT '{}', sort_order INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','PUBLISHED','UNLISTED')),
 is_demo INTEGER NOT NULL DEFAULT 0, booking_enabled INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS campus_bookings (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 provider_id TEXT NOT NULL REFERENCES campus_providers(id), client_id TEXT NOT NULL,
 contact_encrypted TEXT NOT NULL, course TEXT NOT NULL, preferred_date TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'PENDING'
 CHECK(status IN ('PENDING','CONFIRMED','COMPLETED','CANCELLED','REJECTED')),
 reply TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(owner_id,client_id)
);
CREATE TABLE IF NOT EXISTS campus_guides (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
 image TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','PUBLISHED','UNLISTED')),
 updated_at INTEGER NOT NULL
);
