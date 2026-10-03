import json
from dataclasses import replace
from datetime import datetime,timezone
from impresion_bot.engine import Engine
from conftest import message, callback, drain, create


def test_creation_conversation_review_and_repeat(env):
    s,e,bot,w=env
    e.process(message(1,'/formulario impresion')); drain(w)
    session=s.one('SELECT * FROM sessions')
    e.process(message(2,'cliente=Ana ficticia\ncantidad=50',reply=session['prompt_id'])); drain(w)
    assert s.one('SELECT count(*) FROM orders')[0]==0
    token=s.one('SELECT token FROM sessions')[0]
    update=callback(3,f's:{token}:save')
    e.process(update); e.process(update); drain(w)
    assert s.one('SELECT count(*) FROM orders')[0]==1
    assert s.one("SELECT count(*) FROM outbox WHERE kind='card'")[0]==1
    e.process(callback(4,f's:{token}:save')); drain(w)
    assert s.one('SELECT count(*) FROM orders')[0]==1
    assert any('vencida' in p.get('text','') for _,p in bot.calls)


def test_creation_edit_cancel(env):
    s,e,_,w=env
    e.process(message(1,'/formulario')); drain(w)
    e.process(message(2,'cliente=Primero',reply=s.one('SELECT prompt_id FROM sessions')[0])); drain(w)
    token=s.one('SELECT token FROM sessions')[0]
    e.process(callback(3,f's:{token}:edit')); drain(w)
    e.process(message(4,'cliente=Corregido',reply=s.one('SELECT prompt_id FROM sessions')[0])); drain(w)
    assert json.loads(s.one('SELECT data FROM sessions')[0])['values']['cliente']=='Corregido'
    e.process(callback(5,f"s:{s.one('SELECT token FROM sessions')[0]}:cancel")); drain(w)
    assert s.one('SELECT count(*) FROM orders')[0]==0
    assert s.one("SELECT count(*) FROM events WHERE kind='accion_cancelada'")[0]==1


def test_permissions_wrong_chat_user_anonymous_private(env):
    s,e,_,w=env
    updates=[message(1,'/formulario',actor=99),message(2,'/formulario'),message(3,'/formulario',sender_chat={'id':-10012345}),message(4,'/formulario')]
    updates[1]['message']['chat']['id']=-100999
    updates[3]['message']['chat']['id']=11
    for u in updates: e.process(u)
    assert s.one('SELECT count(*) FROM outbox')[0]==0
    e.process(message(5,'/configurar nuevos 99',actor=22)); drain(w)
    assert s.one("SELECT thread_id FROM stages WHERE key='nuevos'")[0]==10


def test_does_not_capture_unrelated_or_other_bot_commands(env):
    s,e,_,w=env
    e.process(message(1,'/formulario'))
    e.process(message(2,'cliente=No capturar'))
    e.process(message(3,'/formulario@otro_bot'))
    drain(w)
    assert s.one('SELECT kind FROM sessions')[0]=='new'
    prompt=s.one('SELECT prompt_id FROM sessions')[0]
    e.process(message(4,'cliente=Otro topic',thread=11,reply=prompt))
    assert s.one('SELECT kind FROM sessions')[0]=='new'


def test_missing_topic_rolls_back_save(env):
    s,e,_,w=env
    with s.db: s.db.execute("UPDATE stages SET thread_id=NULL WHERE key='nuevos'")
    e.process(message(1,'/formulario'));drain(w)
    e.process(message(2,'cliente=Ana',reply=s.one('SELECT prompt_id FROM sessions')[0]));drain(w)
    e.process(callback(3,f"s:{s.one('SELECT token FROM sessions')[0]}:save"));drain(w)
    assert s.one('SELECT count(*) FROM orders')[0]==0
    assert s.one('SELECT kind FROM sessions')[0]=='preview'


def test_callback_id_and_revision_dedup(env):
    s,e,_,w=env;o=create(env);drain(w)
    data=f"o:{o['id']}:1:role:produccion"
    e.process(callback(1,data,qid='same'));e.process(callback(2,data,qid='same'));drain(w)
    e.process(callback(3,data));drain(w)
    assert s.order(o['id'])['version']==2
    assert s.one("SELECT count(*) FROM events WHERE kind='asignacion'")[0]==1


