import sqlite3
import os
from flask import g, current_app

def get_db():
    if 'db' not in g:
        os.makedirs(current_app.instance_path, exist_ok=True)
        db_path = os.path.join(current_app.instance_path, 'panchayat_water.db')
        g.db = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_app(app):
    app.teardown_appcontext(close_db)
    
    with app.app_context():
        db_path = os.path.join(app.instance_path, 'panchayat_water.db')
        if not os.path.exists(db_path):
            os.makedirs(app.instance_path, exist_ok=True)
            db = get_db()
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            db.commit()
