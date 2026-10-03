import json
from types import SimpleNamespace
from telegram.error import TimedOut, BadRequest
from impresion_bot.engine import Engine
from conftest import message, callback, drain


def setup_bot(env):
    s,e,b,w=env
    async def copy_messages(**payload):
        await b.call('copy_messages',**payload)
        result=[]
        for _ in payload['message_ids']:
            b.mid+=1;result.append(SimpleNamespace(message_id=b.mid))
        return result
    async def delete_message(**payload):
        await b.call('delete_message',**payload)
        return True
    b.copy_messages=copy_messages;b.delete_message=delete_message


def capture_order(env):
    s,e,b,w=env;setup_bot(env)
    e.process(message(1,'/nuevo'))
    e.process(message(2,'Ana: imprimir a color'))
    for n in (3,4):
        e.process(message(n,document={'file_id':str(n),'file_unique_id':str(n),'file_name':f'{n}.pdf'},media_group_id='album'))
    e.process(message(5,'/cerrar'));drain(w)
    return s.order(1)


def test_capture_free_form_isolation_and_restart(env):
    s,e,b,w=env;setup_bot(env)
    e.process(message(1,'/nuevo'))
    e.process(message(2,'Texto de otro usuario',actor=22))
    e.process(message(3,'Otro tema',thread=11))
    e.process(message(4,'Hola cliente'))
    e=Engine(s,e.c)
    e.process(message(5,'Más instrucciones'))
    e.process(message(6,'/cerrar'));drain(w)
    assert s.one('SELECT count(*) FROM content')[0]==2
    assert s.one('SELECT count(*) FROM captures')[0]==0
    assert s.order(1)['data']['cliente']=='Hola cliente'
    assert s.order(1)['data']['rapido'] is True


def test_two_people_can_capture_without_mixing(env):
    s,e,b,w=env;setup_bot(env)
    e.process(message(1,'/nuevo A'))
    e.process(message(2,'/nuevo B',actor=22))
    e.process(message(3,'Texto A'))
    e.process(message(4,'Texto B',actor=22))
    e.process(message(5,'/cerrar'))
    e.process(message(6,'/cerrar',actor=22));drain(w)
    assert 'Texto A' in s.one('SELECT payload FROM content WHERE order_id=1')[0]
    assert 'Texto B' in s.one('SELECT payload FROM content WHERE order_id=2')[0]


def test_whole_transfer_album_text_no_delete_without_confirmation(env):
    s,e,b,w=env;o=capture_order(env)
    update=callback(6,f'qmove:1:{o["version"]}:listos')
    e.process(update);e.process(update)
    assert s.order(1)['stage']=='nuevos'
    drain(w)
    assert s.order(1)['stage']=='listos'
    copies=[p for method,p in b.calls if method=='copy_messages']
    assert [len(p['message_ids']) for p in copies]==[1,2]
    assert all(p['message_thread_id']==11 for p in copies)
    assert not any(m=='delete_message' for m,p in b.calls)
    assert s.one('SELECT count(*) FROM transfer_items WHERE destination_id IS NOT NULL')[0]==3
    e.process(callback(7,'qclean:1',thread=11));drain(w)
    assert not any(m=='delete_message' for m,p in b.calls)
    u=callback(8,'qcleanok:1',thread=11);e.process(u);e.process(u);drain(w)
    deleted=[p['message_id'] for m,p in b.calls if m=='delete_message']
    assert {1001,1002,1003,1004,1005} <= set(deleted)
    assert s.one('SELECT count(*) FROM bot_posts WHERE thread_id=10 AND deleted=0')[0]==0
    assert s.one("SELECT count(*) FROM events WHERE kind='mensaje_eliminado'")[0]==len(deleted)


def test_incomplete_copy_never_advances_or_deletes(env):
    s,e,b,w=env;o=capture_order(env)
    async def partial(**payload):
        await b.call('copy_messages',**payload)
        return []
    b.copy_messages=partial
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'));drain(w)
    assert s.order(1)['stage']=='nuevos'
    assert s.one("SELECT count(*) FROM outbox WHERE kind='copy' AND status='uncertain'")[0]==1
    assert len([1 for m,p in b.calls if m=='copy_messages'])==1
    e.process(callback(7,'qcleanok:1'));drain(w)
    assert not any(m=='delete_message' for m,p in b.calls)