def test_attachments_album_and_replay(env):
    s,e,_,w=env;o=create(env);drain(w)
    mid=s.one('SELECT message_id FROM messages WHERE current=1')[0]
    for n in range(1,4):
        u=message(n,reply=mid if n==1 else None,document={'file_id':f'f{n}','file_unique_id':f'u{n}','file_name':'a.pdf','mime_type':'application/pdf','file_size':200},media_group_id='album1')
        e.process(u);e.process(u)
    drain(w)
    assert s.one('SELECT count(*) FROM files')[0]==3
    assert s.one('SELECT count(DISTINCT album) FROM files')[0]==1
    assert s.one('SELECT count(*) FROM messages WHERE current=1')[0]==1
    assert sum(m=='send_message' for m,p in bot_calls(env) if 'T-' in p.get('text',''))==1


def bot_calls(env): return env[2].calls


def test_manual_topic_name_and_conflict(env):
    s,e,_,w=env
    e.process(message(1,'/configurar nuevos 90'));drain(w)
    e.process(message(2,'/configurar nombre nuevos Entrada personalizada'));drain(w)
    assert s.one("SELECT name FROM stages WHERE key='nuevos'")[0]=='Entrada personalizada'
    e.process(message(3,'/configurar listos 90'));drain(w)
    assert s.one("SELECT thread_id FROM stages WHERE key='listos'")[0]==11


def test_summary_search_pagination_and_reminders(env):
    s,e,bot,w=env
    with s.db:
        for n in range(8): e.d.create(11,{'cliente':f'Persona {n}'})
    e.process(message(1,'/buscar Persona'));drain(w)
    assert any('Siguiente'==button['text'] for _,p in bot.calls for row in p.get('reply_markup',{}).get('inline_keyboard',[]) for button in row)
    e2=Engine(s,replace(e.c,reminder_time='09:00'))
    for _ in range(2): e2.reminder(datetime(2026,10,1,16,tzinfo=timezone.utc))
    assert s.one("SELECT count(*) FROM outbox WHERE dedupe LIKE 'reminder:%'")[0]==1


def test_exception_and_block_interactions(env):
    s,e,_,w=env;o=create(env);drain(w)
    e.process(callback(1,f"o:{o['id']}:1:to:armar"));drain(w)
    e.process(message(2,'Trabajo impreso antes del registro',reply=s.one('SELECT prompt_id FROM sessions')[0]));drain(w)
    assert s.order(o['id'])['stage']=='nuevos'
    token=s.one('SELECT token FROM sessions')[0]
    e.process(callback(3,f's:{token}:move'));drain(w)
    assert s.order(o['id'])['stage']=='armar'
    e.process(callback(4,f"o:{o['id']}:2:block"));drain(w)
    e.process(message(5,'Falta pegamento | Magui',reply=s.one('SELECT prompt_id FROM sessions')[0]));drain(w)
    assert s.order(o['id'])['stage']=='falta'
    assert s.one('SELECT owner FROM pending')[0]==11


def test_note_and_postpone_use_auditable_events(env):
    s,e,_,w=env;o=create(env);drain(w)
    e.process(callback(1,'o:1:1:note'));drain(w)
    e.process(message(2,'Nota interna de prueba',reply=s.one('SELECT prompt_id FROM sessions')[0]));drain(w)
    assert s.one("SELECT count(*) FROM events WHERE kind='nota_interna'")[0]==1
    e.process(callback(3,'o:1:2:postpone'));drain(w)
    e.process(message(4,'prometida=2026-10-03 10:00 | Cliente solicita cambio',reply=s.one('SELECT prompt_id FROM sessions')[0]));drain(w)
    event=s.one("SELECT data FROM events WHERE kind='datos_editados'")[0]
    assert 'Cliente solicita cambio' in event
    assert s.order(1)['data']['prometida']=='2026-10-03T16:00:00+00:00'


