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
    
    power_row = db.execute("SELECT value FROM system_state WHERE key = 'power_status'").fetchone()
    power_status = power_row['value'] if power_row else 'POWER AVAILABLE'

    # Auto-complete any RUNNING supplies whose time has elapsed
    running = db.execute("SELECT * FROM supply_schedule WHERE status = 'RUNNING'").fetchone()
    now = datetime.datetime.now()
    if running:
        ref_time = running['resumed_at'] if running['resumed_at'] else running['started_at']
        if isinstance(ref_time, str):
            ref_time = datetime.datetime.strptime(ref_time.split('.')[0], "%Y-%m-%d %H:%M:%S")
        
        base_rem = running['remaining_seconds'] if running['remaining_seconds'] is not None else running['duration_minutes'] * 60
        elapsed = (now - ref_time).total_seconds()
        remaining = int(base_rem - elapsed)
        
        if remaining <= 0:
            db.execute("UPDATE supply_schedule SET status = 'COMPLETED', completed_at = ?, remaining_seconds = 0 WHERE id = ?", (now, running['id']))
            db.commit()
    
    schedules = db.execute("SELECT * FROM supply_schedule ORDER BY created_at DESC").fetchall()
    
    running_supply = None
    all_schedules = []
    
    for row in schedules:
        r = dict(row)
        # Format timestamps
        for field in ['scheduled_at', 'started_at', 'completed_at', 'stopped_at', 'resumed_at', 'power_failure_at', 'created_at']:
            if r.get(field):
                r[field] = r[field].strftime("%Y-%m-%d %H:%M:%S") if isinstance(r[field], datetime.datetime) else str(r[field])
        
        if r['status'] == 'RUNNING':
            ref_time = row['resumed_at'] if row['resumed_at'] else row['started_at']
            if isinstance(ref_time, str):
                ref_time = datetime.datetime.strptime(ref_time.split('.')[0], "%Y-%m-%d %H:%M:%S")
            base_rem = row['remaining_seconds'] if row['remaining_seconds'] is not None else row['duration_minutes'] * 60
            elapsed = (now - ref_time).total_seconds()
            remaining = int(base_rem - elapsed)
            r['current_remaining'] = remaining if remaining > 0 else 0
            running_supply = r
        elif r['status'] == 'POWER_FAILURE':
            r['current_remaining'] = row['remaining_seconds'] if row['remaining_seconds'] is not None else row['duration_minutes'] * 60
            # Even if it's paused, we might want to show it as the current active/paused supply for UI purposes
            running_supply = r
            
        all_schedules.append(r)
        
    return jsonify({
        'power_status': power_status,
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
    power_row = db.execute("SELECT value FROM system_state WHERE key = 'power_status'").fetchone()
    if power_row and power_row['value'] == 'POWER FAILURE':
        return jsonify({'error': 'Cannot start water supply while power is unavailable.'}), 400

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
        
    ref_time = schedule['resumed_at'] if schedule['resumed_at'] else schedule['started_at']
    if isinstance(ref_time, str):
        ref_time = datetime.datetime.strptime(ref_time.split('.')[0], "%Y-%m-%d %H:%M:%S")
    now = datetime.datetime.now()
    elapsed = (now - ref_time).total_seconds()
    base_rem = schedule['remaining_seconds'] if schedule['remaining_seconds'] is not None else schedule['duration_minutes'] * 60
    remaining = int(base_rem - elapsed)
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

@main.route('/api/power/fail', methods=['POST'])
def power_fail():
    db = get_db()
    db.execute("UPDATE system_state SET value = 'POWER FAILURE' WHERE key = 'power_status'")
    
    running = db.execute("SELECT * FROM supply_schedule WHERE status = 'RUNNING'").fetchone()
    if running:
        ref_time = running['resumed_at'] if running['resumed_at'] else running['started_at']
        if isinstance(ref_time, str):
            ref_time = datetime.datetime.strptime(ref_time.split('.')[0], "%Y-%m-%d %H:%M:%S")
        now = datetime.datetime.now()
        elapsed = (now - ref_time).total_seconds()
        base_rem = running['remaining_seconds'] if running['remaining_seconds'] is not None else running['duration_minutes'] * 60
        remaining = int(base_rem - elapsed)
        if remaining < 0: remaining = 0
        
        db.execute("UPDATE supply_schedule SET status = 'POWER_FAILURE', power_failure_at = ?, remaining_seconds = ? WHERE id = ?", (now, remaining, running['id']))
    
    db.commit()
    return jsonify({'success': True})

@main.route('/api/power/restore', methods=['POST'])
def power_restore():
    db = get_db()
    db.execute("UPDATE system_state SET value = 'POWER AVAILABLE' WHERE key = 'power_status'")
    
    paused = db.execute("SELECT * FROM supply_schedule WHERE status = 'POWER_FAILURE' ORDER BY id DESC LIMIT 1").fetchone()
    if paused:
        now = datetime.datetime.now()
        db.execute("UPDATE supply_schedule SET status = 'RUNNING', resumed_at = ? WHERE id = ?", (now, paused['id']))
        
    db.commit()
    return jsonify({'success': True})
