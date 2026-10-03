"""Asocia avisos antiguos únicamente cuando existe evidencia explícita del pedido."""
import json
import re


def backfill(store):
    for row in store.all("SELECT * FROM outbox WHERE method='send_message'"):
        payload=json.loads(row['payload']);oid=row['order_id']
        if not oid:
            for code in re.findall(r'T-\d{4}-\d+',payload.get('text','')):
                order=store.one('SELECT id FROM orders WHERE code=?',(code,))
                if order: oid=order['id'];break
        if not oid:
            for line in payload.get('reply_markup',{}).get('inline_keyboard',[]):
                for button in line:
                    parts=button.get('callback_data','').split(':')
                    if len(parts)<2 or not parts[1].isdigit():continue
                    if parts[0] in {'qclose','qmove','qadd','open','hist','o'}:
                        oid=int(parts[1])
                    elif parts[0] in {'qclean','qcleanok','qkeep'}:
                        t=store.one('SELECT order_id FROM transfers WHERE id=?',(int(parts[1]),))
                        if t:oid=t['order_id']
        if not oid and row['dedupe'].startswith(('cleaned:','cleanup-offer:')):
            t=store.one('SELECT order_id FROM transfers WHERE id=?',(int(row['dedupe'].split(':')[1]),))
            if t:oid=t['order_id']
        if not oid and payload.get('text','').startswith('Copiando el pedido completo a '):
            # Aviso encolado inmediatamente después de las operaciones de copia del traslado.
            before=store.one("SELECT order_id FROM outbox WHERE id<? AND kind='copy' ORDER BY id DESC LIMIT 1",(row['id'],))
            if before:oid=before['order_id']
        if not oid and payload.get('text','').startswith(('Limpieza solicitada.','Limpiando también fichas')):
            before=store.one("SELECT order_id FROM outbox WHERE id<? AND kind='cleanup' ORDER BY id DESC LIMIT 1",(row['id'],))
            if before:oid=before['order_id']
        if not oid or not store.one('SELECT 1 FROM orders WHERE id=?',(oid,)):continue
        store.db.execute('UPDATE outbox SET order_id=? WHERE id=?',(oid,row['id']))
        if row['status']=='sent' and row['message_id'] and payload.get('message_thread_id'):
            store.db.execute('INSERT OR IGNORE INTO bot_posts(chat_id,message_id,thread_id,order_id,outbox_id) VALUES(?,?,?,?,?)',
                (payload['chat_id'],row['message_id'],payload['message_thread_id'],oid,row['id']))
    for row in store.all('SELECT * FROM messages'):
        job=store.one('SELECT id FROM outbox WHERE order_id=? AND message_id=? ORDER BY id LIMIT 1',(row['order_id'],row['message_id']))
        if job and row['thread_id']:
            store.db.execute('INSERT OR IGNORE INTO bot_posts(chat_id,message_id,thread_id,order_id,outbox_id) VALUES(?,?,?,?,?)',
                (row['chat_id'],row['message_id'],row['thread_id'],row['order_id'],job['id']))

    for t in store.all('SELECT * FROM transfers WHERE id NOT IN (SELECT transfer_id FROM transfer_sources)'):
        item=store.one('SELECT source_id FROM transfer_items WHERE transfer_id=? LIMIT 1',(t['id'],))
        thread=None
        if item:
            old=store.one('SELECT t.target_thread FROM transfer_items i JOIN transfers t ON t.id=i.transfer_id WHERE i.destination_id=? AND t.id<? ORDER BY t.id DESC LIMIT 1',(item['source_id'],t['id']))
            if old:thread=old[0]
            else:
                original=store.one('SELECT thread_id FROM files WHERE order_id=? AND message_id=?',(t['order_id'],item['source_id']))
                if original:thread=original[0]
        if thread is not None:
            store.db.execute('INSERT INTO transfer_sources VALUES(?,?)',(t['id'],thread))
