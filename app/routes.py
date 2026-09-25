from flask import Blueprint, render_template, request, jsonify, g
import datetime
import sqlite3
from app.db import get_db

main = Blueprint('main', __name__)

@main.route('/')
def index():
    return render_template('base.html')

@main.route('/api/status', methods=['GET'])
def get_status():
    db = get_db()
    
    # Auto-complete any RUNNING supplies whose time has elapsed
    running = db.execute("SELECT * FROM supply_schedule WHERE status = 'RUNNING'").fetchone()
    if running:
        started_at = running['started_at']
        duration = running['duration_minutes']
        now = datetime.datetime.now()
        elapsed = (now - started_at).total_seconds()
        remaining = int(duration * 60 - elapsed)
        if remaining <= 0:
            db.execute("UPDATE supply_schedule SET status = 'COMPLETED', completed_at = ?, remaining_seconds = 0 WHERE id = ?", (now, running['id']))
            db.commit()
    
    schedules = db.execute("SELECT * FROM supply_schedule ORDER BY created_at DESC").fetchall()
    
    running_supply = None
    all_schedules = []
    
    for row in schedules:
        r = dict(row)
        # Format timestamps
        for field in ['scheduled_at', 'started_at', 'completed_at', 'stopped_at', 'created_at']:
            if r[field]:
                r[field] = r[field].strftime("%Y-%m-%d %H:%M:%S") if isinstance(r[field], datetime.datetime) else str(r[field])
        
        if r['status'] == 'RUNNING':
            if row['started_at']:
                started_at = row['started_at']
                if isinstance(started_at, str):
                    started_at = datetime.datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S.%f")
                duration = row['duration_minutes']
                now = datetime.datetime.now()
                elapsed = (now - started_at).total_seconds()
                remaining = int(duration * 60 - elapsed)
                r['current_remaining'] = remaining if remaining > 0 else 0
            running_supply = r
            
        all_schedules.append(r)
        
    return jsonify({
        'running': running_supply,
        'schedules': all_schedules
    })

@main.route('/api/schedule', methods=['POST'])
def create_schedule():
    data = request.json
    ward = data.get('ward')
    duration = data.get('duration_minutes')
    
    if ward not in ['Ward 1', 'Ward 2']:
        return jsonify({'error': 'Invalid ward'}), 400
    try:
        duration = int(duration)
    except (ValueError, TypeError):
        return jsonify({'error': 'Duration must be an integer'}), 400
        
    if duration < 1 or duration > 60:
        return jsonify({'error': 'Duration must be between 1 and 60 minutes'}), 400
        
    db = get_db()
    now = datetime.datetime.now()
    db.execute('INSERT INTO supply_schedule (ward, duration_minutes, scheduled_at) VALUES (?, ?, ?)', (ward, duration, now))
    db.commit()
    return jsonify({'success': True}), 201

@main.route('/api/schedule/<int:id>/start', methods=['POST'])
def start_schedule(id):
    db = get_db()
    # Check if another is running
    running = db.execute("SELECT * FROM supply_schedule WHERE status = 'RUNNING'").fetchone()
    if running:
        return jsonify({'error': 'Another ward is currently receiving water.'}), 400
        
    schedule = db.execute("SELECT * FROM supply_schedule WHERE id = ?", (id,)).fetchone()
    if not schedule:
        return jsonify({'error': 'Schedule not found'}), 404
    if schedule['status'] != 'SCHEDULED':
        return jsonify({'error': 'Schedule is not in SCHEDULED state'}), 400
        
    now = datetime.datetime.now()
    db.execute("UPDATE supply_schedule SET status = 'RUNNING', started_at = ? WHERE id = ?", (now, id))
    db.commit()
    return jsonify({'success': True})

@main.route('/api/schedule/<int:id>/stop', methods=['POST'])
def stop_schedule(id):
    db = get_db()
    schedule = db.execute("SELECT * FROM supply_schedule WHERE id = ? AND status = 'RUNNING'", (id,)).fetchone()
    if not schedule:
        return jsonify({'error': 'Schedule not running or not found'}), 404
        
    started_at = schedule['started_at']
    duration = schedule['duration_minutes']
    if isinstance(started_at, str):
        started_at = datetime.datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S.%f")
    now = datetime.datetime.now()
    elapsed = (now - started_at).total_seconds()
    remaining = int(duration * 60 - elapsed)
    if remaining < 0: remaining = 0
    
    db.execute("UPDATE supply_schedule SET status = 'STOPPED', stopped_at = ?, remaining_seconds = ? WHERE id = ?", (now, remaining, id))
    db.commit()
    return jsonify({'success': True})

@main.route('/api/schedule/<int:id>/complete', methods=['POST'])
def complete_schedule(id):
    db = get_db()
    schedule = db.execute("SELECT * FROM supply_schedule WHERE id = ? AND status = 'RUNNING'", (id,)).fetchone()
    if not schedule:
        return jsonify({'error': 'Schedule not running or not found'}), 404
        
    now = datetime.datetime.now()
    db.execute("UPDATE supply_schedule SET status = 'COMPLETED', completed_at = ?, remaining_seconds = 0 WHERE id = ?", (now, id))
    db.commit()
    return jsonify({'success': True})
