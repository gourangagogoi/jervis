"""SQLite storage (swappable for PostgreSQL in production)."""
import json, sqlite3, threading

class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS events(
            id TEXT PRIMARY KEY, ts REAL, service TEXT, severity TEXT,
            event_type TEXT, message TEXT, source TEXT, anomalous INTEGER, metadata TEXT);
        CREATE TABLE IF NOT EXISTS incidents(
            id TEXT PRIMARY KEY, first_ts REAL, last_ts REAL, root_cause TEXT, payload TEXT);
        CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
        """)
        self.conn.commit()

    def save_event(self, ev, anomalous):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?)",
                (ev["id"], ev["ts_epoch"], ev["service"], ev["severity"], ev["event_type"],
                 ev["message"], ev["source"], int(bool(anomalous)), json.dumps(ev["metadata"])))
            self.conn.commit()

    def save_incident(self, inc):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO incidents VALUES(?,?,?,?,?)",
                (inc.id, inc.first_ts, inc.last_ts, inc.root_cause, json.dumps(inc.to_dict())))
            self.conn.commit()
