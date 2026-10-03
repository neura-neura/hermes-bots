import sqlite3
from datetime import datetime, timezone
import pytest
from impresion_bot.store import Store
from impresion_bot.ui import card
from conftest import create


def test_persistent_unique_ids_and_immutable_history(env):
    s,e,_,_=env
    first=create(env)
    with s.db:
        second=e.d.create(22,{})
    assert first['code']!=second['code']
    reopened=Store(e.c.database)
    assert reopened.order(first['code'])['id']==first['id']
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute("UPDATE events SET kind='fraude'")
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute('DELETE FROM events')


def test_edit_assign_and_stale_version(env):
    s,e,_,_=env; o=create(env)
    with s.db:
        o=e.d.edit(o['id'],1,11,{'color':'blanco y negro'})
        o=e.d.assign(o['id'],o['version'],22,'ambos')
    assert o['data']['produccion']==o['data']['comunicacion']==22
    with pytest.raises(ValueError,match='cambió'):
        e.d.edit(o['id'],1,11,{'cliente':'Viejo'})


def test_pending_block_return_and_close(env):
    s,e,_,_=env; o=create(env)
    with s.db:
        with pytest.raises(ValueError,match='Primero'):
            e.d.move(o['id'],o['version'],11,'falta')
        o=e.d.pending(o['id'],o['version'],11,'Falta PDF','Kevin','2026-10-01 10:00')
        o=e.d.move(o['id'],o['version'],11,'falta')
        assert o['previous_stage']=='nuevos'
        with pytest.raises(ValueError,match='Resuelve'):
            e.d.move(o['id'],o['version'],11,'nuevos')
        o=e.d.edit_pending(o['id'],o['version'],11,1,'Confirmar PDF','Magui',None)
        o=e.d.resolve(o['id'],o['version'],11,1)
        o=e.d.move(o['id'],o['version'],11,'nuevos')
        for stage in ['listos','proceso','armar','terminados']:
            o=e.d.move(o['id'],o['version'],11,stage)
    assert s.one("SELECT count(*) FROM events WHERE kind='entregado'")[0]==1


def test_exception_requires_reason(env):
    _,e,_,_=env; o=create(env)
    with pytest.raises(ValueError): e.d.move(o['id'],1,11,'armar')
    with pytest.raises(ValueError): e.d.move(o['id'],1,11,'armar',override=True)
    o=e.d.move(o['id'],1,11,'armar','Trabajo ya impreso',True)
    assert o['stage']=='armar'


def test_dates_due_and_postponement(env):
    _,e,_,_=env; o=create(env)
    assert e.d.date('2026-10-01 09:00')=='2026-10-01T15:00:00+00:00'
    o=e.d.edit(o['id'],1,11,e.d.parse('prometida=2026-10-01 09:00\ninterna=2026-10-01 08:00'))
    assert e.d.due_status(o,datetime(2026,10,1,15,1,tzinfo=timezone.utc))=='vencido'
    assert e.d.due_status(o,datetime(2026,9,30,16,tzinfo=timezone.utc))=='próximo (24 h)'
    with pytest.raises(ValueError,match='motivo'):
        e.d.edit(o['id'],o['version'],11,{'prometida':e.d.date('2026-10-02 09:00')})
    o=e.d.edit(o['id'],o['version'],11,{'prometida':e.d.date('2026-10-02 09:00')},'Cliente solicita cambio')
    assert o['data']['prometida'].startswith('2026-10-02')


def test_escape_and_phone_hidden(env):
    _,e,_,_=env; o=create(env)
    o=e.d.edit(o['id'],1,11,{'cliente':'<b>Falso & "</b>','telefono':'123456789'})
    text=card(o,e.d)
    assert '&lt;b&gt;' in text and '123456789' not in text


@pytest.mark.parametrize('text',['pago=tarjeta 1234','comunicacion=Otro','prometida=ayer','xxx=a','telefono=abc','cantidad='+('a'*161)])
def test_validation(env,text):
    with pytest.raises(ValueError): env[1].d.parse(text)


def test_backup_restore(env,tmp_path):
    s,_,_,_=env; o=create(env)
    target=str(tmp_path/'copy.db'); s.backup(target)
    assert Store(target).order(o['code'])['data']==o['data']
    with pytest.raises(ValueError): s.backup(target)


def test_unsupported_schema_rejected_without_new_tables(tmp_path):
    path=str(tmp_path/'future.db')
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
    db.execute("INSERT INTO meta VALUES('schema_version','99')")
    db.commit()
    with pytest.raises(RuntimeError):Store(path)
    assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='orders'").fetchone()[0]==0


def test_mobile_card_with_maximum_unicode_fields(env):
    _,e,_,_=env;o=create(env)
    values={k:'😀'*160 for k in ['cliente','tipo','cantidad','caras','papel','color','acabados']}
    values['descripcion']='😀'*1200
    o=e.d.edit(o['id'],1,11,values)
    for _ in range(3):o=e.d.pending(o['id'],o['version'],11,'😀'*500,'Magui')
    text=EngineText(card(o,e.d))
    assert len(text.encode('utf-16-le'))//2<4096


def EngineText(text):
    from impresion_bot.engine import Engine
    return Engine._plain_html(text)
