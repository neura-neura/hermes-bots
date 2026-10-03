"""Captura libre y traslado durable de contenido con limpieza confirmada."""
import json
from .store import dump, now, FLOW, STAGES
from .ui import esc, keyboard


def busy(s, oid):
    return s.one("SELECT 1 FROM transfers WHERE order_id=? AND status IN ('copying','cleaning')", (oid,))


class Quick:
    def __init__(self, engine):
        self.e = engine
        self.s = engine.s

    def open(self, title=''):
        e = self.e
        if self.s.one('SELECT 1 FROM captures WHERE actor=?',(e.actor,)):
            raise ValueError('Ya tienes un pedido abierto. Envía /cerrar antes de abrir otro.')
        stage = self.s.one("SELECT thread_id FROM stages WHERE key='nuevos'")
        if e.thread != stage['thread_id'] or not e.thread:
            raise ValueError('Abre el pedido dentro del tema Nuevos / Por confirmar.')
        o=e.d.create(e.actor,{'cliente':title[:80] or 'Pedido nuevo','rapido':True})
        self.s.db.execute('INSERT INTO captures VALUES(?,?,?)',(e.actor,o['id'],e.thread))
        e.context_oid=o['id']
        self.control(o['id'])
        e.say(f"<b>{o['code']} abierto.</b> Envía textos, fotos y archivos aquí. Solo se incluye lo que tú envíes en este tema. Al terminar: /cerrar.",keyboard([[('✅ Cerrar captura',f"qclose:{o['id']}")]]))

    def control(self, oid):
        e=self.e
        if e.msg.get('from',{}).get('id')==e.actor and (e.msg.get('text') or '').startswith('/'):
            self.s.db.execute('INSERT OR IGNORE INTO control_posts VALUES(?,?,?,?)',(e.c.group,e.msg['message_id'],e.thread,oid))

    def capture(self, msg, order_id=None):
        e=self.e
        capture=({'order_id':order_id} if order_id is not None else self.s.one('SELECT * FROM captures WHERE actor=? AND thread_id=?',(e.actor,e.thread)))
        if not capture:
            return False
        keys=('text','caption','entities','caption_entities','document','photo','video','audio','voice','animation','video_note','sticker','contact','location','venue')
        if not any(msg.get(k) for k in keys):
            if any(msg.get(k) for k in ('poll','dice','paid_media')):
                e.plain('Ese tipo de mensaje no está soportado en pedidos. Envía texto, fotos o documentos.');return True
            return False
        if msg.get('has_protected_content'):
            e.plain('Ese mensaje está protegido y no se puede trasladar. Envía una versión sin protección.');return True
        payload={k:msg[k] for k in keys if k in msg}
        cur=self.s.db.execute('INSERT OR IGNORE INTO content(order_id,actor,original_id,current_id,thread_id,album,payload) VALUES(?,?,?,?,?,?,?)',
            (capture['order_id'],e.actor,msg['message_id'],msg['message_id'],e.thread,msg.get('media_group_id'),dump(payload)))
        if cur.rowcount:
            e.d.attach(capture['order_id'],e.actor,msg)
            self.s.event(capture['order_id'],e.actor,'contenido_capturado',{'mensaje':msg['message_id'],'contenido':payload})
            o=self.s.order(capture['order_id'])
            if o['data'].get('cliente')=='Pedido nuevo' and (msg.get('text') or msg.get('caption')):
                data=o['data'] | {'cliente':(msg.get('text') or msg['caption']).splitlines()[0][:80]}
                self.s.db.execute('UPDATE orders SET data=? WHERE id=?',(dump(data),o['id']))
        return True

    def close(self, oid=None):
        e=self.e
        cap=self.s.one('SELECT * FROM captures WHERE actor=? AND thread_id=?',(e.actor,e.thread))
        if not cap or (oid is not None and cap['order_id']!=oid):
            raise ValueError('No tienes una captura abierta aquí.')
        e.context_oid=cap['order_id']
        self.control(cap['order_id'])
        n=self.s.one('SELECT count(*) FROM content WHERE order_id=?',(cap['order_id'],))[0]
        if not n:
            raise ValueError('Envía al menos un mensaje o archivo antes de cerrar.')
        self.s.db.execute('DELETE FROM captures WHERE actor=?',(e.actor,))
        self.s.event(cap['order_id'],e.actor,'captura_cerrada',{'mensajes':n})
        e.publish(e.d.touch(cap['order_id']))

    def move(self, oid, version, target):
        e=self.e;o=e.d.check(oid,version)
        if not o['data'].get('rapido'):
            raise ValueError('Este pedido usa la ficha anterior; usa Cambiar etapa.')
        e.context_oid=oid
        source_topic=self.s.one('SELECT thread_id FROM stages WHERE key=?',(o['stage'],))[0]
        if e.thread!=source_topic:
            raise ValueError('Esta ficha está en un tema anterior. Abre /pedido '+o['code']+' dentro del tema actual para avanzar.')
        if busy(self.s,oid) or self.s.one('SELECT 1 FROM captures WHERE order_id=?',(oid,)):
            raise ValueError('Primero termina la captura o el traslado pendiente.')
        allowed = ([FLOW[FLOW.index(o['stage'])+1]] if o['stage'] in FLOW[:-1] else [])
        if o['stage']=='falta': allowed=['armar',o['previous_stage'] or 'proceso']
        elif o['stage']!='terminados': allowed.append('falta')
        if target not in allowed:
            raise ValueError('Ese cambio no corresponde al flujo actual.')
        self.control(oid)
        stage=self.s.one('SELECT * FROM stages WHERE key=?',(target,))
        if not stage or not stage['thread_id']:
            raise ValueError('Falta configurar el tema de destino.')
        rows=self.s.all('SELECT * FROM content WHERE order_id=? ORDER BY current_id',(oid,))
        if not rows: raise ValueError('El pedido no tiene contenido capturado.')
        tid=self.s.db.execute('INSERT INTO transfers(order_id,actor,source_stage,target_stage,target_thread,status,created_at) VALUES(?,?,?,?,?,?,?)',
            (oid,e.actor,o['stage'],target,stage['thread_id'],'copying',now())).lastrowid
        self.s.db.execute('INSERT INTO transfer_sources VALUES(?,?)',(tid,source_topic))
        self.s.db.execute('INSERT INTO transfer_ui(transfer_id,auto_clean) VALUES(?,?)',
            (tid,int(self.s.one("SELECT value FROM meta WHERE key='auto_cleanup'")[0]=='1')))
        jobs=[]
        # Copiar por álbum (o mensaje individual) permite comprobar que no hubo omisiones.
        groups=[]
        for row in rows:
            if groups and row['album'] and groups[-1][0]['album']==row['album'] and len(groups[-1])<100:
                groups[-1].append(row)
            else: groups.append([row])
        for index,group in enumerate(groups):
            self.s.db.executemany('INSERT INTO transfer_items VALUES(?,?,?,NULL)',[(tid,r['id'],r['current_id']) for r in group])
            key=f'transfer:{tid}:{index}'
            self.s.enqueue(key,{'chat_id':e.c.group,'from_chat_id':e.c.group,'message_thread_id':stage['thread_id'],
                'message_ids':[r['current_id'] for r in group],'disable_notification':True},method='copy_messages',kind='copy',oid=oid,version=tid)
            job=self.s.one('SELECT id FROM outbox WHERE dedupe=?',(key,))[0]
            if jobs: self.s.db.execute('INSERT INTO dependencies VALUES(?,?)',(job,jobs[-1]))
            jobs.append(job)
        self.s.event(oid,e.actor,'traslado_solicitado',{'traslado':tid,'destino':target,'mensajes':len(rows)})
        from .progress import start
        start(self.s,e.c,self.s.one('SELECT * FROM transfers WHERE id=?',(tid,)),'copy',source_topic)

    def cleanup(self, tid, confirm=False, automatic=False):
        e=self.e;t=self.s.one('SELECT * FROM transfers WHERE id=?',(tid,))
        if not t or t['status'] not in {'done','cleaned','cleaning'}:
            raise ValueError('El traslado no está listo para limpiar.')
        if t['status']=='cleaning':
            return  # Otro callback no reinicia ni duplica la limpieza.
        e.context_oid=t['order_id']
        rows=self.s.all('SELECT * FROM transfer_items WHERE transfer_id=?',(tid,))
        if not rows or any(r['destination_id'] is None for r in rows):
            raise ValueError('No se borrará nada: faltan copias confirmadas.')
        snapshot=self.s.one('SELECT thread_id FROM transfer_sources WHERE transfer_id=?',(tid,))
        source=snapshot[0] if snapshot else self.s.one('SELECT thread_id FROM stages WHERE key=?',(t['source_stage'],))[0]
        if source==t['target_thread']:
            raise ValueError('Origen y destino coinciden; no se puede limpiar con seguridad.')
        if not confirm:
            e.say('¿Limpiar los mensajes del pedido, sus comandos y los avisos/fichas del bot en el tema anterior? Las copias llegaron completas. Otros pedidos y mensajes fijados del negocio se conservan. Telegram puede rechazar mensajes de más de 48 h.',
                keyboard([[('Sí, limpiar tema anterior',f'qcleanok:{tid}')],[('Conservar originales',f'qkeep:{tid}')]]));return
        ids={r['source_id'] for r in rows}
        ids.update(r['message_id'] for r in self.s.all('SELECT * FROM bot_posts WHERE order_id=? AND thread_id=? AND deleted=0',(t['order_id'],source)))
        ids.update(r['message_id'] for r in self.s.all('SELECT * FROM control_posts WHERE order_id=? AND thread_id=?',(t['order_id'],source)))
        # Nunca eliminar copias vigentes si el pedido regresó a ese Topic.
        current={r['current_id'] for r in self.s.all('SELECT current_id FROM content WHERE order_id=?',(t['order_id'],))}
        ids-=current
        active=self.s.one('SELECT message_id,thread_id FROM messages WHERE order_id=? AND current=1',(t['order_id'],))
        if active and active['thread_id']==source:
            raise ValueError('El pedido regresó a ese tema. Limpia usando el traslado más reciente.')
        for job in self.s.all("SELECT * FROM outbox WHERE order_id=? AND method='send_message' AND status IN ('pending','sending','sent')",(t['order_id'],)):
            if json.loads(job['payload']).get('message_thread_id')==source:
                self.s.db.execute('INSERT OR IGNORE INTO cleanup_bot_jobs VALUES(?,?)',(tid,job['id']))
                if job['status']=='sent' and job['message_id']: ids.add(job['message_id'])
        for mid in sorted(ids):
            enqueue_delete(self.s,e.c,t,mid)
        self.s.db.execute("UPDATE transfers SET status='cleaning' WHERE id=?",(tid,))
        self.s.event(t['order_id'],e.actor,'limpieza_confirmada',{'traslado':tid,'mensajes':sorted(ids),'incluye_bot':True,'automatica':automatic})
        e.thread=t['target_thread']
        if not automatic:
            from .progress import start
            self.s.db.execute('INSERT OR IGNORE INTO transfer_ui(transfer_id) VALUES(?)',(tid,))
            start(self.s,e.c,self.s.one('SELECT * FROM transfers WHERE id=?',(tid,)),'clean',e.thread)
        if not ids:
            cleanup_done(self.s,e.c,{'version':tid,'order_id':t['order_id']})


