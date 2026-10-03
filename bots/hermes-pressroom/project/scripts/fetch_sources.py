from pathlib import Path
import requests,json,datetime,concurrent.futures,subprocess,sys
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]; RUN=ROOT/'newspapers/the-k-times/2026/09/21/r2'
urls=sys.argv[1:]
idx=json.loads((RUN/'research/index.json').read_text()); start=max(x['id'] for x in idx)+1

def fetch(pair):
 i,u=pair
 try:
  r=requests.get(u,timeout=40); s=BeautifulSoup(r.content,'html.parser');(RUN/'research'/f'{i:02}.html').write_text(r.text)
  for e in s(['script','style','nav','footer','header']):e.decompose()
  text=s.get_text('\n',strip=True);(RUN/'research'/f'{i:02}.txt').write_text(text)
  return {'id':i,'url':u,'final_url':r.url,'status':r.status_code,'retrieval_time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'title':s.title.get_text() if s.title else '', 'images':[{'src':x.get('src'),'alt':x.get('alt')} for x in s.select('img')],'links':[{'text':a.get_text(' ',strip=True),'url':a.get('href')} for a in s.select('a[href]')],'text_file':str(RUN/'research'/f'{i:02}.txt')}
 except Exception as e:return {'id':i,'url':u,'error':str(e)}
rs=list(concurrent.futures.ThreadPoolExecutor(max_workers=8).map(fetch,enumerate(urls,start)))
(RUN/'research/index.json').write_text(json.dumps(idx+rs,indent=2,ensure_ascii=False))
for x in rs:
 print(x['id'],x.get('status'),x.get('title'))
 if x.get('status')==200:subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT.parent/'skills/research/grounded-citations/scripts/sources.py'),'--ledger',str(RUN/'sources-ledger.json'),'add',x['url'],'--title',x['title']],check=True)
