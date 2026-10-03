"""Independent post-render audit. Does not trust manifest QA status. Never prints."""
import sys,json,re,hashlib
from pathlib import Path
import pymupdf as fitz
from playwright.sync_api import sync_playwright
from check_geometry import JS
from news_history import require_history, digest

def norm(t):return re.sub(r'\s+','',t).replace('\u00ad','')
def verify(folder):
 folder=Path(folder).resolve();ed=json.loads((folder/'edition.json').read_text())
 require_history(ed,folder=folder)
 pdfpath=next(folder.glob('*.pdf'));html=next(folder.glob('*.html'));pdf=fitz.open(pdfpath);errors=[]
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');page=browser.new_page();page.goto(html.as_uri());page.wait_for_function('window.paginationDone');page.evaluate('document.fonts.ready');page.wait_for_function('Array.from(document.images).every(i=>i.complete)')
  geom=page.evaluate(JS)
  nodes=page.evaluate('''()=>[...document.querySelectorAll('main .bodytext,main h2,main .deck,main .label,main .date,main figcaption,main .sources')].map(n=>{const b=n.getBoundingClientRect(),p=n.closest('.page');return {tag:n.tagName,cls:n.className,text:n.textContent,paragraph:n.dataset.paragraph,story:n.dataset.story,page:[...document.querySelectorAll('.page')].indexOf(p),bbox:[b.left*.75,(b.top-p.getBoundingClientRect().top)*.75,b.right*.75,(b.bottom-p.getBoundingClientRect().top)*.75]}})''')
  crops=page.evaluate('''()=>[...document.querySelectorAll('main img')].map(n=>{const r=n.getBoundingClientRect(),p=n.closest('.page');return {page:[...document.querySelectorAll('.page')].indexOf(p),bbox:[r.left*.75,(r.top-p.getBoundingClientRect().top)*.75,r.right*.75,(r.bottom-p.getBoundingClientRect().top)*.75],ratio:n.naturalWidth/n.naturalHeight,src:n.src}})''')
  clips=page.evaluate('''()=>[...document.querySelectorAll('.page *')].filter(n=>n.tagName!=='IMG'&&['hidden','clip'].includes(getComputedStyle(n).overflow)).map(n=>n.className)''')
  browser.close()
 if clips:errors.append({'clipping_styles':clips})
 if len(pdf)!=len(geom):errors.append('PDF/DOM page counts differ')
 for g in geom:
  if g['errors']:errors.append({'page':g['page'],'dom_errors':g['errors']})
 nodechecks=[];paragraphs={}
 for n in nodes:
  box=fitz.Rect(n['bbox']);box.y1-=1
  extracted=pdf[n['page']].get_textbox(box);ok=norm(extracted)==norm(n['text']);nodechecks.append({'page':n['page']+1,'tag':n['tag'],'class':n['cls'],'match':ok,'expected':n['text'],'extracted':extracted})
  if not ok:errors.append({'missing_or_changed_text':n['text'],'extracted':extracted,'page':n['page']+1})
  if n.get('paragraph'):paragraphs[n['paragraph']]=paragraphs.get(n['paragraph'],'')+norm(extracted)
 for s in ed['stories']:
  for i,text in enumerate(s['paragraphs']):
   if paragraphs.get(f"{s['id']}-{i}")!=norm(text):errors.append({'paragraph_loss':f"{s['id']}-{i}"})
 pagechecks=[]
 for pi,p in enumerate(pdf):
  info=p.get_image_info();words=p.get_text('words');text=p.get_text();footer=[w for w in words if w[1]>748];body=[w for w in words if w[1]<748]
  physical=[w for w in words if w[0]<27 or w[2]>586 or w[1]<26 or w[3]>766]
  footer_overlap=[w for w in body if w[3]>744]
  image_overlap=[]
  for im in info:
   for w in words:
    a=fitz.Rect(im['bbox'])&fitz.Rect(w[:4])
    if not a.is_empty and a.get_area()>1:image_overlap.append(w[4])
  expected=[x for x in crops if x['page']==pi];image_errors=[]
  for im in expected:
   matches=[x for x in info if max(abs(a-b) for a,b in zip(x['bbox'],im['bbox']))<1]
   if len(matches)!=1:image_errors.append(im)
   elif abs((im['bbox'][2]-im['bbox'][0])/(im['bbox'][3]-im['bbox'][1])/im['ratio']-1)>.001:image_errors.append(im) # Compare exact DOM aspect ratio before PDF point-grid rounding
  check={'page':pi+1,'letter':tuple(p.rect)==(0,0,612,792),'physical_boundary_errors':physical,'footer_overlap':footer_overlap,'text_image_overlap':image_overlap,'cropped_or_distorted_image_errors':image_errors,'all_images_grayscale':all(i['colorspace']==1 for i in info if i['bpc']>1),'image_count':len(info),'text_characters':len(text),'page_number_present':bool(re.search('PÁGINA\\s+'+str(pi+1),text)),'replacement_glyphs':'\ufffd' in text}
  pagechecks.append(check)
  if physical or footer_overlap or image_overlap or image_errors or not check['letter'] or not check['all_images_grayscale'] or not check['page_number_present'] or check['replacement_glyphs']:errors.append(check)
 fonts={f[0]:{'name':f[3],'type':f[2],'embedded_bytes':len(pdf.extract_font(f[0])[3]),'type3_charprocs':pdf.xref_get_key(f[0],'CharProcs') if f[2]=='Type3' else None} for p in pdf for f in p.get_fonts()}
 for x,f in fonts.items():
  if f['embedded_bytes']:continue
  # Type3 glyph drawing programs are embedded PDF streams, not a FontFile.
  if f['type']=='Type3':
   kind,value=pdf.xref_get_key(x,'CharProcs');obj=pdf.xref_object(int(value.split()[0])) if kind=='xref' else value
   refs=re.findall(r'(\d+) 0 R',obj)
   f['embedded_glyph_bytes']=sum(len(pdf.xref_stream(int(ref)) or b'') for ref in refs)
   if f['embedded_glyph_bytes']:continue
  errors.append({'unembedded_font':x})
 report={'edition_sha256':digest(ed),'status':'PASS' if not errors else 'FAIL','pdf_sha256':hashlib.sha256(pdfpath.read_bytes()).hexdigest(),'page_count':len(pdf),'story_count':len(ed['stories']),'paragraph_count':sum(len(s['paragraphs']) for s in ed['stories']),'images':sum(p['image_count'] for p in pagechecks),'text_nodes_checked':len(nodes),'full_paragraphs_match':len(paragraphs)==sum(len(s['paragraphs']) for s in ed['stories']) and not any(isinstance(e,dict) and 'paragraph_loss' in e for e in errors),'pages':pagechecks,'fonts':fonts,'errors':errors}
 (folder/'independent-qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(folder/'all-text-node-checks.json').write_text(json.dumps(nodechecks,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['fonts','pages']},ensure_ascii=False,indent=2));return bool(errors)
if __name__=='__main__':sys.exit(verify(sys.argv[1]))
