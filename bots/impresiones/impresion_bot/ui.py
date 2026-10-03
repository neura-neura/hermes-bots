import html
from datetime import datetime


def esc(value):
    return html.escape(str(value if value is not None else 'Por confirmar'))


def keyboard(rows):
    return {'inline_keyboard': [[{'text': text, 'callback_data': data} for text, data in row] for row in rows]}


def link(chat, message):
    raw = str(chat)
    return f'https://t.me/c/{raw[4:]}/{message}' if raw.startswith('-100') else None


def date_label(value, tz):
    return datetime.fromisoformat(value).astimezone(tz).strftime('%d/%m/%Y %H:%M') if value else 'Por confirmar'


def card(order, domain, preview=False):
    s, d = domain.s, order['data']
    if d.get('rapido') and not preview:
        stage=s.one('SELECT name FROM stages WHERE key=?',(order['stage'],))['name']
        n=s.one('SELECT count(*) FROM content WHERE order_id=?',(order['id'],))[0]
        return f"<b>{esc(order['code'])} · {esc(d.get('cliente','Pedido')[:80])}</b>\n{esc(stage)}\n📎 {n} mensajes y archivos\nUsa los botones para pasar el pedido completo."
    name = lambda uid: ('Magui' if uid == domain.c.magui else 'Kevin' if uid == domain.c.kevin else 'Por asignar')
    value = lambda key: esc(str(d.get(key) or 'Por confirmar')[:80])
    stage = s.one('SELECT name FROM stages WHERE key=?', (order['stage'],))['name']
    lines = [f"<b>{esc(order.get('code', 'VISTA PREVIA'))} · {value('cliente')}</b>", esc(stage),
             f"Trabajo: {value('tipo')} · {value('cantidad')} · caras: {value('caras')}",
             f"Papel/tamaño: {value('papel')} · {value('color')}",
             f"{esc((d.get('descripcion') or 'Instrucciones por confirmar')[:500])}",
             f"Acabados: {value('acabados')}",
             f"Cliente: {esc(date_label(d.get('prometida'), domain.tz))}",
             f"Interna: {esc(date_label(d.get('interna'), domain.tz))}",
             f"Pago: {esc(d.get('pago', 'pendiente'))}",
             f"Comunicación: {name(d.get('comunicacion', domain.c.magui))} · Producción: {name(d.get('produccion', domain.c.kevin))}"]
    if preview:
        lines += ['Teléfono guardado (oculto en fichas).' if d.get('telefono') else 'Teléfono: pendiente',
                  f"Nota interna: {esc((d.get('notas') or '—')[:250])}"]
    else:
        opened = s.all('SELECT * FROM pending WHERE order_id=? AND resolved_at IS NULL ORDER BY id', (order['id'],))
        if opened:
            lines.append('⏳ Actúa: ' + ', '.join(sorted({name(p['owner']) for p in opened})))
            lines += [f"• #{p['id']} {esc(p['description'][:120])} — {name(p['owner'])} ({date_label(p['due'], domain.tz)})" for p in opened[:3]]
            if len(opened) > 3:
                lines.append(f'… {len(opened)} pendientes; abre Pendientes.')
        else:
            role = 'comunicacion' if order['stage'] in {'nuevos', 'armar', 'terminados'} else 'produccion'
            lines.append('Actúa: ' + name(d.get(role)))
        lines.append('Plazos: ' + domain.due_status(order))
        count = s.one('SELECT count(*) n FROM files WHERE order_id=?', (order['id'],))['n']
        lines.append(f'📎 {count} archivos/referencias · v{order["version"]}')
        original = s.one('SELECT chat_id,message_id FROM files WHERE order_id=? ORDER BY id LIMIT 1', (order['id'],))
        if original and (url := link(original['chat_id'], original['message_id'])):
            lines.append(f'<a href="{url}">Contexto original (solo miembros)</a>')
    return '\n'.join(lines)


def actions(order):
    if order['data'].get('rapido'):
        p=f"qmove:{order['id']}:{order['version']}:"
        next_stage={'nuevos':('✅ Confirmado → 2','listos'),'listos':('🖨️ Empezar → 3','proceso'),'proceso':('📦 Listos / Armar → 5','armar'),'falta':('📦 Resuelto → 5','armar'),'armar':('✅ Entregado → 6','terminados')}
        rows=[]
        if order['stage'] in next_stage:
            label,target=next_stage[order['stage']];rows.append([(label,p+target)])
        if order['stage'] not in {'falta','terminados'}: rows.append([('🟡 Falta algo → 4',p+'falta')])
        rows.append([('📎 Añadir más',f"qadd:{order['id']}:{order['version']}"),('Historial',f"hist:{order['id']}:0")])
        return keyboard(rows)
    p = f"o:{order['id']}:{order['version']}:"
    return keyboard([
        [('Cambiar etapa', p+'stage'), ('Asignarme', p+'assign')],
        [('Marcar pendiente', p+'block'), ('Pendientes', p+'pending')],
        [('Añadir nota', p+'note'), ('📎 Archivos', p+'files')],
        [('Editar datos', p+'edit'), ('Posponer', p+'postpone')],
        [('Ver historial', p+'history'), ('Cerrar/entregado', p+'close')],
        [('➕ Nuevo pedido', 'new')],
    ])