def test_copy_timeout_no_automatic_repeat_or_delete(env):
    s,e,b,w=env;o=capture_order(env)
    async def timeout(**payload):
        await b.call('copy_messages',**payload)
        raise TimedOut()
    b.copy_messages=timeout
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'));drain(w);drain(w)
    assert s.order(1)['stage']=='nuevos'
    assert len([1 for m,p in b.calls if m=='copy_messages'])==1
    assert s.one("SELECT status FROM outbox WHERE kind='copy' ORDER BY id")[0]=='uncertain'


def test_cleanup_old_message_failure_preserves_new_copy_and_reports(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'));drain(w)
    async def fail(**payload): raise BadRequest('message cannot be deleted')
    b.delete_message=fail
    e.process(callback(7,'qcleanok:1',thread=11));drain(w)
    assert s.order(1)['stage']=='listos'
    assert s.one("SELECT count(*) FROM outbox WHERE kind='cleanup' AND status='failed'")[0]>=3
    assert s.one('SELECT count(*) FROM content WHERE thread_id=11')[0]==3
    assert any('un borrado' in p.get('text','') for m,p in b.calls)


def test_entire_workflow_uses_latest_copies_and_allows_5_4_5(env):
    s,e,b,w=env;o=capture_order(env)
    uid=10
    for target in ('listos','proceso','armar','falta','armar','terminados'):
        before=[r[0] for r in s.all('SELECT current_id FROM content ORDER BY current_id')]
        e.process(callback(uid,f'qmove:1:{s.order(1)["version"]}:{target}',thread=s.one('SELECT thread_id FROM stages WHERE key=?',(s.order(1)['stage'],))[0]));drain(w)
        tid=s.one('SELECT max(id) FROM transfers')[0]
        assert [r[0] for r in s.all('SELECT source_id FROM transfer_items WHERE transfer_id=? ORDER BY source_id',(tid,))]==before
        assert s.order(1)['stage']==target
        uid+=1
    assert s.one('SELECT count(*) FROM transfers')[0]==6


def test_duplicate_click_and_open_capture_block_transfer(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(callback(6,f'qadd:1:{o["version"]}'))
    e.process(callback(7,f'qmove:1:{o["version"]}:listos'));drain(w)
    assert s.one('SELECT count(*) FROM transfers')[0]==0
    e.process(message(8,'Más texto'))
    e.process(message(9,'/cerrar'));drain(w)
    version=s.order(1)['version']
    e.process(callback(10,f'qmove:1:{version}:listos'))
    e.process(callback(11,f'qmove:1:{version}:listos'));drain(w)
    assert s.one('SELECT count(*) FROM transfers')[0]==1


def test_copy_inflight_restart_is_uncertain(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'))
    with s.db:s.db.execute("UPDATE outbox SET status='sending' WHERE kind='copy'")
    w.recover();drain(w)
    assert s.one("SELECT count(*) FROM outbox WHERE kind='copy' AND status='uncertain'")[0]==2
    assert s.order(1)['stage']=='nuevos'


def test_kevin_process_can_only_advance_to_5_not_6(env):
    from impresion_bot.ui import actions
    s,e,b,w=env;o=capture_order(env)
    for uid,target,thread in [(6,'listos',10),(7,'proceso',11)]:
        e.process(callback(uid,f'qmove:1:{s.order(1)["version"]}:{target}',thread=thread));drain(w)
    version=s.order(1)['version']
    buttons=actions(s.order(1))['inline_keyboard']
    assert any(button['callback_data']==f'qmove:1:{version}:armar' for row in buttons for button in row)
    assert not any(button['callback_data'].endswith(':terminados') for row in buttons for button in row)
    e.process(callback(8,f'qmove:1:{version}:terminados',actor=22,thread=12));drain(w)
    assert s.order(1)['stage']=='proceso'
    e.process(callback(9,f'qmove:1:{version}:armar',actor=22,thread=12));drain(w)
    assert s.order(1)['stage']=='armar'


def test_stale_topic_card_cannot_advance_current_order(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'));drain(w)
    e.process(callback(7,f'qmove:1:{s.order(1)["version"]}:proceso',thread=10));drain(w)
    assert s.order(1)['stage']=='listos'
    assert s.one('SELECT count(*) FROM transfers')[0]==1


def test_cleanup_preserves_other_order_and_current_destination(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(message(20,'/nuevo Otro'))
    e.process(message(21,'Trabajo ajeno'))
    e.process(message(22,'/cerrar'));drain(w)
    other_posts={r[0] for r in s.all('SELECT message_id FROM bot_posts WHERE order_id=2')}
    e.process(callback(23,f'qmove:1:{o["version"]}:listos'));drain(w)
    destination={r[0] for r in s.all('SELECT message_id FROM bot_posts WHERE order_id=1 AND thread_id=11')}
    e.process(callback(24,'qcleanok:1',thread=11));drain(w)
    deleted={p['message_id'] for m,p in b.calls if m=='delete_message'}
    assert not deleted & other_posts
    assert not deleted & destination
    assert 1021 not in deleted


def test_delayed_progress_message_is_cleaned_when_it_arrives(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'))
    progress=s.one("SELECT id FROM outbox WHERE dedupe='progress:1:copy:start'")[0]
    with s.db:s.db.execute('UPDATE outbox SET available=99999999999 WHERE id=?',(progress,))
    drain(w)
    e.process(callback(7,'qcleanok:1',thread=11));drain(w)
    assert s.one('SELECT status FROM transfers WHERE id=1')[0]=='cleaning'
    with s.db:s.db.execute('UPDATE outbox SET available=0 WHERE id=?',(progress,))
    drain(w)
    mid=s.one('SELECT message_id FROM outbox WHERE id=?',(progress,))[0]
    assert mid in {p['message_id'] for m,p in b.calls if m=='delete_message'}
    assert s.one('SELECT status FROM transfers WHERE id=1')[0]=='cleaned'


def test_backfill_old_open_and_progress_posts(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'));drain(w)
    with s.db:
        s.db.execute('DELETE FROM bot_posts')
        s.db.execute("UPDATE outbox SET order_id=NULL WHERE kind='message'")
    s.backfill_bot_posts()
    e.process(callback(7,'qcleanok:1',thread=11));drain(w)
    assert s.one('SELECT count(*) FROM bot_posts WHERE order_id=1 AND thread_id=10 AND deleted=0')[0]==0


def test_old_cleanup_cannot_delete_order_when_it_returns_to_source(env):
    s,e,b,w=env;o=capture_order(env)
    for uid,target,thread in [(6,'listos',10),(7,'proceso',11),(8,'armar',12),(9,'falta',14),(10,'armar',13)]:
        e.process(callback(uid,f'qmove:1:{s.order(1)["version"]}:{target}',thread=thread));drain(w)
    e.process(callback(11,'qcleanok:4',thread=14));drain(w)
    assert not any(m=='delete_message' for m,p in b.calls)


def test_confirm_in_old_topic_does_not_leave_new_progress_there(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'));drain(w)
    e.process(callback(7,'qclean:1',thread=10));drain(w)
    e.process(callback(8,'qcleanok:1',thread=10));drain(w)
    assert s.one('SELECT count(*) FROM bot_posts WHERE thread_id=10 AND deleted=0')[0]==0


def test_previous_cleaned_transfer_can_remove_legacy_bot_residuals(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'));drain(w)
    with s.db:
        s.db.execute("UPDATE transfers SET status='cleaned' WHERE id=1")
    e.process(callback(7,'qclean:1',thread=11));drain(w)
    e.process(callback(8,'qcleanok:1',thread=11));drain(w)
    assert s.one('SELECT count(*) FROM bot_posts WHERE thread_id=10 AND deleted=0')[0]==0


def test_cleanup_source_survives_topic_configuration_change(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'));drain(w)
    with s.db:s.db.execute("UPDATE stages SET thread_id=90 WHERE key='nuevos'")
    e.process(callback(7,'qcleanok:1',thread=11));drain(w)
    assert s.one('SELECT count(*) FROM bot_posts WHERE thread_id=10 AND deleted=0')[0]==0


def test_progress_precedes_copy_and_updates_confirmed_counts(env):
    s,e,b,w=env;o=capture_order(env);b.calls.clear()
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'));drain(w)
    first_copy=next(i for i,(m,p) in enumerate(b.calls) if m=='copy_messages')
    start=next(i for i,(m,p) in enumerate(b.calls) if '0/3 mensajes' in p.get('text',''))
    assert start<first_copy
    edits=[p for m,p in b.calls if m=='edit_message_text' and 'mensajes' in p.get('text','')]
    assert any('1/3' in p['text'] for p in edits)
    assert any('3/3' in p['text'] and 'Pedido en la fase' in p['text'] for p in edits)
    assert len({p['message_id'] for p in edits})==1
    assert all(p['reply_markup']['inline_keyboard'][-1][0]['url']=='https://t.me/c/12345/11' for p in edits)


def test_manual_cleanup_progress_is_one_editable_notice(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'));drain(w);b.calls.clear()
    e.process(callback(7,'qcleanok:1',thread=11));drain(w)
    start=next(i for i,(m,p) in enumerate(b.calls) if m=='send_message' and 'Limpiando' in p.get('text',''))
    deleted=next(i for i,(m,p) in enumerate(b.calls) if m=='delete_message')
    assert start<deleted
    edits=[p for m,p in b.calls if m=='edit_message_text' and ('Limpiando' in p.get('text','') or 'Tema anterior limpio' in p.get('text',''))]
    assert len(edits)>1 and len({p['message_id'] for p in edits})==1
    assert 'Tema anterior limpio' in edits[-1]['text']
    assert not s.one("SELECT 1 FROM outbox WHERE dedupe='cleaned:1'")


def test_auto_cleanup_default_off_and_setting_survives_restart(env):
    s,e,b,w=env
    assert s.one("SELECT value FROM meta WHERE key='auto_cleanup'")[0]=='0'
    e.process(message(1,'/autolimpiar activar'));drain(w)
    from impresion_bot.store import Store
    restarted=Store(e.c.database)
    assert restarted.one("SELECT value FROM meta WHERE key='auto_cleanup'")[0]=='1'
    restarted.db.close()
    e=Engine(s,e.c)
    e.process(message(2,'/autolimpiar desactivar'));drain(w)
    assert s.one("SELECT value FROM meta WHERE key='auto_cleanup'")[0]=='0'
    assert s.one("SELECT count(*) FROM events WHERE kind='limpieza_automatica_configurada'")[0]==2


def test_auto_cleanup_requires_app_admin_and_correct_chat(env):
    s,e,b,w=env
    e.process(message(1,'/autolimpiar activar',actor=22))
    wrong=message(2,'/autolimpiar activar');wrong['message']['chat']['id']=-100999
    e.process(wrong);drain(w)
    assert s.one("SELECT value FROM meta WHERE key='auto_cleanup'")[0]=='0'


def test_auto_cleanup_waits_for_confirmed_card_is_silent_and_preserves_destination(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(message(6,'/autolimpiar activar'));drain(w);b.calls.clear()
    u=callback(7,f'qmove:1:{o["version"]}:listos');e.process(u);e.process(u);drain(w)
    assert s.one('SELECT status FROM transfers')[0]=='cleaned'
    calls=[m for m,p in b.calls]
    destination_card=next(i for i,(m,p) in enumerate(b.calls) if m=='send_message' and p.get('message_thread_id')==11 and 'Usa los botones' in p.get('text',''))
    assert destination_card<calls.index('delete_message')
    deleted={p['message_id'] for m,p in b.calls if m=='delete_message'}
    assert {1001,1002,1003,1004,1005}<=deleted
    assert not deleted & {r[0] for r in s.all('SELECT current_id FROM content')}
    assert s.one('SELECT count(*) FROM bot_posts WHERE thread_id=10 AND deleted=0')[0]==0
    assert not any('limpi' in p.get('text','').lower() for m,p in b.calls)
    assert not s.one("SELECT 1 FROM outbox WHERE dedupe IN ('cleanup-offer:1','cleaned:1')")


def test_auto_cleanup_snapshot_does_not_change_mid_transfer(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'))
    e.process(message(7,'/autolimpiar activar'));drain(w)
    assert s.one('SELECT auto_clean FROM transfer_ui')[0]==0
    assert not any(m=='delete_message' for m,p in b.calls)
    e.process(callback(8,f'qmove:1:{s.order(1)["version"]}:proceso',thread=11))
    e.process(message(9,'/autolimpiar desactivar',thread=11));drain(w)
    assert s.one('SELECT status FROM transfers WHERE id=2')[0]=='cleaned'


def test_auto_cleanup_partial_copy_preserves_every_original(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(message(6,'/autolimpiar activar'));drain(w)
    async def partial(**payload): return []
    b.copy_messages=partial
    e.process(callback(7,f'qmove:1:{o["version"]}:listos'));drain(w)
    assert not any(m=='delete_message' for m,p in b.calls)
    assert s.order(1)['stage']=='nuevos'


def test_auto_cleanup_card_failure_preserves_originals(env):
    s,e,b,w=env;o=capture_order(env)
    e.process(message(6,'/autolimpiar activar'));drain(w)
    original=b.send_message
    async def send(**payload):
        if payload.get('message_thread_id')==11 and 'Usa los botones' in payload.get('text',''):
            raise BadRequest('message thread not found')
        return await original(**payload)
    b.send_message=send
    e.process(callback(7,f'qmove:1:{o["version"]}:listos'));drain(w)
    assert not any(m=='delete_message' for m,p in b.calls)
    assert s.one('SELECT status FROM transfers')[0]=='done'
    assert any('ficha sin confirmar' in p.get('text','') for m,p in b.calls)


def test_progress_edit_failure_does_not_block_successful_transfer(env):
    s,e,b,w=env;o=capture_order(env)
    original=b.edit_message_text
    async def edit(**payload):
        if 'mensajes' in payload.get('text',''): raise BadRequest('message to edit not found')
        return await original(**payload)
    b.edit_message_text=edit
    e.process(callback(6,f'qmove:1:{o["version"]}:listos'));drain(w)
    assert s.order(1)['stage']=='listos'
    assert s.one("SELECT status FROM outbox WHERE dedupe='cleanup-offer:1'")[0]=='sent'


def test_new_engine_inside_delivery_transaction_does_not_commit_partially(env):
    s,e,b,w=env
    try:
        with s.db:
            s.event(None,11,'prueba_transaccion',{})
            Engine(s,e.c)
            raise RuntimeError('interrupción simulada')
    except RuntimeError:
        pass
    assert not s.one("SELECT 1 FROM events WHERE kind='prueba_transaccion'")


def test_auto_cleanup_recovers_pending_jobs_after_restart(env):
    import asyncio
    from impresion_bot.delivery import Delivery
    s,e,b,w=env;o=capture_order(env)
    e.process(message(6,'/autolimpiar activar'));drain(w)
    e.process(callback(7,f'qmove:1:{o["version"]}:listos'))
    async def until_cleaning():
        for _ in range(30):
            await w.once()
            if s.one('SELECT status FROM transfers')[0]=='cleaning': return
        raise AssertionError('no empezó limpieza')
    asyncio.run(until_cleaning())
    restarted=Delivery(s,e.c,b);restarted.recover();drain(restarted)
    assert s.one('SELECT status FROM transfers')[0]=='cleaned'
    assert s.one('SELECT count(*) FROM bot_posts WHERE thread_id=10 AND deleted=0')[0]==0
