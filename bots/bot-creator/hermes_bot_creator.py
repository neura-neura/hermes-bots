"""Hermes native profile administration. REST is authoritative; no bot registry."""
from __future__ import annotations
import copy, contextlib, datetime, fcntl, hashlib, json, os, re, secrets, subprocess, tempfile, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote, urlparse
from urllib.error import HTTPError
import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictStr

ROOT = Path.home() / '.hermes'
SOURCE = ROOT / 'hermes-agent'
STATE = ROOT / 'bot-creator-history'
FILES = ('config.yaml', 'SOUL.md', 'profile.yaml')

class BotError(RuntimeError): pass
class Conflict(BotError): pass

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def redact(value):
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if re.search(r'api.?key|token|secret|password|authorization|cookie', str(k), re.I) else redact(v)) for k,v in value.items()}
    if isinstance(value, list): return [redact(v) for v in value]
    return value

def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(value); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

class BotPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: StrictStr | None = Field(None, description='Complete coherent replacement SOUL.md; preserve all unrelated rules, never append conflicting instructions.')
    description: StrictStr | None = None
    display_name: StrictStr | None = None
    model: StrictStr | None = None
    provider: StrictStr | None = None
    tools_add: list[StrictStr] = Field(default_factory=list)
    tools_remove: list[StrictStr] = Field(default_factory=list)
    tools_only: list[StrictStr] | None = None

class BotSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: StrictStr
    prompt: StrictStr
    description: StrictStr = ''
    clone_from: StrictStr = 'default'
    display_name: StrictStr | None = None
    tools_only: list[StrictStr] | None = None
    model: StrictStr | None = None
    provider: StrictStr | None = None

