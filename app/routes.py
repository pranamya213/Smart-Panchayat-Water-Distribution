from flask import Blueprint, render_template, request, jsonify, g
import datetime
import sqlite3
import uuid
import threading
import time
from app.db import get_db

main = Blueprint('main', __name__)

HEARTBEAT_TIMEOUT_SECONDS = 7.0

# ---------------------------------------------------------------------------
# State Management Helpers
# ---------------------------------------------------------------------------

def get_system_state(db, key, default=None):
    row = db.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
    return row['value'] if row else default

def set_system_state(db, key, value):
    db.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES (?, ?)", (key, str(value)))

def check_and_update_heartbeat_timeout(db=None):
    """
    Evaluates running schedules for completion, checks hardware heartbeat staleness,
    and automatically pauses active supplies if the ESP32 disconnects.
    """
    if db is None:
        db = get_db()

    now = datetime.datetime.now()

    # 1. Auto-complete any RUNNING supplies whose time has elapsed
    running = db.execute("SELECT * FROM supply_schedule WHERE status = 'RUNNING'").fetchone()
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
            running = None

    # 2. Check hardware heartbeat staleness
    hb_row = db.execute("SELECT value FROM system_state WHERE key = 'last_hardware_heartbeat'").fetchone()
    hb_val = hb_row['value'] if hb_row else ''
    is_offline = True

    if hb_val:
        try:
            hb_time = datetime.datetime.strptime(hb_val.split('.')[0], "%Y-%m-%d %H:%M:%S")
            if (now - hb_time).total_seconds() <= HEARTBEAT_TIMEOUT_SECONDS:
                is_offline = False
        except Exception:
            is_offline = True

    if is_offline:
        current_hw_online = get_system_state(db, 'hardware_online', 'OFFLINE')
        if current_hw_online != 'OFFLINE':
            set_system_state(db, 'hardware_online', 'OFFLINE')
            db.commit()

        # If a supply is RUNNING and hardware disconnection is detected:
        if running:
            ref_time = running['resumed_at'] if running['resumed_at'] else running['started_at']
            if isinstance(ref_time, str):
                ref_time = datetime.datetime.strptime(ref_time.split('.')[0], "%Y-%m-%d %H:%M:%S")
            base_rem = running['remaining_seconds'] if running['remaining_seconds'] is not None else running['duration_minutes'] * 60
            elapsed = (now - ref_time).total_seconds()
            rem = max(0, int(base_rem - elapsed))
            token = uuid.uuid4().hex[:12]

            # Atomic update: only updates if still RUNNING (save once principle)
            cursor = db.execute(
                "UPDATE supply_schedule SET status = 'POWER_FAILURE', power_failure_at = ?, remaining_seconds = ? WHERE id = ? AND status = 'RUNNING'",
                (now, rem, running['id'])
            )
            if cursor.rowcount > 0:
                set_system_state(db, 'power_status', 'POWER FAILURE')
                set_system_state(db, 'power_failure_source', 'HARDWARE_DISCONNECT')
                set_system_state(db, 'recovery_token', token)
                # Clear previous recovery record on a new disconnect event
                set_system_state(db, 'last_recovery_token', '')
                set_system_state(db, 'last_recovery_schedule_id', '')
                set_system_state(db, 'last_recovery_resumed_at', '')
                set_system_state(db, 'last_recovery_remaining_seconds', '')
                db.commit()
    else:
        current_hw_online = get_system_state(db, 'hardware_online', 'OFFLINE')
        if current_hw_online != 'ONLINE':
            set_system_state(db, 'hardware_online', 'ONLINE')
            db.commit()

        # If hardware is online and no paused schedule is awaiting recovery,
        # restore power_status to POWER AVAILABLE if it was previously flagged as HARDWARE_DISCONNECT
        pfs = get_system_state(db, 'power_failure_source', 'NONE')
        if pfs == 'HARDWARE_DISCONNECT':
            paused = db.execute("SELECT * FROM supply_schedule WHERE status = 'POWER_FAILURE'").fetchone()
            if not paused:
                set_system_state(db, 'power_status', 'POWER AVAILABLE')
                set_system_state(db, 'power_failure_source', 'NONE')
                set_system_state(db, 'recovery_token', '')
                db.commit()

# ---------------------------------------------------------------------------
# Background Heartbeat Monitor
# ---------------------------------------------------------------------------

