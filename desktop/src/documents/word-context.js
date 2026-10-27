'use strict';
window.installWordContext = function(editor,paper,options) {
  const menu=document.getElementById('word-context-menu');
  const sheet=new CSSStyleSheet();document.adoptedStyleSheets=[...document.adoptedStyleSheets,sheet];
  let saved=null,logical=null,position={x:0,y:0},context={table:false};
  const blockOf=node=>(node?.nodeType===1?node:node?.parentElement)?.closest('[data-anchor][contenteditable="true"]');
  function capture(){logical=options.captureSelection?.();const s=getSelection();if(s?.rangeCount&&paper.contains(s.anchorNode))saved={anchor:s.anchorNode,offset:s.anchorOffset,focus:s.focusNode,end:s.focusOffset};}
  function restore(){if(logical&&options.restoreSelection)return options.restoreSelection(logical);if(!saved?.anchor.isConnected||!saved.focus.isConnected)return false;blockOf(saved.anchor)?.focus({preventScroll:true});getSelection().setBaseAndExtent(saved.anchor,saved.offset,saved.focus,saved.end);return true;}
  function close(){menu.hidden=true;}
  function fallback(){
    if(paper.inert)return;
    for(const node of menu.querySelectorAll('[data-context-action]')){const command=node.dataset.contextAction;node.hidden=command==='table'&&!context.table;}
    menu.hidden=false;
    const x=Math.max(4,Math.min(position.x,innerWidth-menu.offsetWidth-4)),y=Math.max(4,Math.min(position.y,innerHeight-menu.offsetHeight-4));
    sheet.replaceSync(`#word-context-menu{left:${x}px;top:${y}px}`);
  }
  function open(event,keyboard=false){
    if(paper.inert||!blockOf(event.target)&&!paper.contains(getSelection()?.anchorNode))return;
    event.preventDefault();event.stopPropagation();options.closePanels();
    const selection=getSelection(),target=blockOf(event.target);
    if(!keyboard&&target&&(selection?.isCollapsed||!paper.contains(selection?.anchorNode))){
      const range=document.caretRangeFromPoint?.(event.clientX,event.clientY);
      if(range&&target.contains(range.startContainer)){target.focus({preventScroll:true});selection.removeAllRanges();selection.addRange(range);}
    }
    capture();if(!saved)return;
    const rect=selection.getRangeAt(0).getBoundingClientRect();position=keyboard?{x:rect.left,y:rect.bottom}:{x:event.clientX,y:event.clientY};
    const block=blockOf(saved.anchor);context={table:Boolean(block?.closest('td,th'))};
    if(options.native)options.request({...position,...context});else fallback();
  }
  function action(command){
    if(paper.inert||!restore())return;close();
    if(command==='select-all'){
      const block=blockOf(saved.anchor),blocks=[...editor.ownerRoot(block).querySelectorAll('[data-anchor][contenteditable="true"]')];
      if(blocks.length)getSelection().setBaseAndExtent(blocks[0],0,blocks.at(-1),blocks.at(-1).childNodes.length);
    }else if(command==='copy'||command==='cut'){
      if(!document.execCommand(command))options.notice('请使用'+(/Mac/.test(navigator.platform)?'⌘':'Ctrl+')+(command==='copy'?'C':'X')+'完成'+(command==='copy'?'复制':'剪切')+'。');
    }else if(command==='paste'){
      // Desktop uses native Paste. Browsers expose clipboard text only on a
      // user gesture; restore the captured selection after the permission UI.
      if(navigator.clipboard?.read&&options.pasteRich){
        navigator.clipboard.read().then(async items=>{
          const html=items.find(item=>item.types.includes('text/html'));
          const plain=items.find(item=>item.types.includes('text/plain'));
          if(html){const value=await(await html.getType('text/html')).text();if(!paper.inert&&restore())options.pasteRich(value);}
          else if(plain){const value=await(await plain.getType('text/plain')).text();if(!paper.inert&&restore())options.paste(value);}
        }).catch(()=>options.notice('请按'+(/Mac/.test(navigator.platform)?'⌘V':'Ctrl+V')+'粘贴。'));return;
      }
      if(!navigator.clipboard?.readText){options.notice('请按'+(/Mac/.test(navigator.platform)?'⌘V':'Ctrl+V')+'粘贴。');return;}
      navigator.clipboard.readText().then(text=>{if(!paper.inert&&restore())options.paste(text);}).catch(()=>options.notice('请按'+(/Mac/.test(navigator.platform)?'⌘V':'Ctrl+V')+'粘贴。'));
    }else options.action(command);
  }
  paper.addEventListener('contextmenu',open);
  paper.addEventListener('keydown',event=>{if(event.key==='ContextMenu'||event.key==='F10'&&event.shiftKey){open(event,true);}});
  menu.addEventListener('mousedown',event=>event.preventDefault());
  menu.addEventListener('click',event=>{const command=event.target.closest('button')?.dataset.contextAction;if(command)action(command);});
  document.addEventListener('pointerdown',event=>{if(!menu.contains(event.target))close();},true);
  document.addEventListener('keydown',event=>{if(event.key==='Escape')close();});
  return {close,action,fallback};
};
