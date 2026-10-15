"""Local persistent, session-scoped evaluation records. Mount the directory for containers."""
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from threading import RLock


class ChatStore:
    def __init__(self, path):
        self.path, self.lock = Path(path), RLock()

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS chat_runs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, created TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS chat_runs_owner ON chat_runs(owner, created)')
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, owner, run_id, kind, payload):
        with self.lock, self.connection() as db:
            db.execute('INSERT INTO chat_runs VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload WHERE owner=excluded.owner',
                (run_id, str(owner), datetime.now(timezone.utc).isoformat(), kind, json.dumps(payload, allow_nan=False)))
            # Retain latest 100 records per session; all queries remain owner-scoped.
            db.execute('DELETE FROM chat_runs WHERE owner=? AND id NOT IN (SELECT id FROM chat_runs WHERE owner=? ORDER BY created DESC LIMIT 100)', (str(owner), str(owner)))
            db.execute('DELETE FROM chat_runs WHERE created < ?', ((datetime.now(timezone.utc)-timedelta(days=7)).isoformat(),))

    def list(self, owner):
        with self.lock, self.connection() as db:
            rows = db.execute('SELECT id,created,kind,payload FROM chat_runs WHERE owner=? ORDER BY created DESC LIMIT 100', (str(owner),)).fetchall()
        return [{'id': i, 'created_at': when, 'kind': kind, **json.loads(payload)} for i, when, kind, payload in rows]

    def get(self, owner, run_id):
        return next((r for r in self.list(owner) if r['id'] == str(run_id)), None)

    def rate(self, owner, run_id, rating):
        with self.lock:
            row = self.get(owner, run_id)
            if not row or row['kind'] != 'turn':
                raise LookupError('Run not found')
            row.setdefault('metrics', {}).setdefault('quality', {})['human_rating'] = rating
            self.save(owner, str(run_id), 'turn', row)
            return row
