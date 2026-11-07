import sqlite3
import os
import uuid
import datetime
from flask import g, current_app

def get_db():
    if 'db' not in g:
        os.makedirs(current_app.instance_path, exist_ok=True)
        db_path = os.path.join(current_app.instance_path, 'panchayat_water.db')
        g.db = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=10.0)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_app(app):
    app.teardown_appcontext(close_db)
    
    with app.app_context():
        os.makedirs(app.instance_path, exist_ok=True)
        db = get_db()
        
        # Ensure tables exist
        db.execute('''
            CREATE TABLE IF NOT EXISTS supply_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ward TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'SCHEDULED',
                scheduled_at TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                stopped_at TIMESTAMP,
                remaining_seconds INTEGER,
                resumed_at TIMESTAMP,
                power_failure_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        
        # Ensure default system_state keys exist
        db.execute('INSERT OR IGNORE INTO system_state (key, value) VALUES ("power_status", "POWER AVAILABLE")')
        db.execute('INSERT OR IGNORE INTO system_state (key, value) VALUES ("power_failure_source", "NONE")')
        db.execute('INSERT OR IGNORE INTO system_state (key, value) VALUES ("hardware_online", "OFFLINE")')
        db.execute('INSERT OR IGNORE INTO system_state (key, value) VALUES ("last_hardware_heartbeat", "")')
        db.execute('INSERT OR IGNORE INTO system_state (key, value) VALUES ("recovery_token", "")')
        db.execute('INSERT OR IGNORE INTO system_state (key, value) VALUES ("last_recovery_token", "")')
        db.execute('INSERT OR IGNORE INTO system_state (key, value) VALUES ("last_recovery_schedule_id", "")')
        db.execute('INSERT OR IGNORE INTO system_state (key, value) VALUES ("last_recovery_resumed_at", "")')
        db.execute('INSERT OR IGNORE INTO system_state (key, value) VALUES ("last_recovery_remaining_seconds", "")')
        
        # Check for stale hardware heartbeat when Flask starts
        now = datetime.datetime.now()
        hb_row = db.execute("SELECT value FROM system_state WHERE key = 'last_hardware_heartbeat'").fetchone()
        is_stale = True
        if hb_row and hb_row['value']:
            try:
                hb_time = datetime.datetime.strptime(hb_row['value'].split('.')[0], "%Y-%m-%d %H:%M:%S")
                if (now - hb_time).total_seconds() <= 7.0:
                    is_stale = False
            except Exception:
                is_stale = True
                
        if is_stale:
            db.execute("UPDATE system_state SET value = 'OFFLINE' WHERE key = 'hardware_online'")
            # If server restarted while a schedule was RUNNING, freeze it to POWER_FAILURE
            running = db.execute("SELECT * FROM supply_schedule WHERE status = 'RUNNING'").fetchone()
            if running:
                ref_time = running['resumed_at'] if running['resumed_at'] else running['started_at']
                if isinstance(ref_time, str):
                    ref_time = datetime.datetime.strptime(ref_time.split('.')[0], "%Y-%m-%d %H:%M:%S")
                base_rem = running['remaining_seconds'] if running['remaining_seconds'] is not None else running['duration_minutes'] * 60
                elapsed = (now - ref_time).total_seconds()
                rem = max(0, int(base_rem - elapsed))
                token = uuid.uuid4().hex[:12]
                db.execute(
                    "UPDATE supply_schedule SET status = 'POWER_FAILURE', power_failure_at = ?, remaining_seconds = ? WHERE id = ? AND status = 'RUNNING'",
                    (now, rem, running['id'])
                )
                db.execute("UPDATE system_state SET value = 'POWER FAILURE' WHERE key = 'power_status'")
                db.execute("UPDATE system_state SET value = 'HARDWARE_DISCONNECT' WHERE key = 'power_failure_source'")
                db.execute("UPDATE system_state SET value = ? WHERE key = 'recovery_token'", (token,))
        
        db.commit()