def test_expired_session_and_forged_admin_callback(env):
    s,e,_,w=env;create(env);drain(w)
    e.process(callback(1,'o:1:1:to:listos'));drain(w)
    token=s.one('SELECT token FROM sessions')[0]
    with s.db:s.db.execute("UPDATE sessions SET expires='2000-01-01T00:00:00+00:00'")
    e.process(callback(2,f's:{token}:move'));drain(w)
    assert s.order(1)['stage']=='nuevos'
    e.process(callback(3,'retry:1',actor=22));drain(w)
    assert s.one('SELECT kind FROM sessions')[0]=='confirmmove'


def test_today_uses_local_midnight_and_pending_deadlines(env):
    s,e,bot,w=env
    now=datetime.now(e.d.tz)
    with s.db:
        o=e.d.create(11,{'cliente':'Hoy','prometida':e.d.date(now.strftime('%Y-%m-%d')+' 23:59')})
        other=e.d.create(11,{'cliente':'Pendiente vencido'})
        e.d.pending(other['id'],1,11,'Dato','Magui','2000-01-01 10:00')
    e.process(message(1,'/hoy'));drain(w)
    ids=json.loads(s.one('SELECT data FROM sessions')[0])['ids']
    assert set(ids)=={o['id'],other['id']}


def test_event_detail_redacts_phone_and_keeps_full_note(env):
    s,e,bot,w=env;o=create(env)
    with s.db:
        e.d.edit(1,1,11,{'telefono':'123456789'})
        e.d.note(1,2,11,'Texto largo '+('x'*900))
    e.process(message(1,'/pedido '+o['code']));drain(w)
    for event in s.all('SELECT id FROM events WHERE order_id=1'):
        e.process(callback(100+event['id'],f"ev:1:{event['id']}"))
    drain(w)
    texts='\n'.join(p.get('text','') for _,p in bot.calls)
    assert '123456789' not in texts
    assert 'x'*900 in texts


def test_album_anchor_last_survives_restart(env):
    s,e,_,w=env;create(env);drain(w)
    mid=s.one('SELECT message_id FROM messages WHERE current=1')[0]
    for n in range(1,10):
        e.process(message(n,photo=[{'file_id':f'p{n}','file_unique_id':f'u{n}'}],media_group_id='photos'))
    assert s.one('SELECT count(*) FROM files')[0]==0
    assert s.one('SELECT count(*) FROM album_buffer')[0]==9
    e=Engine(s,e.c)
    e.process(message(10,reply=mid,photo=[{'file_id':'p10','file_unique_id':'u10'}],media_group_id='photos'))
    drain(w)
    assert s.one('SELECT count(*) FROM files')[0]==10
    assert s.one('SELECT count(*) FROM album_buffer')[0]==0


def test_album_buffers_do_not_mix_actors_topics_or_expired_items(env):
    s,e,_,w=env;create(env);drain(w)
    mid=s.one('SELECT message_id FROM messages WHERE current=1')[0]
    media={'document':{'file_id':'d','file_unique_id':'du','file_name':'archivo.docx'},'media_group_id':'docs'}
    e.process(message(1,actor=22,**media))
    e.process(message(2,thread=11,**media))
    e.process(message(3,**media))
    with s.db:s.db.execute("UPDATE album_buffer SET expires='2000-01-01' WHERE message_id=1003")
    e.process(message(4,actor=999,**media))
    e.process(message(5,reply=mid,**media))
    assert s.one('SELECT count(*) FROM files')[0]==1
    assert s.one('SELECT count(*) FROM album_buffer')[0]==2


def test_multiple_documents_via_files_button(env):
    s,e,_,w=env;create(env);drain(w)
    e.process(callback(1,'o:1:1:files'));drain(w)
    mid=s.one('SELECT prompt_id FROM sessions')[0]
    for n in range(2,7):
        u=message(n,reply=mid if n==4 else None,document={'file_id':str(n),'file_unique_id':str(n),'file_name':f'{n}.pdf'},media_group_id='pdfs')
        e.process(u);e.process(u)
    drain(w)
    assert s.one('SELECT count(*) FROM files')[0]==5
    assert s.one('SELECT count(*) FROM album_buffer')[0]==0