_monitor_started = False
_monitor_lock = threading.Lock()

def start_background_monitor(app):
    """
    Spawns an independent daemon thread that evaluates heartbeat timeouts
    every second, even if no client or browser is polling.
    """
    global _monitor_started
    with _monitor_lock:
        if _monitor_started:
            return
        _monitor_started = True

    def monitor_loop():
        time.sleep(1.0)
        while True:
            try:
                with app.app_context():
                    check_and_update_heartbeat_timeout()
            except Exception:
                pass
            time.sleep(1.0)

    t = threading.Thread(target=monitor_loop, daemon=True, name="HardwareHeartbeatMonitor")
    t.start()

# ---------------------------------------------------------------------------
# Dashboard & Core API Routes
# ---------------------------------------------------------------------------

@main.route('/')
def index():
    return render_template('base.html')

@main.route('/api/status', methods=['GET'])
def get_status():
    db = get_db()
    check_and_update_heartbeat_timeout(db)

    power_status = get_system_state(db, 'power_status', 'POWER AVAILABLE')
    power_failure_source = get_system_state(db, 'power_failure_source', 'NONE')
    hardware_online = (get_system_state(db, 'hardware_online', 'OFFLINE') == 'ONLINE')

    schedules = db.execute("SELECT * FROM supply_schedule ORDER BY created_at DESC").fetchall()

    running_supply = None
    all_schedules = []
    now = datetime.datetime.now()

    for row in schedules:
        r = dict(row)
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
            running_supply = r

        all_schedules.append(r)

    return jsonify({
        'power_status': power_status,
        'power_failure_source': power_failure_source,
        'hardware_online': hardware_online,
        'running': running_supply,
        'schedules': all_schedules
    })

@main.route('/api/schedule', methods=['POST'])
def create_schedule():
    data = request.json or {}
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
    check_and_update_heartbeat_timeout(db)

    power_status = get_system_state(db, 'power_status', 'POWER AVAILABLE')
    if power_status == 'POWER FAILURE':
        return jsonify({'error': 'Cannot start water supply while power is unavailable.'}), 400

    # Check if another ward is currently running
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
    check_and_update_heartbeat_timeout(db)

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
    check_and_update_heartbeat_timeout(db)

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
    set_system_state(db, 'power_status', 'POWER FAILURE')
    set_system_state(db, 'power_failure_source', 'MANUAL')
    set_system_state(db, 'recovery_token', '')
    set_system_state(db, 'last_recovery_token', '')
    set_system_state(db, 'last_recovery_schedule_id', '')
    set_system_state(db, 'last_recovery_resumed_at', '')
    set_system_state(db, 'last_recovery_remaining_seconds', '')

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
    set_system_state(db, 'power_status', 'POWER AVAILABLE')
    set_system_state(db, 'power_failure_source', 'NONE')
    set_system_state(db, 'recovery_token', '')

    paused = db.execute("SELECT * FROM supply_schedule WHERE status = 'POWER_FAILURE' ORDER BY id DESC LIMIT 1").fetchone()
    if paused:
        now = datetime.datetime.now()
        db.execute("UPDATE supply_schedule SET status = 'RUNNING', resumed_at = ? WHERE id = ?", (now, paused['id']))

    db.commit()
    return jsonify({'success': True})

# ---------------------------------------------------------------------------
# Hardware API Endpoints for ESP32 Controller
# ---------------------------------------------------------------------------

