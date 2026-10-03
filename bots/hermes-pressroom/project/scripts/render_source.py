from pathlib import Path
import json
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]; RUN=ROOT/'newspapers/the-k-times/2026/09/21/r2';idx=json.loads((RUN/'research/index.json').read_text())
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True)
 for i in [17,18,19,20]:
  x=next(x for x in idx if x['id']==i);pg=b.new_page()
  try:
   pg.goto(x['url'],wait_until='domcontentloaded',timeout=45000);pg.wait_for_timeout(7000)
   txt=pg.locator('body').inner_text();(RUN/'research'/f'{i:02}-browser.txt').write_text(txt)
   imgs=pg.locator('img').evaluate_all('(xs)=>xs.map(x=>({src:x.currentSrc,alt:x.alt}))');x['browser_images']=imgs
   print(i,txt[:13000]);print('IMAGES',json.dumps(imgs[:12]))
  except Exception as e:print(i,str(e))
  pg.close()
 b.close()
(RUN/'research/index.json').write_text(json.dumps(idx,indent=2,ensure_ascii=False))
