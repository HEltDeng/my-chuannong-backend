"""Synthetic fixtures for retained legacy API tests. Never imported by runtime code."""
def sms_registration(server, payload):
    """Issue a synthetic grant for business tests; delivery is covered separately."""
    import secrets
    import phone_auth
    data = dict(payload)
    at = server.now_ms()
    ident, code = secrets.token_urlsafe(24), '123456'
    with server.connection() as db:
        db.execute('INSERT INTO phone_challenges(id,phone_hash,purpose,code_hash,created_at,expires_at,ip_hash,state) VALUES(?,?,?,?,?,?,?,?)',
                   (ident,server.phone_digest(data['phone']),'register',phone_auth.keyed(vars(server),'sms-code',ident+':'+code),at,at+300000,'synthetic-ip','SENT'))
    data.update(challengeId=ident,code=code,confirmPassword=data.get('password'),policyVersion=phone_auth.POLICY_VERSION,consentAcceptedAt=at)
    return data

def seed_legacy_test_fixtures(server):
    with server.connection() as db:
        for ident,sport,unit,count in [('badminton','BADMINTON','场',8),('table-tennis','TABLE_TENNIS','台',10)]:
            db.execute('INSERT INTO venues(id,sport,name,unit_label,court_count,price_yuan,updated_at) VALUES(?,?,?,?,?,?,?)',
                       (ident,sport,'合成测试场地 '+ident,unit,count,20,server.now_ms()))
            for start,end in [('08:00','09:00'),('09:00','10:00'),('10:00','11:00'),('14:00','15:00'),('15:00','16:00'),('16:00','17:00'),('19:00','20:00'),('20:00','21:00')]:
                db.execute('INSERT INTO venue_time_slots(id,venue_id,start_time,end_time) VALUES(?,?,?,?)',(ident+'-'+start,ident,start,end))
        db.execute("UPDATE remote_flags SET enabled=1 WHERE key='sports_reservation'")
        db.execute("UPDATE app_versions SET latest_version_code=31,latest_version_name='synthetic-31' WHERE platform='android'")
        server.sync_release_history(db)