@main.route('/api/hardware/command', methods=['GET'])
def hardware_command():
    db = get_db()
    check_and_update_heartbeat_timeout(db)

    power_status = get_system_state(db, 'power_status', 'POWER AVAILABLE')
    power_failure_source = get_system_state(db, 'power_failure_source', 'NONE')
    hardware_online = (get_system_state(db, 'hardware_online', 'OFFLINE') == 'ONLINE')

    now = datetime.datetime.now()
    running = db.execute("SELECT * FROM supply_schedule WHERE status = 'RUNNING'").fetchone()

    # Case 1: Active RUNNING supply
    if running:
        if power_status == 'POWER AVAILABLE':
            ref_time = running['resumed_at'] if running['resumed_at'] else running['started_at']
            if isinstance(ref_time, str):
                ref_time = datetime.datetime.strptime(ref_time.split('.')[0], "%Y-%m-%d %H:%M:%S")
            base_rem = running['remaining_seconds'] if running['remaining_seconds'] is not None else running['duration_minutes'] * 60
            elapsed = (now - ref_time).total_seconds()
            rem = max(0, int(base_rem - elapsed))

            is_ward1 = (running['ward'] == 'Ward 1')
            is_ward2 = (running['ward'] == 'Ward 2')

            return jsonify({
                'status': 'SUCCESS',
                'action': 'RUN',
                'power_status': power_status,
                'power_failure_source': power_failure_source,
                'hardware_online': hardware_online,
                'schedule_id': running['id'],
                'active_ward': running['ward'],
                'pump_state': is_ward1,
                'led_state': is_ward2,
                'target_pump': is_ward1,
                'target_led': is_ward2,
                'remaining_seconds': rem,
                'recovery_token': None
            })
        else:
            return jsonify({
                'status': 'POWER_INTERRUPTED',
                'action': 'STOP',
                'power_status': power_status,
                'power_failure_source': power_failure_source,
                'hardware_online': hardware_online,
                'schedule_id': running['id'],
                'active_ward': 'NONE',
                'pump_state': False,
                'led_state': False,
                'target_pump': False,
                'target_led': False,
                'remaining_seconds': running['remaining_seconds'] or 0,
                'recovery_token': None
            })

    # Case 2: Interrupted supply awaiting recovery or manual restore
    paused = db.execute("SELECT * FROM supply_schedule WHERE status = 'POWER_FAILURE' ORDER BY id DESC LIMIT 1").fetchone()
    if paused:
        if power_failure_source == 'HARDWARE_DISCONNECT':
            token = get_system_state(db, 'recovery_token', '')
            if not token:
                token = uuid.uuid4().hex[:12]
                set_system_state(db, 'recovery_token', token)
                db.commit()

            is_target_w1 = (paused['ward'] == 'Ward 1')
            is_target_w2 = (paused['ward'] == 'Ward 2')

            return jsonify({
                'status': 'PENDING_RECOVERY',
                'action': 'RECOVER',
                'power_status': power_status,
                'power_failure_source': power_failure_source,
                'hardware_online': hardware_online,
                'schedule_id': paused['id'],
                'active_ward': paused['ward'],
                'pump_state': False,           # Outputs remain OFF until ACK handshake completes
                'led_state': False,
                'target_pump': is_target_w1,   # Informs ESP32 which output to actuate
                'target_led': is_target_w2,
                'remaining_seconds': paused['remaining_seconds'] or 0,
                'recovery_token': token
            })
        elif power_failure_source == 'MANUAL':
            return jsonify({
                'status': 'MANUAL_POWER_FAILURE',
                'action': 'STOP',
                'power_status': power_status,
                'power_failure_source': power_failure_source,
                'hardware_online': hardware_online,
                'schedule_id': paused['id'],
                'active_ward': 'NONE',
                'pump_state': False,
                'led_state': False,
                'target_pump': False,
                'target_led': False,
                'remaining_seconds': paused['remaining_seconds'] or 0,
                'recovery_token': None
            })

    # Case 3: Idle state
    if power_status == 'POWER FAILURE':
        return jsonify({
            'status': 'MANUAL_POWER_FAILURE' if power_failure_source == 'MANUAL' else 'POWER_INTERRUPTED',
            'action': 'STOP',
            'power_status': power_status,
            'power_failure_source': power_failure_source,
            'hardware_online': hardware_online,
            'schedule_id': None,
            'active_ward': 'NONE',
            'pump_state': False,
            'led_state': False,
            'target_pump': False,
            'target_led': False,
            'remaining_seconds': 0,
            'recovery_token': None
        })

    return jsonify({
        'status': 'IDLE',
        'action': 'IDLE',
        'power_status': power_status,
        'power_failure_source': power_failure_source,
        'hardware_online': hardware_online,
        'schedule_id': None,
        'active_ward': 'NONE',
        'pump_state': False,
        'led_state': False,
        'target_pump': False,
        'target_led': False,
        'remaining_seconds': 0,
        'recovery_token': None
    })

@main.route('/api/hardware/heartbeat', methods=['POST'])
def hardware_heartbeat():
    db = get_db()
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    set_system_state(db, 'last_hardware_heartbeat', now_str)
    set_system_state(db, 'hardware_online', 'ONLINE')
    db.commit()

    return jsonify({
        'success': True,
        'hardware_online': 'ONLINE',
        'timestamp': now_str
    })

