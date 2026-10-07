"""Real installation test; creates/deletes ONLY prefixed disposable profiles."""
import copy, json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hermes_bot_creator import BotService, BotError, Conflict
Path('docs').mkdir(exist_ok=True)
s=BotService(); name='bc-temporary-crud-test'; clone='bc-temporary-copy-test'; renamed='bc-temporary-renamed-'+__import__('secrets').token_hex(4)
assert not any(r['name'] in (name,clone,renamed) for r in s.list()), 'Disposable IDs already exist: refuse overwriting'
report=[]
def ok(label): report.append(label); print('PASS',label,flush=True)
try:
    original=s.create({'name':name,'prompt':'Eres un investigador. Responde detalladamente. Idioma automático. Conserva rigor y paciencia.','description':'Investigación temporal','tools_only':['web','browser']})['bot']
    ok('create/get real native profile')
    before=s.get(name)
    after=s.patch(name,before['revision'],{'prompt':'Eres un investigador. Responde con concisión. Idioma automático. Conserva rigor y paciencia.'})['bot']
    assert before['config']==after['config'] and before['metadata']==after['metadata'] and before['id']==after['id']
    ok('single-property preservation')
    before=after
    after=s.patch(name,before['revision'],{'prompt':'Eres un investigador. Responde con concisión y cita fuentes al final. Idioma automático. Conserva rigor y paciencia.','tools_remove':['browser']})['bot']
    assert after['config']['model']==before['config']['model'] and after['id']==before['id'] and after['metadata']==before['metadata']
    rows={r['name']:r for r in s.adapter.tools(name)}
    assert rows['web']['enabled'] and not rows['browser']['enabled']
    ok('multi-property edit preserving Web Search/model/identity')
    undone=s.rollback(name,after['revision'])['bot']
    assert undone['prompt']==before['prompt'] and undone['config']==before['config']
    ok('undo restores exact snapshot')
    before=undone
    changed=s.patch(name,before['revision'],{'tools_remove':['browser']})['bot']
    changed=s.patch(name,changed['revision'],{'tools_add':['browser']})['bot']
    assert next(r for r in s.adapter.tools(name) if r['name']=='browser')['enabled']
    ok('successive tool removal/addition')
    # Inverse test: normal Hermes editor API changes state outside the service.
    s.adapter.soul(name,'Investigación. Idioma automático. Regla manual: siempre distingue incertidumbre.')
    manual=s.get(name)
    try: s.patch(name,changed['revision'],{'prompt':'No debe guardarse'})
    except Conflict: pass
    else: raise AssertionError('Stale version accepted')
    newer=s.patch(name,manual['revision'],{'prompt':'Investigación concisa. Idioma automático. Regla manual: siempre distingue incertidumbre.'})['bot']
    assert 'Regla manual' in newer['prompt']
    ok('external manual edit reloaded; stale revision rejected')
    for provider in s.adapter.models(name)['providers']:
        if provider['slug']==newer['config']['model']['provider']:
            model=next(m for m in provider['models'] if m!=newer['config']['model']['default'] and m not in provider.get('unavailable_models',[]))
    changed=s.patch(name,newer['revision'],{'model':model})['bot']
    assert changed['config']['model']['default']==model
    ok('native validated model change')
    copied=s.duplicate(name,clone)['bot']
    assert copied['prompt']==changed['prompt'] and copied['config']['model']==changed['config']['model']
    ok('duplicate/create_from source preserved')
    rows=s.adapter.tools(name)
    selection=[r['name'] for r in rows if r['enabled'] and r['tools'] and r['platform']=='cli']
    copied=s.patch(clone,copied['revision'],{'tools_only':selection})['bot']
    ok('copy only tools between bots')
    try: s.get('nonexistent-ambiguous-target')
    except BotError: pass
    else: raise AssertionError('Unknown target accepted')
    try: s.delete(clone,'no-confirmation')
    except BotError: pass
    else: raise AssertionError('Delete accepted without confirmation')
    ok('unknown target and delete confirmation guard')
    copied=s.rename(clone,copied['revision'],renamed)['bot']
    assert copied['id']==renamed
    ok('native canonical rename preserving configuration')
    # Independent service instance confirms disk/backend persistence.
    assert BotService().get(name)['prompt']==changed['prompt']
    ok('reopen persistence')
    # Successive undos form a stack rather than toggling the rollback itself.
    a=s.get(name)
    b=s.patch(name,a['revision'],{'prompt':a['prompt']+'\nResponde de forma cercana.'})['bot']
    c=s.patch(name,b['revision'],{'prompt':b['prompt']+'\nIncluye ejemplos cuando ayuden.'})['bot']
    d=s.rollback(name,c['revision'])['bot']; assert d['prompt']==b['prompt']
    e=s.rollback(name,d['revision'])['bot']; assert e['prompt']==a['prompt']
    ok('multiple undo stack')
    # Simulate an adapter failure AFTER first native write: compensation must restore bytes.
    native=s.adapter.description
    def fail(*args): raise BotError('Simulated backend failure')
    s.adapter.description=fail
    baseline=s.adapter.snapshot(name)
    try:
        s.patch(name,baseline['revision'],{'prompt':'Cambio temporal que debe revertirse','description':'Debe fallar'})
    except BotError as error: assert 'compensated' in str(error)
    else: raise AssertionError('Failure reported success')
    finally: s.adapter.description=native
    assert s.adapter.snapshot(name)['files']==baseline['files']
    ok('failure after write compensates exact state; no false success')
    # Display rename uses Desktop native metadata CAS, preserves ID/config/SOUL.
    baseline=s.get(name)
    renamed_display=s.patch(name,baseline['revision'],{'display_name':'Temporal renombrado'})['bot']
    assert renamed_display['id']==name and renamed_display['config']==baseline['config'] and renamed_display['prompt']==baseline['prompt']
    ok('native Bot Mode title rename preserves ID')

finally:
    for row in s.list():
        if row['name'] in (name,clone,renamed):
            ticket=s.prepare_delete(row['name'])
            s.delete(row['name'],ticket['confirmation_token'])
    ok('native confirmed deletion and disposable cleanup')
    Path('docs/crud-results.json').write_text(json.dumps(report,indent=2))
