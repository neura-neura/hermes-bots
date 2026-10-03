/* Measured line-boundary newspaper pagination. No clipping, grid rows or Paged.js. */
async function paginate(edition) {
 await document.fonts.ready;
 const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const pages=[]; let main,col,ci=0,pageNo=0,story=null;
 const name=esc(edition.publication),subtitle=esc(edition.subtitle||'');
 const displayDate=new Date(edition.date+'T12:00:00Z').toLocaleDateString('es-MX',{weekday:'long',day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).toUpperCase();
 const cutoffDate=edition.cutoff.slice(0,10).split('-').reverse().join('/'),cutoffTime=edition.cutoff.slice(11,16);
 function node(tag,cls,text){const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n;}
 function newPage(){
  pageNo++;const p=node('section','page');document.body.append(p);pages.push(p);
  const head=node('header','running');
  head.innerHTML=pageNo===1?'<h1>'+name+'</h1><div class="subtitle">'+subtitle+'</div>':'<div class="runningname">'+name+' <span>TECNOLOGÍA · CULTURA · CIENCIA</span></div>';
  head.innerHTML+='<div class="dateline">'+displayDate+' <span>EDICIÓN EN ESPAÑOL · R'+edition.revision+'</span></div>';p.append(head);
  main=node('main','content');p.append(main);const top=head.offsetTop+head.offsetHeight+8;main.style.top=top+'px';main.style.height=(984-top)+'px';
  const footer=node('footer','footer');footer.innerHTML='<span>'+name+' · Cierre: '+cutoffDate+', '+cutoffTime+' · '+esc(edition.timezone)+'</span><span>PÁGINA '+pageNo+'</span>';p.append(footer);
  ci=0;newColumn();
 }
 function newColumn(){col=node('div','column');col.dataset.page=pageNo;col.dataset.col=ci+1;col.style.left=(ci*188)+'px';const lead=Number(main.dataset.leadHeight||0);col.style.top=lead+'px';col.style.height=(main.clientHeight-lead)+'px';main.append(col);}
 function advance(){ci++;if(ci===4)newPage();else newColumn();}
 function used(){return col.lastElementChild?col.lastElementChild.offsetTop+col.lastElementChild.offsetHeight:0;}
 function available(){return col.clientHeight-used()-24;}
 function add(n){col.append(n);return n;}
 function height(n){col.append(n);const h=n.offsetHeight;n.remove();return h;}
 function marker(text){return node('div','continuation',text);}
 function continueStory(){const from=[pageNo,ci+1];const to=ci===3?[pageNo+1,1]:[pageNo,ci+2];add(marker('Continúa en p. '+to[0]+', col. '+to[1]));advance();add(marker(story.short+' · Viene de p. '+from[0]+', col. '+from[1]));}
 newPage();
 for(let si=0;si<edition.stories.length;si++){
  story=edition.stories[si];story.short=story.headline.split(' ').slice(0,4).join(' ');
  let header=node('div','storyhead');header.dataset.story=story.id;
  header.innerHTML='<div class="label">'+esc(story.label)+'</div><h2>'+esc(story.headline)+'</h2>'+(story.deck?'<div class="deck">'+esc(story.deck)+'</div>':'')+'<div class="date">'+esc(story.date)+'</div>';
  if(si===0){header.classList.add('lead');main.prepend(header);header.style.width='740px';const h=header.offsetHeight+8;col.style.top=h+'px';col.style.height=(main.clientHeight-h)+'px';main.dataset.leadHeight=h;}
  else {
   if(si===edition.stories.length-1 && ci===3 && height(header)+120>available()){
    const prior=edition.stories[si-1];const h=main.querySelector('[data-story="'+prior.id+'"]');
    const tail=[...main.querySelectorAll('[data-story="'+prior.id+'"]')].filter(n=>n!==h);
    if(h)h.remove();tail.forEach(n=>n.remove());advance();if(h)add(h);tail.forEach(n=>add(n));
   }
   if(height(header)+54>available())advance();add(header);
  }
  let paras=story.paragraphs.map((text,i)=>({text,id:story.id+'-'+i}));
  // Put the credited picture after the opening paragraph, preserving reading order.
  const blocks=[];paras.forEach((p,i)=>{blocks.push({type:'p',...p});if(i===0&&story.image)blocks.push({type:'image'});});
  while(blocks.length){
   const block=blocks.shift();
   if(block.type==='image'){
    const f=node('figure','photo');f.dataset.story=story.id;
    const im=node('img','');im.src=story.image.path;im.alt=story.image.caption;im.style.width=story.image.display_width+'px';im.style.height=story.image.display_height+'px';f.append(im);
    f.append(node('figcaption','',story.image.caption+' Crédito: '+story.image.credit));
    if(height(f)>available()){
     // On an interior page, avoid starting a picture that would consume
     // most of a new page when it can flow naturally into the next column.
     if(pageNo>1 && ci===3 && blocks[0]?.type==='p' && available()<200 && height(f)>220){
      blocks.splice(1,0,block);continue;
     }
     // For the last story, fit the image into the current column when its text already
     // starts here, rather than creating a nearly empty trailing page.
     if(story.image_position==='before'){
      continueStory();
     }else if(blocks[0]?.type==='p'){
      blocks.splice(1,0,block);continue;
     }else if(si===edition.stories.length-1){
      const existing=main.querySelector('[data-story="'+story.id+'"]');
      if(existing){add(f);continue;}
      continueStory();
     }else{
      continueStory();
     }
    }add(f);continue;
   }
   let words=block.text.split(/\s+/);let offset=0;
   while(offset<words.length){
    let p=node('p','bodytext');p.dataset.paragraph=block.id;p.dataset.story=story.id;p.dataset.offset=offset;
    p.textContent=words.slice(offset).join(' ');const fullHeight=height(p), room=available();
    if(fullHeight<=room){add(p);offset=words.length;}
    else {
     if(room<48){continueStory();continue;}
     let lo=0,hi=words.length-offset;
     while(lo<hi){const mid=Math.ceil((lo+hi)/2);p.textContent=words.slice(offset,offset+mid).join(' ');if(height(p)<=room)lo=mid;else hi=mid-1;}
     // Keep at least two lines in the next fragment; do not strand a last word.
     const remainder=node('p','bodytext');remainder.textContent=words.slice(offset+lo).join(' ');
     while(lo>0&&height(remainder)<30){lo--;remainder.textContent=words.slice(offset+lo).join(' ');}
     if(lo===0)throw new Error('Column too small for paragraph '+block.id);
     p.textContent=words.slice(offset,offset+lo).join(' ');add(p);offset+=lo;continueStory();
    }
   }
  }
  const credit=node('div','sources','Fuentes: '+story.sources.map(x=>'['+x+']').join(' ')+' · Redacción Hermes Pressroom');
  if(height(credit)>available())continueStory();add(credit);
 }
 // Subsequent columns on the front page share the full-width lead exclusion.
 window.paginationDone=true;
 return {pages:pages.length,stories:edition.stories.length};
}
