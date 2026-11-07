import os
import sys
import time
import json
import datetime
import threading
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.db import get_db

class TestPhase1Backend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = cls.app.test_client()

    def setUp(self):
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM supply_schedule WHERE ward IN ('Ward 1', 'Ward 2') AND duration_minutes IN (3, 5, 10)")
            db.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('power_status', 'POWER AVAILABLE')")
            db.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('power_failure_source', 'NONE')")
            db.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('hardware_online', 'OFFLINE')")
            db.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('last_hardware_heartbeat', '')")
            db.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('recovery_token', '')")
            db.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('last_recovery_token', '')")
            db.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('last_recovery_schedule_id', '')")
            db.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('last_recovery_resumed_at', '')")
            db.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('last_recovery_remaining_seconds', '')")
            db.commit()

    def test_01_index_and_status(self):
        """Test dashboard index route and /api/status structure."""
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Smart Panchayat Water Distribution', res.data)

        res = self.client.get('/api/status')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn('power_status', data)
        self.assertIn('power_failure_source', data)
        self.assertIn('hardware_online', data)
        self.assertIn('schedules', data)

    def test_02_heartbeat_registration(self):
        """Test POST /api/hardware/heartbeat marks hardware online."""
        res = self.client.post('/api/hardware/heartbeat', json={
            'esp32_id': 'esp32_test_01',
            'pump_state': False,
            'led_state': False,
            'uptime_ms': 1000
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['hardware_online'], 'ONLINE')

        res_status = self.client.get('/api/status')
        self.assertTrue(res_status.get_json()['hardware_online'])

    def test_03_hardware_command_idle_both_outputs_off(self):
        """Test GET /api/hardware/command returns IDLE with both pump_state and led_state FALSE."""
        res = self.client.get('/api/hardware/command')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['action'], 'IDLE')
        self.assertEqual(data['active_ward'], 'NONE')
        self.assertFalse(data['pump_state'])
        self.assertFalse(data['led_state'])

    def test_04_ward_specific_output_combinations_running(self):
        """Test exact output combination when Ward 1 is running vs when Ward 2 is running."""
        self.client.post('/api/hardware/heartbeat', json={'esp32_id': 'esp32_01'})

        # Ward 1 RUNNING: pump_state=True, led_state=False
        with self.app.app_context():
            db = get_db()
            now = datetime.datetime.now()
            cursor = db.execute(
                "INSERT INTO supply_schedule (ward, duration_minutes, status, started_at, remaining_seconds) VALUES ('Ward 1', 3, 'RUNNING', ?, 180)",
                (now,)
            )
            w1_id = cursor.lastrowid
            db.execute("UPDATE system_state SET value = 'POWER AVAILABLE' WHERE key = 'power_status'")
            db.execute("UPDATE system_state SET value = 'NONE' WHERE key = 'power_failure_source'")
            db.commit()

        res_w1 = self.client.get('/api/hardware/command')
        d1 = res_w1.get_json()
        self.assertEqual(d1['action'], 'RUN')
        self.assertEqual(d1['active_ward'], 'Ward 1')
        self.assertTrue(d1['pump_state'])
        self.assertFalse(d1['led_state'])

        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE supply_schedule SET status = 'COMPLETED' WHERE id = ?", (w1_id,))
            db.commit()

        # Ward 2 RUNNING: pump_state=False, led_state=True
        with self.app.app_context():
            db = get_db()
            now = datetime.datetime.now()
            cursor = db.execute(
                "INSERT INTO supply_schedule (ward, duration_minutes, status, started_at, remaining_seconds) VALUES ('Ward 2', 3, 'RUNNING', ?, 180)",
                (now,)
            )
            w2_id = cursor.lastrowid
            db.commit()

        res_w2 = self.client.get('/api/hardware/command')
        d2 = res_w2.get_json()
        self.assertEqual(d2['action'], 'RUN')
        self.assertEqual(d2['active_ward'], 'Ward 2')
        self.assertFalse(d2['pump_state'])
        self.assertTrue(d2['led_state'])

        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE supply_schedule SET status = 'COMPLETED' WHERE id = ?", (w2_id,))
            db.commit()

    def test_05_manual_power_failure_both_outputs_off(self):
        """Test that during MANUAL_POWER_FAILURE, both pump and LED remain OFF and heartbeat cannot clear it."""
        self.client.post('/api/power/fail')

        res = self.client.get('/api/hardware/command')
        data = res.get_json()
        self.assertEqual(data['action'], 'STOP')
        self.assertEqual(data['status'], 'MANUAL_POWER_FAILURE')
        self.assertEqual(data['power_failure_source'], 'MANUAL')
        self.assertFalse(data['pump_state'])
        self.assertFalse(data['led_state'])

        # ESP32 heartbeat does not clear manual failure
        self.client.post('/api/hardware/heartbeat', json={'esp32_id': 'esp32_01'})
        c2 = self.client.get('/api/hardware/command').get_json()
        self.assertEqual(c2['action'], 'STOP')
        self.assertFalse(c2['pump_state'])
        self.assertFalse(c2['led_state'])

        # Restore power
        self.client.post('/api/power/restore')
        c3 = self.client.get('/api/hardware/command').get_json()
        self.assertEqual(c3['action'], 'IDLE')

    def test_06_hardware_disconnect_outputs_off_until_ack(self):
        """Test that during HARDWARE_DISCONNECT, outputs remain OFF in command while target outputs indicate interrupted ward."""
        with self.app.app_context():
            db = get_db()
            now = datetime.datetime.now()
            cursor = db.execute(
                "INSERT INTO supply_schedule (ward, duration_minutes, status, power_failure_at, remaining_seconds) VALUES ('Ward 1', 5, 'POWER_FAILURE', ?, 200)",
                (now,)
            )
            w1_id = cursor.lastrowid
            token = "tok_w1_test"
            db.execute("UPDATE system_state SET value = 'POWER FAILURE' WHERE key = 'power_status'")
            db.execute("UPDATE system_state SET value = 'HARDWARE_DISCONNECT' WHERE key = 'power_failure_source'")
            db.execute("UPDATE system_state SET value = ? WHERE key = 'recovery_token'", (token,))
            db.commit()

        res_cmd = self.client.get('/api/hardware/command')
        d1 = res_cmd.get_json()
        self.assertEqual(d1['action'], 'RECOVER')
        self.assertEqual(d1['active_ward'], 'Ward 1')
        self.assertFalse(d1['pump_state'])
        self.assertFalse(d1['led_state'])
        self.assertTrue(d1['target_pump'])
        self.assertFalse(d1['target_led'])
        self.assertEqual(d1['recovery_token'], token)

        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE supply_schedule SET status = 'COMPLETED' WHERE id = ?", (w1_id,))
            db.commit()

    def test_07_idempotent_recovery_ack_and_retry(self):
        """Test idempotent recovery: first ACK succeeds, subsequent retries return original success without resetting clock."""
        with self.app.app_context():
            db = get_db()
            now = datetime.datetime.now()
            cursor = db.execute(
                "INSERT INTO supply_schedule (ward, duration_minutes, status, power_failure_at, remaining_seconds) VALUES ('Ward 1', 10, 'POWER_FAILURE', ?, 420)",
                (now,)
            )
            sch_id = cursor.lastrowid
            token = "idempotent_token_xyz"
            db.execute("UPDATE system_state SET value = 'POWER FAILURE' WHERE key = 'power_status'")
            db.execute("UPDATE system_state SET value = 'HARDWARE_DISCONNECT' WHERE key = 'power_failure_source'")
            db.execute("UPDATE system_state SET value = ? WHERE key = 'recovery_token'", (token,))
            db.commit()

        ack1 = self.client.post('/api/hardware/ack', json={
            'schedule_id': sch_id,
            'recovery_token': token,
            'pump_active': True,
            'led_active': False
        })
        self.assertEqual(ack1.status_code, 200)
        res1 = ack1.get_json()
        self.assertTrue(res1['success'])
        self.assertEqual(res1['status'], 'RUNNING')
        self.assertEqual(res1['remaining_seconds'], 420)
        self.assertFalse(res1['is_retry'])
        first_resumed_at = res1['resumed_at']

        time.sleep(0.5)

        # Retry ACK
        ack_retry1 = self.client.post('/api/hardware/ack', json={
            'schedule_id': sch_id,
            'recovery_token': token,
            'pump_active': True,
            'led_active': False
        })
        self.assertEqual(ack_retry1.status_code, 200)
        res_retry1 = ack_retry1.get_json()
        self.assertTrue(res_retry1['success'])
        self.assertTrue(res_retry1['is_retry'])
        self.assertEqual(res_retry1['resumed_at'], first_resumed_at)
        self.assertEqual(res_retry1['remaining_seconds'], 420)

        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE supply_schedule SET status = 'COMPLETED' WHERE id = ?", (sch_id,))
            db.commit()

    def test_08_heartbeat_timeout_and_remaining_time_preservation(self):
        """Test physical disconnection detection saves remaining duration exactly once."""
        self.client.post('/api/hardware/heartbeat', json={'esp32_id': 'esp32_01'})

        self.client.post('/api/schedule', json={'ward': 'Ward 1', 'duration_minutes': 5})
        res_status = self.client.get('/api/status')
        sch = [s for s in res_status.get_json()['schedules'] if s['ward'] == 'Ward 1' and s['duration_minutes'] == 5 and s['status'] == 'SCHEDULED'][0]
        sch_id = sch['id']
        self.client.post(f'/api/schedule/{sch_id}/start')

        # Simulate timeout (>7.0s)
        with self.app.app_context():
            db = get_db()
            stale_time = (datetime.datetime.now() - datetime.timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute("UPDATE system_state SET value = ? WHERE key = 'last_hardware_heartbeat'", (stale_time,))
            db.commit()

        res_cmd = self.client.get('/api/hardware/command')
        cmd_data = res_cmd.get_json()
        self.assertEqual(cmd_data['status'], 'PENDING_RECOVERY')
        self.assertEqual(cmd_data['action'], 'RECOVER')
        self.assertEqual(cmd_data['power_failure_source'], 'HARDWARE_DISCONNECT')
        saved_rem = cmd_data['remaining_seconds']
        self.assertGreater(saved_rem, 0)
        self.assertLessEqual(saved_rem, 300)

        time.sleep(0.5)
        res_cmd2 = self.client.get('/api/hardware/command')
        self.assertEqual(res_cmd2.get_json()['remaining_seconds'], saved_rem)

        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE supply_schedule SET status = 'COMPLETED' WHERE id = ?", (sch_id,))
            db.commit()

if __name__ == '__main__':
    unittest.main()
