"""Synthetic fixtures for retained legacy API tests. Never imported by runtime code."""
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