@main.route('/api/hardware/ack', methods=['POST'])
def hardware_ack():
    data = request.json or {}
    schedule_id = data.get('schedule_id')
    recovery_token = data.get('recovery_token')

    if not schedule_id or not recovery_token:
        return jsonify({'error': 'Missing schedule_id or recovery_token', 'code': 'BAD_REQUEST'}), 400

    db = get_db()

    # 1. Idempotent Retry Handling:
    # If this exact recovery token was already processed successfully, return the original success response.
    # Do not restart countdown, do not update resumed_at, do not alter remaining duration.
    last_token = get_system_state(db, 'last_recovery_token', '')
    last_sch_id = get_system_state(db, 'last_recovery_schedule_id', '')

    if last_token and recovery_token == last_token and str(schedule_id) == str(last_sch_id):
        schedule = db.execute("SELECT * FROM supply_schedule WHERE id = ?", (schedule_id,)).fetchone()
        if schedule and schedule['status'] in ['RUNNING', 'COMPLETED']:
            resumed_at_str = get_system_state(db, 'last_recovery_resumed_at', '')
            rem_sec = int(get_system_state(db, 'last_recovery_remaining_seconds', schedule['remaining_seconds'] or 0))
            return jsonify({
                'success': True,
                'schedule_id': schedule_id,
                'status': schedule['status'],
                'resumed_at': resumed_at_str,
                'remaining_seconds': rem_sec,
                'is_retry': True
            }), 200

    # 2. Validate against active recovery token
    stored_token = get_system_state(db, 'recovery_token', '')
    if not stored_token or stored_token != recovery_token:
        return jsonify({'error': 'Invalid, expired, or unrecognized recovery token', 'code': 'INVALID_TOKEN'}), 409

    power_failure_source = get_system_state(db, 'power_failure_source', 'NONE')
    if power_failure_source != 'HARDWARE_DISCONNECT':
        return jsonify({'error': 'No hardware recovery in progress', 'code': 'INVALID_STATE'}), 409

    schedule = db.execute("SELECT * FROM supply_schedule WHERE id = ?", (schedule_id,)).fetchone()
    if not schedule:
        return jsonify({'error': 'Schedule not found', 'code': 'NOT_FOUND'}), 404
    if schedule['status'] != 'POWER_FAILURE':
        return jsonify({'error': 'Schedule is not in POWER_FAILURE status', 'code': 'INVALID_STATUS'}), 409

    if schedule['ward'] == 'Ward 1' and not data.get('pump_active'):
        return jsonify({'error': 'Ward 1 requires pump_active=true', 'code': 'ACTUATION_MISMATCH'}), 400
    if schedule['ward'] == 'Ward 2' and not data.get('led_active'):
        return jsonify({'error': 'Ward 2 requires led_active=true', 'code': 'ACTUATION_MISMATCH'}), 400

    # 3. First-time transition: mark RUNNING and record recovery metadata for idempotent retries
    now = datetime.datetime.now()
    cursor = db.execute(
        "UPDATE supply_schedule SET status = 'RUNNING', resumed_at = ? WHERE id = ? AND status = 'POWER_FAILURE'",
        (now, schedule_id)
    )
    if cursor.rowcount == 0:
        return jsonify({'error': 'Failed to transition schedule; state conflict', 'code': 'CONFLICT'}), 409

    resumed_at_str = now.strftime("%Y-%m-%d %H:%M:%S")
    set_system_state(db, 'power_status', 'POWER AVAILABLE')
    set_system_state(db, 'power_failure_source', 'NONE')
    set_system_state(db, 'recovery_token', '')  # Clear active token

    # Persist completed recovery data so any future retry with this token returns the exact same result
    set_system_state(db, 'last_recovery_token', recovery_token)
    set_system_state(db, 'last_recovery_schedule_id', schedule_id)
    set_system_state(db, 'last_recovery_resumed_at', resumed_at_str)
    set_system_state(db, 'last_recovery_remaining_seconds', schedule['remaining_seconds'])
    db.commit()

    return jsonify({
        'success': True,
        'schedule_id': schedule_id,
        'status': 'RUNNING',
        'resumed_at': resumed_at_str,
        'remaining_seconds': schedule['remaining_seconds'],
        'is_retry': False
    })
