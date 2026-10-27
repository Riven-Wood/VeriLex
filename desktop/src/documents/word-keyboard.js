/* Keyboard adapter for the pinned Docxodus editor. Native text/IME editing stays
 * with the browser; structural edits always pass through the DOCX session. */
'use strict';
window.installWordKeyboard = function(editor, paper, options) {
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  const primary = event => mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
  const chrome = 'del, [data-list-marker], [class$="para-mark-del"], a.footnote-ref, a.endnote-ref, a[class$="-backref"]';
  const content = node => {const copy=node.cloneNode(true);copy.querySelectorAll(chrome).forEach(el=>el.remove());return copy.textContent.replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,'');};
  function rawKeyboardText(block) {
    const copy=block.cloneNode(true);copy.querySelectorAll(chrome).forEach(node=>node.remove());
    copy.querySelectorAll('br').forEach(node=>node.replaceWith('\n'));
    copy.querySelectorAll('[data-docx-tab]').forEach(node=>node.replaceWith('\t'));
    return copy.textContent.replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,'');
  }
  function keyboardText(block) {
    let value=rawKeyboardText(block);
    if(block.dataset?.vlEmptyPlaceholder==='true')value=value.replace(/\u00a0/,'');
    return value;
  }
  function normalizedOffset(block,text,offset) {
    const placeholder=block.dataset?.vlEmptyPlaceholder==='true'?text.indexOf('\u00a0'):-1;
    return Math.max(0,Math.min(offset-(placeholder>=0&&offset>placeholder?1:0),text.length-(placeholder<0?0:1)));
  }
  const committed=new WeakMap();
  function remember(block){
    if(!('vlEmptyPlaceholder' in block.dataset))block.dataset.vlEmptyPlaceholder=String(!(block.dataset.committedText||'').trim()&&block.textContent.endsWith('\u00a0'));
    committed.set(block,keyboardText(block));
  }
  const wireBlock=editor.wireBlock.bind(editor);
  editor.wireBlock=block=>{wireBlock(block);remember(block);};
  paper.querySelectorAll('[contenteditable="true"]').forEach(remember);
  editor.commitTextChange=(block,id)=>{
    const text=keyboardText(block);
    if(committed.get(block)===text)return null;
    const result=JSON.parse(editor.exports.DocxSessionBridge.SyncKeyboardText(editor.handle,id,text));
    if(!result.success)throw new Error('keyboard_text_rejected');
    committed.set(block,text);
    return result;
  };
  const blockOf = node => (node?.nodeType===1?node:node?.parentElement)?.closest('[data-anchor][contenteditable="true"]');
  function caret(block, end=false) {
    block.focus();const range=document.createRange();range.selectNodeContents(block);range.collapse(!end);
    const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
  }
  function boundary(block) {
    const selection=getSelection();if(!selection?.rangeCount||!selection.isCollapsed||!block.contains(selection.anchorNode))return null;
    const range=selection.getRangeAt(0).cloneRange();range.selectNodeContents(block);range.setEnd(selection.anchorNode,selection.anchorOffset);
    const wrap=document.createElement('div');wrap.append(range.cloneContents());
    // Structural engine offsets exclude br/tab; text sync includes them. Both
    // spaces exclude the same known placeholder, only when before the caret.
    const text=content(block);return {offset:normalizedOffset(block,text,content(wrap).length),length:normalizedOffset(block,text,text.length)};
  }
  function adjacent(block, backwards) {
    const blocks=[...editor.ownerRoot(block).querySelectorAll('[data-anchor][contenteditable="true"]')];
    return blocks[blocks.indexOf(block)+(backwards?-1:1)];
  }
  const modifier=mac?'⌘':'Ctrl+';
  for(const [command,key] of Object.entries({undo:'Z',redo:mac?'⇧Z':'Y',bold:'B',italic:'I',underline:'U'})) {
    const button=document.querySelector(`[data-command="${command}"]`);
    if(button)button.title=`${button.getAttribute('aria-label')||button.textContent}（${modifier}${key}）`;
  }
  document.getElementById('keyboard-help').textContent=mac
    ? 'Mac：⌘C 复制 · ⌘X 剪切 · ⌘V 保留格式粘贴 · ⇧⌘V 仅文字 · ⌘F 查找替换 · ⌘A 全选 · ⌘Z 撤销 · ⇧⌘Z 重做 · ⌘S 保存；Delete 向前删，Fn+Delete 向后删；⌥+方向键按词移动，⌘+左右到行首尾；单元格内用 ⌥Tab 插入制表符。'
    : 'Windows：Ctrl+C 复制 · Ctrl+X 剪切 · Ctrl+V 保留格式粘贴 · Ctrl+Shift+V 仅文字 · Ctrl+F 查找替换 · Ctrl+A 全选 · Ctrl+Z 撤销 · Ctrl+Y 重做 · Ctrl+S 保存；Backspace 向前删，Delete 向后删；Ctrl+方向键按词移动，Home/End 到行首尾；单元格内用 Ctrl+Tab 插入制表符。';
  for(const command of ['undo','redo']) {
    const original=editor[command].bind(editor);
    editor[command]=()=>{
      const block=editor.activeBlock,id=block&&editor.anchorIdOf(block),position=block&&boundary(block);
      const before=[...paper.querySelectorAll('[contenteditable="true"]')],index=before.indexOf(block);
      options.flush();const replacing=editor.replacing;editor.replacing=true;
      try { original();
      const after=[...paper.querySelectorAll('[contenteditable="true"]')];
      const target=after.find(node=>editor.anchorIdOf(node)===id)||after[Math.max(0,Math.min(index,after.length-1))];
      if(target)point(target,position?.offset??content(target).length);
      } finally {editor.replacing=replacing;}
    };
  }
  // Preserve tracked boundaries when the engine retains the second paragraph.
  // Ordinary adjacent paragraphs can be refreshed locally; full reconciliation
  // repaginates the document and is reserved for lists, bands or complex wrappers.
  editor.mergeWithPrevious=(previous,block)=>{
    const previousId=editor.anchorIdOf(previous), nextId=editor.anchorIdOf(block);
    const joinOffset=normalizedOffset(previous,content(previous),content(previous).length);
    options.flush();
    const result=JSON.parse(editor.exports.DocxSessionBridge.MergeParagraphs(editor.handle,previousId,nextId));
    if(!result.success)throw new Error('paragraph_merge_failed');
    const replacing=editor.replacing;editor.replacing=true;
    try {
      const nodes=[previous,block],modified=result.modified||[],removed=result.removed||[];
      const simple=previous.parentElement===block.parentElement&&previous.nextElementSibling===block&&
        nodes.every(node=>node.matches('p')&&!node.closest('table')&&!node.querySelector('[data-list-marker]')&&
          !editor.isBandBlock?.(node)&&Number(node.dataset.fragmentIndex||0)===0)&&
        !editor.affectsList?.(result)&&!Array.from(previous.parentElement.children).some(node=>node.matches('li'))&&
        ['Top','Bottom','Left','Right'].every(side=>parseFloat(getComputedStyle(previous.parentElement)[`border${side}Width`])===0)&&
        modified.length>=1&&modified.length<=2&&modified.every(item=>[previousId,nextId].includes(item.id))&&
        removed.every(item=>item.id===nextId);
      let patched=false;
      if(simple){
        try {
          const fresh=modified.length===2?editor.renderTwo(modified[0].id,modified[1].id):[editor.renderInto(modified[0].id)];
          if(fresh.every(Boolean)){
            patched=modified.every((item,index)=>editor.replaceNode(item.id===previousId?previous:block,fresh[index]));
            if(patched){
              if(removed.length)block.remove();
              for(const item of removed)editor.unidToFullId?.delete(item.unid);
              modified.forEach((item,index)=>{editor.unidToFullId?.set(item.unid,item.id);editor.wireBlock(fresh[index]);});
            }
          }
        }catch(_){patched=false;}
      }
      if(!patched)editor.reconcile();
      const target=[...paper.querySelectorAll('[data-anchor][contenteditable="true"]')].find(el=>editor.anchorIdOf(el)===result.modified?.[0]?.id);
      if(target)point(target,joinOffset);
      options.changed();
    }finally{editor.replacing=replacing;}
  };
  function insertControl(block,kind) {
    const position=boundary(block);if(!position)throw new Error('keyboard_selection_missing');
    options.flush();
    const id=editor.anchorIdOf(block);
    const result=JSON.parse(editor.exports.DocxSessionBridge.InsertKeyboardControl(editor.handle,id,position.offset,kind));
    if(!result.success)throw new Error('inline_control_failed');
    const fresh=editor.renderInto(id);if(!fresh)throw new Error('inline_control_render_failed');
    if(!editor.replaceNode(block,fresh))throw new Error('inline_control_replace_failed');
    editor.wireBlock(fresh);
    const control=[...fresh.querySelectorAll(kind==='br'?'br':'[data-docx-tab]')].find(node=>{
      if(node.closest(chrome))return false;
      const before=document.createRange();before.selectNodeContents(fresh);before.setEndBefore(node);
      const wrap=document.createElement('div');wrap.append(before.cloneContents());return content(wrap).length===position.offset;
    });
    if(control){
      // WebKit may place typing before a zero-height tab box when the caret is
      // only between elements. A nonprinting direction mark supplies a real
      // text position after it; both engine and adapter exclude that mark.
      const anchor=document.createTextNode('\u200e');control.after(anchor);fresh.focus();
      const range=document.createRange();range.setStart(anchor,1);range.collapse(true);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
    }
    else point(fresh,position.offset);
    options.changed();
  }
  function selectionBlocks() {
    const selection=getSelection();if(!selection?.rangeCount||selection.isCollapsed)return null;
    const range=selection.getRangeAt(0),first=blockOf(range.startContainer),last=blockOf(range.endContainer);
    if(!first||!last||first===last)return null;
    const blocks=[...editor.ownerRoot(first).querySelectorAll('[data-anchor][contenteditable="true"]')];
    const from=blocks.indexOf(first),to=blocks.indexOf(last);
    if(from<0||to<from)return null;
    return {range,blocks:blocks.slice(from,to+1)};
  }
  function point(block,offset) {
    const text=content(block),placeholder=block.dataset?.vlEmptyPlaceholder==='true'?text.indexOf('\u00a0'):-1;
    if(placeholder>=0&&offset>=placeholder)offset++;
    block.focus();const walk=document.createTreeWalker(block,NodeFilter.SHOW_TEXT);let node;
    while((node=walk.nextNode())){if(node.parentElement.closest(chrome))continue;if(offset<=node.textContent.length){const range=document.createRange();range.setStart(node,offset);range.collapse(true);const sel=getSelection();sel.removeAllRanges();sel.addRange(range);return;}offset-=node.textContent.length;}
    caret(block,true);
  }
  function selectedTextSpan(block,range) {
    const before=document.createRange();before.selectNodeContents(block);
    const selected=document.createRange();selected.selectNodeContents(block);
    let rawStart=0;
    if(block.contains(range.startContainer)){before.setEnd(range.startContainer,range.startOffset);const wrap=document.createElement('div');wrap.append(before.cloneContents());rawStart=rawKeyboardText(wrap).length;selected.setStart(range.startContainer,range.startOffset);}
    if(block.contains(range.endContainer))selected.setEnd(range.endContainer,range.endOffset);
    const wrap=document.createElement('div');wrap.append(selected.cloneContents());
    const rawText=rawKeyboardText(block),start=normalizedOffset(block,rawText,rawStart);
    const end=normalizedOffset(block,rawText,rawStart+rawKeyboardText(wrap).length);
    return {start,length:end-start,text:keyboardText(block)};
  }
  function replaceAcrossBlocks(text) {
    const state=selectionBlocks();if(!state)return false;
    const {range,blocks}=state;
    const bridge=editor.exports.DocxSessionBridge,handle=editor.handle;
    const spans=blocks.map(block=>({block,id:editor.anchorIdOf(block),...selectedTextSpan(block,range)}));
    const tx=bridge.BeginTransaction(handle);
    try {
      for(const span of spans)editor.syncBlock(span.block,span.id);
      for(let i=spans.length-1;i>=0;i--){const span=spans[i];if(span.length||(i===0&&text)){const result=JSON.parse(bridge.SyncKeyboardText(handle,span.id,span.text.slice(0,span.start)+(i===0?text:'')+span.text.slice(span.start+span.length)));if(!result.success)throw new Error('selection_text_rejected');}}
      // Join only real sibling paragraphs. Table cells remain independent.
      for(let i=spans.length-2;i>=0;i--){const left=spans[i],right=spans[i+1];if(!left.block.closest('table')&&!right.block.closest('table')){const result=JSON.parse(bridge.MergeParagraphs(handle,left.id,right.id));if(!result.success)throw new Error('selection_merge_rejected');}}
      bridge.CommitTransaction(tx);
    } catch(error){bridge.RollbackTransaction(tx);editor.reconcile();throw error;}
    editor.reconcile();
    const first=[...paper.querySelectorAll('[contenteditable="true"]')].find(block=>editor.anchorIdOf(block)===spans[0].id);
    if(first)point(first,spans[0].start+text.length);
    options.changed();return true;
  }
  paper.addEventListener('beforeinput',event=>{
    if(options.composing()||event.isComposing)return;
    // Native Edit menus can deliver history input without a keyboard event.
    // Keep that action in the DOCX history rather than the browser DOM history.
    if(['historyUndo','historyRedo'].includes(event.inputType)){
      event.preventDefault();event.stopImmediatePropagation();
      try{event.inputType==='historyUndo'?editor.undo():editor.redo();options.changed();}catch(error){options.error(error);}
      return;
    }
    if(!selectionBlocks()&&event.inputType.startsWith('delete')){
      const target=event.getTargetRanges?.()[0],first=blockOf(target?.startContainer),last=blockOf(target?.endContainer);
      if(first&&last&&first!==last){const range=document.createRange();range.setStart(target.startContainer,target.startOffset);range.setEnd(target.endContainer,target.endOffset);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);}
    }
    if(!selectionBlocks())return;
    if(event.inputType==='insertText'||event.inputType.startsWith('delete')){
      event.preventDefault();try{replaceAcrossBlocks(event.inputType==='insertText'?event.data||'':'');}catch(error){options.error(error);}
    }
  });
  for(const type of ['copy','cut'])paper.addEventListener(type,event=>{
    const state=selectionBlocks();if(!state)return;
    const text=state.blocks.map(block=>{const span=selectedTextSpan(block,state.range);return span.text.slice(span.start,span.start+span.length);}).join('\n');
    event.preventDefault();event.clipboardData.setData('text/plain',text);
    if(type==='cut')try{replaceAcrossBlocks('');}catch(error){options.error(error);}
  });
  function atomicEdit(action) {
    // Pending typing predates this command and must survive a failed paste.
    options.flush();
    const bridge=editor.exports.DocxSessionBridge,transaction=bridge.BeginTransaction(editor.handle);
    const replacing=editor.replacing;
    editor.replacing=true;
    try {
      action();options.flush();bridge.CommitTransaction(transaction);options.changed();
    } catch(error) {
      bridge.RollbackTransaction(transaction);
      // Reconcile may retain unchanged render tokens despite uncommitted DOM
      // typing. Remount from the rolled-back session to discard that typing.
      editor.remount();throw error;
    } finally {editor.replacing=replacing;}
  }
  function splitParagraph(block) {
    const id=editor.anchorIdOf(block);editor.splitAtCaret(block);
    const next=blockOf(getSelection()?.anchorNode);
    if(!next||editor.anchorIdOf(next)===id)throw new Error('paragraph_split_rejected');
  }
  function deleteSelection() {
    if(!replaceAcrossBlocks('')&&!getSelection()?.isCollapsed&&!document.execCommand('delete'))throw new Error('selection_delete_rejected');
  }
  function pasteText(text){
    if(!text||options.composing()||paper.inert)return;
    try{
      atomicEdit(()=>{
        const lines=text.replace(/\r\n?/g,'\n').split('\n');
        replaceAcrossBlocks('');
        const insert=line=>{if(line&&!document.execCommand('insertText',false,line))throw new Error('paste_text_rejected');};
        insert(lines[0]);
        for(const line of lines.slice(1)){
          const block=blockOf(getSelection()?.anchorNode);if(!block)throw new Error('paste_selection_lost');
          splitParagraph(block);
          insert(line);
        }
      });
    }catch(error){options.error(error);}
  }
  function pasteRich(html){
    if(options.composing()||paper.inert)return;
    try{
      const data=window.parseWordClipboard(html);
      atomicEdit(()=>{
        deleteSelection();options.flush();
        for(const [index,paragraph] of data.paragraphs.entries()){
          let block=blockOf(getSelection()?.anchorNode);if(!block)throw new Error('paste_selection_lost');
          if(index){splitParagraph(block);block=blockOf(getSelection()?.anchorNode);}
          const position=boundary(block);if(!position)throw new Error('paste_selection_lost');
          const empty=keyboardText(block)==='';
          const spans=[];let offset=position.offset;
          for(const run of paragraph.runs){
            for(const part of run.text.split(/([\n\t])/)){
              if(!part)continue;
              if(part==='\n'||part==='\t')insertControl(blockOf(getSelection()?.anchorNode),part==='\n'?'br':'tab');
              else {
                if(!document.execCommand('insertText',false,part))throw new Error('paste_text_rejected');
                spans.push({start:offset,length:part.length,format:run.format});offset+=part.length;
              }
            }
          }
          options.flush();block=blockOf(getSelection()?.anchorNode);
          const id=editor.anchorIdOf(block),api=editor.exports.DocxSessionBridge;
          const bookmark=options.captureSelection?.();
          for(const span of spans){const result=JSON.parse(api.ApplyFormat(editor.handle,id,JSON.stringify({start:span.start,length:span.length}),JSON.stringify(span.format)));if(!result.success)throw new Error('paste_format_rejected');}
          if((empty||data.paragraphs.length>1)&&Object.keys(paragraph.format).length){const result=JSON.parse(api.SetParagraphFormat(editor.handle,id,JSON.stringify(paragraph.format)));if(!result.success)throw new Error('paste_paragraph_rejected');}
          if(paragraph.list){const result=JSON.parse(api.ApplyListFormat(editor.handle,id,paragraph.list));if(!result.success)throw new Error('paste_list_rejected');}
          editor.reconcile();
          if(!bookmark||!options.restoreSelection?.(bookmark))throw new Error('paste_selection_lost');
        }
      });
    }catch(error){options.error(error);}
  }
  let plainPaste=false;
  paper.addEventListener('keyup',event=>{if(event.key.toLowerCase()==='v')plainPaste=false;});
  paper.addEventListener('paste',event=>{
    if(options.composing())return;
    event.preventDefault();event.stopImmediatePropagation();
    const html=event.clipboardData?.getData('text/html');
    if(html&&!plainPaste&&typeof window.parseWordClipboard==='function')pasteRich(html);
    else if(Array.from(event.clipboardData?.types||[]).includes('text/plain'))pasteText(event.clipboardData.getData('text/plain'));
    plainPaste=false;
  },true);
  paper.addEventListener('keydown',event=>{
    if(options.composing()||event.isComposing||event.keyCode===229)return;
    const block=blockOf(getSelection()?.focusNode)||blockOf(event.target);if(!block)return;
    const key=event.key.toLowerCase();
    const stop=()=>{event.preventDefault();event.stopImmediatePropagation();};
    try {
      const documentEdge=primary(event)&&!event.altKey&&(mac?['ArrowUp','ArrowDown'].includes(event.key):['Home','End'].includes(event.key));
      if(documentEdge){
        stop();const selection=getSelection(),anchor=selection?.anchorNode,offset=selection?.anchorOffset;
        const blocks=[...editor.ownerRoot(block).querySelectorAll('[data-anchor][contenteditable="true"]')];
        const end=event.key==='ArrowDown'||event.key==='End';const target=end?blocks.at(-1):blocks[0];
        if(target){caret(target,end);if(event.shiftKey&&anchor){const focus=selection.focusNode,focusOffset=selection.focusOffset;selection.setBaseAndExtent(anchor,offset,focus,focusOffset);}}return;
      }
      if(primary(event)&&!event.altKey) {
        if(key==='v'){plainPaste=event.shiftKey;return;}
        if(key==='s'){stop();options.save();return;}
        if(['z','y','b','i','u'].includes(key)) {
          stop();
          if(['b','i','u'].includes(key)){
            const action=()=>editor.format({b:'bold',i:'italic',u:'underline'}[key]);
            if(options.runCommand)options.runCommand(action,{singleFormat:true});else atomicEdit(action);
            return;
          }
          options.flush();
          if(key==='z')event.shiftKey?editor.redo():editor.undo();
          else if(key==='y'&&!mac)editor.redo();
          options.changed();return;
        }
        if(key==='a') {
          stop();const blocks=[...editor.ownerRoot(block).querySelectorAll('[data-anchor][contenteditable="true"]')];
          if(blocks.length){const range=document.createRange();range.setStart(blocks[0],0);range.setEnd(blocks.at(-1),blocks.at(-1).childNodes.length);const sel=getSelection();sel.removeAllRanges();sel.addRange(range);}return;
        }
      }
      // macOS Control+B/I/U are native cursor/control chords, not Cmd formatting.
      if(((mac&&event.ctrlKey&&!event.metaKey)||(!mac&&event.metaKey&&!event.ctrlKey))&&['b','i','u','z','y'].includes(key)){event.stopImmediatePropagation();return;}
      const cellTab=event.key==='Tab'&&!event.shiftKey&&!event.metaKey&&block.closest('td,th')&&
        (mac?event.altKey&&!event.ctrlKey:event.ctrlKey&&!event.altKey);
      if(cellTab){stop();atomicEdit(()=>{deleteSelection();const current=blockOf(getSelection()?.anchorNode);if(!current)throw new Error('keyboard_selection_missing');insertControl(current,'tab');});return;}
      if(event.key==='Tab'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!block.closest('table')&&!block.matches('li,[data-list-marker]')&&!block.querySelector('[data-list-marker]')) {
        stop();if(!event.shiftKey)insertControl(block,'tab');return;
      }
      if(event.key==='Enter'&&event.shiftKey&&!event.metaKey&&!event.ctrlKey&&!event.altKey){
        stop();atomicEdit(()=>{deleteSelection();const current=blockOf(getSelection()?.anchorNode);if(!current)throw new Error('keyboard_selection_missing');insertControl(current,'br');});return;
      }
      if(event.key==='Enter'&&!event.shiftKey&&!event.metaKey&&!event.ctrlKey&&!event.altKey){
        stop();atomicEdit(()=>{deleteSelection();const current=blockOf(getSelection()?.anchorNode);if(!current)throw new Error('keyboard_selection_missing');splitParagraph(current);});return;
      }
      const pos=boundary(block);if(!pos)return;
      const backwards=event.key==='Backspace';
      if((backwards&&pos.offset===0||event.key==='Delete'&&pos.offset===pos.length)&&!event.altKey&&!event.ctrlKey&&!event.metaKey) {
        const other=adjacent(block,backwards);
        if(other&&!block.closest('table')&&!other.closest('table')) {
          stop();editor.mergeWithPrevious(backwards?other:block,backwards?block:other);return;
        }
      }
      if(!event.metaKey&&!event.ctrlKey&&!event.altKey&&!event.shiftKey&&['ArrowUp','ArrowDown'].includes(event.key)) {
        const selection=getSelection(),range=selection.getRangeAt(0),box=range.getBoundingClientRect();
        const full=document.createRange();full.selectNodeContents(block);const lines=[...full.getClientRects()];
        const up=event.key==='ArrowUp';
        if(box.height&&lines.length&&(up?box.top<=Math.min(...lines.map(r=>r.top))+2:box.bottom>=Math.max(...lines.map(r=>r.bottom))-2)){
          const target=adjacent(block,up);if(target){stop();caret(target,up);const rect=target.getBoundingClientRect();const y=up?rect.bottom-3:rect.top+3;
            const hit=document.caretRangeFromPoint?.(box.left,y);if(hit&&target.contains(hit.startContainer)){selection.removeAllRanges();selection.addRange(hit);}}
        }
      }
      if(!event.metaKey&&!event.ctrlKey&&!event.altKey&&!event.shiftKey&&
        (event.key==='ArrowLeft'&&pos.offset===0||event.key==='ArrowRight'&&pos.offset===pos.length)) {
        const back=event.key==='ArrowLeft',other=adjacent(block,back);if(other){stop();caret(other,back);}
      }
    } catch(error){stop();options.error(error);}
  },true);
  return {mac,primary,pasteText,pasteRich};
};
