"""Reglas de negocio. El llamador controla la transacción SQLite."""
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .store import FLOW, now, dump

FIELDS = {'cliente', 'telefono', 'tipo', 'descripcion', 'papel', 'color', 'cantidad', 'caras',
          'acabados', 'prometida', 'interna', 'pago', 'comunicacion', 'produccion', 'notas'}


class Domain:
    def __init__(self, store, config):
        self.s, self.c = store, config
        self.tz = ZoneInfo(config.timezone)
        with nullcontext() if store.db.in_transaction else store.db:
            for uid, name in [(config.magui, 'Magui'), (config.kevin, 'Kevin')]:
                store.db.execute('INSERT INTO users VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,admin=excluded.admin',
                                 (uid, name, int(uid in config.admins)))

    def owner(self, text):
        users = {'magui': self.c.magui, 'kevin': self.c.kevin,
                 str(self.c.magui): self.c.magui, str(self.c.kevin): self.c.kevin}
        if str(text).lower() not in users:
            raise ValueError('Responsable: Magui o Kevin.')
        return users[str(text).lower()]

    def date(self, text):
        if not text or text == '-':
            return None
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                local = dt.replace(tzinfo=self.tz)
                if local.astimezone(timezone.utc).astimezone(self.tz).replace(tzinfo=None) != dt:
                    raise ValueError()
                if local.utcoffset() != dt.replace(tzinfo=self.tz, fold=1).utcoffset():
                    raise ValueError()
                dt = local
            return dt.astimezone(timezone.utc).isoformat(timespec='seconds')
        except ValueError:
            raise ValueError('Fecha inválida o ambigua. Usa AAAA-MM-DD HH:MM; añade -06:00 si cambia el horario.') from None

    def parse(self, text):
        if len(text) > 6000:
            raise ValueError('Usa como máximo 6000 caracteres.')
        values = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            key, sep, value = line.partition('=')
            key, value = key.strip().lower(), value.strip()
            if not sep or key not in FIELDS:
                raise ValueError('Usa campo=valor, una línea por campo. Campos: ' + ', '.join(sorted(FIELDS)))
            if len(value) > (1200 if key in {'descripcion', 'notas'} else 160):
                raise ValueError(f'{key}: texto demasiado largo.')
            if key in {'prometida', 'interna'}:
                value = self.date(value)
            elif key in {'comunicacion', 'produccion'}:
                value = self.owner(value)
            elif key == 'pago' and value not in {'pendiente', 'parcial', 'pagado'}:
                raise ValueError('Pago: pendiente, parcial o pagado.')
            elif key == 'telefono' and value != '-' and any(c not in '+0123456789 ()-' for c in value):
                raise ValueError('Teléfono inválido.')
            values[key] = None if value == '-' else value
        if not values:
            raise ValueError('Escribe al menos un campo, por ejemplo cliente=Ejemplo.')
        return values

    def create(self, actor, values):
        data = {'cliente': 'Por confirmar', 'pago': 'pendiente', 'comunicacion': self.c.magui,
                'produccion': self.c.kevin} | values
        stamp = now()
        cur = self.s.db.execute('INSERT INTO orders(stage,data,created_at,updated_at) VALUES(?,?,?,?)',
                                ('nuevos', dump(data), stamp, stamp))
        oid = cur.lastrowid
        code = f'T-{datetime.now(self.tz).year}-{oid:04d}'
        self.s.db.execute('UPDATE orders SET code=? WHERE id=?', (code, oid))
        self.s.event(oid, actor, 'creado', data)
        return self.s.order(oid)

    def check(self, oid, version):
        order = self.s.order(oid)
        if order['version'] != int(version):
            raise ValueError('Esta ficha cambió. Abre /pedido ' + order['code'])
        return order

    def touch(self, oid):
        self.s.db.execute('UPDATE orders SET version=version+1,updated_at=? WHERE id=?', (now(), oid))
        return self.s.order(oid)

    def edit(self, oid, version, actor, values, reason=''):
        order = self.check(oid, version)
        changes = {k: {'antes': order['data'].get(k), 'despues': v} for k, v in values.items() if order['data'].get(k) != v}
        if not changes:
            return order
        for field in ('prometida', 'interna'):
            old, new = order['data'].get(field), values.get(field)
            if field in values and old and (not new or new > old) and not reason.strip():
                raise ValueError('Para posponer o quitar una fecha usa el botón Posponer y escribe un motivo.')
        self.s.db.execute('UPDATE orders SET data=? WHERE id=?', (dump(order['data'] | values), oid))
        self.s.event(oid, actor, 'datos_editados', {'cambios': changes, 'motivo': reason})
        return self.touch(oid)

    def assign(self, oid, version, actor, role):
        if role not in {'comunicacion', 'produccion', 'ambos'}:
            raise ValueError('Responsabilidad inválida.')
        order = self.check(oid, version)
        keys = ['comunicacion', 'produccion'] if role == 'ambos' else [role]
        self.s.db.execute('UPDATE orders SET data=? WHERE id=?', (dump(order['data'] | dict.fromkeys(keys, actor)), oid))
        self.s.event(oid, actor, 'asignacion', {'roles': keys, 'responsable': actor})
        return self.touch(oid)

    def valid_transition(self, order, target):
        if target == order['stage']:
            return False
        if order['stage'] == 'falta':
            return target == order['previous_stage']
        if target == 'falta':
            return order['stage'] != 'terminados'
        return order['stage'] in FLOW[:-1] and FLOW[FLOW.index(order['stage']) + 1] == target

    def move(self, oid, version, actor, target, reason='', override=False):
        order = self.check(oid, version)
        if not self.s.one('SELECT 1 FROM stages WHERE key=?', (target,)):
            raise ValueError('Etapa desconocida.')
        if target == order['stage']:
            raise ValueError('El pedido ya está en esa etapa.')
        if not self.valid_transition(order, target) and not (override and reason.strip()):
            raise ValueError('Transición excepcional: requiere motivo y confirmación.')
        opened = self.s.one('SELECT 1 FROM pending WHERE order_id=? AND resolved_at IS NULL', (oid,))
        if target == 'falta' and not opened:
            raise ValueError('Primero indica qué falta y quién lo resuelve.')
        if order['stage'] == 'falta' and opened:
            raise ValueError('Resuelve los pendientes antes de salir de Falta algo.')
        if target == 'terminados' and opened:
            raise ValueError('No se puede entregar con pendientes abiertos.')
        previous = order['stage'] if target == 'falta' else order['previous_stage']
        self.s.db.execute('UPDATE orders SET stage=?,previous_stage=? WHERE id=?', (target, previous, oid))
        self.s.event(oid, actor, 'entregado' if target == 'terminados' else 'etapa',
                     {'antes': order['stage'], 'despues': target, 'motivo': reason, 'excepcion': override})
        return self.touch(oid)

    def pending(self, oid, version, actor, description, owner, due=None):
        order = self.check(oid, version)
        if order['stage'] == 'terminados':
            raise ValueError('Reabre el pedido antes de añadir pendientes.')
        if not description.strip() or len(description) > 500:
            raise ValueError('Describe el pendiente en 1–500 caracteres.')
        owner = self.owner(owner)
        cur = self.s.db.execute('INSERT INTO pending(order_id,description,owner,due) VALUES(?,?,?,?)',
                                (oid, description, owner, self.date(due)))
        self.s.event(oid, actor, 'pendiente_creado', {'id': cur.lastrowid, 'descripcion': description, 'responsable': owner, 'fecha': due})
        return self.touch(oid)

    def resolve(self, oid, version, actor, pid):
        self.check(oid, version)
        row = self.s.one('SELECT * FROM pending WHERE id=? AND order_id=? AND resolved_at IS NULL', (pid, oid))
        if not row:
            raise ValueError('El pendiente ya está resuelto o no existe.')
        self.s.db.execute('UPDATE pending SET resolved_at=? WHERE id=?', (now(), pid))
        self.s.event(oid, actor, 'pendiente_resuelto', {'id': int(pid), 'descripcion': row['description']})
        return self.touch(oid)

    def edit_pending(self, oid, version, actor, pid, description, owner, due):
        self.check(oid, version)
        row = self.s.one('SELECT * FROM pending WHERE id=? AND order_id=? AND resolved_at IS NULL', (pid, oid))
        if not row or not description.strip() or len(description) > 500:
            raise ValueError('Pendiente inválido o resuelto.')
        values = (description, self.owner(owner), self.date(due))
        self.s.db.execute('UPDATE pending SET description=?,owner=?,due=? WHERE id=?', (*values, pid))
        self.s.event(oid, actor, 'pendiente_editado', {'antes': dict(row), 'despues': values})
        return self.touch(oid)

    def note(self, oid, version, actor, text):
        self.check(oid, version)
        if not text.strip() or len(text) > 1200:
            raise ValueError('La nota debe tener 1–1200 caracteres.')
        self.s.event(oid, actor, 'nota_interna', {'texto': text})
        return self.touch(oid)

    def attach(self, oid, actor, message):
        # Cada elemento de un álbum llega en un Update separado.
        kind, obj = 'referencia', {}
        for candidate in ('document', 'photo', 'video', 'audio', 'voice', 'animation', 'video_note', 'sticker'):
            if candidate in message:
                kind = candidate
                obj = message[candidate][-1] if candidate == 'photo' else message[candidate]
                break
        cur = self.s.db.execute('''INSERT OR IGNORE INTO files(order_id,file_id,unique_id,name,mime,size,kind,chat_id,thread_id,message_id,album)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (oid, obj.get('file_id'), obj.get('file_unique_id'), obj.get('file_name'),
            obj.get('mime_type'), obj.get('file_size'), kind, message['chat']['id'], message.get('message_thread_id'),
            message['message_id'], message.get('media_group_id')))
        if cur.rowcount:
            self.s.event(oid, actor, 'archivo_vinculado', {'id': cur.lastrowid, 'tipo': kind, 'mensaje': message['message_id'], 'album': message.get('media_group_id')})
        return bool(cur.rowcount)

    def due_status(self, order, at=None):
        at = at or datetime.now(timezone.utc)
        if order['stage'] == 'terminados':
            return 'cerrado'
        dates = [order['data'].get(k) for k in ('prometida', 'interna')]
        dates += [r['due'] for r in self.s.all('SELECT due FROM pending WHERE order_id=? AND resolved_at IS NULL', (order['id'],))]
        ds = [datetime.fromisoformat(x) for x in dates if x]
        if any(x < at for x in ds):
            return 'vencido'
        if any((x - at).total_seconds() <= 86400 for x in ds):
            return 'próximo (24 h)'
        return 'sin urgencia'
