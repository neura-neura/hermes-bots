import asyncio
import pytest
from types import SimpleNamespace
from impresion_bot.config import Config
from impresion_bot.store import Store, STAGES
from impresion_bot.engine import Engine
from impresion_bot.delivery import Delivery


class FakeBot:
    def __init__(self):
        self.calls=[]
        self.failures=[]
        self.mid=100

    async def call(self, method, **payload):
        self.calls.append((method,payload))
        if self.failures:
            raise self.failures.pop(0)
        self.mid+=1
        return SimpleNamespace(message_id=payload.get('message_id',self.mid))

    async def send_message(self, **kw): return await self.call('send_message',**kw)
    async def edit_message_text(self, **kw): return await self.call('edit_message_text',**kw)
    async def answer_callback_query(self, **kw): return await self.call('answer_callback_query',**kw)


@pytest.fixture
def env(tmp_path):
    config=Config('fake',-10012345,11,22,frozenset({11}),database=str(tmp_path/'bot.db'))
    store=Store(config.database)
    engine=Engine(store,config)
    engine.username='pruebas_bot'
    with store.db:
        for n,key in enumerate(STAGES,10):
            store.db.execute('UPDATE stages SET thread_id=? WHERE key=?',(n,key))
    bot=FakeBot()
    return store,engine,bot,Delivery(store,config,bot)


def drain(worker, limit=100):
    async def work():
        for _ in range(limit):
            if not await worker.once(): return
        raise AssertionError('La cola no termina')
    asyncio.run(work())


def message(uid,text='',actor=11,thread=10,reply=None,**extra):
    m={'message_id':uid+1000,'chat':{'id':-10012345,'type':'supergroup'},'from':{'id':actor,'is_bot':False},'message_thread_id':thread,'text':text}
    if reply: m['reply_to_message']={'message_id':reply}
    m.update(extra)
    return {'update_id':uid,'message':m}


def callback(uid,data,actor=11,thread=10,qid=None):
    return {'update_id':uid,'callback_query':{'id':qid or str(uid),'from':{'id':actor,'is_bot':False},'data':data,
            'message':{'message_id':50,'chat':{'id':-10012345},'message_thread_id':thread}}}


def create(env):
    s,e,_,_=env
    with s.db:
        o=e.d.create(11,e.d.parse('cliente=Prueba\ntipo=impresión'))
        e.publish(o)
    return o
