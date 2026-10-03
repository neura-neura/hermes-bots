PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES('schema_version','1');
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, admin INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS stages(key TEXT PRIMARY KEY, name TEXT NOT NULL, thread_id INTEGER UNIQUE);
CREATE TABLE IF NOT EXISTS orders(
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, stage TEXT NOT NULL REFERENCES stages(key),
 previous_stage TEXT, version INTEGER NOT NULL DEFAULT 1, data TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER REFERENCES orders(id),
 actor INTEGER NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'Historial inmutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'Historial inmutable'); END;
CREATE TABLE IF NOT EXISTS pending(
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
 description TEXT NOT NULL, owner INTEGER NOT NULL REFERENCES users(id), due TEXT, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS files(
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
 file_id TEXT, unique_id TEXT, name TEXT, mime TEXT, size INTEGER, kind TEXT NOT NULL,
 chat_id INTEGER NOT NULL, thread_id INTEGER, message_id INTEGER NOT NULL, album TEXT,
 UNIQUE(order_id,chat_id,message_id)
);
CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS callbacks(id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS sessions(
 user_id INTEGER PRIMARY KEY, token TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,
 thread_id INTEGER, prompt_id INTEGER, expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS albums(album TEXT PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), actor INTEGER NOT NULL, thread_id INTEGER, expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(
 id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe TEXT UNIQUE NOT NULL, method TEXT NOT NULL,
 payload TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'message', order_id INTEGER REFERENCES orders(id),
 version INTEGER, session_token TEXT, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 available REAL NOT NULL DEFAULT 0, error TEXT, message_id INTEGER
);
CREATE TABLE IF NOT EXISTS messages(
 chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, thread_id INTEGER,
 order_id INTEGER NOT NULL REFERENCES orders(id), version INTEGER NOT NULL, current INTEGER NOT NULL,
 PRIMARY KEY(chat_id,message_id)
);
CREATE INDEX IF NOT EXISTS outbox_status ON outbox(status,available);
CREATE INDEX IF NOT EXISTS events_order ON events(order_id,id);
-- Buffer temporal: solo metadatos de archivos autorizados; nunca texto de conversaciones.
CREATE TABLE IF NOT EXISTS album_buffer(
 chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, album TEXT NOT NULL,
 actor INTEGER NOT NULL, thread_id INTEGER, payload TEXT NOT NULL, expires TEXT NOT NULL,
 PRIMARY KEY(chat_id,message_id)
);
CREATE INDEX IF NOT EXISTS album_buffer_group ON album_buffer(album,actor,thread_id);
-- Flujo rápido, extensión aditiva compatible con datos anteriores.
CREATE TABLE IF NOT EXISTS captures(actor INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), thread_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS content(
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
 actor INTEGER NOT NULL, original_id INTEGER NOT NULL, current_id INTEGER NOT NULL,
 thread_id INTEGER NOT NULL, album TEXT, payload TEXT NOT NULL,
 UNIQUE(order_id,original_id)
);
CREATE TABLE IF NOT EXISTS transfers(
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
 actor INTEGER NOT NULL, source_stage TEXT NOT NULL, target_stage TEXT NOT NULL,
 target_thread INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_transfer ON transfers(order_id) WHERE status='copying';
CREATE TABLE IF NOT EXISTS transfer_items(
 transfer_id INTEGER NOT NULL REFERENCES transfers(id), content_id INTEGER NOT NULL REFERENCES content(id),
 source_id INTEGER NOT NULL, destination_id INTEGER,
 PRIMARY KEY(transfer_id,content_id)
);
CREATE TABLE IF NOT EXISTS dependencies(job_id INTEGER NOT NULL REFERENCES outbox(id), prerequisite INTEGER NOT NULL REFERENCES outbox(id), PRIMARY KEY(job_id,prerequisite));
CREATE TABLE IF NOT EXISTS bot_posts(
 chat_id INTEGER NOT NULL,message_id INTEGER NOT NULL,thread_id INTEGER NOT NULL,
 order_id INTEGER NOT NULL REFERENCES orders(id),outbox_id INTEGER NOT NULL REFERENCES outbox(id),
 deleted INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(chat_id,message_id)
);
CREATE TABLE IF NOT EXISTS control_posts(
 chat_id INTEGER NOT NULL,message_id INTEGER NOT NULL,thread_id INTEGER NOT NULL,
 order_id INTEGER NOT NULL REFERENCES orders(id),PRIMARY KEY(chat_id,message_id)
);
CREATE TABLE IF NOT EXISTS cleanup_bot_jobs(
 transfer_id INTEGER NOT NULL REFERENCES transfers(id),outbox_id INTEGER NOT NULL REFERENCES outbox(id),
 PRIMARY KEY(transfer_id,outbox_id)
);
CREATE TABLE IF NOT EXISTS transfer_sources(transfer_id INTEGER PRIMARY KEY REFERENCES transfers(id),thread_id INTEGER NOT NULL);
-- Preferencia global persistente; cada traslado conserva el modo con el que empezó.
INSERT OR IGNORE INTO meta VALUES('auto_cleanup','0');
CREATE TABLE IF NOT EXISTS transfer_ui(
 transfer_id INTEGER PRIMARY KEY REFERENCES transfers(id), auto_clean INTEGER NOT NULL DEFAULT 0,
 copy_job INTEGER REFERENCES outbox(id), clean_job INTEGER REFERENCES outbox(id)
);
