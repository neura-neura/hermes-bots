"""Progreso confirmado por Telegram: una vista editable por operación, sin spam."""
import json
from .ui import link


def navigation(config, transfer, rows=None):
    rows = list(rows or [])
    url = link(config.group, transfer['target_thread'])
    if url:
        rows.append([{'text': '➡️ Abrir fase de destino', 'url': url}])
    return {'inline_keyboard': rows}


def start(store, config, transfer, phase, thread):
    key = f'progress:{transfer["id"]}:{phase}:start'
    store.enqueue(key, {'chat_id': config.group, 'message_thread_id': thread,
        'text': text(store, transfer, phase), 'disable_notification': True,
        'reply_markup': navigation(config, transfer)}, kind='progress',
        oid=transfer['order_id'], version=transfer['id'])
    job = store.one('SELECT id FROM outbox WHERE dedupe=?', (key,))[0]
    column = 'copy_job' if phase == 'copy' else 'clean_job'
    store.db.execute(f'UPDATE transfer_ui SET {column}=? WHERE transfer_id=?', (job, transfer['id']))


def text(store, transfer, phase):
    code = store.order(transfer['order_id'])['code']
    if phase == 'copy':
        done, total = store.one('SELECT count(destination_id),count(*) FROM transfer_items WHERE transfer_id=?', (transfer['id'],))
        ready = transfer['status'] in {'done', 'cleaning', 'cleaned'}
        # Una copia completa todavía requiere confirmar la publicación de la ficha.
        card = store.one("SELECT 1 FROM outbox WHERE dedupe=? AND status='sent'", (f'card:{transfer["order_id"]}:{store.order(transfer["order_id"])["version"]}',))
        if ready and card:
            return f'✅ {code} · Pedido en la fase de destino. {done}/{total} mensajes copiados.'
        label = 'Publicando la ficha…' if total and done == total else 'Copiando…'
    else:
        done, total = store.one("SELECT sum(status='sent'),count(*) FROM outbox WHERE kind='cleanup' AND version=?", (transfer['id'],))
        done = done or 0
        if transfer['status'] == 'cleaned':
            return f'✅ {code} · Tema anterior limpio. {done}/{total} mensajes retirados.'
        label = 'Limpiando tema anterior…'
    percent = int(100 * done / total) if total else 0
    return f'⏳ {code} · {label}\n{done}/{total} mensajes · {percent}%'


def refresh(store, config, tid, phase, error=None):
    column = 'copy_job' if phase == 'copy' else 'clean_job'
    row = store.one(f'SELECT o.* FROM transfer_ui u JOIN outbox o ON o.id=u.{column} WHERE u.transfer_id=?', (tid,))
    if not row or row['status'] != 'sent' or not row['message_id']:
        return
    # Una limpieza puede haber retirado el aviso del traslado del tema de origen.
    if store.one('SELECT 1 FROM bot_posts WHERE outbox_id=? AND deleted=1', (row['id'],)):
        return
    transfer = store.one('SELECT * FROM transfers WHERE id=?', (tid,))
    content = error or text(store, transfer, phase)
    payload = {'chat_id': config.group, 'message_id': row['message_id'],
        'text': content, 'reply_markup': navigation(config, transfer)}
    # Solo un edit pendiente por operación. Se reemplaza por el estado más reciente.
    key = f'progress:{tid}:{phase}:edit'
    existing = store.one('SELECT * FROM outbox WHERE dedupe=?', (key,))
    if existing and json.loads(existing['payload']) == payload:
        return
    store.enqueue(key, payload, method='edit_message_text', kind='progress_edit',
        oid=transfer['order_id'], version=tid)
    if existing:
        store.db.execute("UPDATE outbox SET payload=?,status='pending',attempts=0,available=0,error=NULL WHERE id=?", (json.dumps(payload), existing['id']))
