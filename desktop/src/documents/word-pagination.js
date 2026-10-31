/* Keep each OOXML paragraph as one editing host even when its visual lines cross
 * pages. Continuation content is moved back into that host; empty presentation
 * slots reserve its space on later pages and never enter the saved document. */
'use strict';
window.installWordPagination = function(Editor, PaginationEngine) {
  if(typeof PaginationEngine?.prototype.measureBlocks!=='function')throw new Error('pagination_measure_hook_missing');
  const measure=PaginationEngine.prototype.measureBlocks;
  PaginationEngine.prototype.measureBlocks=function(section,...args){
    prepareWordTypography(section);
    return measure.call(this,section,...args);
  };
  const wire=Editor.prototype.wireBlocks;
  Editor.prototype.wireBlocks=function(root){
    for(const page of root.querySelectorAll(".page-box")){page.style.contain="none";page.style.willChange="auto";}
    const groups=new Map();
    for(const p of root.querySelectorAll('.page-content p[data-source-anchor-id]')){
      if(p.closest('table'))continue;
      const id=p.dataset.sourceAnchorId;
      if(!groups.has(id))groups.set(id,[]);groups.get(id).push(p);
    }
    for(const parts of groups.values()){
      if(parts.length<2||!parts[0].hasAttribute('data-anchor'))continue;
      const first=parts[0],boxes=parts.map(p=>p.getBoundingClientRect());
      const scale=boxes[0].width/first.offsetWidth||1;
      const expected=parts.map(p=>p.textContent).join('');
      const content=document.createDocumentFragment();
      parts.forEach((p,i)=>{
        const span=document.createElement('span');span.className='vl-page-fragment';
        span.style.display='block';span.style.textIndent=i?'0':getComputedStyle(first).textIndent;
        if(i<parts.length-1&&getComputedStyle(first).textAlign==='justify')span.style.textAlignLast='justify';
        if(i)span.style.paddingTop=`${Math.max(0,(boxes[i].top-boxes[i-1].bottom)/scale)}px`;
        while(p.firstChild)span.append(p.firstChild);
        content.append(span);
        if(i){const slot=document.createElement('div');slot.className='vl-page-slot';slot.contentEditable='false';slot.setAttribute('aria-hidden','true');slot.style.height=`${boxes[i].height/scale}px`;slot.style.setProperty('--vl-grid-trailing-space',String(wordGridTrailingSpacePx(p)));slot.style.marginTop=getComputedStyle(p).marginTop;slot.style.marginBottom=getComputedStyle(p).marginBottom;p.replaceWith(slot);}
      });
      first.append(content);first.classList.add('vl-spanning-paragraph');
      first.style.position='relative';first.style.zIndex='2';
      const page=first.closest('.page-box'),body=first.closest('.page-content');
      if(page){page.classList.add('vl-spanning-page');page.style.overflow='visible';page.style.contain='none';page.style.willChange='auto';}
      if(body)body.style.overflow='visible';
      if(first.textContent!==expected)throw new Error('pagination_content_mismatch');
    }
    const result=wire.call(this,root);
    this.vlLayoutVersion=this.exports.DocxSessionBridge.GetVersion(this.handle);
    return result;
  };
};