class HermesBotAdapter:
    """Talk to the same authenticated REST backend used by Hermes Desktop."""
    def __init__(self, base=None, root=ROOT):
        self.root = Path(root)
        self.base = base
        self.token = None

    def connect(self):
        candidates = [self.base] if self.base else []
        if not candidates:
            processes = subprocess.check_output(['ps','ax','-o','pid=,command='], text=True)
            pids = [line.strip().split()[0] for line in processes.splitlines() if 'hermes_cli.main' in line and (' serve ' in line or "'serve'" in line or ' dashboard ' in line or "'dashboard'" in line) and 'python' in line]
            for pid in pids:
                result = subprocess.run(['lsof','-nP','-a','-p',pid,'-iTCP','-sTCP:LISTEN'],capture_output=True,text=True)
                for port in re.findall(r'(?:127\.0\.0\.1|\*|0\.0\.0\.0):(\d+) \(LISTEN\)',result.stdout):
                    candidates.append('http://127.0.0.1:'+port)
        for base in candidates:
            if urlparse(base).hostname not in ('127.0.0.1','localhost'): raise BotError('Only loopback Hermes backends are allowed')
            try:
                html = urlopen(base+'/',timeout=5).read().decode()
                match = re.search(r'window\.__HERMES_SESSION_TOKEN__\s*=\s*("[^"]+")',html)
                if not match: continue
                self.base, self.token = base, json.loads(match.group(1))
                roster = self.request('GET','/api/profiles')['profiles']
                if not any(Path(r['path']).resolve() == self.root.resolve() for r in roster): continue
                return
            except (OSError, ValueError, KeyError): continue
        self.token = None
        raise BotError('No authenticated local Hermes backend for this installation. Open Hermes Desktop or dashboard.')

    def request(self, method, path, body=None):
        if self.token is None: self.connect()
        data = None if body is None else json.dumps(body).encode()
        req = Request(self.base+path,data=data,method=method,headers={'X-Hermes-Session-Token':self.token,'Content-Type':'application/json'})
        try:
            with urlopen(req,timeout=90) as response: return json.load(response)
        except HTTPError as e:
            if e.code == 401:
                self.token = None
                raise BotError('Hermes authentication changed; retry to reconnect') from None
            # Server exception text may contain secrets; return status only.
            raise BotError(f'Hermes rejected {method} {path.split("?")[0]} (HTTP {e.code})') from None
        except OSError:
            self.token = None
            raise BotError('Hermes backend unavailable; result must be checked before retrying') from None

    def rpc(self, method, params):
        from websockets.sync.client import connect
        if self.token is None: self.connect()
        with connect(self.base.replace('http:','ws:')+'/api/ws?'+urlencode({'token':self.token}),max_size=20000000,open_timeout=10) as ws:
            ws.send(json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}))
            while True:
                frame=json.loads(ws.recv(timeout=90))
                if frame.get('id')==1:
                    if 'error' in frame: raise BotError('Native Hermes RPC rejected operation')
                    return frame['result']

    def list(self): return self.request('GET','/api/profiles')['profiles']
    def resolve(self, target):
        rows = self.list()
        exact = [r for r in rows if r['name'] == target]
        if exact: return exact[0]
        matches = [r for r in rows if target.casefold() in [str(r.get(k,'')).casefold() for k in ('name','display_name','bot_title')]]
        if len(matches) != 1: raise BotError('Bot absent or ambiguous; select a canonical profile ID from list_bots')
        return matches[0]

    def path(self, row):
        expected = self.root if row['name']=='default' else self.root/'profiles'/row['name']
        if expected.resolve() != Path(row['path']).resolve() or expected.is_symlink(): raise BotError('Unexpected profile path')
        for name in FILES:
            if (expected/name).is_symlink(): raise BotError('Managed identity/config files must not be symlinks')
        return expected

    def snapshot(self, target):
        row = self.resolve(target); path = self.path(row)
        files = {name: (path/name).read_text() if (path/name).exists() else None for name in FILES}
        config = self.request('GET','/api/config?'+urlencode({'profile':row['name']}))
        raw_config = yaml.safe_load(files['config.yaml'] or '') or {}
        config['model'] = raw_config.get('model', {'default': config.get('model', '')})
        if isinstance(config['model'], str): config['model'] = {'default': config['model']}
        config.pop('model_context_length', None)
        soul = self.request('GET',f'/api/profiles/{quote(row["name"])}/soul')['content']
        # Protect against a disk/API read race; retry must start from a fresh snapshot.
        again = {name: (path/name).read_text() if (path/name).exists() else None for name in FILES}
        if files != again: raise Conflict('Bot changed while being read; reload')
        metadata = yaml.safe_load(files['profile.yaml'] or '') or {}
        return {'id':row['name'],'name':row.get('bot_title') or row.get('display_name') or row['name'], 'description':row.get('description',''), 'prompt':soul,'config':config,'metadata':metadata,'files':files,'revision':digest(files)}

    def tools(self, target): return self.request('GET','/api/tools/toolsets?'+urlencode({'profile':target}))
    def models(self, target): return self.request('GET','/api/model/options?'+urlencode({'profile':target}))
    def create(self, name, source, description):
        return self.request('POST','/api/profiles',{'name':name,'clone_from':source,'clone_all':False,'clone_channels':False,'description':description})
    def config(self, target, changes): return self.request('PUT','/api/config',{'profile':target,'config':changes})
    def soul(self, target, prompt): return self.request('PUT',f'/api/profiles/{quote(target)}/soul',{'content':prompt})
    def description(self, target, value): return self.request('PUT',f'/api/profiles/{quote(target)}/description',{'description':value})
    def model(self, target, provider, model): return self.request('PUT',f'/api/profiles/{quote(target)}/model',{'provider':provider,'model':model})
    def display_name(self, target, value):
        # Bot Mode uses profiles.configure with per-plugin metadata CAS.
        snap=self.snapshot(target)
        metadata=snap['metadata']
        botmeta=copy.deepcopy((metadata.get('ui_meta') or {}).get('hermes-bots') or {})
        botmeta['title']=value
        revision=(metadata.get('_ui_meta_revisions') or {}).get('hermes-bots',0)
        result=self.rpc('profiles.configure',{'name':target,'ui_meta':{'hermes-bots':botmeta},'ui_meta_expected_revisions':{'hermes-bots':revision}})
        if not result.get('ok') or not result.get('applied',{}).get('ui_meta'):
            raise Conflict('Native Bot Mode metadata CAS rejected title change')
    def rename(self, target, name):
        # Native rename currently leaves a stale destination tombstone when reusing a deleted ID.
        # Refuse that case before any mutation; preserve the existing profile safely.
        normalized=name.strip().lower()
        if target!='default' and (self.root/'profiles'/'.deleted'/normalized).exists():
            raise BotError('Destination ID was previously deleted; choose a fresh canonical ID or use display rename')
        return self.request('PATCH',f'/api/profiles/{quote(target)}',{'new_name':name})
    def delete(self, target): return self.request('DELETE',f'/api/profiles/{quote(target)}')
    def restore_files(self, target, files, expected_revision):
        # REST deep-merge cannot remove newly introduced keys or restore absent files.
        # Exact restoration uses the backend's own atomic file primitive, never a second DB.
        current = self.snapshot(target)
        if current['revision'] != expected_revision: raise Conflict('Concurrent changes prevent exact restoration')
        import sys
        sys.path.insert(0,str(SOURCE))
        from utils import atomic_write_text
        path = self.path(self.resolve(target))
        for name, content in files.items():
            if content is None:
                (path/name).unlink(missing_ok=True)
            else: atomic_write_text(path/name,content,preserve_mode=True,create_mode=0o600)
        after = self.snapshot(target)
        if after['files'] != files: raise BotError('Restoration failed verification')
        return after

