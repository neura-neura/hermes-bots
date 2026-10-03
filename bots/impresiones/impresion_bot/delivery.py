"""Outbox durable: los envíos ambiguos nunca se repiten automáticamente."""
import json
import time
from datetime import timedelta
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError
from .store import now
from .ui import esc, keyboard, link


def delivered(store, config, job, message_id):
    payload = json.loads(job['payload'])
    store.db.execute("UPDATE outbox SET status='sent',message_id=?,error=NULL WHERE id=?", (message_id,job['id']))
    if job['order_id'] and message_id and job['method']=='send_message' and payload.get('message_thread_id'):
        store.db.execute('INSERT OR IGNORE INTO bot_posts(chat_id,message_id,thread_id,order_id,outbox_id) VALUES(?,?,?,?,?)',
            (config.group,message_id,payload['message_thread_id'],job['order_id'],job['id']))
        from .quick import enqueue_delete
        for t in store.all('SELECT t.* FROM transfers t JOIN cleanup_bot_jobs c ON c.transfer_id=t.id WHERE c.outbox_id=?',(job['id'],)):
            enqueue_delete(store,config,t,message_id)
    if job['kind']=='cleanup' and message_id:
        for old in store.all("SELECT id,payload FROM outbox WHERE kind IN ('moved','progress_edit') AND status='pending'"):
            if json.loads(old['payload']).get('message_id')==message_id:
                store.db.execute("UPDATE outbox SET status='sent',error='Aviso innecesario: ficha limpiada' WHERE id=?",(old['id'],))
        store.db.execute('UPDATE bot_posts SET deleted=1 WHERE chat_id=? AND message_id=?',(config.group,message_id))
        store.db.execute('UPDATE messages SET current=0 WHERE chat_id=? AND message_id=?',(config.group,message_id))
    if job['kind'] == 'prompt':
        store.db.execute('UPDATE sessions SET prompt_id=? WHERE token=?', (message_id,job['session_token']))
    if job['kind']=='progress':
        from .progress import refresh
        phase='copy' if ':copy:' in job['dedupe'] else 'clean'
        refresh(store,config,job['version'],phase)
    if job['kind'] not in {'card','view'}:
        return
    if job['kind'] == 'card':
        previous = store.all('SELECT * FROM messages WHERE order_id=? AND current=1 AND message_id!=?', (job['order_id'],message_id))
        store.db.execute('UPDATE messages SET current=0 WHERE order_id=?', (job['order_id'],))
        for old in previous:
            order = store.order(job['order_id'])
            automatic=store.one("SELECT 1 FROM transfers t JOIN transfer_ui u ON u.transfer_id=t.id WHERE t.order_id=? AND t.status='done' AND u.auto_clean=1",(job['order_id'],))
            if automatic:
                continue  # La limpieza retirará la ficha; no deja avisos de traslado.
            stage = store.one('SELECT name FROM stages WHERE thread_id=?', (payload.get('message_thread_id'),))
            name = stage['name'] if stage else 'la ficha vigente'
            url = link(config.group,message_id)
            text = f"{esc(order['code'])} · Movido a {esc(name)} → " + (f'<a href="{url}">ver {esc(order["code"])}</a>' if url else esc(order['code']))
            store.enqueue(f"moved:{job['id']}:{old['message_id']}",
                          {'chat_id':config.group,'message_id':old['message_id'],'text':text,'parse_mode':'HTML',
                           'reply_markup':{'inline_keyboard':[[{'text':'➡️ Abrir fase de destino','url':url}]]} if url else keyboard([[('Abrir pedido',f"open:{job['order_id']}")]])},
                          method='edit_message_text',kind='moved',oid=job['order_id'],version=job['version'])
    thread = payload.get('message_thread_id')
    if thread is None:
        known = store.one('SELECT thread_id FROM messages WHERE chat_id=? AND message_id=?',(config.group,message_id))
        thread = known['thread_id'] if known else None
    store.db.execute('''INSERT INTO messages VALUES(?,?,?,?,?,?) ON CONFLICT(chat_id,message_id)
        DO UPDATE SET version=excluded.version,current=excluded.current''',
        (config.group,message_id,thread,job['order_id'],job['version'],int(job['kind']=='card')))
    if job['kind']=='card':
        from .quick import card_ready
        card_ready(store,config,job)


