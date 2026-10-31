/* Word's punctuation compression adjusts blank glyph space, never document text.
 * The browser still performs Unicode line breaking, shaping and justification. */
'use strict';
const wordTypographyGraphemes=new Intl.Segmenter(undefined,{granularity:'grapheme'});
function prepareWordAutoSpacing(p) {
  if(p.dataset.vlAutospacePrepared==='true')return;
  p.dataset.vlAutospacePrepared='true';
  const css=getComputedStyle(p),mode=css.getPropertyValue('text-autospace');
  const enabled=(property,keyword)=>{const value=css.getPropertyValue(property).trim();return value?value==='1':mode==='normal'||mode.includes(keyword);};
  const latin=enabled('--docx-auto-space-de','ideograph-alpha'),digits=enabled('--docx-auto-space-dn','ideograph-numeric');
  // Word uses a quarter em. Browser defaults use an eighth em and differ at
  // run boundaries. Generated collapsible spaces retain native line-edge
  // trimming without adding characters to selections or the saved document.
  p.style.setProperty('text-autospace','no-autospace');
  if(!latin&&!digits)return;
  const ideograph=/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;
  const western=c=>(latin&&/\p{Script=Latin}/u.test(c))||(digits&&/[0-9]/u.test(c));
  const insertions=new Map(),sizes=new WeakMap();let previous=null;
  const walker=document.createTreeWalker(p,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT,{acceptNode(node){
    if(node.nodeType===Node.ELEMENT_NODE&&(node.getAttribute('contenteditable')==='false'||getComputedStyle(node).display==='none'))return NodeFilter.FILTER_REJECT;
    return NodeFilter.FILTER_ACCEPT;
  }});
  while(walker.nextNode()){
    const node=walker.currentNode;
    if(node.nodeType!==Node.TEXT_NODE){if(/^(BR|IMG|SVG|MATH)$/.test(node.tagName))previous=null;continue;}
    for(const {segment:char,index:offset} of wordTypographyGraphemes.segment(node.data)){
      if(/^\p{Mark}+$/u.test(char))continue;
      const current={char,node};
      if(previous&&((ideograph.test(previous.char)&&western(char))||(western(previous.char)&&ideograph.test(char)))){
        const east=ideograph.test(char)?node.parentElement:previous.node.parentElement;
        if(!sizes.has(east))sizes.set(east,parseFloat(getComputedStyle(east).fontSize)/4);
        if(!insertions.has(node))insertions.set(node,[]);
        insertions.get(node).push({offset,width:sizes.get(east)});
      }
      previous=current;
    }
  }
  for(const [node,gaps] of insertions){
    const fragment=document.createDocumentFragment();let start=0;
    for(const {offset,width} of gaps){
      if(offset>start)fragment.append(document.createTextNode(node.data.slice(start,offset)));
      const gap=document.createElement('span');gap.className='vl-word-autospace';gap.setAttribute('aria-hidden','true');gap.style.setProperty('--vl-auto-gap',`${width}px`);fragment.append(gap);start=offset;
    }
    if(start<node.length)fragment.append(document.createTextNode(node.data.slice(start)));node.replaceWith(fragment);
  }
}
window.prepareWordTypography = function(root) {
  const closing=/[，。、；：！？）］｝〉》」』】〕〗〙〛”’]/u;
  const opening=/[（［｛〈《「『【〔〖〘〚“‘]/u;
  const canvas=document.createElement('canvas');canvas.width=512;canvas.height=256;
  const metrics=canvas.getContext('2d',{willReadFrequently:true}),bearings=new Map();
  const blankSpace=(mark,css,size)=>{
    const key=`${css.fontStyle}|${css.fontWeight}|${css.fontFamily}|${mark.span.textContent}`;
    if(!bearings.has(key)){
      metrics.font=`${css.fontStyle} ${css.fontWeight} 128px ${css.fontFamily}`;
      const glyph=metrics.measureText(mark.span.textContent);let left=-glyph.actualBoundingBoxLeft,right=glyph.actualBoundingBoxRight;
      // Some WebKit builds report the advance box instead of ink bounds.
      // Measure the rendered glyph in that case, so tightening never overlaps ink.
      if(left===0&&Math.abs(right-glyph.width)<.01){
        metrics.clearRect(0,0,512,256);metrics.fillText(mark.span.textContent,128,160);
        const pixels=metrics.getImageData(0,0,512,256).data;let first=512,last=-1;
        for(let y=0;y<256;y++)for(let x=0;x<512;x++)if(pixels[(y*512+x)*4+3]>32){first=Math.min(first,x);last=Math.max(last,x);}
        left=first-128;right=last+1-128;
      }
      bearings.set(key,{leading:Math.max(0,left/128),trailing:Math.max(0,(glyph.width-right)/128)});
    }
    return size*bearings.get(key)[mark.opening?'leading':'trailing'];
  };
  for(const p of root.matches?.('p')?[root]:root.querySelectorAll('p')){
    // WebKit's editable default is after-white-space, which can leave a
    // Chinese full stop alone on the next line when an emergency wrap occurs.
    p.style.setProperty('line-break','normal');
    prepareWordAutoSpacing(p);
    if(p.dataset.vlPunctuationPrepared==='true'||getComputedStyle(p).getPropertyValue('--docx-compress-punctuation').trim()!=='1')continue;
    p.dataset.vlPunctuationPrepared='true';
    const original=p.textContent,walker=document.createTreeWalker(p,NodeFilter.SHOW_TEXT),nodes=[];
    while(walker.nextNode())if(!walker.currentNode.parentElement.closest('[contenteditable="false"]'))nodes.push(walker.currentNode);
    const marks=[];
    for(const node of nodes){
      if(![...node.data].some(c=>closing.test(c)||opening.test(c)))continue;
      const fragment=document.createDocumentFragment();let text='';
      for(const char of node.data){
        if(!closing.test(char)&&!opening.test(char)){text+=char;continue;}
        if(text){fragment.append(document.createTextNode(text));text='';}
        const span=document.createElement('span');span.className='vl-cjk-punctuation';span.textContent=char;fragment.append(span);
        marks.push({span,opening:opening.test(char)});
      }
      if(text)fragment.append(document.createTextNode(text));node.replaceWith(fragment);
    }
    const align=p.style.getPropertyValue('text-align'),priority=p.style.getPropertyPriority('text-align');
    const last=p.style.getPropertyValue('text-align-last'),lastPriority=p.style.getPropertyPriority('text-align-last');
    p.style.setProperty('text-align','left','important');p.style.setProperty('text-align-last','left','important');
    const usable=[];
    for(const mark of marks){
      const css=getComputedStyle(mark.span),size=parseFloat(css.fontSize),width=mark.span.getBoundingClientRect().width;
      if(!Number.isFinite(size)||width<size*.75)continue;
      mark.spacing=parseFloat(css.letterSpacing)||0;mark.size=size;mark.advance=width;
      const blank=blankSpace(mark,css,size)+(mark.opening?0:mark.spacing);
      mark.max=Math.max(0,Math.min(width/2,size/2,blank));
      mark.base=0;
      mark.apply=additional=>{const value=mark.base+additional;if(mark.opening)mark.span.style.marginLeft=`${-value}px`;else mark.span.style.letterSpacing=`${mark.spacing-value}px`;};
      usable.push(mark);
    }
    const byElement=new Map(usable.map(mark=>[mark.span,mark]));
    const gaps=new Map([...p.querySelectorAll('.vl-word-autospace')].map(span=>{
      const width=parseFloat(span.style.getPropertyValue('--vl-auto-gap'));
      return [span,{max:width/2,autospace:true,apply:value=>span.style.setProperty('--vl-auto-gap',`${width-value}px`)}];
    }));
    const glyphs=[],textWalker=document.createTreeWalker(p,NodeFilter.SHOW_TEXT);
    while(textWalker.nextNode()){
      const node=textWalker.currentNode;
      if(node.parentElement.closest('[contenteditable="false"]'))continue;
      let offset=0;
      for(const char of node.data){
        const range=document.createRange();range.setStart(node,offset);offset+=char.length;range.setEnd(node,offset);
        if(range.getBoundingClientRect().height>0)glyphs.push({char,range,mark:byElement.get(node.parentElement),gap:offset===char.length?gaps.get(node.previousSibling):null});
      }
    }
    // Consecutive closing punctuation shares one half-em of blank space.
    // This pair kerning precedes line fitting and is disabled by Word's setting.
    if(getComputedStyle(p).getPropertyValue('--docx-punctuation-kerning').trim()!=='0'){
      for(let i=0;i+1<glyphs.length;i++){
        const mark=glyphs[i].mark,next=glyphs[i+1].mark;
        if(mark&&next&&!mark.opening&&!next.opening){
          const between=document.createRange();between.setStartAfter(mark.span);between.setEndBefore(next.span);
          if(between.cloneContents().querySelector('br,img,svg,math,[contenteditable="false"]'))continue;
          mark.base=mark.max;mark.max=0;mark.apply(0);
        }
      }
    }
    const sameLine=(a,b)=>Math.abs((a.top+a.bottom-b.top-b.bottom)/2)<Math.min(a.height,b.height)*.45;
    // Tighten only the next legal break group. Maximizing the number of glyphs
    // on every line over-compresses punctuation-heavy prose and changes pages.
    for(let start=0;start<glyphs.length;){
      const line=glyphs[start].range.getBoundingClientRect();let end=start+1;
      while(end<glyphs.length&&sameLine(line,glyphs[end].range.getBoundingClientRect()))end++;
      if(end===glyphs.length)break;
      let next=end+1;
      if(/[A-Za-z0-9]/u.test(glyphs[end].char))while(next<glyphs.length&&/[A-Za-z0-9]/u.test(glyphs[next].char))next++;
      while(next<glyphs.length&&(closing.test(glyphs[next].char)||/[,.!?;:]/u.test(glyphs[next].char)||opening.test(glyphs[next-1].char)))next++;
      const available=[...new Set(glyphs.slice(start,next).flatMap(g=>[g.mark,g.gap]).filter(Boolean))];
      const punctuation=available.filter(m=>!m.autospace);
      const candidates=punctuation.length?punctuation:available;
      const lastMark=glyphs[next-1].mark;
      const hanging=lastMark&&!lastMark.opening&&closing.test(lastMark.span.textContent)
        &&/^zh(?:-|$)/i.test(lastMark.span.closest('[lang]')?.getAttribute('lang')||document.documentElement.lang)
        &&getComputedStyle(p).getPropertyValue('--docx-overflow-punctuation').trim()!=='0'?lastMark:null;
      const ordinary=candidates.filter(m=>m!==hanging),total=ordinary.reduce((sum,m)=>sum+m.max,0);
      const budget=Math.min(total,punctuation.length?Math.max(...punctuation.map(m=>m.size/2)):total);
      const apply=ratio=>{ordinary.forEach(m=>m.apply(m.max*ratio*(total?budget/total:0)));if(hanging)hanging.apply(hanging.advance*ratio);};
      if(candidates.length){
        apply(1);
        if(sameLine(line,glyphs[next-1].range.getBoundingClientRect())){
          let low=0,high=1;
          for(let i=0;i<9;i++){const mid=(low+high)/2;apply(mid);if(sameLine(line,glyphs[next-1].range.getBoundingClientRect()))high=mid;else low=mid;}
          apply(high);end=next;
        }else apply(0);
      }
      start=end;
    }
    if(align)p.style.setProperty('text-align',align,priority);else p.style.removeProperty('text-align');
    if(last)p.style.setProperty('text-align-last',last,lastPriority);else p.style.removeProperty('text-align-last');
    if(p.textContent!==original)throw new Error('typography_content_mismatch');
  }
};
window.wordGridTrailingSpacePx = function(element) {
  if(!element)return 0;
  const css=getComputedStyle(element),slot=parseFloat(css.getPropertyValue('--vl-grid-trailing-space'));
  if(Number.isFinite(slot))return slot;
  if(css.getPropertyValue('--docx-line-grid').trim()!=='1')return 0;
  if(element.querySelector('img,svg,math,table,canvas,video'))return 0;
  const line=parseFloat(css.lineHeight),font=Math.max(parseFloat(css.fontSize),parseFloat(css.getPropertyValue('--docx-grid-font-px'))||0);
  return Number.isFinite(line)&&Number.isFinite(font)?Math.max(0,(line-font)/2):0;
};
