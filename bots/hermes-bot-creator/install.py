"""Idempotent installer. Does not modify Hermes core or unrelated profiles."""
import json,sys
from pathlib import Path
from hermes_bot_creator import BotService, BotError, SOURCE
repo=Path(__file__).resolve().parent
s=BotService(); ident='hermes-bot-creator'
prompt=(repo/'BOT_CREATOR_SOUL.md').read_text()
if not any(r['name']==ident for r in s.list()):
    s.create({'name':ident,'display_name':'hermes-bot-creator','description':'Crea, inspecciona y modifica bots nativos de Hermes; herramientas, modelos, historial y rollback.','prompt':prompt,'tools_only':[]})
else:
    old=s.get(ident)
    if old['prompt']!=prompt: s.patch(ident,old['revision'],{'prompt':prompt})
# Scope the administrative MCP to this new native profile only.
old=s.adapter.snapshot(ident)
config={'mcp_servers':{'bot_creator':{'command':sys.executable,'args':[str(repo/'server.py')],'timeout':120}},'platform_toolsets':{'cli':['clarify','bot_creator']},'onboarding':{'profile_build':'off'},'agent':{'coding_context':'off'}}
s.adapter.config(ident,config)
# Remove inherited MCP and disabled clarify, using native atomic YAML primitive.
# The general REST endpoint deep-merges dictionaries, so cannot remove inherited keys.
import yaml
sys.path.insert(0,str(SOURCE))
from utils import atomic_yaml_write
path=s.adapter.path(s.adapter.resolve(ident))
cfg=yaml.safe_load((path/'config.yaml').read_text())
cfg['mcp_servers']=config['mcp_servers']
cfg.setdefault('agent',{})['disabled_toolsets']=[n for n in cfg.get('agent',{}).get('disabled_toolsets',[]) if n!='clarify']
cfg['platform_toolsets']['cli']=['clarify','bot_creator']
atomic_yaml_write(path/'config.yaml',cfg,sort_keys=False)
# Native Bot Mode metadata API with CAS.
s.adapter.display_name(ident,'Hermes Bot Creator')
after=s.adapter.snapshot(ident)
assert after['prompt']==prompt and after['config']['mcp_servers']==config['mcp_servers']
s.record('install',old,after)
print(json.dumps({'verified':True,'id':ident,'name':after['name'],'path':str(path),'model':after['config']['model'],'mcp':'bot_creator'},ensure_ascii=False))
