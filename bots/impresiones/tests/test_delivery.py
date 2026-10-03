import asyncio
import json
from telegram.error import TimedOut, RetryAfter, Forbidden, BadRequest
from conftest import create,drain,message,callback


def test_topic_move_new_card_marks_only_bot_card(env):
    s,e,bot,w=env;o=create(env);drain(w)
    original=s.one('SELECT message_id FROM messages WHERE current=1')[0]
    with s.db:
        o=e.d.move(o['id'],o['version'],11,'listos');e.publish(o)
    drain(w)
    current=s.one('SELECT * FROM messages WHERE current=1')
    assert current['thread_id']==11 and current['message_id']!=original
    assert any(m=='edit_message_text' and p['message_id']==original and 'Movido a' in p['text'] for m,p in bot.calls)
    assert not any('delete' in m or 'copy' in m for m,p in bot.calls)


def test_ambiguous_send_stops_automatic_retry_and_later_versions(env):
    s,e,bot,w=env;o=create(env);bot.failures=[TimedOut()];drain(w)
    assert s.one("SELECT status FROM outbox WHERE kind='card'")[0]=='uncertain'
    with s.db: e.publish(e.d.assign(o['id'],1,22,'produccion'))
    drain(w)
    assert len(bot.calls)==1
    assert s.one("SELECT count(*) FROM events WHERE kind='error_sincronizacion'")[0]==1


def test_retry_after_and_edit_network_retry(env):
    s,e,bot,w=env;create(env);bot.failures=[RetryAfter(1)];drain(w)
    assert s.one('SELECT status FROM outbox')[0]=='pending'
    with s.db:s.db.execute('UPDATE outbox SET available=0')
    drain(w)
    o=s.order(1)
    with s.db:e.publish(e.d.assign(1,o['version'],22,'produccion'))
    bot.failures=[TimedOut()];drain(w)
    assert s.one('SELECT status FROM outbox ORDER BY id DESC')[0]=='pending'
    with s.db:s.db.execute('UPDATE outbox SET available=0')
    drain(w)
    assert s.one("SELECT count(*) FROM outbox WHERE status!='sent'")[0]==0


def test_forbidden_recorded_and_retry_confirmation(env):
    s,e,bot,w=env;create(env);bot.failures=[Forbidden('denied')];drain(w)
    job=s.one('SELECT id FROM outbox')[0]
    e.process(callback(1,f'retry:{job}'));drain(w)
    assert s.one('SELECT status FROM outbox WHERE id=?',(job,))[0]=='failed'
    token=s.one('SELECT token FROM sessions')[0]
    e.process(callback(2,f's:{token}:retry'));drain(w)
    assert s.one('SELECT status FROM outbox WHERE id=?',(job,))[0]=='sent'


def test_crash_recovery(env):
    s,e,_,w=env;create(env)
    with s.db:s.db.execute("UPDATE outbox SET status='sending'")
    w.recover()
    assert s.one('SELECT status FROM outbox')[0]=='uncertain'


def test_not_modified_is_success(env):
    s,e,bot,w=env; o=create(env);drain(w)
    with s.db:e.publish(e.d.assign(1,1,22,'produccion'))
    bot.failures=[BadRequest('Message is not modified')];drain(w)
    assert s.one('SELECT status FROM outbox ORDER BY id DESC')[0]=='sent'


def test_deleted_card_republishes(env):
    s,e,bot,w=env;create(env);drain(w)
    with s.db:e.publish(e.d.assign(1,1,22,'produccion'))
    bot.failures=[BadRequest('Message to edit not found')];drain(w)
    assert s.one('SELECT count(*) FROM messages WHERE current=1')[0]==1
    assert s.one('SELECT status FROM outbox ORDER BY id DESC')[0]=='sent'


def test_recover_uncertain_without_duplicate(env):
    s,e,bot,w=env;create(env);bot.failures=[TimedOut()];drain(w)
    job=s.one('SELECT * FROM outbox')
    text=e._plain_html(json.loads(job['payload'])['text'])
    u=message(1,f"/configurar recuperar {job['id']} 900",reply_to_message={'message_id':900,'from':{'is_bot':True,'username':'pruebas_bot'},'text':text})
    e.process(u);drain(w)
    assert s.one('SELECT message_id FROM messages WHERE current=1')[0]==900
    assert len([1 for method,p in bot.calls if 'T-' in p.get('text','')])==1


def test_retarget_requires_confirmation_and_audits(env):
    s,e,bot,w=env;create(env);bot.failures=[BadRequest('message thread not found')];drain(w)
    job=s.one('SELECT id FROM outbox')[0]
    e.process(message(1,'/configurar nuevos 90'));drain(w)
    e.process(message(2,f'/configurar redestinar {job} 90'));drain(w)
    assert json.loads(s.one('SELECT payload FROM outbox WHERE id=?',(job,))[0])['message_thread_id']==10
    token=s.one('SELECT token FROM sessions')[0]
    e.process(callback(3,f's:{token}:retarget'));drain(w)
    assert s.one('SELECT thread_id FROM messages WHERE current=1')[0]==90
    assert s.one("SELECT count(*) FROM events WHERE kind='sincronizacion_redestinada'")[0]==1


def test_retry_exhaustion_is_bounded(env):
    s,e,bot,w=env;create(env)
    bot.failures=[RetryAfter(1)]*5
    for _ in range(5):
        with s.db:s.db.execute('UPDATE outbox SET available=0')
        drain(w)
    assert len(bot.calls)==5
    assert s.one('SELECT status FROM outbox')[0]=='failed'


def test_queued_moves_preserve_one_current_card(env):
    s,e,bot,w=env;o=create(env)
    with s.db:
        for stage in ['listos','proceso','armar']:
            o=e.d.move(o['id'],o['version'],11,stage)
            e.publish(o)
    drain(w)
    current=s.one('SELECT * FROM messages WHERE current=1')
    assert current['thread_id']==14 and current['version']==4
    assert s.one('SELECT count(*) FROM messages WHERE current=1')[0]==1
    assert len([1 for method,p in bot.calls if method=='send_message'])==4
    assert len([1 for method,p in bot.calls if 'Movido a' in p.get('text','')])==3