def copied(store, config, job, result):
    payload=json.loads(job['payload']); tid=job['version']
    mids=[r.message_id for r in result]
    if len(mids)!=len(payload['message_ids']):
        store.db.execute("UPDATE outbox SET status='uncertain',error=? WHERE id=?",('Copia incompleta; no borrar originales. Revisar destino antes de reintentar.',job['id']))
        store.event(job['order_id'],0,'copia_incompleta',{'traslado':tid,'recibidos':mids})
        notify(store,config,job,'Copia incompleta. Se conservaron los originales y la etapa anterior. Revisa /configurar fallos.');return
    for source,destination in zip(payload['message_ids'],mids):
        store.db.execute('UPDATE transfer_items SET destination_id=? WHERE transfer_id=? AND source_id=?',(destination,tid,source))
    store.db.execute("UPDATE outbox SET status='sent',error=NULL WHERE id=?",(job['id'],))
    from .progress import refresh
    refresh(store,config,tid,'copy')
    if store.one('SELECT 1 FROM transfer_items WHERE transfer_id=? AND destination_id IS NULL',(tid,)): return
    t=store.one('SELECT * FROM transfers WHERE id=?',(tid,))
    for item in store.all('SELECT * FROM transfer_items WHERE transfer_id=?',(tid,)):
        store.db.execute('UPDATE content SET current_id=?,thread_id=? WHERE id=?',(item['destination_id'],t['target_thread'],item['content_id']))
    previous=t['source_stage'] if t['target_stage']=='falta' else store.order(t['order_id'])['previous_stage']
    store.db.execute('UPDATE orders SET stage=?,previous_stage=?,version=version+1,updated_at=? WHERE id=?',(t['target_stage'],previous,now(),t['order_id']))
    store.db.execute("UPDATE transfers SET status='done' WHERE id=?",(tid,))
    store.event(t['order_id'],t['actor'],'entregado' if t['target_stage']=='terminados' else 'etapa',{'antes':t['source_stage'],'despues':t['target_stage'],'traslado':tid})
    from .engine import Engine
    Engine(store,config).publish(store.order(t['order_id']))
    card_job=store.one("SELECT id FROM outbox WHERE dedupe=?",(f"card:{t['order_id']}:{store.order(t['order_id'])['version']}",))[0]
    mode=store.one('SELECT auto_clean FROM transfer_ui WHERE transfer_id=?',(tid,))
    if mode and mode[0]: return  # La ficha confirmada dispara la limpieza automática.
    from .progress import navigation
    store.enqueue(f'cleanup-offer:{tid}',{'chat_id':config.group,'message_thread_id':t['target_thread'],
        'text':'✅ Todo el contenido fue copiado. Limpiar retirará también los avisos y fichas del bot del tema anterior.',
        'reply_markup':navigation(config,t,[[{'text':'🧹 Limpiar tema anterior','callback_data':f'qclean:{tid}'}]])},oid=t['order_id'])
    offer=store.one('SELECT id FROM outbox WHERE dedupe=?',(f'cleanup-offer:{tid}',))[0]
    store.db.execute('INSERT INTO dependencies VALUES(?,?)',(offer,card_job))