class BotService:
    def __init__(self, adapter=None, state=STATE):
        self.adapter = adapter or HermesBotAdapter()
        self.state = Path(state)
        self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(self.state,0o700)

    @contextlib.contextmanager
    def lock(self):
        with open(self.state/'operations.lock','a') as f:
            os.chmod(self.state/'operations.lock',0o600)
            fcntl.flock(f,fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(f,fcntl.LOCK_UN)

    def public(self, snap): return redact({k:v for k,v in snap.items() if k!='files'})
    def get(self, target): return self.public(self.adapter.snapshot(target))
    def list(self, query=''):
        rows = self.adapter.list()
        return [r for r in rows if query.casefold() in ' '.join(str(r.get(k,'')) for k in ('name','display_name','bot_title','description')).casefold()]
    def record(self, op, before, after=None, status='committed', detail=None):
        ident = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-'+secrets.token_hex(4)
        entry = {'version':ident,'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'operation':op,'bot_id':(after or before)['id'],'before':before,'after':after,'status':status,'detail':detail,'fields_modified':[]}
        if before and after:
            entry['fields_modified']=[k for k in ('id','prompt','config','metadata','description') if before.get(k)!=after.get(k)]
        private_write(self.state/(ident+'.json'),json.dumps(entry,ensure_ascii=False))
        return entry
    def entries(self, target):
        ident = self.adapter.resolve(target)['name']
        ids = {ident}; result=[]
        for path in sorted(self.state.glob('*.json'),reverse=True):
            e=json.loads(path.read_text())
            if e.get('bot_id') in ids or (e.get('after') or {}).get('id') in ids:
                result.append(e)
                if e['operation']=='rename' and e.get('before'): ids.add(e['before']['id'])
        return result
    def history(self, target):
        return [redact({k:v for k,v in e.items() if k not in ('before','after')}) | {'before':self.public(e['before']) if e.get('before') else None,'after':self.public(e['after']) if e.get('after') else None} for e in self.entries(target)]

    def check(self, before, revision):
        if before['revision'] != revision: raise Conflict('Bot changed since inspection; get_bot again and reconcile the requested edit')

    def plan(self, before, patch):
        patch = BotPatch.model_validate(patch)
        if patch.display_name is not None and (not patch.display_name.strip() or len(patch.display_name)>64): raise BotError('Name must contain 1–64 characters')
        if patch.display_name:
            for row in self.adapter.list():
                if row['name'] != before['id'] and patch.display_name.casefold() in [str(row.get(k,'')).casefold() for k in ('name','display_name','bot_title')]: raise BotError('Duplicate bot name')
        if patch.prompt is not None and (not patch.prompt.strip() or len(patch.prompt)>100000): raise BotError('Prompt must contain 1–100000 characters')
        if set(patch.tools_add)&set(patch.tools_remove): raise BotError('Contradictory tool additions/removals')
        if patch.tools_only is not None and (patch.tools_add or patch.tools_remove): raise BotError('tools_only cannot be combined with add/remove')
        config = {}
        if patch.tools_add or patch.tools_remove or patch.tools_only is not None:
            rows = self.adapter.tools(before['id']); catalog={r['name']:r for r in rows}
            names=set(patch.tools_add+patch.tools_remove+(patch.tools_only or []))
            if names-set(catalog): raise BotError('Unknown tools; use discover_capabilities and exact native toolset names')
            enabling=set(patch.tools_add+(patch.tools_only or []))
            if any(not catalog[n]['configured'] for n in enabling): raise BotError('Tool prerequisites/credentials are not configured for this profile')
            platforms = {catalog[n]['platform'] for n in names}
            if patch.tools_only is not None: platforms.add('cli')
            current=before['config']
            disabled=set((current.get('agent') or {}).get('disabled_toolsets') or [])
            disabled-=enabling
            disabled.update(patch.tools_remove)
            for platform in platforms:
                eligible={r['name'] for r in rows if r['platform']==platform and r['tools']}
                enabled={r['name'] for r in rows if r['platform']==platform and r['enabled'] and r['tools']}
                if patch.tools_only is not None: enabled=set(patch.tools_only)&eligible
                else: enabled=(enabled|set(patch.tools_add))-set(patch.tools_remove)
                # Preserve MCP/custom composite entries that are not catalog checklist/bundle entries.
                existing=(current.get('platform_toolsets') or {}).get(platform,[])
                preserved={n for n in existing if n not in catalog and not n.startswith('hermes-') and n!='no_mcp'}
                if patch.tools_only is not None:
                    preserved=set(); disabled.update(eligible-enabled)
                    preserved.add('no_mcp')  # exclusive request also excludes implicit MCP servers
                config.setdefault('platform_toolsets',{})[platform]=sorted(enabled|preserved)
                config.setdefault('known_builtin_toolsets',{})[platform]=sorted(catalog)
            config['agent']={'disabled_toolsets':sorted(disabled)}
        model=None
        if patch.model is not None or patch.provider is not None:
            old=before['config'].get('model') or {}
            provider=patch.provider or old.get('provider'); name=patch.model or old.get('default')
            options=self.adapter.models(before['id'])
            candidate=next((p for p in options['providers'] if p['slug']==provider),None)
            if not candidate or not candidate.get('authenticated') or name not in candidate['models'] or name in candidate.get('unavailable_models',[]): raise BotError('Model/provider absent, unavailable or unauthenticated in this profile catalog')
            model={'provider':provider,'model':name}
        return patch,config,model

    def validate(self, target, revision, changes):
        before=self.adapter.snapshot(target); self.check(before,revision)
        patch,config,model=self.plan(before,changes)
        return {'valid':True,'bot_id':before['id'],'revision':revision,'changes':patch.model_dump(exclude_none=True),'native_config_patch':config,'model_assignment':model}

    def patch(self, target, revision, changes, operation='patch'):
        with self.lock():
            before=self.adapter.snapshot(target); self.check(before,revision)
            patch,config,model=self.plan(before,changes)
            pending=self.record(operation,before,status='prepared',detail=patch.model_dump(exclude_none=True))
            path=self.state/(pending['version']+'.json')
            last=before
            try:
                actions=[]
                if config: actions.append(lambda:self.adapter.config(before['id'],config))
                if model: actions.append(lambda:self.adapter.model(before['id'],**model))
                if patch.prompt is not None: actions.append(lambda:self.adapter.soul(before['id'],patch.prompt))
                if patch.description is not None: actions.append(lambda:self.adapter.description(before['id'],patch.description))
                if patch.display_name is not None: actions.append(lambda:self.adapter.display_name(before['id'],patch.display_name))
                for action in actions:
                    self.check(self.adapter.snapshot(before['id']),last['revision'])
                    action()
                    last=self.adapter.snapshot(before['id'])
                after=last
                if patch.prompt is not None and after['prompt'] != patch.prompt: raise BotError('Prompt did not persist')
                if patch.description is not None and after['description'] != patch.description.strip(): raise BotError('Description did not persist')
                if patch.display_name is not None and after['name'] != patch.display_name.strip(): raise BotError('Name did not persist')
                if model:
                    cfg=after['config']['model']
                    if cfg.get('default')!=model['model'] or cfg.get('provider')!=model['provider']: raise BotError('Model did not persist')
                for key,value in config.items():
                    for sub,v in value.items():
                        if after['config'].get(key,{}).get(sub)!=v: raise BotError('Configuration patch did not persist')
                # Verify all semantic config properties outside requested writes.
                def untouched(cfg):
                    cfg=copy.deepcopy(cfg)
                    for key,value in config.items():
                        for sub in value: cfg.get(key,{}).pop(sub,None)
                    if model: cfg.pop('model',None)
                    return {k:v for k,v in cfg.items() if v!={}}
                if untouched(before['config'])!=untouched(after['config']): raise Conflict('Unrelated configuration changed during update')
                if patch.prompt is None and before['prompt']!=after['prompt']: raise Conflict('Unrelated prompt changed during update')
                def unrelated_meta(value):
                    value=copy.deepcopy(value)
                    if patch.description is not None:
                        value.pop('description',None); value.pop('description_auto',None)
                    if patch.display_name is not None:
                        value.pop('display_name',None)
                        ((value.get('ui_meta') or {}).get('hermes-bots') or {}).pop('title',None)
                        if 'hermes-bots' in (value.get('_ui_meta_revisions') or {}): value['_ui_meta_revisions'].pop('hermes-bots')
                        if not value.get('_ui_meta_revisions'): value.pop('_ui_meta_revisions',None)
                        if value.get('ui_meta',{}).get('hermes-bots')=={}: value['ui_meta'].pop('hermes-bots')
                        if not value.get('ui_meta'): value.pop('ui_meta',None)
                    return value
                if unrelated_meta(before['metadata'])!=unrelated_meta(after['metadata']): raise Conflict('Unrelated metadata changed during update')

                pending.update(after=after,status='committed',fields_modified=[k for k in ('id','prompt','config','metadata','description') if before.get(k)!=after.get(k)])
                private_write(path,json.dumps(pending,ensure_ascii=False))
                return {'verified':True,'bot':self.public(after),'version':pending['version'],'changed':patch.model_dump(exclude_none=True)}
            except Exception as exc:
                # Never blindly overwrite concurrent/manual edits, even to compensate.
                pending.update(status='failed',after=last,detail=str(exc))
                try:
                    now=self.adapter.snapshot(before['id'])
                    if not isinstance(exc, Conflict) and now['revision']==last['revision']:
                        self.adapter.restore_files(before['id'],before['files'],now['revision'])
                        pending['status']='compensated'
                    else: pending['status']='needs_reconciliation'
                except Exception: pending['status']='needs_reconciliation'
                private_write(path,json.dumps(pending,ensure_ascii=False))
                raise BotError(f'Update failed; status={pending["status"]}; history={pending["version"]}. {exc}') from None

    def create(self, spec):
        spec=BotSpec.model_validate(spec)
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',spec.name): raise BotError('Canonical ID must use lowercase letters, digits, hyphens or underscores, starting with a letter/digit')
        if spec.name=='default' or any(r['name']==spec.name for r in self.adapter.list()): raise BotError('Bot ID already exists')
        source=self.adapter.snapshot(spec.clone_from)
        patch=BotPatch(prompt=spec.prompt,description=spec.description,display_name=spec.display_name or spec.name,tools_only=spec.tools_only,model=spec.model,provider=spec.provider)
        self.plan(source,patch)
        with self.lock():
            self.check(self.adapter.snapshot(source['id']),source['revision'])
            self.adapter.create(spec.name,source['id'],spec.description)
        # API create can partially succeed; errors below must never be reported as success.
        before=self.adapter.snapshot(spec.name)
        try:
            result=self.patch(spec.name,before['revision'],patch.model_dump(exclude_none=True),operation='create')
            return result
        except Exception as e:
            raise BotError(f'Profile {spec.name} exists but creation configuration failed; inspect/reconcile it. {e}') from None

    def duplicate(self, target, name, display_name=None):
        source=self.adapter.snapshot(target)
        return self.create(BotSpec(name=name,prompt=source['prompt'],description=source['description'],clone_from=source['id'],display_name=display_name))

    def rename(self, target, revision, new_name):
        with self.lock():
            before=self.adapter.snapshot(target); self.check(before,revision)
            if before['id'] in ('default','bot-creator'): raise BotError('Use display rename for protected profiles')
            pending=self.record('rename',before,status='prepared')
            result=self.adapter.rename(before['id'],new_name)
            target_id='default' if before['id']=='default' else result['name']
            for attempt in range(20):
                try:
                    after=self.adapter.snapshot(target_id)
                    break
                except BotError:
                    if attempt==19: raise
                    time.sleep(0.15)
            if before['config']!=after['config'] or before['prompt']!=after['prompt']: raise BotError('Rename did not preserve config/prompt')
            pending.update(after=after,status='committed',bot_id=after['id'])
            private_write(self.state/(pending['version']+'.json'),json.dumps(pending,ensure_ascii=False))
            return {'verified':True,'bot':self.public(after),'version':pending['version'],'note':'Native canonical rename changes profile ID; display_name patch preserves it.'}

    def rollback(self, target, revision, version=None, at=None):
        with self.lock():
            now=self.adapter.snapshot(target); self.check(now,revision)
            entries=self.entries(target)
            if version and at: raise BotError('Choose version OR timestamp')
            if at:
                cutoff=datetime.datetime.fromisoformat(at.replace('Z','+00:00'))
                if cutoff.tzinfo is None: raise BotError('Timestamp requires timezone')
                entry=next((e for e in entries if e.get('after') and e['status']=='committed' and datetime.datetime.fromisoformat(e['timestamp'])<=cutoff),None)
                restore=entry['after'] if entry else None
            elif version:
                entry=next((e for e in entries if e['version']==version and e.get('before')),None)
                restore=entry['before'] if entry else None
            else:
                # Undo stack: committed edits not already undone; rollback operations don't become new undos.
                undone={e.get('detail',{}).get('undoes') for e in entries if e['operation']=='rollback' and e['status']=='committed' and isinstance(e.get('detail'),dict)}
                entry=next((e for e in entries if e['operation'] not in ('rollback','create','delete','rename') and e['status']=='committed' and e['version'] not in undone),None)
                restore=entry['before'] if entry else None
                expected=[e for e in entries if e['status']=='committed' and e.get('after')]
                if entry and expected and now['revision']!=expected[0]['after']['revision']: raise Conflict('Bot was edited outside Bot Creator; preview an explicit version instead of undoing blindly')
            if not restore: raise BotError('No restorable snapshot found')
            if restore['id']!=now['id']: raise BotError('Canonical rename requires explicit native rename back before restoration')
            pending=self.record('rollback',now,status='prepared',detail={'undoes':entry['version']})
            after=self.adapter.restore_files(now['id'],restore['files'],now['revision'])
            pending.update(after=after,status='committed')
            private_write(self.state/(pending['version']+'.json'),json.dumps(pending,ensure_ascii=False))
            return {'verified':True,'bot':self.public(after),'version':pending['version']}

    def prepare_delete(self,target):
        before=self.adapter.snapshot(target)
        if before['id'] in ('default','bot-creator'): raise BotError('Protected installation/Bot Creator profile')
        token=secrets.token_urlsafe(24)
        private_write(self.state/('confirmation-'+digest(token)+'.ticket'),json.dumps({'id':before['id'],'revision':before['revision'],'expires':datetime.datetime.now(datetime.timezone.utc).timestamp()+600}))
        return {'confirmation_token':token,'bot_id':before['id'],'revision':before['revision'],'question':f'¿Confirmas eliminar {before["name"]} ({before["id"]})? Espera la confirmación explícita del usuario antes de llamar delete_bot.'}

    def delete(self,target,token):
        with self.lock():
            ticket=self.state/('confirmation-'+digest(token)+'.ticket')
            if not ticket.exists(): raise BotError('Explicit delete confirmation token required')
            data=json.loads(ticket.read_text()); before=self.adapter.snapshot(target)
            if data['id']!=before['id'] or data['expires']<datetime.datetime.now(datetime.timezone.utc).timestamp(): raise BotError('Confirmation target/expiry mismatch')
            self.check(before,data['revision']); ticket.unlink()
            entry=self.record('delete',before,status='prepared')
            self.adapter.delete(before['id'])
            if any(r['name']==before['id'] for r in self.adapter.list()): raise BotError('Delete did not persist')
            entry['status']='committed'
            private_write(self.state/(entry['version']+'.json'),json.dumps(entry,ensure_ascii=False))
            return {'verified':True,'deleted':before['id'],'version':entry['version'],'note':'Hermes keeps its native .deleted profile backup; normal undo applies to edits, not deletion.'}