class Delivery:
    def __init__(self, store, config, bot):
        self.s, self.c, self.bot = store, config, bot

    def recover(self):
        with self.s.db:
            for job in self.s.all("SELECT * FROM outbox WHERE status='sending'"):
                status = 'pending' if job['method'] not in {'send_message','copy_messages'} else 'uncertain'
                self.s.db.execute('UPDATE outbox SET status=?,error=? WHERE id=?',(status,'Proceso interrumpido durante envío',job['id']))
                self.s.event(job['order_id'],0,'sincronizacion_interrumpida',{'job':job['id'],'estado':status})

    async def once(self):
        # No adelantar revisiones de un pedido cuya ficha anterior aún no se confirmó.
        job = self.s.one("""SELECT * FROM outbox o WHERE status='pending' AND available<=?
            AND (kind!='card' OR NOT EXISTS(SELECT 1 FROM outbox p WHERE p.order_id=o.order_id
                AND p.kind='card' AND p.id<o.id AND p.status!='sent'))
            AND NOT EXISTS(SELECT 1 FROM dependencies d JOIN outbox p ON p.id=d.prerequisite WHERE d.job_id=o.id AND p.status!='sent')
            ORDER BY CASE WHEN kind='ack' THEN 0 WHEN kind IN ('progress','progress_edit') THEN 1 ELSE 2 END,id LIMIT 1""", (time.time(),))
        if not job:
            return False
        job = dict(job)
        self.last_kind=job['kind']
        payload = json.loads(job['payload'])
        with self.s.db:
            if job['kind'] == 'card' and job['method'] == 'send_message':
                current = self.s.one('SELECT * FROM messages WHERE order_id=? AND current=1',(job['order_id'],))
                if current and current['thread_id'] == payload.get('message_thread_id'):
                    payload.pop('message_thread_id',None)
                    payload.pop('disable_notification',None)
                    payload['message_id'] = current['message_id']
                    job['method'], job['payload'] = 'edit_message_text', json.dumps(payload)
                    self.s.db.execute('UPDATE outbox SET method=?,payload=? WHERE id=?',(job['method'],job['payload'],job['id']))
            self.s.db.execute("UPDATE outbox SET status='sending',attempts=attempts+1 WHERE id=?",(job['id'],))
        try:
            result = await getattr(self.bot, job['method'])(**payload)
            if job['kind']=='copy':
                from .quick import copied
                with self.s.db: copied(self.s,self.c,job,result)
                return True
            mid = getattr(result,'message_id',None) or payload.get('message_id')
            with self.s.db:
                delivered(self.s,self.c,job,mid)
                if job['kind']=='cleanup':
                    self.s.event(job['order_id'],0,'mensaje_eliminado',{'mensaje':payload['message_id'],'traslado':job['version']})
                    from .quick import cleanup_done
                    cleanup_done(self.s,self.c,job)
        except RetryAfter as error:
            delay = error.retry_after
            delay = delay.total_seconds() if isinstance(delay,timedelta) else delay
            self.failure(job,'pending','Telegram solicita esperar',max(1,delay)+1)
        except BadRequest as error:
            if job['kind']=='cleanup' and 'message to delete not found' in str(error).lower():
                with self.s.db:
                    delivered(self.s,self.c,job,payload['message_id'])
                    self.s.event(job['order_id'],0,'mensaje_ya_ausente',{'mensaje':payload['message_id']})
                    from .quick import cleanup_done
                    cleanup_done(self.s,self.c,job)
                return True
            # No persistir mensajes arbitrarios de excepción: podrían contener credenciales.
            if job['method']=='edit_message_text' and 'message is not modified' in str(error).lower():
                with self.s.db:
                    delivered(self.s,self.c,job,payload['message_id'])
            elif job['method']=='edit_message_text' and job['kind']=='card' and 'message to edit not found' in str(error).lower():
                # La ficha del bot desapareció: una nueva publicación es segura.
                with self.s.db:
                    old = self.s.one('SELECT thread_id FROM messages WHERE chat_id=? AND message_id=?',(self.c.group,payload['message_id']))
                    if old and old['thread_id']:
                        payload.pop('message_id')
                        payload['message_thread_id'] = old['thread_id']
                        payload['disable_notification'] = True
                        self.s.db.execute("UPDATE outbox SET method='send_message',payload=?,status='pending' WHERE id=?",(json.dumps(payload),job['id']))
                        self.s.db.execute('UPDATE messages SET current=0 WHERE order_id=?',(job['order_id'],))
                        self.s.event(job['order_id'],0,'ficha_ausente',{'job':job['id']})
                    else:
                        self.failure(job,'failed','Ficha ausente sin Topic registrado')
            else:
                self.failure(job,'failed','Solicitud rechazada: revisa Topic, permisos o callback vencido')
        except Forbidden:
            self.failure(job,'failed','Sin permisos en Telegram')
        except NetworkError:
            status = 'uncertain' if job['method'] in {'send_message','copy_messages'} else 'pending'
            self.failure(job,status,'Resultado de red incierto', min(300,2**min(job['attempts']+1,8)))
        except TelegramError:
            self.failure(job,'failed','Error de Telegram; revisar configuración')
        return True

    def failure(self, job, status, message, delay=0):
        if status=='pending' and job['attempts']+1>=5:
            status='failed'
        if job['kind']=='ack':
            status='sent'  # La confirmación efímera no bloquea las operaciones durables.
        with self.s.db:
            self.s.db.execute('UPDATE outbox SET status=?,error=?,available=? WHERE id=?',(status,message,time.time()+delay,job['id']))
            if job['kind'] in {'copy','cleanup'} and status in {'failed','uncertain'}:
                from .quick import notify
                notify(self.s,self.c,job,'No se completó '+('la copia; originales conservados' if job['kind']=='copy' else 'un borrado; la copia nueva está guardada')+'. Revisa /configurar fallos.')
            if job['kind'] in {'copy','cleanup'}:
                from .progress import refresh
                phase='copy' if job['kind']=='copy' else 'clean'
                label='⏸️ Telegram pide esperar; reintento pendiente.' if status=='pending' else '⚠️ Operación sin completar. Revisa /configurar fallos.'
                refresh(self.s,self.c,job['version'],phase,label)
            elif job['kind']=='card' and status in {'failed','uncertain'}:
                transfer=self.s.one("SELECT * FROM transfers WHERE order_id=? AND status='done' ORDER BY id DESC LIMIT 1",(job['order_id'],))
                if transfer:
                    from .progress import refresh
                    from .quick import notify
                    refresh(self.s,self.c,transfer['id'],'copy','⚠️ Contenido copiado; ficha sin confirmar. Originales conservados. Revisa /configurar fallos.')
                    notify(self.s,self.c,dict(job,version=transfer['id']),'La ficha nueva no se pudo confirmar. No se borraron los originales. Revisa /configurar fallos.')
            if job['kind']!='ack':
                self.s.event(job['order_id'],0,'error_sincronizacion',{'job':job['id'],'estado':status,'error':message})
