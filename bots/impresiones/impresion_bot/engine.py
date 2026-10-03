"""Update -> transacción de dominio y outbox. Sin llamadas de red dentro de la transacción."""
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter
from .domain import Domain
from .store import STAGES, now, dump
from .ui import esc, keyboard, card, actions, link

HELP = '''<b>Pedidos de impresión</b>
/nuevo — abrir pedido sin formulario
/cerrar — terminar de enviar mensajes y archivos
/pasar ID [2|3|4|5|6] — copiar todo a otra etapa
/formulario — captura detallada opcional
/pedido T-2026-0001 — ficha
/buscar texto · /pendientes · /hoy · /resumen
/configurar — administradores
/autolimpiar activar|desactivar — limpieza automática (administradores)
/cancelar — cancelar captura

Responde a una ficha con archivos para vincularlos. Para texto usa Añadir nota o /vincular ID respondiendo al mensaje original. No se capturan mensajes sueltos. Las notas internas son visibles al grupo.
Usa /comando@NombreDelBot si hay otros bots.'''


class Engine:
    def __init__(self, store, config):
        self.s, self.c = store, config
        self.d = Domain(store, config)
        store.backfill_bot_posts()
        path = Path(config.templates_path) if config.templates_path else Path(__file__).with_name('templates.json')
        self.templates = json.loads(path.read_text())
        # Validar también las plantillas locales antes de arrancar.
        import re
        if not isinstance(self.templates,dict) or not self.templates or len(self.templates)>12:
            raise ValueError('Configura entre 1 y 12 plantillas.')
        for key,template in self.templates.items():
            if not re.fullmatch('[a-z0-9_]{1,32}',key) or not isinstance(template,dict):
                raise ValueError('Plantilla inválida: usa nombre corto y campos reconocidos.')
            self.templates[key] = self.d.parse('\n'.join(f'{k}={v}' for k, v in template.items()))
        self.username = ''
        self.seq = 0

    def authorized(self, update):
        query = update.get('callback_query')
        msg = query.get('message', {}) if query else update.get('message', {})
        actor = (query or msg).get('from', {})
        return (msg.get('chat', {}).get('id') == self.c.group and
                actor.get('id') in {self.c.magui, self.c.kevin} and not actor.get('is_bot') and
                not msg.get('sender_chat'))

    def process(self, update):
        uid = update['update_id']
        with self.s.db:
            if self.s.one('SELECT 1 FROM updates WHERE id=?', (uid,)):
                return
            self.s.db.execute('INSERT INTO updates VALUES(?,?)', (uid, now()))
            self.s.db.execute('DELETE FROM album_buffer WHERE expires<=?', (now(),))
            if not self.authorized(update):
                return
            query = update.get('callback_query')
            self.msg = query['message'] if query else update['message']
            self.actor = (query or self.msg)['from']['id']
            self.thread = self.msg.get('message_thread_id')
            self.key, self.seq = f'u:{uid}', 0
            self.context_oid = None
            if query:
                bits=query.get('data','').split(':')
                if bits[0] in {'qmove','qclose','qadd','open','hist','ev','file','pend','o'} and len(bits)>1 and bits[1].isdigit():
                    self.context_oid=int(bits[1])
                elif bits[0] in {'qclean','qcleanok','qkeep'} and len(bits)>1 and bits[1].isdigit():
                    transfer=self.s.one('SELECT order_id FROM transfers WHERE id=?',(int(bits[1]),))
                    if transfer: self.context_oid=transfer['order_id']
            if query:
                if self.s.one('SELECT 1 FROM callbacks WHERE id=?', (query['id'],)):
                    return
                self.s.db.execute('INSERT INTO callbacks VALUES(?)', (query['id'],))
                self.s.enqueue(self.key+':ack', {'callback_query_id': query['id']}, method='answer_callback_query', kind='ack')
            self.s.db.execute('SAVEPOINT action')
            try:
                if query:
                    self.callback(query.get('data', ''))
                else:
                    self.message()
                self.s.db.execute('RELEASE action')
            except (ValueError, sqlite3.IntegrityError) as error:
                self.s.db.execute('ROLLBACK TO action')
                self.s.db.execute('RELEASE action')
                self.plain(str(error) if isinstance(error, ValueError) else 'Conflicto: ese Topic ya está asociado. Revisa /configurar.')

    def say(self, text, markup=None, *, kind='message', token=None):
        self.seq += 1
        payload = {'chat_id': self.c.group, 'text': text, 'parse_mode': 'HTML', 'disable_notification': True}
        if self.thread:
            payload['message_thread_id'] = self.thread
        if markup:
            payload['reply_markup'] = markup
        self.s.enqueue(f'{self.key}:{self.seq}', payload, kind=kind, token=token, oid=getattr(self,'context_oid',None))

    def plain(self, text):
        self.say(esc(text))

    def session(self, kind, data, prompt=None):
        token = secrets.token_hex(6)
        expires = (datetime.now(timezone.utc)+timedelta(hours=24)).isoformat(timespec='seconds')
        self.s.db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?,NULL,?)',
                          (self.actor, token, kind, dump(data), self.thread, expires))
        if prompt:
            # Mention makes ForceReply selective for the initiating operator.
            self.say(f'<a href="tg://user?id={self.actor}">Tu respuesta</a> · {esc(prompt)}',
                     {'force_reply': True, 'selective': True}, kind='prompt', token=token)
        return token

    def get_session(self, token=None):
        s = self.s.one('SELECT * FROM sessions WHERE user_id=?', (self.actor,))
        if not s or s['expires'] < now() or s['thread_id'] != self.thread or (token and token != s['token']):
            raise ValueError('Captura vencida o de otro Topic. Vuelve a iniciar la acción.')
        return dict(s) | {'data': json.loads(s['data'])}

    def end_session(self):
        self.s.db.execute('DELETE FROM sessions WHERE user_id=?', (self.actor,))

    def publish(self, order):
        stage = self.s.one('SELECT * FROM stages WHERE key=?', (order['stage'],))
        if stage['thread_id'] is None:
            raise ValueError('Falta asociar el Topic de ' + stage['name'] + ' con /configurar.')
        # Una sola publicación por revisión. Si existe en este Topic, editarla es idempotente.
        current = self.s.one('SELECT * FROM messages WHERE order_id=? AND current=1', (order['id'],))
        payload = {'chat_id': self.c.group, 'text': card(order, self.d), 'parse_mode': 'HTML', 'reply_markup': actions(order)}
        url=link(self.c.group,stage['thread_id'])
        if url:
            payload['reply_markup']['inline_keyboard'].append([{'text':'➡️ Abrir esta fase','url':url}])
        if current and current['thread_id'] == stage['thread_id']:
            payload['message_id'] = current['message_id']
            method = 'edit_message_text'
        else:
            payload |= {'message_thread_id': stage['thread_id'], 'disable_notification': True}
            method = 'send_message'
        self.s.enqueue(f"card:{order['id']}:{order['version']}", payload, method=method, kind='card', oid=order['id'], version=order['version'])

    def show(self, order):
        self.context_oid=order['id']
        # Vista adicional registrada; las respuestas también pueden adjuntar archivos aquí.
        self.seq += 1
        payload = {'chat_id': self.c.group, 'text': card(order, self.d), 'parse_mode': 'HTML', 'reply_markup': actions(order), 'disable_notification': True}
        if self.thread:
            payload['message_thread_id'] = self.thread
        self.s.enqueue(f'{self.key}:{self.seq}', payload, kind='view', oid=order['id'], version=order['version'])

    def start_new(self, template='otro'):
        if template not in self.templates:
            raise ValueError('Plantillas: ' + ', '.join(self.templates))
        data = dict(self.templates[template])
        example = '\n'.join(f'{k}={v}' for k,v in {'cliente':'Nombre corto', **data}.items())
        self.session('new', {'values': data}, 'Responde con campo=valor, una línea por campo. Puedes omitir lo desconocido.\n'+example+'\n\nOpcionales: telefono, descripcion, papel, color, cantidad, caras, acabados, prometida, interna, pago, comunicacion, produccion, notas. Fechas: AAAA-MM-DD HH:MM. /cancelar para salir.')

    def preview(self, values, source=None):
        token = self.session('preview', {'values': values, 'source': source})
        for field, threshold in [(k,500 if k=='descripcion' else 250 if k=='notas' else 80) for k,v in values.items() if isinstance(v,str) and k!='telefono']:
            if len(values.get(field) or '') > threshold:
                self.plain(field.capitalize()+' completa para revisar:\n'+values[field])
        self.say(card({'stage': 'nuevos', 'data': values}, self.d, preview=True), keyboard([
            [('Confirmar pedido', f's:{token}:save'), ('Editar', f's:{token}:edit')],
            [('Cancelar', f's:{token}:cancel')]]))

    def message(self):
        text = self.msg.get('text', '')
        if text.startswith('/'):
            word, _, arg = text.partition(' ')
            cmd, _, botname = word[1:].partition('@')
            if botname and botname.lower() != self.username.lower():
                return
            arg = arg.strip()
            if cmd in {'ayuda', 'start'}:
                self.say(HELP, keyboard([[('➕ Nuevo pedido', 'new')]]))
            elif cmd in {'nuevo','abrir'}:
                from .quick import Quick
                Quick(self).open(arg)
            elif cmd == 'cerrar':
                from .quick import Quick
                Quick(self).close()
            elif cmd == 'formulario':
                if self.s.one('SELECT 1 FROM captures WHERE actor=?',(self.actor,)): raise ValueError('Primero cierra la captura con /cerrar.')
                self.start_new(arg or 'otro')
            elif cmd == 'pasar':
                from .quick import Quick
                parts=arg.split()
                if not parts: raise ValueError('Usa /pasar ID etapa (2, 3, 4, 5 o 6).')
                o=self.s.order(parts[0])
                targets={'2':'listos','3':'proceso','4':'falta','5':'armar','6':'terminados'}
                target=targets.get(parts[1],parts[1]) if len(parts)>1 else {'nuevos':'listos','listos':'proceso','proceso':'armar','falta':'armar','armar':'terminados'}.get(o['stage'])
                Quick(self).move(o['id'],o['version'],target)
            elif cmd == 'cancelar':
                if self.s.one('SELECT 1 FROM captures WHERE actor=?',(self.actor,)):
                    raise ValueError('El pedido ya guarda tus mensajes. Usa /cerrar para conservarlo y terminar la captura.')
                self.s.event(None, self.actor, 'captura_cancelada', {})
                self.end_session()
                self.plain('Captura cancelada. Los pedidos guardados se conservan.')
            elif cmd == 'pedido':
                order = self.s.order(arg)
                self.show(order)
                self.history(order['id'], 0, limit=3)
            elif cmd in {'buscar', 'pendientes', 'hoy', 'resumen'}:
                self.listing(cmd, arg)
            elif cmd == 'configurar':
                self.configure(arg)
            elif cmd == 'autolimpiar':
                if self.actor not in self.c.admins:
                    raise ValueError('Solo los administradores configurados pueden cambiar la limpieza automática.')
                if arg not in {'','activar','desactivar'}:
                    raise ValueError('Usa /autolimpiar activar o /autolimpiar desactivar.')
                if arg:
                    enabled=int(arg=='activar')
                    self.s.db.execute("UPDATE meta SET value=? WHERE key='auto_cleanup'",(str(enabled),))
                    self.s.event(None,self.actor,'limpieza_automatica_configurada',{'activa':bool(enabled)})
                enabled=self.s.one("SELECT value FROM meta WHERE key='auto_cleanup'")[0]=='1'
                self.plain('Limpieza automática '+('ACTIVADA. En los próximos traslados se borrará el contenido, comandos y avisos del pedido en la fase anterior solo después de confirmar todas las copias y la ficha nueva. Sin avisos de limpieza. Se aplica a ambos y se conserva al reiniciar. Para apagar: /autolimpiar desactivar.' if enabled else 'DESACTIVADA. Limpiar requiere confirmación. Para automatizar los próximos traslados de ambos: /autolimpiar activar.'))
            elif cmd == 'vincular':
                source = self.msg.get('reply_to_message')
                if not source:
                    raise ValueError('Responde al mensaje o archivo original con /vincular ID.')
                order = self.s.order(arg)
                source = dict(source) | {'chat': self.msg['chat']}
                self.attach(order, source)
                self.plain('Referencia guardada; el original se conserva.')
            return
        from .quick import Quick
        if Quick(self).capture(self.msg): return
        s = self.s.one('SELECT * FROM sessions WHERE user_id=?', (self.actor,))
        reply = self.msg.get('reply_to_message', {}).get('message_id')
        if s and reply is not None and s['prompt_id'] == reply and s['thread_id'] == self.thread:
            self.reply(self.get_session(), text)
            return
        ref = self.s.one('SELECT * FROM messages WHERE chat_id=? AND message_id=?', (self.c.group, reply)) if reply else None
        has_file = any(k in self.msg for k in ('document','photo','video','audio','voice','animation','video_note','sticker'))
        album = self.msg.get('media_group_id')
        group = self.s.one('SELECT * FROM albums WHERE album=? AND actor=? AND expires>?', (album, self.actor, now())) if album else None
        if has_file and (ref or (group and group['thread_id'] == self.thread)):
            oid = ref['order_id'] if ref else group['order_id']
            self.attach(self.s.order(oid), self.msg)
        elif has_file and album:
            self.buffer_album(self.msg)

    def buffer_album(self, message):
        # Telegram entrega un Update por archivo; la respuesta puede llegar al final.
        media = {k: message[k] for k in ('document','photo','video','audio','voice','animation','video_note','sticker') if k in message}
        payload = media | {k: message[k] for k in ('chat','message_id','message_thread_id','media_group_id') if k in message}
        payload['chat'] = {'id': message['chat']['id']}
        expires = (datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(timespec='seconds')
        self.s.db.execute('INSERT OR IGNORE INTO album_buffer VALUES(?,?,?,?,?,?,?)',
            (self.c.group,message['message_id'],message['media_group_id'],self.actor,self.thread,dump(payload),expires))
        # Acotar el almacenamiento de lotes todavía no asociados.
        self.s.db.execute('DELETE FROM album_buffer WHERE rowid NOT IN (SELECT rowid FROM album_buffer ORDER BY rowid DESC LIMIT 500)')

    def attach(self, order, source):
        if source['chat']['id'] != self.c.group:
            raise ValueError('Solo se vinculan mensajes de este grupo.')
        if order['data'].get('rapido'):
            from .quick import Quick, busy
            stage=self.s.one('SELECT thread_id FROM stages WHERE key=?',(order['stage'],))
            if busy(self.s,order['id']) or self.thread!=stage['thread_id']:
                raise ValueError('Espera a que termine el traslado y añade los archivos desde la ficha actual.')
            if Quick(self).capture(source,order['id']):
                self.publish(self.d.touch(order['id']))
            return
        added = self.d.attach(order['id'], self.actor, source)
        if source.get('media_group_id'):
            expires = (datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(timespec='seconds')
            existing = self.s.one('SELECT order_id FROM albums WHERE album=? AND expires>?',(source['media_group_id'],now()))
            if existing and existing['order_id'] != order['id']:
                raise ValueError('Ese lote ya pertenece a otro pedido. Abre su ficha original.')
            self.s.db.execute('INSERT OR REPLACE INTO albums VALUES(?,?,?,?,?)',
                (source['media_group_id'], order['id'], self.actor, self.thread, expires))
            buffered = self.s.all('SELECT * FROM album_buffer WHERE chat_id=? AND album=? AND actor=? AND thread_id IS ? AND expires>? ORDER BY message_id',
                (self.c.group,source['media_group_id'],self.actor,self.thread,now()))
            for item in buffered:
                added = self.d.attach(order['id'],self.actor,json.loads(item['payload'])) or added
                self.s.db.execute('DELETE FROM album_buffer WHERE chat_id=? AND message_id=?',(item['chat_id'],item['message_id']))
        if added:
            self.publish(self.d.touch(order['id']))

    def reply(self, s, text):
        data, kind = s['data'], s['kind']
        if kind in {'new', 'newedit'}:
            self.preview(data['values'] | self.d.parse(text), {'chat': self.msg['chat'], 'message_id': self.msg['message_id'], 'message_thread_id': self.thread})
            return
        if kind == 'attach':
            order = self.s.order(data['oid'])
            self.attach(order, self.msg)
            self.plain('Vinculado. Puedes seguir respondiendo a esta solicitud durante 24 h.')
            return
        oid, version = data['oid'], data['version']
        if kind == 'edit':
            order = self.d.edit(oid, version, self.actor, self.d.parse(text))
        elif kind == 'note':
            order = self.d.note(oid, version, self.actor, text)
        elif kind == 'postpone':
            field, sep, rest = text.partition('=')
            value, sep2, reason = rest.partition('|')
            if field.strip() not in {'prometida', 'interna'} or not sep or not sep2 or not reason.strip():
                raise ValueError('Usa prometida=AAAA-MM-DD HH:MM | motivo (o interna=...).')
            order = self.d.edit(oid, version, self.actor, {field.strip(): self.d.date(value.strip())}, reason.strip()[:500])
        elif kind in {'block', 'pendingedit'}:
            parts = [p.strip() for p in text.split('|')]
            if len(parts) not in {2,3}:
                raise ValueError('Usa descripción | Magui o Kevin | fecha opcional.')
            description, owner = parts[:2]
            due = parts[2] if len(parts) == 3 else None
            if kind == 'pendingedit':
                order = self.d.edit_pending(oid, version, self.actor, data['pid'], description, owner, due)
            else:
                order = self.d.pending(oid, version, self.actor, description, owner, due)
                if order['stage'] != 'falta':
                    order = self.d.move(oid, order['version'], self.actor, 'falta')
        elif kind == 'exception':
            if not text.strip() or len(text) > 500:
                raise ValueError('Escribe un motivo de 1–500 caracteres.')
            self.d.check(oid, version)
            token = self.session('confirmmove', data | {'reason': text})
            self.say('¿Confirmar excepción? ' + esc(text), keyboard([[('Confirmar cambio', f's:{token}:move'), ('Cancelar', f's:{token}:cancel')]]))
            return
        else:
            raise ValueError('Usa los botones de la vista previa.')
        self.end_session()
        self.publish(order)
        self.plain('Guardado: ' + order['code'])

    def callback(self, payload):
        parts = payload.split(':')
        if payload == 'new':
            from .quick import Quick
            Quick(self).open(); return
        if parts[0] in {'qclose','qmove','qclean','qcleanok','qkeep','qadd'}:
            from .quick import Quick, busy
            q=Quick(self)
            if parts[0]=='qclose': q.close(int(parts[1]))
            elif parts[0]=='qmove': q.move(int(parts[1]),int(parts[2]),parts[3])
            elif parts[0] in {'qclean','qcleanok'}: q.cleanup(int(parts[1]),parts[0]=='qcleanok')
            elif parts[0]=='qkeep': self.plain('Originales conservados. Puedes limpiar después desde el botón del traslado.')
            elif parts[0]=='qadd':
                o=self.d.check(int(parts[1]),int(parts[2]))
                stage=self.s.one('SELECT thread_id FROM stages WHERE key=?',(o['stage'],))
                if self.thread!=stage['thread_id'] or busy(self.s,o['id']): raise ValueError('Abre la ficha en su tema actual y espera a que termine el traslado.')
                if self.s.one('SELECT 1 FROM captures WHERE actor=? OR order_id=?',(self.actor,o['id'])): raise ValueError('Ya hay una captura abierta. Ciérrala primero.')
                self.s.db.execute('INSERT INTO captures VALUES(?,?,?)',(self.actor,o['id'],self.thread))
                self.plain('Añade mensajes o archivos aquí. Termina con /cerrar.')
            return
        if parts[0] == 'template':
            self.start_new(parts[1]); return
        if parts[0] == 's':
            s = self.get_session(parts[1]); data = s['data']; op = parts[2]
            if op == 'cancel':
                self.s.event(data.get('oid'), self.actor, 'accion_cancelada', {'tipo': s['kind']})
                self.end_session(); self.plain('Cancelado.'); return
            if op == 'save' and s['kind'] == 'preview':
                order = self.d.create(self.actor, data['values'])
                if data.get('source'):
                    self.d.attach(order['id'], self.actor, data['source'])
                self.publish(order); self.end_session(); self.plain('Creado: '+order['code']); return
            if op == 'edit' and s['kind'] == 'preview':
                self.session('newedit', data, 'Responde solo los campos que quieras cambiar: campo=valor.'); return
            if op == 'move' and s['kind'] == 'confirmmove':
                order = self.d.move(data['oid'], data['version'], self.actor, data['target'], data.get('reason',''), data.get('override',False))
                self.publish(order); self.end_session(); self.plain('Etapa guardada: '+order['code']); return
            if op == 'retarget' and s['kind'] == 'retarget':
                self.require_admin()
                job = self.s.one("SELECT * FROM outbox WHERE id=? AND status='failed' AND method='send_message' AND kind='card'",(data['job'],))
                if not job:
                    raise ValueError('La operación ya cambió o no se puede redestinar.')
                payload = json.loads(job['payload'])
                old = payload.get('message_thread_id')
                payload['message_thread_id'] = data['thread']
                self.s.db.execute("UPDATE outbox SET payload=?,status='pending',attempts=0,available=0 WHERE id=?",(dump(payload),job['id']))
                self.s.event(job['order_id'],self.actor,'sincronizacion_redestinada',{'job':job['id'],'antes':old,'despues':data['thread']})
                self.end_session(); self.plain('Destino corregido y reintento solicitado.'); return
            if op == 'retry' and s['kind'] == 'retry':
                self.retry(data['job']); self.end_session(); return
            raise ValueError('Botón vencido.')
        if parts[0] == 'list':
            self.page(parts[1], int(parts[2])); return
        if parts[0] == 'ev':
            row = self.s.one('SELECT * FROM events WHERE order_id=? AND id=?',(int(parts[1]),int(parts[2])))
            if not row:
                raise ValueError('Evento no encontrado.')
            data = json.loads(row['data'])
            data.pop('telefono',None)
            if isinstance(data.get('cambios'),dict):
                data['cambios'].pop('telefono',None)
            text = f"Evento #{row['id']} · {row['at']} · {row['kind']}\n" + json.dumps(data,ensure_ascii=False,indent=2)
            for start in range(0,len(text),1500):
                self.plain(text[start:start+1500])
            return
        if parts[0] == 'hist':
            self.history(int(parts[1]), int(parts[2])); return
        if parts[0] == 'file':
            self.files(int(parts[1]), int(parts[2])); return
        if parts[0] == 'pend':
            self.pending_page(self.s.order(int(parts[1])), int(parts[2])); return
        if parts[0] == 'open':
            self.show(self.s.order(parts[1])); return
        if parts[0] == 'retry':
            self.require_admin()
            job = self.s.one('SELECT * FROM outbox WHERE id=? AND status IN (\'uncertain\',\'failed\')', (int(parts[1]),))
            if not job:
                raise ValueError('La operación ya fue resuelta.')
            token = self.session('retry', {'job': job['id']})
            self.say('Revisa el Topic antes. Si el envío sí llegó, este reintento puede duplicarlo. Puedes vincular el envío existente con /configurar recuperar JOB MENSAJE.', keyboard([[('Reintentar conscientemente',f's:{token}:retry'),('Cancelar',f's:{token}:cancel')]])); return
        if parts[0] != 'o' or len(parts) < 4:
            raise ValueError('Botón desconocido.')
        oid, version, op = int(parts[1]), int(parts[2]), parts[3]
        order = self.d.check(oid, version)
        if order['data'].get('rapido'):
            raise ValueError('Usa los botones de la ficha rápida o /pasar ID etapa.')
        prefix = f'o:{oid}:{version}:'
        data = {'oid': oid, 'version': version}
        if op == 'stage':
            self.say('Elige etapa. Un salto fuera del flujo requiere motivo y confirmación.', keyboard([[(name, prefix+'to:'+key)] for key,name in [(r['key'],r['name']) for r in self.s.all('SELECT key,name FROM stages')] if key != order['stage']]))
        elif op in {'to', 'close'}:
            target = parts[4] if op == 'to' else 'terminados'
            if target == 'falta':
                self.session('block', data, '¿Qué falta? Responde: descripción | Magui o Kevin | AAAA-MM-DD HH:MM (fecha opcional)')
            elif not self.d.valid_transition(order, target):
                self.session('exception', data | {'target': target, 'override': True}, 'Este cambio está fuera del flujo. Escribe el motivo; después podrás confirmar.')
            else:
                token = self.session('confirmmove', data | {'target': target})
                self.say('¿Confirmar '+esc(self.s.one('SELECT name FROM stages WHERE key=?',(target,))['name'])+'?', keyboard([[('Confirmar',f's:{token}:move'),('Cancelar',f's:{token}:cancel')]]))
        elif op == 'assign':
            self.say('¿Qué responsabilidad asumes?', keyboard([[(label,prefix+'role:'+role)] for label,role in [('Comunicación','comunicacion'),('Producción','produccion'),('Ambas','ambos')]]))
        elif op == 'role':
            order = self.d.assign(oid, version, self.actor, parts[4]); self.publish(order); self.plain('Asignación guardada.')
        elif op in {'edit','note','block','postpone'}:
            prompts = {'edit':'Responde campo=valor, una línea por campo. Campos: cliente, telefono, tipo, descripcion, papel, color, cantidad, caras, acabados, prometida, interna, pago, comunicacion, produccion, notas. Usa - para vaciar un dato.',
                       'note':'Escribe una nota interna (visible a miembros del grupo).',
                       'block':'Responde: descripción | Magui o Kevin | fecha opcional AAAA-MM-DD HH:MM',
                       'postpone':'Responde: prometida=AAAA-MM-DD HH:MM | motivo (o interna=...).'}
            self.session(op, data, prompts[op])
        elif op == 'files':
            self.files(oid, 0)
            self.session('attach', data, 'Responde aquí con archivos o un álbum. O responde al original con /vincular '+order['code'])
        elif op == 'history':
            self.history(oid, 0)
        elif op == 'pending':
            self.pending_page(order, 0)
        elif op == 'pedit':
            self.session('pendingedit', data | {'pid': int(parts[4])}, 'Edita el pendiente: descripción | Magui o Kevin | fecha opcional')
        elif op == 'resolve':
            order = self.d.resolve(oid, version, self.actor, int(parts[4])); self.publish(order)
            self.plain('Pendiente resuelto.')
            if order['stage'] == 'falta':
                self.say('Cuando todos estén resueltos, regresar a '+esc(self.s.one('SELECT name FROM stages WHERE key=?',(order['previous_stage'],))['name'])+'.', keyboard([[('Regresar',f"o:{oid}:{order['version']}:to:{order['previous_stage']}")]]))
        else:
            raise ValueError('Acción desconocida.')

    def pending_page(self, order, offset):
        rows = self.s.all('SELECT * FROM pending WHERE order_id=? AND resolved_at IS NULL ORDER BY id LIMIT 6 OFFSET ?', (order['id'], max(0, offset)))
        buttons = []
        for row in rows[:5]:
            self.plain(f"#{row['id']}: {row['description']} · {row['owner']} · {row['due'] or 'sin fecha'}")
            prefix = f"o:{order['id']}:{order['version']}:"
            buttons.append([(f"Resolver #{row['id']}", prefix+f"resolve:{row['id']}"), (f"Editar #{row['id']}", prefix+f"pedit:{row['id']}")])
        if len(rows) > 5:
            buttons.append([('Siguientes',f"pend:{order['id']}:{offset+5}")])
        self.say('Pendientes abiertos' if rows else 'Sin pendientes abiertos.', keyboard(buttons) if buttons else None)

    def history(self, oid, offset, limit=5):
        self.context_oid=oid
        order = self.s.order(oid)
        rows = self.s.all('SELECT * FROM events WHERE order_id=? ORDER BY id DESC LIMIT ? OFFSET ?', (oid, limit+1, max(0,offset)))
        lines = [f"<b>Historial {esc(order['code'])}</b>"]
        for row in rows[:limit]:
            data = json.loads(row['data'])
            # Teléfono queda en base, no se publica ni en el historial del grupo.
            data.pop('telefono', None)
            if isinstance(data.get('cambios'), dict):
                data['cambios'].pop('telefono', None)
            actor = 'Magui' if row['actor'] == self.c.magui else 'Kevin' if row['actor'] == self.c.kevin else 'Sistema'
            lines.append(esc(f"{row['at']} · {actor} · {row['kind']}\n{json.dumps(data, ensure_ascii=False)[:300]}"))
        buttons = [[(f"Detalle #{r['id']} · {r['kind']}", f"ev:{oid}:{r['id']}")] for r in rows[:limit]]
        if len(rows)>limit:
            buttons.append([('Más',f'hist:{oid}:{offset+limit}')])
        self.say('\n\n'.join(lines), keyboard(buttons) if buttons else None)

    def files(self, oid, offset):
        self.s.order(oid)
        rows = self.s.all('SELECT * FROM files WHERE order_id=? ORDER BY id LIMIT 9 OFFSET ?', (oid,max(0,offset)))
        lines = ['<b>Archivos y contexto</b> (acceso solo dentro del grupo)']
        for r in rows[:8]:
            label = esc(f"#{r['id']} {r['name'] or r['kind']} · {r['size'] or '?'} bytes · álbum {r['album'] or '—'}")
            url = link(r['chat_id'], r['message_id'])
            lines.append(f'<a href="{url}">{label}</a>' if url else label)
        if not rows:
            lines.append('Sin archivos. Vincula el original; no se descarga ni se duplica.')
        self.say('\n'.join(lines), keyboard([[('Más',f'file:{oid}:{offset+8}')]]) if len(rows)>8 else None)

    def listing(self, kind, query=''):
        if len(query) > 150:
            raise ValueError('Búsqueda demasiado larga.')
        orders = [self.s.order(r['id']) for r in self.s.all('SELECT id FROM orders ORDER BY id DESC')]
        if kind == 'buscar':
            q = query.casefold()
            orders = [o for o in orders if q in ' '.join([o['code']]+[str(o['data'].get(k,'')) for k in ('cliente','tipo','descripcion')]).casefold()]
        if kind == 'pendientes':
            orders = [o for o in orders if o['stage'] != 'terminados' and (o['stage'] in {'nuevos','falta'} or self.s.one('SELECT 1 FROM pending WHERE order_id=? AND resolved_at IS NULL',(o['id'],)))]
        if kind == 'hoy':
            today = datetime.now(self.d.tz).date()
            orders = [o for o in orders if o['stage'] != 'terminados' and (self.d.due_status(o) == 'vencido' or any(datetime.fromisoformat(o['data'][k]).astimezone(self.d.tz).date() == today for k in ('prometida','interna') if o['data'].get(k)))]
        if kind == 'resumen':
            counts = Counter(o['stage'] for o in orders)
            open_orders = [o for o in orders if o['stage'] != 'terminados']
            due = Counter(self.d.due_status(o) for o in open_orders)
            owners = Counter(o['data'].get(role) for o in open_orders for role in ('comunicacion','produccion'))
            self.say('\n'.join([f"{esc(r['name'])}: {counts[r['key']]}" for r in self.s.all('SELECT key,name FROM stages')]+[f"Responsabilidades abiertas · Magui: {owners[self.c.magui]} · Kevin: {owners[self.c.kevin]}",esc(dict(due))]))
        token = self.session('list', {'ids': [o['id'] for o in orders]})
        self.page(token, 0)

    def page(self, token, offset):
        s = self.get_session(token)
        if s['kind'] != 'list':
            raise ValueError('Lista vencida.')
        ids = s['data']['ids']; offset = max(0,offset)
        rows = []
        for oid in ids[offset:offset+6]:
            o = self.s.order(oid)
            rows.append([(f"{o['code']} · {str(o['data'].get('cliente') or '?')[:25]} · {self.d.due_status(o)}",f'open:{oid}')])
        nav = []
        if offset: nav.append(('Anterior',f'list:{token}:{max(0,offset-6)}'))
        if offset+6<len(ids): nav.append(('Siguiente',f'list:{token}:{offset+6}'))
        if nav: rows.append(nav)
        self.say(f'{len(ids)} pedidos · página {offset//6+1}', keyboard(rows) if rows else None)

    def require_admin(self):
        if self.actor not in self.c.admins:
            raise ValueError('Solo administradores autorizados en ADMIN_IDS.')

    def configure(self, arg):
        self.require_admin()
        args = arg.split(maxsplit=3)
        if not args:
            rows = self.s.all('SELECT * FROM stages')
            text = '\n'.join(f"{r['key']}: {r['name']} → {r['thread_id'] or 'sin asociar'}" for r in rows)
            self.plain(text+'\n\n/configurar etapa — asocia el Topic actual\n/configurar etapa ID — ID manual\n/configurar nombre etapa Nombre visible\n/configurar fallos [página]\n/configurar recuperar JOB MENSAJE — vincula envío incierto existente\n/configurar reintentar JOB — pide confirmación\n/configurar redestinar JOB TOPIC — corrige una ficha de envío fallida')
            return
        if args[0] == 'fallos':
            page = max(0,int(args[1])) if len(args)>1 else 0
            rows = self.s.all("SELECT * FROM outbox WHERE status IN ('uncertain','failed') ORDER BY id LIMIT 7 OFFSET ?",(page*6,))
            for r in rows[:6]:
                self.say(esc(f"Operación {r['id']} · {r['kind']} · pedido {r['order_id']} · {r['status']} · {r['error']}"), keyboard([[("Revisar reintento",f"retry:{r['id']}")]]))
            self.plain(('Más: /configurar fallos '+str(page+1)) if len(rows)>6 else 'Fin de operaciones pendientes de revisión.')
            return
        if args[0] == 'redestinar' and len(args)==3:
            job = self.s.one("SELECT * FROM outbox WHERE id=? AND status='failed' AND method='send_message' AND kind='card'",(int(args[1]),))
            thread = int(args[2])
            if not job or not self.s.one('SELECT 1 FROM stages WHERE thread_id=?',(thread,)):
                raise ValueError('Solo fichas fallidas de envío y un Topic ya configurado. Los envíos inciertos deben revisarse primero.')
            token = self.session('retarget',{'job':job['id'],'thread':thread})
            self.say(f'¿Corregir destino de operación {job["id"]} al Topic {thread} y reintentar?',keyboard([[('Confirmar',f's:{token}:retarget'),('Cancelar',f's:{token}:cancel')]])); return
        if args[0] == 'reintentar' and len(args)==2:
            self.callback('retry:'+args[1]); return
        if args[0] == 'recuperar' and len(args)==3:
            from .delivery import delivered
            job = self.s.one("SELECT * FROM outbox WHERE id=? AND status='uncertain' AND method='send_message'", (int(args[1]),))
            mid = int(args[2])
            replied = self.msg.get('reply_to_message', {})
            if not job or mid<=0 or replied.get('message_id') != mid or not replied.get('from',{}).get('is_bot') or replied.get('from',{}).get('username','').lower() != self.username.lower():
                raise ValueError('Responde a la publicación de este bot con /configurar recuperar JOB MENSAJE; verifica que corresponde.')
            payload = json.loads(job['payload'])
            if payload.get('message_thread_id') != self.thread or replied.get('text') != self._plain_html(payload['text']):
                raise ValueError('El Topic o texto no coincide con el envío incierto.')
            delivered(self.s, self.c, job, mid)
            self.s.event(job['order_id'], self.actor, 'sincronizacion_recuperada', {'job':job['id'],'mensaje':mid})
            self.plain('Publicación vinculada sin volver a enviar.'); return
        if args[0] == 'nombre' and len(args)>=3:
            stage, name = args[1], ' '.join(args[2:])
            if stage not in STAGES or len(name)>80:
                raise ValueError('Etapa o nombre inválido.')
            self.s.db.execute('UPDATE stages SET name=? WHERE key=?', (name,stage))
            self.s.event(None,self.actor,'topic_nombre',{'etapa':stage,'nombre':name})
            self.plain('Nombre actualizado para próximas fichas.'); return
        stage = args[0]
        if stage not in STAGES or len(args)>2:
            raise ValueError('Etapas: '+', '.join(STAGES))
        thread = int(args[1]) if len(args)==2 else self.thread
        if not thread or thread<=0:
            raise ValueError('Ejecuta el comando dentro de un Topic o indica su ID positivo.')
        self.s.db.execute('UPDATE stages SET thread_id=? WHERE key=?', (thread,stage))
        self.s.event(None,self.actor,'topic_configurado',{'etapa':stage,'topic':thread})
        self.plain('Topic asociado. Las próximas publicaciones usarán esta configuración.')

    @staticmethod
    def _plain_html(text):
        from html import unescape
        import re
        return unescape(re.sub('<[^>]+>', '', text))

    def retry(self, job_id):
        self.require_admin()
        row = self.s.one("SELECT * FROM outbox WHERE id=? AND status IN ('uncertain','failed')", (job_id,))
        if not row:
            raise ValueError('La operación ya fue resuelta.')
        self.s.db.execute("UPDATE outbox SET status='pending',available=0,attempts=0 WHERE id=?", (job_id,))
        self.s.event(row['order_id'],self.actor,'reintento_confirmado',{'job':job_id,'posible_duplicado':row['status']=='uncertain'})
        self.plain('Reintento solicitado; consulta /configurar fallos para verificar.')

    def reminder(self, at=None):
        at = (at or datetime.now(timezone.utc)).astimezone(self.d.tz)
        if not self.c.reminder_time or at.strftime('%H:%M') < self.c.reminder_time:
            return
        key = 'reminder:'+at.date().isoformat()
        with self.s.db:
            if self.s.one('SELECT 1 FROM outbox WHERE dedupe=?',(key,)):
                return
            stage = self.s.one("SELECT thread_id FROM stages WHERE key='nuevos'")
            if not stage['thread_id']: return
            counts = Counter(self.d.due_status(self.s.order(r['id']),at) for r in self.s.all("SELECT id FROM orders WHERE stage!='terminados'"))
            self.s.enqueue(key, {'chat_id':self.c.group,'message_thread_id':stage['thread_id'],'text':f"Resumen diario · vencidos: {counts['vencido']} · próximos 24 h: {counts['próximo (24 h)']}. /hoy · /resumen",'disable_notification':True})
