"""Framework real con transporte simulado: valida serialización, no solo mocks de Bot."""
import asyncio
import json
from telegram import Bot, Update
from telegram.request import BaseRequest
from impresion_bot.delivery import Delivery
from conftest import create


class Transport(BaseRequest):
    def __init__(self): self.sent=[]
    @property
    def read_timeout(self): return 5
    async def initialize(self): pass
    async def shutdown(self): pass
    async def do_request(self,url,method,request_data=None,**kwargs):
        endpoint=url.rsplit('/',1)[-1]
        params=request_data.parameters if request_data else {}
        self.sent.append((endpoint,params))
        if endpoint=='getMe':
            result={'id':123,'is_bot':True,'first_name':'Pruebas','username':'pruebas_bot'}
        elif endpoint in {'answerCallbackQuery','deleteMessage'}: result=True
        elif endpoint=='copyMessages': result=[{'message_id':700+i} for i,_ in enumerate(params['message_ids'])]
        else:
            result={'message_id':params.get('message_id',50),'date':1780000000,'chat':{'id':-10012345,'type':'supergroup'},'text':params['text'],'message_thread_id':params.get('message_thread_id',10)}
        return 200,json.dumps({'ok':True,'result':result}).encode()


def test_real_framework_send_edit_force_reply_and_update(env):
    s,e,_,_=env;create(env)
    transport=Transport()
    async def execute():
        async with Bot('123:FAKE',request=transport,get_updates_request=Transport()) as bot:
            w=Delivery(s,e.c,bot)
            await w.once()
            update=Update.de_json({'update_id':9,'message':{'message_id':8,'date':1780000000,'from':{'id':11,'is_bot':False,'first_name':'Magui'},'chat':{'id':-10012345,'type':'supergroup'},'message_thread_id':10,'text':'/formulario'}},bot)
            e.process(update.to_dict())
            await w.once()
            with s.db: e.publish(e.d.assign(1,1,22,'produccion'))
            await w.once()
    asyncio.run(execute())
    calls={name:params for name,params in transport.sent}
    assert calls['sendMessage']['reply_markup']['force_reply'] is True
    assert calls['editMessageText']['reply_markup']['inline_keyboard']
    assert calls['editMessageText']['message_id']==50
    assert s.one('SELECT prompt_id FROM sessions')[0]==50


def test_real_framework_quick_copy_album_contract(env):
    from conftest import message, callback
    s,e,_,_=env
    e.process(message(1,'/nuevo'))
    for uid in (2,3):
        e.process(message(uid,document={'file_id':str(uid),'file_unique_id':str(uid)},media_group_id='album'))
    e.process(message(4,'/cerrar'))
    transport=Transport()
    async def execute():
        async with Bot('123:FAKE',request=transport,get_updates_request=Transport()) as bot:
            w=Delivery(s,e.c,bot)
            while await w.once(): pass
            e.process(callback(5,f'qmove:1:{s.order(1)["version"]}:listos'))
            while await w.once(): pass
    asyncio.run(execute())
    copies=[params for name,params in transport.sent if name=='copyMessages']
    assert copies[0]['message_ids']==[1002,1003]
    assert copies[0]['message_thread_id']==11
    assert s.order(1)['stage']=='listos'
