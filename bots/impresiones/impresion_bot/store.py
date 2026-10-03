import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

STAGES = {
    'nuevos': '📥 NUEVOS / POR CONFIRMAR', 'listos': '🟢 LISTOS PARA HACER',
    'proceso': '🖨️ EN PROCESO', 'falta': '🟡 FALTA ALGO',
    'armar': '📦 LISTOS / ARMAR', 'terminados': '✅ TERMINADOS',
}
FLOW = ['nuevos', 'listos', 'proceso', 'armar', 'terminados']


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class Store:
    def __init__(self, path):
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=5000')
        if self.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"):
            existing = self.one("SELECT value FROM meta WHERE key='schema_version'")
            if existing and existing['value'] != '1':
                raise RuntimeError('Versión de base incompatible; requiere migración explícita.')
        self.db.executescript(Path(__file__).with_name('schema.sql').read_text())
        version = self.one("SELECT value FROM meta WHERE key='schema_version'")['value']
        if version != '1':
            raise RuntimeError('Versión de base incompatible; no se modificó el esquema existente.')
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO stages(key,name) VALUES(?,?)', STAGES.items())

    def backfill_bot_posts(self):
        from .tracking import backfill
        if self.db.in_transaction:
            backfill(self)
        else:
            with self.db: backfill(self)

    def one(self, sql, args=()):
        return self.db.execute(sql, args).fetchone()

    def all(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def event(self, oid, actor, kind, data):
        self.db.execute('INSERT INTO events(order_id,actor,at,kind,data) VALUES(?,?,?,?,?)',
                        (oid, actor, now(), kind, dump(data)))

    def enqueue(self, key, payload, *, method='send_message', kind='message', oid=None, version=None, token=None):
        self.db.execute('INSERT OR IGNORE INTO outbox(dedupe,method,payload,kind,order_id,version,session_token) VALUES(?,?,?,?,?,?,?)',
                        (key, method, dump(payload), kind, oid, version, token))

    def order(self, identity):
        row = self.one('SELECT * FROM orders WHERE id=? OR code=?', (identity, str(identity).upper()))
        if not row:
            raise ValueError('Pedido no encontrado.')
        return dict(row) | {'data': json.loads(row['data'])}

    def backup(self, target):
        if Path(target).exists():
            raise ValueError('La copia de destino ya existe; usa un nombre nuevo.')
        dest = sqlite3.connect(target)
        try:
            self.db.backup(dest)
        finally:
            dest.close()