def notify(store, config, job, text):
    t=store.one('SELECT * FROM transfers WHERE id=?',(job['version'],))
    if t:
        store.enqueue(f'notice:{job["version"]}:{job["kind"]}',{'chat_id':config.group,'message_thread_id':t['target_thread'],'text':text,'disable_notification':True},oid=t['order_id'] if 'order_id' in t.keys() else job['order_id'])


def cleanup_done(store, config, job):
    from .progress import refresh
    refresh(store,config,job['version'],'clean')
    if store.one("SELECT 1 FROM cleanup_bot_jobs c JOIN outbox o ON o.id=c.outbox_id WHERE c.transfer_id=? AND o.status!='sent'",(job['version'],)):
        return
    if store.one("SELECT 1 FROM outbox WHERE kind='cleanup' AND version=? AND status!='sent'",(job['version'],)):
        return
    store.db.execute("UPDATE transfers SET status='cleaned' WHERE id=?",(job['version'],))
    store.event(job['order_id'],0,'limpieza_completada',{'traslado':job['version']})
    mode=store.one('SELECT * FROM transfer_ui WHERE transfer_id=?',(job['version'],))
    if mode and (mode['auto_clean'] or mode['clean_job']):
        refresh(store,config,job['version'],'clean')
        return
    t=store.one('SELECT target_thread FROM transfers WHERE id=?',(job['version'],))
    store.enqueue(f'cleaned:{job["version"]}',{'chat_id':config.group,'message_thread_id':t['target_thread'],
        'text':'✅ Tema anterior limpio: contenido, comandos, fichas y avisos del pedido retirados.','disable_notification':True},oid=t['order_id'] if 'order_id' in t.keys() else job['order_id'])


