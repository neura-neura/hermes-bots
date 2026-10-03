from pathlib import Path
import json,requests,datetime,hashlib,io
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from PIL import Image,ImageOps,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1];RUN=ROOT/'newspapers/the-k-times/2026/09/21/r2';(RUN/'images').mkdir(exist_ok=True)
idx=json.loads((RUN/'research/index.json').read_text()); out=[]
specs=[('sanrio',16,0,'Sanrio Games booth concept, issued August 20; not a photograph of the canceled day.','© 2026 Sanrio Co., Ltd. / official press image'),('kitty',16,1,'Hello Kitty Party Land promotional artwork.','© 2026 Sanrio Co., Ltd.'),('ai',13,0,'Anthropic’s own automation index; a company measurement, not an independent audit.','Anthropic / September 17 research report'),('maps',1,0,'GNOME Maps 51 shows a downloaded region for offline use.','GNOME Project / release screenshot'),('papers',1,4,'Papers adds a visual signature; this is not a cryptographic guarantee.','GNOME Project / release screenshot'),('bobby',1,5,'Bobby displays tables from a SQLite database.','GNOME Project / release screenshot'),('townfall',29,0,'Publisher screenshot from Silent Hill: Townfall.','Konami / PlayStation.Blog'),('control',30,0,'Publisher screenshot from Control Resonant.','Remedy Entertainment / PlayStation.Blog'),('book',31,1,'Cover of Yoichi Ochiai’s novel; publisher says the cover was made with AI.','Cross Media Publishing / publisher publicity'),('moon',32,4,'McGetchin crater, imaged on December 5, 2025.','NASA Goddard / Intuitive Machines'),('moonpair',32,2,'Impact ejecta: 2025 view at left, 2011 comparison at right.','NASA Goddard / Intuitive Machines / Robert Wagner'),('galaxy',33,1,'Hubble’s image of the spiral galaxy NGC 4698.','ESA/Hubble & NASA, D. Thilker, MAUVE-HST Team'),('enterprise',34,1,'Enterprise rollout, September 17, 1976, with Star Trek guests.','NASA / historical photograph'),('progress',35,0,'Progress 96 approaching the station on September 19.','NASA+'),('rayearth',48,4,'Key visual for the new Magic Knight Rayearth anime.','© CLAMP・ST / Kodansha・TMS・TV Asahi; via P-3 / ANN'),('villainess',50,0,'Promotional visual for the January 2027 second part.','© Satsuki Nakamura / Ichijinsha / production committee; Toho / ANN'),('webtoon',49,0,'World Webtoon Awards promotional image.','© MCST / KOCCA; via Anime News Network')]
for name,i,n,caption,credit in specs:
 x=next(x for x in idx if x['id']==i);s=BeautifulSoup((RUN/'research'/f'{i:02}.html').read_text(),'html.parser');a=s.select_one('div.meat') or s.select_one('article') or s.select_one('main') or s
 imgs=a.select('img')
 if i==1:imgs=[im for im in imgs if '.webp' in im.get('src','')]
 im=imgs[n];u=urljoin(x['url'],im.get('data-src') or im.get('src'))
 try:
  r=requests.get(u,timeout=40);r.raise_for_status();image=Image.open(io.BytesIO(r.content)); orig=RUN/'images'/(name+'-original'+('.png' if image.format=='PNG' else '.jpg'));orig.write_bytes(r.content)
  image=ImageOps.exif_transpose(image).convert('RGB'); bg=Image.new('RGB',image.size,'white');bg.paste(image);gray=ImageOps.grayscale(bg);gray.save(RUN/'images'/f'{name}.jpg',quality=94)
  rights='NASA media guideline; third-party credit retained' if i in [32,33,34,35] else 'Copyright retained. Limited image reproduced for reporting/commentary in private edition; no blanket redistribution license asserted.'
  out.append(dict(id=name,source_url=u,originating_article=x['url'],publisher=x['title'],photographer=None,agency=credit,caption=caption,credit=credit,rights=rights,download_time=datetime.datetime.now(datetime.timezone.utc).isoformat(),local_filename=f'images/{name}.jpg',original_filename=str(orig.relative_to(RUN)),width=image.width,height=image.height,sha256=hashlib.sha256((RUN/'images'/f'{name}.jpg').read_bytes()).hexdigest()))
  print(name,image.size)
 except Exception as e: print('FAILED',name,u,str(e))
# Music publicity fetched with browser because source uses client rendering.
x=next(x for x in idx if x['id']==20)
for name,n,cap in [('natalia',0,'Natalia single cover, supplied in VAP’s September 21 announcement.'),('momoko',2,'Momoko Kikuchi in a VAP publicity photograph; photographer not identified.')]:
 u=x['browser_images'][n]['src'];r=requests.get(u,timeout=40);r.raise_for_status();im=Image.open(io.BytesIO(r.content));(RUN/'images'/f'{name}-original.jpg').write_bytes(r.content);ImageOps.grayscale(im.convert('RGB')).save(RUN/'images'/f'{name}.jpg',quality=94)
 out.append(dict(id=name,source_url=u,originating_article=x['url'],publisher='VAP via PR TIMES',photographer=None,agency='VAP',caption=cap,credit='VAP / PR TIMES publicity',rights='Copyright retained; limited personal editorial reproduction, no redistribution license asserted.',download_time=datetime.datetime.now(datetime.timezone.utc).isoformat(),local_filename=f'images/{name}.jpg',width=im.width,height=im.height))
(RUN/'image_sources.json').write_text(json.dumps(out,indent=2,ensure_ascii=False))
# Open-font license is archived alongside the masthead face.
for file,url in [('UnifrakturCook.ttf','https://raw.githubusercontent.com/google/fonts/main/ofl/unifrakturcook/UnifrakturCook-Bold.ttf'),('UnifrakturCook-OFL.txt','https://raw.githubusercontent.com/google/fonts/main/ofl/unifrakturcook/OFL.txt')]:
 r=requests.get(url,timeout=30);r.raise_for_status();(ROOT/'fonts'/file).write_bytes(r.content)
print('Assets:',len(out))
