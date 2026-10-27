/* Search uses engine text spans, so revision decorations never become targets. */
'use strict';
window.installWordSearch=function(editor,paper,options){
  const api=editor.exports.DocxSessionBridge,handle=editor.handle;
  const panel=document.getElementById('search-panel'),openButton=document.getElementById('search-open');
  const query=document.getElementById('search-query'),replacement=document.getElementById('search-replacement');
  const sensitive=document.getElementById('search-case'),count=document.getElementById('search-count');
  const editable='[data-anchor][contenteditable="true"]',chrome='del,[data-list-marker],[class$="para-mark-del"],a.footnote-ref,a.endnote-ref,a[class$="-backref"]';
  let matches=[],index=-1,version=null,timer,highlight=null,searched='';
  const listeners=[];
  const on=(node,event,fn,capture=false)=>{node.addEventListener(event,fn,capture);listeners.push(()=>node.removeEventListener(event,fn,capture));};
  function clearHighlight(){if(globalThis.CSS?.highlights)CSS.highlights.delete('word-search');highlight=null;}
  function controls(){
    for(const id of ['search-previous','search-next','search-replace','search-replace-all'])document.getElementById(id).disabled=!matches.length||paper.inert;
    count.textContent=!query.value?'输入查找文字':matches.length?`${index+1} / ${matches.length}`:'未找到匹配文字';
  }
  function locate(scroll=true){
    clearHighlight();const match=matches[index];if(!match)return;
    const nodes=[...paper.querySelectorAll(editable)].filter(node=>editor.anchorIdOf(node)===match.enclosingAnchor.id);
    // Pagination can split one paragraph into several rendered fragments.
    const textNodes=[];for(const block of nodes){const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT);let node;while((node=walker.nextNode()))if(!node.parentElement.closest(chrome))textNodes.push(node);}
    function point(offset){for(const node of textNodes){const clean=node.data.replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,'');if(offset<=clean.length){let raw=0,seen=0;while(raw<node.length&&seen<offset){if(!/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/.test(node.data[raw]))seen++;raw++;}return[node,raw];}offset-=clean.length;}return null;}
    const start=point(match.span.start),end=point(match.span.start+match.span.length);if(!start||!end)return;
    const range=document.createRange();range.setStart(...start);range.setEnd(...end);highlight=range;
    if(globalThis.CSS?.highlights&&typeof Highlight==='function')CSS.highlights.set('word-search',new Highlight(range));
    else{const selection=getSelection();selection.removeAllRanges();selection.addRange(range);}
    if(scroll){const box=range.getBoundingClientRect(),port=document.getElementById('workarea'),view=port.getBoundingClientRect();port.scrollTop+=box.top-view.top-view.height/3;}
  }
  function read(keep=false){
    if(!options.prepare())return false;
    const previous=keep?matches[index]:null;
    try{
      const pattern=query.value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
      const result=pattern?JSON.parse(api.Grep(handle,pattern,JSON.stringify({scope:1,contextChars:0,regexOptions:sensitive.checked?0:1}))):[];
      if(!Array.isArray(result))throw new Error('search_failed');
      if(result.length>5000){matches=[];index=-1;clearHighlight();controls();count.textContent='匹配超过 5000 处，请输入更完整的文字。';return false;}
      matches=result;version=api.GetVersion(handle);searched=query.value+'|'+sensitive.checked;index=matches.length?0:-1;
      if(previous){const found=matches.findIndex(m=>m.enclosingAnchor.id===previous.enclosingAnchor.id&&m.span.start>=previous.span.start);if(found>=0)index=found;}
      controls();return true;
    }catch{matches=[];index=-1;clearHighlight();controls();count.textContent='查找未完成，请重试。';return false;}
  }
  function refresh(scroll=false){if(!panel.hidden&&read(true))locate(scroll);}
  function open(){
    options.closePanels();panel.hidden=false;openButton.setAttribute('aria-expanded','true');
    const selected=getSelection()?.toString();if(selected&&selected.length<=500&&!/[\r\n]/.test(selected))query.value=selected;
    if(read())locate();query.focus();query.select();
  }
  function close(focus=false){clearTimeout(timer);panel.hidden=true;openButton.setAttribute('aria-expanded','false');clearHighlight();if(focus)openButton.focus();}
  function next(delta){if(!read(true)||!matches.length)return;index=(index+delta+matches.length)%matches.length;controls();locate();}
  function replace(all){
    if(paper.inert||!options.prepare())return;
    if(/[\r\n\t]/.test(replacement.value)){count.textContent='替换内容请使用单行文字。';return;}
    if(api.GetVersion(handle)!==version||searched!==query.value+'|'+sensitive.checked){refresh();count.textContent='正文或查找条件已更新，请核对结果后再替换。';return;}
    const selected=matches[index];if(!selected)return;
    const targets=(all?matches:[selected]).filter(match=>match.text!==replacement.value);
    if(!targets.length){count.textContent='替换文字与原文相同。';return;}
    // Reverse document order keeps every yet-to-be-replaced offset stable.
    const ordered=[...targets].reverse(),aliases=new Map();
    const first=[...paper.querySelectorAll(editable)].find(node=>editor.anchorIdOf(node)===selected.enclosingAnchor.id);
    if(!first){count.textContent='这处内容当前无法编辑。';return;}
    editor.activeBlock=first;
    const success=options.runCommand(()=>{
      for(const match of ordered){
        const original=match.enclosingAnchor.id,id=aliases.get(original)||original;
        const result=JSON.parse(api.ReplaceTextAtSpan(handle,id,match.span.start,match.span.length,replacement.value));
        if(!result.success)throw new Error('search_replace_rejected');
        const modified=result.modified?.find(anchor=>['p','h','li'].includes(anchor.kind));if(modified)aliases.set(original,modified.id);
      }
      editor.reconcile();
    });
    if(!success){refresh();count.textContent='替换未完成，本次替换已撤回。';return;}
    const anchors=[...new Set(matches.map(match=>match.enclosingAnchor.id))],oldAnchorIndex=anchors.indexOf(selected.enclosingAnchor.id);
    read(true);
    if(!all&&matches.length){
      const currentId=aliases.get(selected.enclosingAnchor.id)||selected.enclosingAnchor.id;
      const next=matches.findIndex(match=>match.enclosingAnchor.id===currentId?match.span.start>=selected.span.start+replacement.value.length:anchors.indexOf(match.enclosingAnchor.id)>oldAnchorIndex);
      index=next<0?0:next;controls();
    }
    locate();count.textContent=`已替换 ${targets.length} 处${matches.length?'，剩余 '+matches.length+' 处':''}`;
  }
  on(openButton,'click',open);on(document.getElementById('search-close'),'click',()=>close(true));
  on(query,'input',()=>{clearTimeout(timer);timer=setTimeout(()=>{if(read())locate();},150);});
  on(sensitive,'change',()=>{if(read())locate();});
  on(document.getElementById('search-previous'),'click',()=>next(-1));on(document.getElementById('search-next'),'click',()=>next(1));
  on(document.getElementById('search-replace'),'click',()=>replace(false));on(document.getElementById('search-replace-all'),'click',()=>replace(true));
  on(panel,'keydown',event=>{if(event.isComposing||event.keyCode===229)return;if(event.key==='Escape'){event.preventDefault();close(true);}else if(event.key==='Enter'){event.preventDefault();event.target===replacement?replace(false):next(event.shiftKey?-1:1);}});
  on(document,'keydown',event=>{if(event.isComposing||event.keyCode===229)return;const primary=/Mac|iPhone|iPad/.test(navigator.platform)?event.metaKey&&!event.ctrlKey:event.ctrlKey&&!event.metaKey;if(primary&&!event.altKey&&event.key.toLowerCase()==='f'){event.preventDefault();event.stopImmediatePropagation();open();}},true);
  on(paper,'input',()=>{clearTimeout(timer);timer=setTimeout(()=>refresh(),700);});
  return{open,close,refresh,schedule(){if(!panel.hidden){clearTimeout(timer);timer=setTimeout(()=>refresh(),700);}},dispose(){clearTimeout(timer);clearHighlight();listeners.forEach(remove=>remove());}};
};
