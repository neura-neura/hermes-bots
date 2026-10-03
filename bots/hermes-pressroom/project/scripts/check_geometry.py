"""Independent browser text-range and image printable-boundary regression test."""
import json,sys
from pathlib import Path
from playwright.sync_api import sync_playwright
JS=r'''() => [...document.querySelectorAll('.page')].map((page,i)=>{
 const pr=page.getBoundingClientRect(), foot=page.querySelector('footer').getBoundingClientRect();
 const main=page.querySelector('main'), mr=main.getBoundingClientRect();
 const bounds={left:mr.left,right:mr.right,top:mr.top,bottom:Math.min(foot.top-8,pr.bottom-34)};
 let rects=[],errors=[]; const walker=document.createTreeWalker(main,NodeFilter.SHOW_TEXT);let n;
 while(n=walker.nextNode()){if(!n.textContent.trim())continue;let r=document.createRange();r.selectNodeContents(n);for(const b of r.getClientRects())rects.push({kind:'text',text:n.textContent.slice(0,90),left:b.left,right:b.right,top:b.top,bottom:b.bottom});}
 for(const im of main.querySelectorAll('img')){let b=im.getBoundingClientRect();rects.push({kind:'image',src:im.src,left:b.left,right:b.right,top:b.top,bottom:b.bottom});if(!im.complete||!im.naturalWidth)errors.push({kind:'missing_image',src:im.src});}
 for(const r of rects)if(r.left<bounds.left-.5||r.right>bounds.right+.5||r.top<bounds.top-.5||r.bottom>bounds.bottom+.5)errors.push(r);
 return {page:i+1,bounds,range_and_image_count:rects.length,errors,rects};})'''
def audit(html,out):
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');p=b.new_page();p.goto(Path(html).resolve().as_uri());p.evaluate('document.fonts.ready');p.wait_for_function('Array.from(document.images).every(i=>i.complete)');result=p.evaluate(JS);b.close()
 Path(out).write_text(json.dumps(result,ensure_ascii=False,indent=2));count=sum(len(p['errors']) for p in result);print(json.dumps({'pages':len(result),'violations':count,'report':out}));return count
if __name__=='__main__':sys.exit(bool(audit(*sys.argv[1:3])))
