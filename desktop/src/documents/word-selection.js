/* One native editing host lets WebKit select across paragraphs and pages.
 * Stable paragraph anchors still own every edit committed to the DOCX engine. */
'use strict';
window.installWordSelection = function(editor,paper) {
  const pending=new Set();
  const blockOf=node=>(node?.nodeType===1?node:node?.parentElement)?.closest('[data-anchor][contenteditable="true"]');
  const current=()=>{const selection=getSelection(),block=blockOf(selection?.focusNode)||blockOf(selection?.anchorNode);return block&&paper.contains(block)?block:null;};
  const wire=editor.wireBlock.bind(editor);
  function focusable(block){if(block.matches('[data-anchor][contenteditable="true"]'))block.tabIndex=-1;}
  editor.wireBlock=block=>{wire(block);focusable(block);};
  paper.querySelectorAll('[data-anchor][contenteditable="true"]').forEach(focusable);
  paper.contentEditable='true';paper.setAttribute('role','textbox');paper.setAttribute('aria-multiline','true');
  // The upstream cross-host mouse bridge is unnecessary with a shared host.
  editor.clearDragSelection?.();
  for(const [type,listener] of [['mousedown',editor.onMouseDown],['mousemove',editor.onMouseMove],['mouseup',editor.onMouseUp]])if(listener)document.removeEventListener(type,listener,true);
  // Keep the mouse anchor fixed. WebKit can return the editing host's start
  // when dragging into a page margin; normalizing that range selected the
  // preceding pages. Resolve the endpoint against the nearby text instead.
  const viewport=paper.closest('#workarea')||paper;
  const rootOf=block=>editor.ownerRoot?.(block)||paper;
  let drag=null,scrollFrame=0;
  const caretAt=(x,y)=>{
    const point=document.caretPositionFromPoint?.(x,y);
    if(point)return {node:point.offsetNode,offset:point.offset};
    const range=document.caretRangeFromPoint?.(x,y);
    return range?{node:range.startContainer,offset:range.startOffset}:null;
  };
  function pointIn(block,x,y){
    const box=block.getBoundingClientRect();
    const cx=Math.max(box.left+1,Math.min(x,box.right-1));
    const cy=Math.max(box.top+1,Math.min(y,box.bottom-1));
    let point=caretAt(cx,cy);
    if(point&&block.contains(point.node))return point;
    // Line spacing and wrappers are not text. Hit the nearest rendered line.
    const range=document.createRange();range.selectNodeContents(block);
    const lines=[...range.getClientRects()].filter(r=>r.width>0&&r.height>0);
    const distance=r=>Math.max(r.top-y,0,y-r.bottom)*10000+Math.max(r.left-x,0,x-r.right);
    lines.sort((a,b)=>distance(a)-distance(b));
    for(const line of lines){
      point=caretAt(Math.max(line.left+1,Math.min(x,line.right-1)),Math.max(line.top+1,Math.min(y,line.bottom-1)));
      if(point&&block.contains(point.node))return point;
    }
    return {node:block,offset:y<box.top||x<box.left?0:block.childNodes.length};
  }
  function endpoint(x,y){
    const bounds=viewport.getBoundingClientRect();
    y=Math.max(bounds.top+1,Math.min(y,bounds.bottom-1));
    let nearest=null,distance=Infinity;
    for(const block of drag.blocks){
      if(!block.isConnected)continue;
      const r=block.getBoundingClientRect();if(!r.width||!r.height)continue;
      const d=Math.max(r.top-y,0,y-r.bottom)*10000+Math.max(r.left-x,0,x-r.right);
      if(d<distance){nearest=block;distance=d;}
    }
    return nearest?pointIn(nearest,x,y):null;
  }
  function applyDrag(){
    if(!drag?.anchor.node.isConnected)return;
    const point=endpoint(drag.x,drag.y);if(!point)return;
    getSelection()?.setBaseAndExtent(drag.anchor.node,drag.anchor.offset,point.node,point.offset);
    const block=blockOf(point.node);if(block)editor.activeBlock=block;
  }
  function stopDrag(){drag=null;if(scrollFrame)cancelAnimationFrame(scrollFrame);scrollFrame=0;}
  function autoScroll(){
    scrollFrame=0;if(!drag)return;
    const bounds=viewport.getBoundingClientRect();
    const delta=drag.y<bounds.top?-Math.min(16,(bounds.top-drag.y)/2):drag.y>bounds.bottom?Math.min(16,(drag.y-bounds.bottom)/2):0;
    if(delta){const top=viewport.scrollTop;viewport.scrollTop+=delta;applyDrag();if(viewport.scrollTop!==top)scrollFrame=requestAnimationFrame(autoScroll);}
  }
  function mouseDown(event){
    if(paper.inert||event.button!==0||event.ctrlKey||event.detail>1)return;
    const block=blockOf(event.target);
    if(!block||!paper.contains(block)||event.target.closest?.('[contenteditable="false"]')){event.preventDefault();return;}
    stopDrag();event.preventDefault();
    const selection=getSelection(),old=blockOf(selection?.anchorNode);
    const extended=event.shiftKey&&old&&rootOf(old)===rootOf(block)?{node:selection.anchorNode,offset:selection.anchorOffset}:null;
    block.focus({preventScroll:true});
    // Focusing may commit and replace another paragraph, so hit test afterwards.
    if(!block.isConnected)return;
    const point=pointIn(block,event.clientX,event.clientY),root=rootOf(block);
    drag={anchor:extended?.node.isConnected?extended:point,root,blocks:[...root.querySelectorAll('[data-anchor][contenteditable="true"]')].filter(b=>paper.contains(b)&&rootOf(b)===root),x:event.clientX,y:event.clientY};
    selection.setBaseAndExtent(drag.anchor.node,drag.anchor.offset,point.node,point.offset);editor.activeBlock=block;
  }
  function mouseMove(event){
    if(!drag)return;if(!(event.buttons&1)){stopDrag();return;}
    event.preventDefault();drag.x=event.clientX;drag.y=event.clientY;applyDrag();
    if(!scrollFrame)scrollFrame=requestAnimationFrame(autoScroll);
  }
  function mouseUp(event){if(!drag)return;event.preventDefault();drag.x=event.clientX;drag.y=event.clientY;applyDrag();stopDrag();}
  paper.addEventListener('mousedown',mouseDown,true);
  document.addEventListener('mousemove',mouseMove,true);document.addEventListener('mouseup',mouseUp,true);
  window.addEventListener('blur',stopDrag);
  function activate(){
    const selection=getSelection();
    if(!drag&&selection?.rangeCount&&!selection.isCollapsed&&paper.contains(selection.anchorNode)&&paper.contains(selection.focusNode)){
      const range=selection.getRangeAt(0),first=blockOf(range.startContainer),last=blockOf(range.endContainer);
      if(!first||!last){
        const blocks=[...paper.querySelectorAll('[data-anchor][contenteditable="true"]')].filter(block=>range.intersectsNode(block));
        if(blocks.length){
          const backwards=selection.anchorNode!==range.startContainer||selection.anchorOffset!==range.startOffset;
          const start=first?[range.startContainer,range.startOffset]:[blocks[0],0],end=last?[range.endContainer,range.endOffset]:[blocks.at(-1),blocks.at(-1).childNodes.length];
          selection.setBaseAndExtent(...(backwards?end:start),...(backwards?start:end));
        }
      }
    }
    const block=current();if(block)editor.activeBlock=block;return block;
  }
  function beforeInput(event){
    const block=activate();
    // Page margins, numbering and read-only revision decorations never become
    // editable just because their common parent is the native editing host.
    if(!block){event.preventDefault();return;}
    pending.add(block);
  }
  function input(){const block=activate();if(block)pending.add(block);}
  function keydown(event){
    // In a shared host the event may target the surface rather than a paragraph.
    // The app's capture handler has already handled its own commands.
    if(!event.defaultPrevented&&!blockOf(event.target)){const block=activate();if(block)editor.onKeydown(block,event);}
  }
  document.addEventListener('selectionchange',activate);
  paper.addEventListener('keydown',activate,true);paper.addEventListener('copy',activate,true);paper.addEventListener('cut',activate,true);
  paper.addEventListener('beforeinput',beforeInput,true);paper.addEventListener('input',input,true);paper.addEventListener('keydown',keydown);
  return {flushPending(){for(const block of pending){if(block.isConnected){const id=editor.anchorIdOf(block);if(id)editor.syncBlock(block,id);}pending.delete(block);}},dispose(){stopDrag();paper.removeEventListener('mousedown',mouseDown,true);document.removeEventListener('mousemove',mouseMove,true);document.removeEventListener('mouseup',mouseUp,true);window.removeEventListener('blur',stopDrag);document.removeEventListener('selectionchange',activate);paper.removeEventListener('keydown',activate,true);paper.removeEventListener('copy',activate,true);paper.removeEventListener('cut',activate,true);paper.removeEventListener('beforeinput',beforeInput,true);paper.removeEventListener('input',input,true);paper.removeEventListener('keydown',keydown);}};
};
