import sqlite3
db = sqlite3.connect('instance/panchayat_water.db')
try:
    db.execute('ALTER TABLE supply_schedule ADD COLUMN resumed_at TIMESTAMP;')
except Exception as e:
    print(e)
try:
    db.execute('ALTER TABLE supply_schedule ADD COLUMN power_failure_at TIMESTAMP;')
except Exception as e:
    print(e)
db.execute('CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);')
db.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('power_status', 'POWER AVAILABLE');")
db.commit()
print("Done")
