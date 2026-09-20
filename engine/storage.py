"""Transactional checkpoint/event journal and a process-level single-writer lock."""

from contextlib import closing, contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3


def encode(value):
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


@contextmanager
def writer_lock(data_dir):
    path = Path(data_dir)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (path / "bot.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RuntimeError("Another bot already owns this data directory") from e
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class StateStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        os.chmod(self.path, 0o600)
        # Rollback journal supports a genuinely read-only dashboard volume even
        # after the writer exits (WAL readers may need to create -shm/-wal).
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS heartbeat (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
        """)

    def load(self):
        row = self.conn.execute("SELECT revision, payload FROM checkpoint WHERE id=1").fetchone()
        if not row:
            return 0, None
        payload = json.loads(row[1])
        if not isinstance(payload, dict) or not payload:
            raise ValueError("Empty or malformed checkpoint; refusing to reset")
        return row[0], payload

    def save(self, state, events=(), expected_revision=0):
        payload = encode(state)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT revision FROM checkpoint WHERE id=1").fetchone()
            revision = row[0] if row else 0
            if revision != expected_revision:
                raise RuntimeError("Stale writer; refusing to overwrite a newer checkpoint")
            self.conn.execute(
                "INSERT INTO checkpoint VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload",
                (revision + 1, payload),
            )
            for event in events:
                key = f"{event['time']}:{event['type']}:{event.get('trade_id', '')}"
                self.conn.execute("INSERT INTO events(event_key,payload) VALUES (?,?)", (key, encode(event)))
            self.conn.execute("COMMIT")
            return revision + 1
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def heartbeat(self, payload):
        self.conn.execute(
            "INSERT INTO heartbeat VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (encode(payload),),
        )

    def close(self):
        self.conn.close()


def read_status(path, limit=30):
    """Read-only, bounded view; no creation, migrations, or exchange credentials."""
    path = Path(path).resolve()
    if not path.exists():
        return {"heartbeat": {}, "recent_events": []}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        heartbeat = conn.execute("SELECT payload FROM heartbeat WHERE id=1").fetchone()
        rows = conn.execute(
            "SELECT payload FROM events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)
        ).fetchall()
        payload = json.loads(heartbeat[0]) if heartbeat else {}
        events = [json.loads(row[0]) for row in rows]
        if not isinstance(payload, dict) or not all(isinstance(event, dict) for event in events):
            raise ValueError("Invalid status schema")
        return {"heartbeat": payload, "recent_events": events}
