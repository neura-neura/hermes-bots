"""E2E through Desktop's normal authenticated WS + real model + profile MCP."""
import asyncio,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import websockets
from hermes_bot_creator import BotService
Path('docs').mkdir(exist_ok=True)
s=BotService(); name='bc-temporary-conversation-test'; update='bc-temporary-external-update-test'
async def main():
    s.adapter.connect()
    base=s.adapter.base.replace('http:','ws:')
    transcript=[]; request_id=0
    async with websockets.connect(base+'/api/ws?token='+s.adapter.token,max_size=20000000) as ws:
        async def rpc(method,params):
            nonlocal request_id
            request_id+=1; rid=request_id
            await ws.send(json.dumps({'jsonrpc':'2.0','id':rid,'method':method,'params':params}))
            while True:
                frame=json.loads(await asyncio.wait_for(ws.recv(),timeout=60))
                if frame.get('id')==rid:
                    if 'error' in frame: raise RuntimeError(frame['error'])
                    return frame['result']
        session=await rpc('session.create',{'profile':'hermes-bot-creator','source':'gui','title':'Bot Creator - prueba E2E temporal','follow_profile_config':True,'close_on_disconnect':True})
        sid=session['session_id']
        print('PASS native Desktop session.create',sid,flush=True)
        async def turn(text):
            print('USER',text,flush=True)
            await rpc('prompt.submit',{'session_id':sid,'text':text})
            deadline=time.monotonic()+240
            while time.monotonic()<deadline:
                frame=json.loads(await asyncio.wait_for(ws.recv(),timeout=60))
                if frame.get('method')!='event': continue
                params=frame['params']; kind=params['type']; payload=params.get('payload') or {}
                if kind in ('tool.start','tool.complete','error'): print('EVENT',kind,str(payload)[:500],flush=True)
                if kind=='message.complete' and params.get('session_id')==sid:
                    print('ASSISTANT',payload.get('text',''),flush=True)
                    transcript.append({'user':text,'assistant':payload.get('text',''),'status':payload.get('status')})
                    assert payload.get('status')!='error',payload
                    await asyncio.sleep(1)
                    return payload
            raise TimeoutError('Conversation turn exceeded deadline')
        await turn(f'Crea un bot TEMPORAL llamado {name}, muy conciso. Su propósito es responder dudas generales, en idioma automático. Sin herramientas. No necesito preview.')
        first=s.get(name)
        await turn('Ahora hazlo más amigable, pero conserva que sea muy conciso y conserva todo lo demás.')
        second=s.get(name)
        assert first['id']==second['id'] and first['config']==second['config'] and first['metadata']==second['metadata'] and first['prompt']!=second['prompt']
        print('PASS conversational create and contextual semantic update preserving config',flush=True)
        await turn('También añade búsqueda web; conserva lo demás.')
        third=s.get(name)
        assert third['prompt']==second['prompt'] and next(r for r in s.adapter.tools(name) if r['name']=='web')['enabled']
        await turn('Deshaz eso.')
        fourth=s.get(name)
        assert fourth['config']==second['config'] and fourth['prompt']==second['prompt']
        print('PASS conversational add Web Search and contextual undo',flush=True)
        # Create outside Bot Creator with native REST, then edit by natural conversation.
        s.adapter.create(update,'default','Investigación temporal externa')
        s.adapter.soul(update,'Eres un investigador paciente. Propósito: investigación general. Responde detalladamente. Idioma automático. Mantén rigor y cita fuentes.')
        s.adapter.request('PUT','/api/tools/toolsets/web',{'profile':update,'enabled':True})
        s.adapter.request('PUT','/api/tools/toolsets/browser',{'profile':update,'enabled':True})
        external=s.get(update)
        await turn(f'Inspecciona {update} y hazlo más conciso y quítale Browser, pero conserva Web Search y todo lo demás. Es un bot temporal ya existente; edítalo sin recrearlo.')
        edited=s.get(update)
        assert external['id']==edited['id'] and external['name']==edited['name'] and external['metadata']==edited['metadata']
        assert external['config']['model']==edited['config']['model'] and external['prompt']!=edited['prompt']
        rows={r['name']:r for r in s.adapter.tools(update)}
        assert rows['web']['enabled'] and not rows['browser']['enabled']
        print('PASS mandatory conversational edit of externally created native bot',flush=True)
        # Manual editor writes a new rule; Bot Creator must preserve it.
        s.adapter.soul(update,edited['prompt']+'\nRegla manual: conserva siempre la expresión «CONTROL_MANUAL_782».')
        manual=s.get(update)
        await turn('Ahora hazlo un poco menos formal, conservando su paciencia, tools, modelo y todas las reglas actuales.')
        final=s.get(update)
        assert 'CONTROL_MANUAL_782' in final['prompt'] and final['config']==manual['config']
        print('PASS inverse/manual edit synchronization through natural conversation',flush=True)
        Path('docs/conversation-results.json').write_text(json.dumps(transcript,ensure_ascii=False,indent=2))
try:
    assert not any(r['name'] in (name,update) for r in s.list()),'Disposable bot already exists'
    asyncio.run(main())
finally:
    for row in s.list():
        if row['name'] in (name,update):
            ticket=s.prepare_delete(row['name']);s.delete(row['name'],ticket['confirmation_token'])
            print('CLEANUP',row['name'],flush=True)
