/* Editing guides are painted outside the editable document. They never enter
 * text offsets, clipboard HTML, history snapshots or the exported DOCX. */
'use strict';
window.installWordGuides=function({paper,scrollport,surface,button}){
  const ns='http://www.w3.org/2000/svg';
  const layer=document.createElementNS(ns,'svg');
  layer.id='word-editing-guides';layer.setAttribute('aria-hidden','true');
  surface.append(layer);
  let enabled=true,frame=0;
  const ignored='del,[contenteditable="false"],[data-list-marker],.page-number,[class$="para-mark-del"],a.footnote-ref,a.endnote-ref,a[class$="-backref"]';
  const visible=r=>r.width>=0&&r.height>0;
  function paint(){
    frame=0;
    const origin=surface.getBoundingClientRect(),view=scrollport.getBoundingClientRect();
    const fragment=document.createDocumentFragment();
    const onScreen=r=>r.bottom>=view.top&&r.top<=view.bottom&&r.right>=view.left&&r.left<=view.right;
    const rects=range=>[...range.getClientRects()].filter(visible);
    function mark(value,r,center=false){
      if(!r||!onScreen(r))return;
      const text=document.createElementNS(ns,'text'),size=Math.max(8,Math.min(24,r.height*.6));
      text.textContent=value;text.setAttribute('font-size',String(size));
      text.setAttribute('x',String((center?(r.left+r.right)/2:r.right+2)-origin.left));
      text.setAttribute('y',String(r.bottom-origin.top-size*.16));
      if(center)text.setAttribute('text-anchor','middle');
      fragment.append(text);
    }
    for(const content of paper.querySelectorAll('.page-box > .page-content')){
      const r=content.getBoundingClientRect();if(!onScreen(r))continue;
      const path=document.createElementNS(ns,'path'),x=r.left-origin.left,y=r.top-origin.top,right=r.right-origin.left,bottom=r.bottom-origin.top,len=14;
      path.setAttribute('d',`M${x-len} ${y}H${x}V${y-len} M${right+len} ${y}H${right}V${y-len} M${x-len} ${bottom}H${x}V${bottom+len} M${right+len} ${bottom}H${right}V${bottom+len}`);
      fragment.append(path);
    }
    if(enabled)for(const block of paper.querySelectorAll('[data-anchor][contenteditable="true"]')){
      if(!onScreen(block.getBoundingClientRect())||block.closest(ignored))continue;
      let last=null;
      const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT|NodeFilter.SHOW_ELEMENT);
      let node;
      while((node=walker.nextNode())){
        const element=node.nodeType===Node.ELEMENT_NODE?node:node.parentElement;
        if(element.closest(ignored))continue;
        if(node.nodeType===Node.ELEMENT_NODE){
          if(node.matches('[data-docx-tab]')){
            const r=node.getBoundingClientRect();mark('→',r,true);if(visible(r))last=r;
          }else if(node.tagName==='BR'){
            const range=document.createRange();range.selectNode(node);
            mark('↲',last||rects(range)[0]);last=null;
          }
          continue;
        }
        if(!node.length||element.closest('[data-docx-tab]'))continue;
        const range=document.createRange();
        // Query only whitespace positions plus the last visible character;
        // ordinary characters do not need an overlay or a geometry read.
        if(block.dataset.vlEmptyPlaceholder!=='true')for(const match of node.data.matchAll(/[ \u00a0\u3000\t\n]/g)){
          range.setStart(node,match.index);range.setEnd(node,match.index+1);
          mark(match[0]==='\t'?'→':match[0]==='\n'?'↲':'·',rects(range)[0],true);
        }
        const end=node.data.search(/[\u200e\u200f]*$/);
        if(end>0){
          let start=end-1;if(start>0&&/[\uDC00-\uDFFF]/.test(node.data[start]))start--;
          range.setStart(node,start);range.setEnd(node,end);
          const r=rects(range).at(-1);if(r)last=r;
        }
      }
      // Empty paragraphs still get a mark at their insertion position.
      if(block.dataset.vlEmptyPlaceholder==='true'){
        if(last)last={...last,left:last.left,right:last.left,top:last.top,bottom:last.bottom,height:last.height,width:0};
        else{const r=block.getBoundingClientRect();last={left:r.left,right:r.left,top:r.top,bottom:r.bottom,height:r.height,width:0};}
      }
      mark('↵',last);
    }
    layer.replaceChildren(fragment);
  }
  function schedule(){if(!frame)frame=requestAnimationFrame(paint);}
  function toggle(){enabled=!enabled;button.setAttribute('aria-pressed',String(enabled));schedule();}
  button.addEventListener('click',toggle);
  scrollport.addEventListener('scroll',schedule,{passive:true});
  const resize=new ResizeObserver(schedule);resize.observe(scrollport);resize.observe(surface);
  const mutations=new MutationObserver(schedule);mutations.observe(paper,{childList:true,characterData:true,subtree:true,attributes:true,attributeFilter:['class','style']});
  document.fonts?.addEventListener('loadingdone',schedule);
  schedule();
  return{refresh:paint,dispose(){cancelAnimationFrame(frame);resize.disconnect();mutations.disconnect();button.removeEventListener('click',toggle);scrollport.removeEventListener('scroll',schedule);document.fonts?.removeEventListener('loadingdone',schedule);layer.remove();}};
};
