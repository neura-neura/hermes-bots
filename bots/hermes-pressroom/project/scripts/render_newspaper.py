"""Reusable Spanish print renderer. Input is researched edition.json, not live news.
Always PDF-only. Requires separate visual approval before any downstream printing.
"""
import argparse,json,hashlib
from pathlib import Path
from html import escape
from playwright.sync_api import sync_playwright
import pymupdf as fitz
from PIL import Image
from check_geometry import JS
from verify_edition import verify
from news_history import require_history
ROOT=Path(__file__).resolve().parents[1]
def render(edition_path,output):
 ed=json.loads(Path(edition_path).read_text());assert ed.get('language')=='es','Renderer requires Spanish editorial input'
 history_check=require_history(ed,select=True)
 out=Path(output).resolve();out.mkdir(parents=True,exist_ok=True)
 (out/'history-check.json').write_text(json.dumps(history_check,indent=2))
 assert not list(out.glob('*.pdf')),'Choose a new output directory; archived PDFs are immutable'
 (out/'edition.json').write_text(json.dumps(ed,ensure_ascii=False,indent=2))
 css=(ROOT/'templates/flow_newspaper.css').read_text()+"@font-face{font-family:UnifrakturCook;src:url('"+(ROOT/'fonts/UnifrakturCook.ttf').as_uri()+"')}"
 script=(ROOT/'templates/flow_newspaper.js').read_text()
 stem='the-k-times_'+ed['date']+'_r'+str(ed['revision']);html=out/(stem+'.html');pdfpath=out/(stem+'.pdf')
 html.write_text('<!doctype html><html lang="es"><head><meta charset="utf-8"><title>'+escape(ed['publication'])+'</title><style>'+css+'</style></head><body><script>'+script+'\npaginate('+json.dumps(ed,ensure_ascii=False).replace('</','<\\/')+');</script></body></html>')
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');p=b.new_page();p.goto(html.as_uri());p.wait_for_function('window.paginationDone');p.wait_for_function('Array.from(document.images).every(i=>i.complete)');g=p.evaluate(JS)
  (out/'dom-geometry.json').write_text(json.dumps(g,ensure_ascii=False,indent=2));assert not any(x['errors'] for x in g),'Printable area violation'
  density=p.evaluate('''()=>[...document.querySelectorAll('.page')].map((p,i)=>({page:i+1,occupancy:[...p.querySelectorAll('.column')].reduce((sum,c)=>sum+(c.lastElementChild?c.lastElementChild.offsetTop+c.lastElementChild.offsetHeight:0)/c.clientHeight,0)/4}))''')
  (out/'density-check.json').write_text(json.dumps(density,indent=2))
  p.pdf(path=str(pdfpath),format='Letter',prefer_css_page_size=True,print_background=True,margin={k:'0' for k in ['top','bottom','left','right']});b.close()
 doc=fitz.open(pdfpath);(out/'renders').mkdir(exist_ok=True)
 for i,p in enumerate(doc,1):
  file=out/'renders'/f'page-{i:02d}.png';p.get_pixmap(matrix=fitz.Matrix(2,2),colorspace=fitz.csGRAY).save(file);im=Image.open(file);im.crop((0,im.height-520,im.width,im.height)).save(out/'renders'/f'page-{i:02d}-bottom.png')
 failed=verify(out);thin=[p for p in density if p['occupancy']<.72]
 result={'status':'FAIL' if failed or thin else 'AUTOMATED_PASS_VISUAL_PENDING','pdf_path':str(pdfpath),'pdf_sha256':hashlib.sha256(pdfpath.read_bytes()).hexdigest(),'pages':len(doc),'density_failures':thin,'print_status':'NOT_PRINTED','visual_review':'PENDING'}
 (out/'render-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));return bool(failed or thin)
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('edition_json');a.add_argument('--output-dir',required=True);args=a.parse_args();raise SystemExit(render(args.edition_json,args.output_dir))