def enqueue_delete(store, config, transfer, mid):
    store.enqueue(f"clean-message:{transfer['id']}:{mid}",{'chat_id':config.group,'message_id':mid},
        method='delete_message',kind='cleanup',oid=transfer['order_id'],version=transfer['id'])


def card_ready(store, config, job):
    """Borrar solo después de confirmar todas las copias y la ficha del traslado."""
    t=store.one("SELECT t.* FROM transfers t WHERE t.order_id=? AND t.status='done' ORDER BY t.id DESC LIMIT 1",(job['order_id'],))
    if not t or store.order(t['order_id'])['version']!=job['version']:
        return
    from .progress import refresh
    refresh(store,config,t['id'],'copy')
    mode=store.one('SELECT auto_clean FROM transfer_ui WHERE transfer_id=?',(t['id'],))
    if mode and mode[0]:
        from .engine import Engine
        e=Engine(store,config)
        e.actor=t['actor'];e.thread=t['target_thread'];e.context_oid=t['order_id']
        e.msg={};e.key=f'auto-clean:{t["id"]}'
        try:
            Quick(e).cleanup(t['id'],confirm=True,automatic=True)
        except ValueError:
            notify(store,config,{'version':t['id'],'kind':'cleanup','order_id':t['order_id']},'No se pudo limpiar automáticamente. La copia está guardada; revisa /configurar fallos.')
