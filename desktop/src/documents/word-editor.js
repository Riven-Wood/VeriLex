/* Docxodus integration, pinned by renderer-lock.json. The engine remains MIT
 * licensed; see third_party/licenses/documents. No document HTML is executable. */
'use strict';
let editor, searchControls, layoutControls, contextControls, selectionControls, keyboardControls, channel, author='VeriLex', tracking=true, batching=false, composing = false, queued = false, fatal = false;
let savedVersion, savedBytes, saveTimer;
const paper = document.getElementById('paper');
const scrollport=document.getElementById('workarea');
const reviewSurface=document.getElementById('review-surface');
const status = document.getElementById('status');
const sheet = new CSSStyleSheet();
document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
const cssRules = new Set(), inlineClasses = new Map();
let appliedCssRuleCount=0;
const send = (type, payload = {}) => parent.postMessage({channel, type, ...payload}, location.origin);
// WKWebView extends the native caret through the previous line's leading.
// Draw only its visual bar from glyph metrics; selection, input and IME remain
// native. Windows WebView2 retains its native caret. The bar shares document
// scrolling and never becomes part of editable content or the saved package.
const glyphCaret=/AppleWebKit/.test(navigator.userAgent)&&!/Chrome|Chromium|Edg/.test(navigator.userAgent);
// WKWebView includes inherited CSS zoom in lh before the viewport scales it
// again. Compensate the display unit; exact Word line spacing is untouched.
const zoomedLhBug=glyphCaret;
const textCaret=document.createElement('div');
textCaret.id='word-text-caret';textCaret.hidden=true;textCaret.setAttribute('aria-hidden','true');
reviewSurface.append(textCaret);
let caretFrame;
function textCaretRect(range,block){
  const chrome='del,[data-list-marker],[class$="para-mark-del"],a.footnote-ref,a.endnote-ref,a[class$="-backref"]';
  const before=[],after=[],walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT);let node;
  while((node=walker.nextNode())){
    if(!node.length||node.parentElement.closest(chrome))continue;
    if(node===range.startContainer){
      if(range.startOffset)before.push([node,range.startOffset]);
      if(range.startOffset<node.length)after.push([node,range.startOffset]);
    }else if(range.comparePoint(node,node.length)<0)before.push([node,node.length]);
    else if(range.comparePoint(node,0)>0)after.push([node,0]);
  }
  function nearest(points,previous){
    for(const [node,offset] of points){
      let i=offset;
      while(previous?i>0:i<node.length){
        let start=i,end=i;
        if(previous){start=i-1;if(start>0&&/[\uDC00-\uDFFF]/.test(node.data[start]))start--;i=start;}
        else{end=i+String.fromCodePoint(node.data.codePointAt(i)).length;i=end;}
        const glyph=document.createRange();glyph.setStart(node,start);glyph.setEnd(node,end);
        const rect=[...glyph.getClientRects()].find(r=>r.width>0&&r.height>0);
        if(rect){const rtl=getComputedStyle(node.parentElement).direction==='rtl';return{rect,x:previous!==rtl?rect.right:rect.left};}
      }
    }
  }
  const preceding=nearest(before.reverse(),true),following=nearest(after,false);
  const candidates=[preceding,following].filter(Boolean);if(!candidates.length)return null;
  const collapsed=[...range.getClientRects()].find(r=>r.height>0);
  // Use the browser's affinity at a wrapped-line boundary, but never its
  // inflated caret height. Empty collapsed ranges use the adjacent glyph edge.
  if(collapsed)candidates.sort((a,b)=>Math.abs(a.rect.bottom-collapsed.bottom)-Math.abs(b.rect.bottom-collapsed.bottom));
  const glyph=candidates[0];
  return{left:collapsed?collapsed.left:glyph.x,top:glyph.rect.top,height:glyph.rect.height};
}
function refreshTextCaret(){
  textCaret.hidden=true;paper.classList.remove('has-text-caret');
  if(!glyphCaret||fatal||!document.hasFocus()||!paper.contains(document.activeElement))return;
  const selection=getSelection();if(!selection?.isCollapsed||!selection.rangeCount)return;
  const range=selection.getRangeAt(0),element=range.startContainer.nodeType===Node.TEXT_NODE?range.startContainer.parentElement:range.startContainer;
  const block=element.closest?.('[data-source-anchor-id]');
  if(!block||!paper.contains(block))return;
  const rect=textCaretRect(range,block);if(!rect)return;
  const surface=reviewSurface.getBoundingClientRect();
  Object.assign(textCaret.style,{left:`${rect.left-surface.left}px`,top:`${rect.top-surface.top}px`,height:`${rect.height}px`});
  textCaret.hidden=false;paper.classList.add('has-text-caret');
  for(const animation of textCaret.getAnimations())animation.currentTime=0;
}
function scheduleTextCaret(){cancelAnimationFrame(caretFrame);caretFrame=requestAnimationFrame(refreshTextCaret);}
document.addEventListener('selectionchange',scheduleTextCaret);
for(const event of ['input','focusin','focusout','compositionstart','compositionend'])paper.addEventListener(event,scheduleTextCaret);
window.addEventListener('focus',scheduleTextCaret);
window.addEventListener('blur',()=>{textCaret.hidden=true;paper.classList.remove('has-text-caret');});
const fail = () => {
  fatal = true;status.textContent='';
  paper.inert=true;document.getElementById('tools').inert=true;
  send('error', {message:'这次编辑未能保存，请先另存可用副本后重新打开。'});
};

// View-only zoom uses the pinned engine's viewport, never document formatting or
// a remount (which would disturb the current selection and uncommitted input).
let zoomMode='width', manualZoom=1, zoomFrame;
const zoomLevel=document.getElementById('zoom-level');
const zoomCurrent=document.getElementById('zoom-current');
const pageContentSizes=new WeakMap();
function fitPageContents(){
  // Ordinary paragraphs paginate into visual fragments. Grow a sheet only for
  // unsupported oversized content; empty grid leading may hang below the body.
  // These view dimensions never change the saved document's page settings.
  for(const page of paper.querySelectorAll('.page-box')){
    if(page.classList.contains('vl-spanning-page'))continue;
    const content=page.querySelector(':scope > .page-content');if(!content)continue;
    let size=pageContentSizes.get(page);
    if(!size){size={page:parseFloat(getComputedStyle(page).height),content:parseFloat(getComputedStyle(content).height)};pageContentSizes.set(page,size);}
    if(!Number.isFinite(size.page)||!Number.isFinite(size.content))continue;
    content.style.height=`${size.content}px`;
    const extra=Math.max(0,content.scrollHeight-content.clientHeight-wordGridTrailingSpacePx(content.lastElementChild));
    content.style.height=`${size.content+extra}px`;
    page.style.height=`${size.page+extra}px`;
  }
}
function refreshZoom(){
  if(!editor?.viewport?.root)return;
  fitPageContents();
  const viewport=editor.viewport;
  const pages=[...paper.querySelectorAll('.page-box')];
  const width=viewport.natural.width*96/72;
  const height=Math.max(0,...pages.map(page=>page.offsetHeight));
  const style=getComputedStyle(paper);
  const railWidth=pane.hidden?0:pane.getBoundingClientRect().width;
  const availableWidth=scrollport.clientWidth-railWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight);
  const availableHeight=scrollport.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom);
  let scale=manualZoom;
  if(zoomMode!=='manual'&&width>0){
    scale=Math.min(2,Math.max(.1,availableWidth/width));
    if(zoomMode==='page'&&height>0)scale=Math.min(scale,Math.max(.1,availableHeight/height));
  }
  // The engine's own ResizeObserver reads these same options, so it cannot
  // reset a manual percentage when a sidebar or window changes size.
  paper.style.minWidth=`${Math.ceil(width*scale+parseFloat(style.paddingLeft)+parseFloat(style.paddingRight))}px`;
  paper.style.setProperty('--verilex-lh-zoom',String(zoomedLhBug?scale:1));
  viewport.options.fitToWidth=false;
  viewport.options.scale=scale;
  viewport.refresh();
  scheduleReviewLayout();
  scheduleTextCaret();
  const percent=String(Math.round(scale*100));
  const preset=[...zoomLevel.options].some(option=>option.value===percent);
  zoomCurrent.hidden=preset;zoomCurrent.textContent=`${percent}%`;
  zoomLevel.value=preset?percent:'current';zoomLevel.disabled=false;
  document.getElementById('zoom-out').disabled=scale<=.5;
  document.getElementById('zoom-in').disabled=scale>=2;
  for(const [id,mode] of [['zoom-width','width'],['zoom-page','page']]){
    const button=document.getElementById(id);button.disabled=false;button.setAttribute('aria-pressed',String(zoomMode===mode));
  }
}
function scheduleZoom(){cancelAnimationFrame(zoomFrame);zoomFrame=requestAnimationFrame(refreshZoom);}
function setZoom(mode,value){
  if(!editor)return;
  const previous=editor.zoom||1,top=scrollport.scrollTop/previous,left=scrollport.scrollLeft/previous;
  zoomMode=mode;
  if(mode==='manual')manualZoom=Math.min(2,Math.max(.5,value));
  refreshZoom();
  scrollport.scrollTop=top*editor.zoom;scrollport.scrollLeft=left*editor.zoom;
}
zoomLevel.addEventListener('change',()=>{if(zoomLevel.value!=='current')setZoom('manual',Number(zoomLevel.value)/100);});
document.getElementById('zoom-out').addEventListener('click',()=>setZoom('manual',Math.round((editor.zoom-.1)*100)/100));
document.getElementById('zoom-in').addEventListener('click',()=>setZoom('manual',Math.round((editor.zoom+.1)*100)/100));
document.getElementById('zoom-width').addEventListener('click',()=>setZoom('width'));
document.getElementById('zoom-page').addEventListener('click',()=>setZoom('page'));
document.getElementById('view-tools').addEventListener('mousedown',event=>{if(event.target.closest('button'))event.preventDefault();});
// Chromium emits ctrl+wheel for a trackpad pinch; WKWebView also exposes gesture events.
let gestureZoom=null;
scrollport.addEventListener('wheel',event=>{
  if(!event.ctrlKey||!editor)return;
  event.preventDefault();
  if(gestureZoom===null)setZoom('manual',editor.zoom*Math.exp(-event.deltaY*.01));
},{passive:false});
scrollport.addEventListener('gesturestart',event=>{if(!editor)return;event.preventDefault();gestureZoom=editor.zoom;},{passive:false});
scrollport.addEventListener('gesturechange',event=>{if(gestureZoom===null)return;event.preventDefault();if(Number.isFinite(event.scale))setZoom('manual',gestureZoom*event.scale);},{passive:false});
scrollport.addEventListener('gestureend',event=>{event.preventDefault();gestureZoom=null;},{passive:false});
new ResizeObserver(scheduleZoom).observe(scrollport);
new MutationObserver(records=>{
  if(records.some(record=>record.type==='characterData'&&record.target.parentElement?.closest('[data-source-anchor-id]')||!record.target.closest?.('.review-paper-highlights')&&[...record.addedNodes,...record.removedNodes].some(node=>!node.classList?.contains('review-paper-highlights'))))scheduleZoom();
}).observe(paper,{childList:true,characterData:true,subtree:true});

function safeCss(css) {
  const clean=css.replace(/calc\(1lh\s*\*\s*var\(--docx-auto-line-spacing\)\)/g,'calc(1lh * var(--docx-auto-line-spacing) / var(--verilex-lh-zoom, 1))').replace(/@import[^;]*;/gi,'').replace(/url\([^)]*\)/gi,'none')
    .replace(/(ins\.rev-ins\s*\{)([^}]*)(\})/g,(_,open,rules,close)=>open+rules.replace(/text-decoration:\s*underline\s*;/i,'text-decoration:none;').replace(/background-color:\s*#[0-9a-f]+\s*;/i,'background-color:transparent;')+close)
    .replace(/(del\.rev-del\s*\{)([^}]*)(\})/g,(_,open,rules,close)=>open+rules+'display:none!important;'+close);
  // A tracked paragraph mark is an annotation, not paragraph indentation.
  // Draw its gutter rule outside the box instead of consuming border/padding width.
  return clean.replace(/(\.rev-para-(?:ins|del)\s*\{)([^}]*)(\})/g,(_,open,rules,close)=>{
    const border=rules.match(/border-left:\s*3px solid (#[0-9a-f]+)\s*;/i);
    if(!border)return open+rules+close;
    return open+rules.replace(border[0],`box-shadow:-3px 0 0 ${border[1]};`).replace(/padding-left:\s*4px\s*;/i,'')+close;
  });
}

function safeHtml(html) {
  if (typeof html !== 'string' || html.startsWith('{')) return html;
  const template = document.createElement('template');
  // XElement emits empty non-void elements as <span/>. HTML parsers keep those
  // spans open, nesting later text under PAGE fields; substituting the page
  // number would then erase the rest of the footer. Close them before parsing.
  template.innerHTML = html.replace(/<(span|p|div|ins|del|a)(\s[^<>]*?)?\s*\/>/gi,'<$1$2></$1>').replace(/<style[^>]*>([\s\S]*?)<\/style>/gi, (_, css) => { cssRules.add(safeCss(css)); return ''; }).replace(/\sstyle=/gi,' data-vl-style=');
  const parsed = template.content;
  parsed.querySelectorAll('script,iframe,object,embed,link,meta,base,form,input,button,textarea,svg,math').forEach(n => n.remove());
  for (const style of parsed.querySelectorAll('style')) {
    // Network resources and imports are never required for DOCX layout.
    const css = safeCss(style.textContent);
    cssRules.add(css); style.remove();
  }
  for (const element of parsed.querySelectorAll('*')) {
    for (const attr of [...element.attributes]) {
      if (/^on/i.test(attr.name) || ['srcdoc','formaction','href','xlink:href'].includes(attr.name)) element.removeAttribute(attr.name);
      if (attr.name === 'src' && !/^data:image\/(?:png|jpeg|gif|webp|bmp);base64,/i.test(attr.value)) element.removeAttribute(attr.name);
    }
    const inline = element.getAttribute('data-vl-style');
    if (inline) {
      let name = inlineClasses.get(inline);
      if (!name) { name = `vl-word-${inlineClasses.size}`; inlineClasses.set(inline,name); cssRules.add(safeCss(`.${name}{${inline}}`)); }
      element.classList.add(name); element.removeAttribute('data-vl-style');
    }
  }
  // Constructable stylesheets apply synchronously before page measurement and
  // work with the strict CSP without enabling inline styles or remote resources.
  if(cssRules.size!==appliedCssRuleCount){sheet.replaceSync([...cssRules].join('\n'));appliedCssRuleCount=cssRules.size;}
  return html.includes('<html') || /<!doctype/i.test(html) ? `<!doctype html><html><head></head><body>${template.innerHTML}</body></html>` : template.innerHTML;
}

function exportsForEditor(exports) {
  const bridge = {...exports.DocxSessionBridge};
  for (const key of Object.keys(bridge)) {
    if (/^RenderBlocksHtml/.test(key)) {
      const fn=bridge[key]; bridge[key]=(...args)=>{const values=JSON.parse(fn(...args)); if(values.error)return JSON.stringify(values); return JSON.stringify(Object.fromEntries(Object.entries(values).map(([id,html])=>[id,safeHtml(html)])));};
    } else if (/^Render.*Html/.test(key)) { const fn=bridge[key]; bridge[key]=(...args)=>safeHtml(fn(...args)); }
    else if (/^(Replace|Split|Merge|Unmerge|Delete|Insert|Apply|Set|Move|Add|Remove|Update|Accept|Reject)/.test(key)) {
      const fn=bridge[key]; bridge[key]=(...args)=>{
        // The pinned editor omits dates for newly authored comments. Stamp only
        // that new AddComment call; existing document metadata stays untouched.
        if(key==='AddComment'&&!args[5])args[5]=new Date().toISOString();
        const value=fn(...args);
        if(typeof value==='string' && value.startsWith('{')) { const result=JSON.parse(value); if(result.success===false) {const error=new Error('word_edit_rejected');error.code=result.error?.code||result.errorCode;throw error;} }
        return value;
      };
    }
  }
  return {DocxSessionBridge:bridge, DocumentConverter:{ConvertDocxToHtmlComplete:(...args)=>safeHtml(exports.DocumentConverter.ConvertDocxToHtmlComplete(...args))}};
}

function flushFocused() {
  selectionControls?.flushPending();
  const block=editor.activeBlock;
  if(block?.isConnected) { const anchor=editor.anchorIdOf(block); if(anchor) editor.syncBlock(block,anchor); }
}
function captureBytes(){
  const version=bridge().GetVersion(editor.handle);
  if(version!==savedVersion){savedBytes=editor.save();savedVersion=bridge().GetVersion(editor.handle);}
  return savedBytes;
}
function snapshot(requestId, markup) {
  clearTimeout(saveTimer);queued=false;
  if (!editor || composing || fatal) { if(requestId) send('snapshot-error',{requestId}); return false; }
  try {
    // The pinned upstream public save() does not flush focused text. Its own
    // syncBlock path preserves caret and run formatting; guard that adapter
    // contract and flush before every snapshot, including immediately after input.
    flushFocused();
    const bytes=captureBytes();
    if(editor.vlLayoutVersion!==bridge().GetVersion(editor.handle)){
      const selection=layoutControls?.captureSelection(),position=captureReadingPosition(),focused=paper.contains(document.activeElement);
      const replacing=editor.replacing;
      try{editor.replacing=true;editor.reconcile();layoutControls?.restoreSelection(selection,focused);}
      finally{editor.replacing=replacing;restoreReadingPosition(position);}
    }
    keyboardControls?.refreshDeletedEmpty();
    send('snapshot',{bytes,requestId,markup});refreshReview();return true;
  } catch (_) { fail(); if(requestId) send('snapshot-error',{requestId});return false; }
}
function changed() {
  if(fatal || composing || batching) return;
  searchControls?.schedule();
  status.textContent='';
  if(!queued)send('dirty');
  clearTimeout(saveTimer);queued=true;
  saveTimer=setTimeout(()=>{queued=false;snapshot();},600);
}
window.addEventListener('message', async event => {
  if(event.source!==parent || event.origin!==location.origin) return;
  const data=event.data;
  if(data?.type==='open' && !channel) {
    channel=data.channel;
    for(const id of ["history-undo","history-redo"])document.getElementById(id).hidden=!data.historyToolbar;
    author=data.author||'VeriLex';tracking=data.tracking!==false;
    if(data.label){document.title=data.label;paper.setAttribute('aria-label',data.label+'正文');}
    document.getElementById('tracking').textContent=tracking?'修订：开':'修订：关';document.getElementById('tracking').setAttribute('aria-pressed',String(tracking));
    try {
      await Docxodus.initialize(new URL('./wasm/',location.href).href);
      await document.fonts.load('16px "仿宋_GB2312"');
      installWordPagination(Docxodus.DocxEditor,Docxodus.PaginationEngine);
      const exports=exportsForEditor(Docxodus.getWasmExports());
      editor=Docxodus.DocxEditor.open(paper,new Uint8Array(data.bytes),exports,{paginated:true,fragmentParagraphs:true,fitToWidth:true,blockDrag:false,trackedChanges:tracking?1:0,revisionAuthor:author,onEdit:changed});
      if(typeof editor.syncBlock!=='function'||typeof editor.anchorIdOf!=='function'||typeof exports.DocxSessionBridge.ListRevisions!=='function'||typeof editor.reconcile!=='function'||typeof editor.viewport?.refresh!=='function'||!editor.viewport.options) throw new Error('word_editor_version_mismatch');
      selectionControls=installWordSelection(editor,paper);
      if(data.selectionEvents)installDraftSelection(data.selectionActionLabel);
      keyboardControls=installWordKeyboard(editor,paper,{flush:flushFocused,changed,composing:()=>composing,captureSelection:()=>layoutControls.captureSelection(),restoreSelection:value=>layoutControls.restoreSelection(value),runCommand:(action,details)=>runEditingCommand(action,details),save:()=>{if(snapshot())send('save');},error:error=>{if(error?.message==='clipboard_format_unsupported')status.textContent='这段内容包含暂不能完整保留的格式或对象，尚未粘贴。可用 Shift+⌘V（Mac）或 Ctrl+Shift+V（Windows）仅粘贴文字。';else status.textContent='';}});
      layoutControls=installWordLayout(editor,paper,{runCommand:action=>runEditingCommand(action),prepare:()=>{if(composing||fatal)return false;try{flushFocused();return true;}catch(_){fail();return false;}},tracking:()=>tracking,status});
      searchControls=installWordSearch(editor,paper,{runCommand:action=>runEditingCommand(action),prepare:()=>{if(composing||fatal||paper.inert)return false;try{flushFocused();return true;}catch(_){fail();return false;}},closePanels:()=>layoutControls.close({focusDocument:false})});
      contextControls=installWordContext(editor,paper,{captureSelection:layoutControls.captureSelection,restoreSelection:layoutControls.restoreSelection,native:Boolean(data.nativeContextMenu),request:position=>send('context-menu',position),closePanels:()=>layoutControls.close({restoreSelection:false,focusDocument:false}),notice:message=>{status.textContent=message;},paste:keyboardControls.pasteText,pasteRich:keyboardControls.pasteRich,action:command=>{
        if(['undo','redo','bullet','decimal'].includes(command)){runToolbarCommand(command);return;}
        if(command==='font'){layoutControls.openFont();return;}
        const target={comment:'#new-comment',paragraph:'#paragraph-open',table:'#table-open'}[command];
        if(target)document.querySelector(target)?.click();
      }});
      savedBytes=new Uint8Array(data.bytes);savedVersion=exports.DocxSessionBridge.GetVersion(editor.handle);
      status.textContent='';refreshReview();refreshZoom();send('ready');
    } catch (_) { status.textContent='Word 编辑器未能打开这份文件，请用 Word/WPS 另存为 .docx 后重试。';send('error',{message:status.textContent}); }
  } else if(data?.channel===channel && data.type==='snapshot') snapshot(data.requestId);
  else if(data?.channel===channel && data.type==='draft-highlight') highlightDraftRange(data.range,data.locate);
  else if(data?.channel===channel && data.type==='apply-review') applyReview(data.plan,data.requestId);
  else if(data?.channel===channel && data.type==='apply-ai-edit') applyAiEdit(data.plan,data.requestId);
  else if(data?.channel===channel && data.type==='close-controls'){layoutControls?.close({focusDocument:false});contextControls?.close();}
  else if(data?.channel===channel && data.type==='context-action'&&!fatal) contextControls?.action(data.command);
  else if(data?.channel===channel && data.type==='history-action'&&!fatal&&['undo','redo'].includes(data.command)){
    if(document.activeElement?.matches('input,textarea,[contenteditable="true"]:not(#paper):not([data-anchor])'))document.execCommand(data.command);
    else runToolbarCommand(data.command);
  }
  else if(data?.channel===channel && data.type==='context-fallback'&&!fatal) contextControls?.fallback();
});
paper.addEventListener('compositionstart',()=>{composing=true;send('dirty');});
paper.addEventListener('compositionend',()=>{composing=false;changed();});
paper.addEventListener('input',changed);
paper.addEventListener('click',e=>{if(e.target.closest('a'))e.preventDefault();});
paper.addEventListener('drop',event=>event.preventDefault());
document.getElementById('tools').addEventListener('mousedown',event=>{if(event.target.closest('button')) event.preventDefault();});
// A rejected command is not a failed document save. Flush existing typing
// first, then roll back the entire command if any selected block rejects it.
let readingPositionEpoch=0;
function captureReadingPosition(){
  const view=scrollport.getBoundingClientRect();
  const fragments=new Map();
  const anchors=[...paper.querySelectorAll('[data-source-anchor-id]')].map(node=>{
    const rect=node.getBoundingClientRect(),id=node.getAttribute('data-source-anchor-id');
    const fragment=fragments.get(id)||0;fragments.set(id,fragment+1);
    return {id,fragment,offset:rect.top-view.top,bottom:rect.bottom-view.top};
  }).filter(value=>value.bottom>0);
  // Keep nearby alternatives when an accepted/rejected paragraph disappears.
  return {top:scrollport.scrollTop,left:scrollport.scrollLeft,anchors:anchors.slice(0,8),editor};
}
function restoreReadingPosition(position){
  const epoch=++readingPositionEpoch;
  const restore=()=>{
    refreshZoom();
    const view=scrollport.getBoundingClientRect();
    const nodes=[...paper.querySelectorAll('[data-source-anchor-id]')];
    const saved=position.anchors.find(value=>nodes.some(node=>node.getAttribute('data-source-anchor-id')===value.id));
    const matches=saved?nodes.filter(node=>node.getAttribute('data-source-anchor-id')===saved.id):[];
    const node=saved&&matches[Math.min(saved.fragment,matches.length-1)];
    scrollport.scrollTop=node?scrollport.scrollTop+node.getBoundingClientRect().top-view.top-saved.offset:position.top;
    scrollport.scrollLeft=position.left;
  };
  restore();
  const expectedTop=scrollport.scrollTop,expectedLeft=scrollport.scrollLeft;
  requestAnimationFrame(()=>{
    // Reflow may finish on the next frame; never override a newer user scroll,
    // a subsequent command, or a newly opened document.
    if(epoch===readingPositionEpoch&&editor===position.editor&&Math.abs(scrollport.scrollTop-expectedTop)<1&&Math.abs(scrollport.scrollLeft-expectedLeft)<1)restore();
  });
}
function preserveReadingPosition(action){
  const position=captureReadingPosition();
  try{return action();}finally{restoreReadingPosition(position);}
}
function runEditingCommand(action,{history=false,tableCell=false,singleFormat=false}={}) {
  if(!editor||composing||fatal)return false;
  const readingPosition=history?captureReadingPosition():null;
  if(!history&&!editor.activeBlock?.isConnected){return false;}
  if(tableCell&&!editor.activeBlock?.closest('td,th')){status.textContent='请先点击表格中的单元格，再使用表格操作。';return false;}
  try{flushFocused();}catch(_){fail();return false;}
  const api=editor.exports.DocxSessionBridge;let transaction,formatVersion;
  const replacing=editor.replacing;
  try {
    // ApplyFormat already records and rolls back its own single-paragraph
    // operation. Only multi-paragraph/structural commands need a package batch.
    if(singleFormat&&editor.selectedBlocks?.().length===1)formatVersion=api.GetVersion(editor.handle);
    else if(!history)transaction=api.BeginTransaction(editor.handle);
    // Repainting a command must not submit stale DOM on blur.
    editor.replacing=true;
    action();
    if(transaction!==undefined)api.CommitTransaction(transaction);
    status.textContent='';changed();return true;
  }catch(error) {
    try{if(transaction!==undefined)api.RollbackTransaction(transaction);else if(formatVersion!==undefined&&api.GetVersion(editor.handle)!==formatVersion)editor.undo();editor.reconcile();}
    catch(_){fail();return false;}
    const code=String(error.code||'').replaceAll('_','').toLowerCase();
    status.textContent=code==='trackedoperationunsupported'
      ? '所选内容含有嵌套表格、批注或其他复杂标记，暂不能安全调整结构。已有内容可继续编辑和保存，这项调整请在 Word/WPS 中完成。'
      : ['trackedstructureunsupported','unresolvedstructuralrevision'].includes(code)
        ? '所选内容中有尚未处理的修订，暂不能安全调整结构。请先核对并处理相关修订，已有内容可继续编辑和保存。'
        : '当前选区暂不支持这项操作，请重新选择正文后再试。已有内容可继续编辑和保存。';
    return false;
  }finally{editor.replacing=replacing;if(readingPosition)restoreReadingPosition(readingPosition);}
}
function runToolbarCommand(command){
  if(!editor||composing||fatal)return;
  runEditingCommand(()=>{
    if(['bold','italic','underline'].includes(command))editor.format(command);
    else if(['left','center','right'].includes(command))editor.setAlignment(command);
    else if(['bullet','decimal'].includes(command))editor.toggleList(command);
    else if(command==='table')editor.insertTable(2,2);
    else if(command==='row')editor.insertTableRow('below');
    else editor[command]();
  },{history:['undo','redo'].includes(command),tableCell:command==='row',singleFormat:['bold','italic','underline'].includes(command)});
}
document.getElementById('tools').addEventListener('click',event=>{
  const command=event.target.closest('[data-command]')?.dataset.command;if(command)runToolbarCommand(command);
});
document.getElementById('font').addEventListener('change',event=>{if(event.target.value)runEditingCommand(()=>editor.setFontFamily(event.target.value),{singleFormat:true});});
document.getElementById('size').addEventListener('change',event=>{if(event.target.value)runEditingCommand(()=>editor.setFontSize(Number(event.target.value)),{singleFormat:true});});

window.addEventListener('pagehide',()=>snapshot());

document.addEventListener('pointerdown',event=>{
  document.querySelectorAll('details[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;});
},true);
const pane=document.getElementById('review-pane');
const reviewList=document.getElementById('review-list');
const reviewScroll=document.getElementById('review-scroll');
const reviewConnectors=document.getElementById('review-connectors');
const commentForm=document.getElementById('comment-form');
const wordNamespace='http://schemas.openxmlformats.org/wordprocessingml/2006/main';
let commentRange, commentBlock, reviewItems=[], selectedReview=null, reviewLayoutFrame;
let reviewFingerprint='', reviewVersion, commentTargetVersion, commentTargets=new Map(),commentSpans=new Map(),commentBlockText=new Map();
let revisionCarriers=new WeakMap();
const bridge=()=>editor.exports.DocxSessionBridge;
const listRevisions=()=>JSON.parse(bridge().ListRevisions(editor.handle));
const plainMarkdown=text=>text.replace(/[\\`*_{}\[\]()<>#+!|~]/g,'\\$&');
function showReview(){pane.hidden=false;document.getElementById('show-review').setAttribute('aria-expanded','true');scheduleZoom();scheduleReviewLayout();}
function hideReview(){pane.hidden=true;paper.querySelectorAll('.review-paper-highlights').forEach(layer=>layer.remove());document.getElementById('show-review').setAttribute('aria-expanded','false');reviewConnectors.replaceChildren();scheduleZoom();}
function reviewAction(action){
  if(!editor||composing||fatal)return false;
  return preserveReadingPosition(()=>{
    try{flushFocused();action();editor.reconcile();if(!snapshot())return false;status.textContent='';return true;}
    catch(_){status.textContent='这处修订或批注暂时不能处理，内容已保留，请在 Word/WPS 中核对。';return false;}
  });
}
function addButton(parent,text,action,label=text){
  const b=document.createElement('button');b.type='button';b.textContent=text;b.title=label;b.setAttribute('aria-label',label);
  b.addEventListener('click',event=>{event.stopPropagation();action();});parent.append(b);return b;
}
function renderedAnchor(element){return element.getAttribute('data-source-anchor-id')||element.getAttribute('data-hf-anchor')||editor.anchorIdOf(element);}
function renderedBlocks(){
  return [...paper.querySelectorAll('[data-source-anchor-id],[data-anchor]')].filter(element=>{
    const anchor=renderedAnchor(element);return anchor&&!anchor.includes(':cmt:');
  });
}
// The fixed editor deliberately hides native comment markers. Its read-only XML
// API lets the rail connect the definition's w:id to real paragraph markers,
// including point comments left after a revision is accepted. No text guessing.
function refreshCommentTargets(comments,blocks){
  const version=bridge().GetVersion(editor.handle);
  if(version===commentTargetVersion)return;
  commentTargetVersion=version;commentTargets=new Map();commentSpans=new Map();commentBlockText=new Map();revisionCarriers=new WeakMap();
  if(!comments.length||typeof bridge().RawGetXml!=='function')return;
  const nativeIds=new Map(),parser=new DOMParser();
  for(const comment of comments){
    try{
      const xml=parser.parseFromString(bridge().RawGetXml(editor.handle,comment.anchorId),'application/xml');
      const id=xml.documentElement.getAttributeNS(wordNamespace,'id');
      if(id!==null)nativeIds.set(id,comment.anchorId);
    }catch(_){/* An unlocated comment stays visible without an invented anchor. */}
  }
  const visited=new Set(),active=new Set();
  for(const block of blocks){
    const anchor=renderedAnchor(block);if(!['p','h','li'].includes(anchor.split(':')[0])||visited.has(anchor))continue;visited.add(anchor);
    try{
      const raw=bridge().RawGetXml(editor.handle,anchor);
      if(!active.size&&!raw.includes('commentRangeStart')&&!raw.includes('commentReference'))continue;
      const xml=parser.parseFromString(raw,'application/xml');
      let text='';const spans=new Map([...active].map(id=>[id,{anchor,start:0,end:null}]));
      for(const marker of xml.getElementsByTagNameNS(wordNamespace,'*')){
        const name=marker.localName,id=nativeIds.get(marker.getAttributeNS(wordNamespace,'id'));
        if(['t','delText'].includes(name))text+=reviewText(marker.textContent);
        else if(id&&name==='commentRangeStart'){active.add(id);spans.set(id,{anchor,start:text.length,end:null});}
        else if(id&&name==='commentRangeEnd'){if(spans.has(id))spans.get(id).end=text.length;active.delete(id);}
        else if(id&&name==='commentReference'&&!spans.has(id)&&!commentTargets.has(id))spans.set(id,{anchor,start:text.length,end:text.length});
      }
      commentBlockText.set(anchor,text);
      for(const [id,span] of spans){span.end??=text.length;if(!commentTargets.has(id))commentTargets.set(id,anchor);if(!commentSpans.has(id))commentSpans.set(id,[]);commentSpans.get(id).push(span);}
      if(!active.size&&commentTargets.size===nativeIds.size)break;
    }catch(_){/* Header/footer or unsupported anchors can remain unlocated. */}
  }
  for(const comment of comments)if(!commentTargets.has(comment.anchorId)&&comment.parentAnchorId&&commentTargets.has(comment.parentAnchorId)){commentTargets.set(comment.anchorId,commentTargets.get(comment.parentAnchorId));commentSpans.set(comment.anchorId,commentSpans.get(comment.parentAnchorId));}
}
function reviewText(text){return text.replace(/[\s\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,'');}
function reviewRange(block,start,end,expected){
  const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT),points=[];let text='',node;
  while((node=walker.nextNode())){
    if(node.parentElement.closest('[data-list-marker],[class$="para-mark-del"],a.footnote-ref,a.endnote-ref,a[class$="-backref"]'))continue;
    for(let i=0;i<node.length;i++)if(reviewText(node.data[i])){text+=node.data[i];points.push([node,i]);}
  }
  if(expected!==undefined&&text!==expected||!points.length)return null;
  const range=document.createRange(),first=points[Math.min(start,points.length-1)],last=points[Math.max(0,Math.min(end-1,points.length-1))];
  if(start===end){range.setStart(first[0],first[1]+(start>=points.length?1:0));range.collapse(true);return range;}
  range.setStart(first[0],first[1]);range.setEnd(last[0],last[1]+1);return range;
}
function rangesForReview(item,blocks){
  if(item.kind==='comment')return (commentSpans.get(item.value.anchorId)||[]).flatMap(span=>{const block=blocks.find(element=>renderedAnchor(element)===span.anchor);if(!block)return [];const range=reviewRange(block,span.start,span.end,commentBlockText.get(span.anchor));return range?[range]:[];});
  if(!item.target)return [];const range=reviewRange(item.target,0,Infinity);return range?[range]:[];
}
function reviewTarget(item,blocks){
  const value=item.value;
  const anchors=item.kind==='comment'?[commentTargets.get(value.anchorId)]:[value.anchorId,...(value.affectedAnchors||[]).map(anchor=>anchor.id)];
  const block=blocks.find(element=>anchors.includes(renderedAnchor(element)));
  if(!block)return null;
  if(item.kind==='revision'&&['insert','delete'].includes(value.type)){
    // Repeated deleted text in one paragraph is ambiguous by text alone. Match
    // native revision ids through the verified XML/DOM carrier sequence.
    const tag=value.type==='insert'?'ins':'del';
    if(value.constituentIds?.length){
      let mappings=revisionCarriers.get(block);if(!mappings){mappings=new Map();revisionCarriers.set(block,mappings);}
      if(!mappings.has(tag)){
        const matched=new Map();
        try{
          const xml=new DOMParser().parseFromString(bridge().RawGetXml(editor.handle,renderedAnchor(block)),'application/xml');
          const native=[...xml.getElementsByTagNameNS(wordNamespace,tag)],visible=[...block.querySelectorAll(tag)];
          if(native.length===visible.length&&native.every((node,index)=>reviewText([...node.getElementsByTagNameNS(wordNamespace,'*')].filter(n=>['t','delText'].includes(n.localName)).map(n=>n.textContent).join(''))===reviewText(visible[index].textContent))){
            native.forEach((node,index)=>matched.set(node.getAttributeNS(wordNamespace,'id'),visible[index]));
          }
        }catch(_){/* Keep the paragraph fallback when the carrier sequence cannot be verified. */}
        mappings.set(tag,matched);
      }
      const targets=[...mappings.get(tag)].filter(([id])=>value.constituentIds.includes(id)).map(([,node])=>node);
      if(targets.length)return targets.at(-1);
    }
    const carriers=[...block.querySelectorAll(value.type==='insert'?'ins':'del')].filter(element=>element.textContent===value.text&&(!value.author||!element.dataset.author||element.dataset.author===value.author));
    if(carriers.length===1)return carriers[0];
  }
  return block;
}
// Hidden deletion runs have no box. Anchor their balloon at the adjacent live
// caret position; an entirely deleted paragraph falls back to its paragraph box.
function reviewSourceRect(item){
  const target=item.target;if(!target)return null;
  const own=target.getBoundingClientRect();
  if(own.height||own.width)return own;
  const block=target.closest('[data-source-anchor-id],[data-anchor]');
  if(!block)return own;
  const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT),before=[],after=[];let node;
  while((node=walker.nextNode())){
    if(!node.length||node.parentElement.closest('del,[data-list-marker],[class$="para-mark-del"]'))continue;
    const order=target.compareDocumentPosition(node);
    if(order&Node.DOCUMENT_POSITION_FOLLOWING)after.push(node);
    if(order&Node.DOCUMENT_POSITION_PRECEDING)before.push(node);
  }
  // WebKit can return no rectangles for a collapsed Range. Measure an actual
  // neighbouring glyph, then use its leading/trailing edge as the deletion caret.
  // Prefer the following glyph at a wrapped line boundary, matching the text
  // that now occupies the deletion point. Skip collapsed whitespace/hidden runs.
  for(const [nodes,atEnd] of [[after,false],[before.reverse(),true]])for(const node of nodes){
    const glyphs=[];let offset=0;
    for(const text of node.data){glyphs.push({start:offset,end:offset+text.length});offset+=text.length;}
    if(atEnd)glyphs.reverse();
    for(const glyph of glyphs){
      const range=document.createRange();range.setStart(node,glyph.start);range.setEnd(node,glyph.end);
      const rect=Array.from(range.getClientRects?.()||[]).find(rect=>rect.height>0&&rect.width>0);
      if(!rect)continue;
      const rtl=getComputedStyle(node.parentElement).direction==='rtl';
      const x=atEnd!==rtl?rect.right:rect.left;
      return {left:x,right:x,top:rect.top,bottom:rect.bottom,width:0,height:rect.height};
    }
  }
  return block.getBoundingClientRect();
}
function scheduleReviewLayout(){if(reviewLayoutFrame)return;reviewLayoutFrame=requestAnimationFrame(()=>{reviewLayoutFrame=null;layoutReview();});}
// Position balloons using the same exact range endpoint as their connector.
// A paragraph's top can precede an earlier deletion inside that same paragraph.
function reviewConnectionRect(item){
  const rectangles=reviewLineRects((item.ranges||[]).flatMap(range=>Array.from(range.getClientRects?.()||[])).filter(rect=>rect.width>0&&rect.height>0));
  return rectangles.at(-1)||reviewSourceRect(item);
}
function layoutReview(){
  if(pane.hidden||!editor)return;
  const blocks=renderedBlocks();refreshCommentTargets(reviewItems.filter(item=>item.kind==='comment').map(item=>item.value),blocks);
  const listRect=reviewList.getBoundingClientRect();
  for(const item of reviewItems){
    item.target=reviewTarget(item,blocks);
    item.ranges=rangesForReview(item,blocks);
    const source=item.target?reviewConnectionRect(item):null;
    item.sourceTop=source?source.top-listRect.top:Infinity;
    item.sourceBottom=source?source.bottom-listRect.top:Infinity;
    item.sourceRight=source?source.right:Infinity;
  }
  const ordered=[...reviewItems].sort((a,b)=>a.sourceBottom-b.sourceBottom||a.sourceRight-b.sourceRight||a.order-b.order);
  const focused=document.activeElement;
  ordered.forEach((item,index)=>{if(reviewList.children[index]!==item.row)reviewList.insertBefore(item.row,reviewList.children[index]||null);});
  if(focused?.isConnected&&document.activeElement!==focused)focused.focus({preventScroll:true});
  let bottom=8;
  for(const item of ordered){
    const top=Math.max(bottom,Number.isFinite(item.sourceTop)?item.sourceTop:bottom);
    item.row.style.top=`${Math.round(top)}px`;
    item.positionNote.hidden=!!item.target;
    item.row.setAttribute('aria-label',`${item.kind==='comment'?'批注':'修订'}：${item.value.author||'未署名'}，${item.value.text||'格式或结构修改'}${item.target?'':'，未找到正文位置'}`);
    bottom=top+item.row.offsetHeight+12;
  }
  reviewList.style.height=`${bottom}px`;
  drawReviewHighlights();drawReviewConnectors();
}
// Stable per-author colors, with collision avoidance within this workbench.
const reviewAuthorColors=new Map();
const reviewPalette=['#159d99','#cb3b9e','#397ac3','#b46b16','#8152b8','#b84b4b','#527d24','#4c689d'];
function reviewColor(author){
  const name=(author||'未署名').trim().normalize('NFC');
  if(!reviewAuthorColors.has(name)){
    let hash=2166136261;for(const character of name)hash=Math.imul(hash^character.codePointAt(0),16777619)>>>0;
    const used=new Set(reviewAuthorColors.values());let color;
    for(let i=0;i<reviewPalette.length;i++){const candidate=reviewPalette[(hash+i)%reviewPalette.length];if(!used.has(candidate)){color=candidate;break;}}
    reviewAuthorColors.set(name,color||`hsl(${(hash%360+reviewAuthorColors.size*137.508)%360} 65% 38%)`);
  }
  return reviewAuthorColors.get(name);
}
// Group by line before sorting by x: Chinese and Latin runs have different
// font-box tops even at the same baseline. Their bands share one height.
function reviewLineRects(rectangles){
  const lines=[];
  for(const rect of [...rectangles].sort((a,b)=>a.top-b.top||a.left-b.left)){
    const line=lines.find(line=>Math.min(line.bottom,rect.bottom)-Math.max(line.top,rect.top)>=Math.min(line.height,rect.height)*.6);
    if(line){line.left=Math.min(line.left,rect.left);line.right=Math.max(line.right,rect.right);line.top=Math.min(line.top,rect.top);line.bottom=Math.max(line.bottom,rect.bottom);line.width=line.right-line.left;line.height=line.bottom-line.top;}
    else lines.push({left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,width:rect.width,height:rect.height});
  }
  return lines.sort((a,b)=>a.top-b.top||a.left-b.left);
}
// Paint in each page's own coordinate space. Native/compositor scrolling moves
// text and bands together; scroll handlers only redraw the cross-pane lines.
function drawReviewHighlights(){
  const layers=new Map(),namespace='http://www.w3.org/2000/svg';
  if(!pane.hidden)for(const item of reviewItems){
    if(item.kind!=='comment')continue;
    const byPage=new Map();
    for(const range of item.ranges||[]){
      const element=range.startContainer.nodeType===1?range.startContainer:range.startContainer.parentElement;
      const page=element.closest('.page-box,.docx-body-flow');if(!page)continue;
      if(!byPage.has(page))byPage.set(page,[]);
      byPage.get(page).push(...Array.from(range.getClientRects?.()||[]).filter(rect=>rect.width>0&&rect.height>0));
    }
    for(const [page,rectangles] of byPage){
      if(!layers.has(page))layers.set(page,[]);
      const bounds=page.getBoundingClientRect(),scale=bounds.width/page.offsetWidth;if(!Number.isFinite(scale)||scale<=0)continue;
      for(const rect of reviewLineRects(rectangles))layers.get(page).push({x:(rect.left-bounds.left)/scale-page.clientLeft,y:(rect.top-bounds.top)/scale-page.clientTop,width:rect.width/scale,height:rect.height/scale,color:item.color,selected:item.key===selectedReview});
    }
  }
  for(const old of paper.querySelectorAll('.review-paper-highlights'))if(!layers.has(old.parentElement))old.remove();
  for(const [page,marks] of layers){
    let layer=page.querySelector(':scope > .review-paper-highlights');
    if(!layer){layer=document.createElementNS(namespace,'svg');layer.classList.add('review-paper-highlights');layer.setAttribute('aria-hidden','true');layer.setAttribute('contenteditable','false');page.append(layer);}
    const signature=JSON.stringify(marks);if(layer.dataset.marks===signature)continue;layer.dataset.marks=signature;
    const nodes=marks.map(mark=>{const rect=document.createElementNS(namespace,'rect');for(const key of ['x','y','width','height'])rect.setAttribute(key,String(mark[key]));rect.setAttribute('fill',mark.color);rect.setAttribute('fill-opacity',mark.selected?'.30':'.19');return rect;});layer.replaceChildren(...nodes);
  }
}
function drawReviewConnectors(){
  if(pane.hidden){reviewConnectors.replaceChildren();return;}
  // All vertical geometry lives in the one native scrolling surface. Scrolling
  // never rebuilds paths or moves cards; clipping is handled by the scrollport.
  const area=reviewSurface.getBoundingClientRect(),rail=pane.getBoundingClientRect();
  reviewConnectors.setAttribute('width',String(area.width));reviewConnectors.setAttribute('height',String(area.height));reviewConnectors.setAttribute('viewBox',`0 0 ${area.width} ${area.height}`);
  const paths=[];
  for(const item of reviewItems){
    if(!item.target?.isConnected)continue;
    const source=reviewConnectionRect(item),balloon=item.row.getBoundingClientRect();
    if(source.left>=rail.left)continue;
    const x=Math.min(rail.left-7,source.right)-area.left,y=source.bottom-area.top;
    const rowX=balloon.left-area.left,rowY=balloon.top+17-area.top;
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.style.stroke=item.color;
    path.setAttribute('d',`M ${x} ${y} H ${rail.left-area.left-5} L ${rowX-5} ${rowY} H ${rowX}`);
    if(item.key===selectedReview)path.classList.add('is-selected');paths.push(path);
  }
  reviewConnectors.replaceChildren(...paths);
}
function selectReview(item,scroll=true){
  selectedReview=item.key;
  for(const row of reviewItems)row.row.classList.toggle('is-selected',row===item);
  if(scroll&&item.target?.isConnected){
    const rect=reviewConnectionRect(item),view=scrollport.getBoundingClientRect();
    scrollport.scrollTop+=rect.top-view.top-64;
    if(rect.left<view.left+12)scrollport.scrollLeft+=rect.left-view.left-24;
    else if(rect.right>view.right-12&&rect.width<view.width)scrollport.scrollLeft+=rect.right-view.right+24;
  }
  drawReviewHighlights();drawReviewConnectors();
}
function reviewDate(raw){
  if(!raw)return null;
  const value=new Date(raw);if(!Number.isFinite(value.getTime()))return null;
  const time=document.createElement('time');time.className='review-date';time.dateTime=raw;
  time.textContent=new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(value);
  time.title=value.toLocaleString('zh-CN',{hour12:false});return time;
}
function makeReviewRow(kind,value,index){
  const row=document.createElement('article'),key=`${kind}:${kind==='comment'?value.anchorId:value.id}`;
  row.className=`review-balloon ${kind==='comment'?'is-comment':`revision-${value.type}`}${value.resolved?' resolved':''}`;
  row.tabIndex=0;row.dataset.reviewKey=key;
  const color=reviewColor(value.author);row.style.setProperty('--review-color',color);
  const item={key,kind,value,row,color,order:index,target:null};
  const header=document.createElement('header');header.className='review-balloon-header';
  if(kind==='comment'){
    const avatar=document.createElement('span');avatar.className='review-avatar';avatar.setAttribute('aria-hidden','true');
    avatar.textContent=Array.from(value.initials||value.author||'评').slice(0,/^[a-z]/i.test(value.initials||value.author||'')?2:1).join('').toUpperCase();header.append(avatar);
  }
  const label=document.createElement('span');label.className='review-author';label.textContent=value.author||'未署名';label.title=label.textContent;header.append(label);
  if(value.resolved){const resolved=document.createElement('span');resolved.className='review-resolved';resolved.textContent='已解决';label.append(resolved);}
  const date=reviewDate(value.dateUtc||value.date);if(date)header.append(date);
  if(kind==='revision'){
    const actions=document.createElement('span');actions.className='review-actions';
    if(!value.resolutionStatus||value.resolutionStatus==='supported'){
      addButton(actions,'✓',()=>reviewAction(()=>bridge().AcceptRevision(editor.handle,value.id)),'接受此处修订');
      addButton(actions,'×',()=>reviewAction(()=>bridge().RejectRevision(editor.handle,value.id)),'拒绝此处修订').className='review-reject';
    }
    header.append(actions);
  }else{
    const menu=document.createElement('details');menu.className='review-comment-menu';
    const summary=document.createElement('summary');summary.textContent='···';summary.title='批注操作';summary.setAttribute('aria-label','批注操作');
    summary.addEventListener('click',event=>{event.stopPropagation();selectReview(item,false);});
    menu.addEventListener('keydown',event=>{if(event.key==='Escape'){menu.open=false;summary.focus();event.stopPropagation();}});
    const actions=document.createElement('div');
    addButton(actions,'编辑批注',()=>{
      menu.open=false;
      const form=document.createElement('form');form.className='review-comment-edit';
      const input=document.createElement('textarea');input.value=value.text||'';input.maxLength=4000;input.rows=4;input.setAttribute('aria-label','编辑批注内容');
      const save=document.createElement('button');save.type='submit';save.textContent='保存';
      const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消';
      cancel.addEventListener('click',event=>{event.stopPropagation();form.remove();text.hidden=false;summary.focus();});
      form.addEventListener('click',event=>event.stopPropagation());
      form.addEventListener('submit',event=>{event.preventDefault();event.stopPropagation();if(!input.value.trim())return;
        if(reviewAction(()=>bridge().UpdateComment(editor.handle,value.anchorId,plainMarkdown(input.value)))){form.remove();text.hidden=false;}
      });
      form.append(input,save,cancel);text.hidden=true;text.after(form);input.focus();
    });
    addButton(actions,value.resolved?'重新打开':'解决批注',()=>reviewAction(()=>editor.setCommentResolved(value.anchorId,!value.resolved)));
    addButton(actions,'删除批注',()=>reviewAction(()=>bridge().RemoveComment(editor.handle,value.anchorId)));
    menu.append(summary,actions);header.append(menu);
  }
  const text=document.createElement('p');
  text.textContent=kind==='revision'?`${({insert:'插入',delete:'删除',move:'移动',format:'格式',structure:'结构'})[value.type]||'修订'}：${value.text||'格式或结构修改'}`:value.text;
  row.append(header,text);
  if(kind==='revision'&&value.resolutionStatus&&value.resolutionStatus!=='supported'){const note=document.createElement('p');note.className='review-position-note';note.textContent='此处请在 Word/WPS 中处理。';row.append(note);}
  item.positionNote=document.createElement('span');item.positionNote.className='review-position-note';item.positionNote.textContent='未找到正文位置';item.positionNote.hidden=true;row.append(item.positionNote);
  row.addEventListener('click',event=>{if(event.target.closest('button,summary'))return;selectReview(item);row.focus({preventScroll:true});});
  row.addEventListener('keydown',event=>{if(event.target===row&&['Enter',' '].includes(event.key)){event.preventDefault();selectReview(item);}});
  row.classList.toggle('is-selected',key===selectedReview);return item;
}
// Color newly typed text without replacing the live editing DOM or moving the caret.
// The saved OOXML supplies exact ranges; deleted text and review chrome are excluded.
function refreshInsertedColors(revisions){
  if(!globalThis.CSS?.highlights||typeof Highlight==='undefined')return;
  const ranges=[],anchors=new Set(revisions.filter(value=>value.type==='insert').flatMap(value=>[value.anchorId,...(value.affectedAnchors||[]).map(item=>item.id)]));
  for(const block of paper.querySelectorAll('[data-anchor][contenteditable="true"]')){
    const anchor=renderedAnchor(block);if(!anchors.has(anchor))continue;
    try{
      const xml=new DOMParser().parseFromString(bridge().RawGetXml(editor.handle,anchor),'application/xml');
      let expected='';const spans=[];
      for(const text of xml.getElementsByTagNameNS(wordNamespace,'t')){
        let parent=text.parentElement,inserted=false,deleted=false;
        while(parent){if(parent.namespaceURI===wordNamespace){inserted||=parent.localName==='ins';deleted||=parent.localName==='del';}parent=parent.parentElement;}
        if(deleted)continue;
        const start=expected.length;expected+=reviewText(text.textContent);if(inserted&&expected.length>start)spans.push([start,expected.length]);
      }
      const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT),points=[];let actual='',node;
      while((node=walker.nextNode())){
        if(node.parentElement.closest('del,[data-list-marker],[class$="para-mark-del"],a.footnote-ref,a.endnote-ref,a[class$="-backref"]'))continue;
        for(let i=0;i<node.length;i++)if(reviewText(node.data[i])){actual+=node.data[i];points.push([node,i]);}
      }
      if(actual!==expected)continue;
      for(const [start,end] of spans){const range=document.createRange();range.setStart(...points[start]);range.setEnd(points[end-1][0],points[end-1][1]+1);ranges.push(range);}
    }catch(_){/* Canonical ins elements retain their color when a range cannot be mapped. */}
  }
  CSS.highlights.set('word-insertions',new Highlight(...ranges));
}
function refreshReview(){
  if(!editor)return;
  const version=bridge().GetVersion(editor.handle);if(version===reviewVersion){scheduleReviewLayout();return;}reviewVersion=version;
  const revisions=listRevisions(),comments=editor.listComments();
  const visibleRevisions=revisions.filter(value=>value.type!=='insert');
  refreshInsertedColors(revisions);
  document.getElementById('show-review').textContent=`修订 ${revisions.length} · 批注 ${comments.length}`;
  document.getElementById('review-count').textContent=`${visibleRevisions.length+comments.length} 条`;
  const fingerprint=JSON.stringify([revisions,comments]);
  if(fingerprint!==reviewFingerprint){
    [...new Set([...revisions,...comments].map(value=>value.author||'未署名'))].sort().forEach(reviewColor);
    reviewFingerprint=fingerprint;reviewItems=[...visibleRevisions.map((value,index)=>makeReviewRow('revision',value,index)),...comments.map((value,index)=>makeReviewRow('comment',value,visibleRevisions.length+index))];
    reviewList.replaceChildren(...reviewItems.map(item=>item.row));
    if(!reviewItems.length){const empty=document.createElement('p');empty.className='review-empty';empty.textContent=revisions.length?'新增内容已在正文中标色，可在正文右键处理。':'当前没有修订或批注。选中文字后可新建批注。';reviewList.append(empty);}
    if(!reviewItems.some(item=>item.key===selectedReview))selectedReview=null;
  }
  scheduleReviewLayout();
}
let connectorFrame;
function scheduleReviewConnectors(){if(connectorFrame)return;connectorFrame=requestAnimationFrame(()=>{connectorFrame=null;drawReviewConnectors();});}
let reviewScrollLeft=0;
scrollport.addEventListener('scroll',()=>{
  // Horizontal scrolling changes the sticky rail's x coordinate only.
  if(scrollport.scrollLeft!==reviewScrollLeft){reviewScrollLeft=scrollport.scrollLeft;scheduleReviewConnectors();}
},{passive:true});
new ResizeObserver(scheduleReviewLayout).observe(reviewScroll);
new ResizeObserver(scheduleReviewLayout).observe(commentForm);
document.getElementById('close-review').addEventListener('click',hideReview);
document.getElementById('tracking').addEventListener('click',()=>reviewAction(()=>{
  tracking=!tracking;bridge().SetTrackedChanges(editor.handle,tracking?1:0);
  const button=document.getElementById('tracking');button.textContent=tracking?'修订：开':'修订：关';button.setAttribute('aria-pressed',String(tracking));
}));
document.getElementById('show-review').addEventListener('click',()=>{if(pane.hidden){showReview();refreshReview();}else hideReview();});
document.getElementById('new-comment').addEventListener('click',()=>{
  if(!editor?.activeBlock||composing){status.textContent='请先在正文中选择文字或放置光标。';return;}
  const selection=getSelection();commentRange=selection?.rangeCount?selection.getRangeAt(0).cloneRange():null;commentBlock=editor.activeBlock;
  showReview();commentForm.hidden=false;document.getElementById('comment-text').focus();
});
document.getElementById('cancel-comment').addEventListener('click',()=>{commentForm.hidden=true;document.getElementById('comment-text').value='';scheduleReviewLayout();});
document.getElementById('save-comment').addEventListener('click',()=>{
  const input=document.getElementById('comment-text');if(!input.value.trim())return;
  const saved=reviewAction(()=>{if(!commentBlock?.isConnected)throw new Error('selection_changed');editor.activeBlock=commentBlock;commentBlock.focus();if(commentRange){const selection=getSelection();selection.removeAllRanges();selection.addRange(commentRange);}editor.addComment(plainMarkdown(input.value),author);});
  if(saved){commentForm.hidden=true;input.value='';}
});

function applyReview(plan,requestId){
  if(!editor||composing||fatal){send('request-error',{requestId,message:'请先完成当前输入。'});return;}
  batching=true;
  try{
    flushFocused();const current=captureBytes(),expected=Uint8Array.from(atob(plan.source),c=>c.charCodeAt(0));
    if(current.length!==expected.length||current.some((v,i)=>v!==expected[i]))throw new Error('当前文档已改动，请按当前文档重新审查。');
    const b=bridge(),h=editor.handle;let applied=0,skipped=0;
    const entries=plan.opinions.map(opinion=>{
      const pattern=opinion.anchor_text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
      const matches=JSON.parse(b.Grep(h,pattern,JSON.stringify({scope:1,contextChars:0})));
      const match=matches.length===1&&(opinion.occurrence==null||opinion.occurrence===0)?matches[0]:Number.isInteger(opinion.occurrence)?matches[opinion.occurrence]:null;
      return {opinion,match};
    });
    for(const entry of entries)if(entry.match)entry.overlap=entries.some(other=>other!==entry&&other.match&&other.match.enclosingAnchor.id===entry.match.enclosingAnchor.id&&other.match.span.start<entry.match.span.start+entry.match.span.length&&entry.match.span.start<other.match.span.start+other.match.span.length);
    entries.sort((a,b)=>(b.match?.span.start??0)-(a.match?.span.start??0));
    const anchors=new Map();
    b.SetTrackedChanges(h,1);b.SetRevisionAuthor(h,plan.author);
    for(const {opinion,match,overlap} of entries){
      if(!match||overlap){skipped++;continue;}
      const original=match.enclosingAnchor.id,anchor=anchors.get(original)||original;
      const transaction=b.BeginTransaction(h);
      try{
        let modified;
        if(opinion.replacement_text!=null&&opinion.replacement_text!==opinion.anchor_text){
          const previous=new Set(listRevisions().map(r=>r.id));
          const replacement=opinion.replacement_text.replace(/\r\n?/g,'\n');
          const result=JSON.parse(b.ReplaceTextAtSpan(h,anchor,match.span.start,match.span.length,replacement.replace(/[\n\t]/g,'')));
          modified=result.modified?.find(r=>['p','h','li'].includes(r.kind));
          let offset=match.span.start;
          for(const char of replacement){
            if(char==='\n'||char==='\t')b.InsertKeyboardControl(h,modified?.id||anchor,offset,char==='\n'?'br':'tab');
            offset+=char.length;
          }
          const fresh=listRevisions().filter(r=>!previous.has(r.id));const target=fresh.find(r=>r.type==='insert')||fresh[0];
          if(!target)throw new Error('missing_revision');
          b.AddCommentToRevision(h,target.id,plan.author,'',new Date().toISOString(),plainMarkdown(opinion.comment));
        }else b.AddComment(h,anchor,JSON.stringify(match.span),plan.author,'',new Date().toISOString(),plainMarkdown(opinion.comment));
        b.CommitTransaction(transaction);if(modified)anchors.set(original,modified.id);applied++;
      }catch(_){b.RollbackTransaction(transaction);skipped++;}
    }
    b.SetTrackedChanges(h,tracking?1:0);b.SetRevisionAuthor(h,author);
    editor.reconcile();showReview();
    snapshot(requestId,{review_id:plan.review_id,applied,skipped});
  }catch(error){send('request-error',{requestId,message:error instanceof Error?error.message:'修订和批注未能写入。'});}
  finally{
    bridge().SetTrackedChanges(editor.handle,tracking?1:0);
    bridge().SetRevisionAuthor(editor.handle,author);
    batching=false;
  }
}
function applyAiEdit(plan,requestId){
  if(!editor||composing||fatal){send('request-error',{requestId,message:'请先完成当前输入。'});return;}
  batching=true;let transaction;
  try{
    flushFocused();const selection=plan.selection;
    const blocks=[...paper.querySelectorAll('[data-anchor][contenteditable="true"]')].filter(n=>!n.closest('.page-header,.page-footer,.docx-header,.docx-footer'));
    const clean=node=>{const clone=node.cloneNode(true);clone.querySelectorAll('del,[class$="para-mark-del"],[data-list-marker],a.footnote-ref,a.endnote-ref').forEach(n=>n.remove());clone.querySelectorAll('br').forEach(n=>n.replaceWith('\n'));clone.querySelectorAll('[data-docx-tab]').forEach(n=>n.replaceWith('\t'));return (clone.textContent||'').replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,'');};
    const texts=blocks.map(n=>n.dataset.vlEmptyPlaceholder==='true'&&clean(n)==='\u00a0'?'':clean(n));
    const selected=texts.slice(selection.start,selection.end+1).map((t,i)=>t.slice(i===0?selection.from:0,i===selection.end-selection.start?selection.to:undefined)).join('\n');
    if(texts.length!==selection.texts.length||texts.some((t,i)=>t!==selection.texts[i])||selected!==plan.before_text)throw new Error('文档或选中文字已改变，请重新选择后再试。');
    const b=bridge(),h=editor.handle,anchor=editor.anchorIdOf(blocks[selection.start]);
    if(selection.start!==selection.end){
      const selectedBlocks=blocks.slice(selection.start,selection.end+1);
      if(selectedBlocks.some(block=>block.closest('table'))||selectedBlocks.some((block,i)=>i>0&&block.previousElementSibling!==selectedBlocks[i-1]))throw new Error('跨表格或复杂结构的文字暂不能直接修改，请缩小选区。');
      transaction=b.BeginTransaction(h);b.SetTrackedChanges(h,tracking?1:0);b.SetRevisionAuthor(h,author+' · AI');
      const ids=selectedBlocks.map(block=>editor.anchorIdOf(block));
      for(let i=ids.length-1;i>=0;i--){const text=texts[selection.start+i],from=i===0?selection.from:0,to=i===ids.length-1?selection.to:text.length;
        const result=JSON.parse(b.SyncKeyboardText(h,ids[i],text.slice(0,from)+(i===0?plan.after_text.replace(/\r\n?/g,'\n'):'')+text.slice(to)));if(!result.success)throw new Error('所选内容暂不能安全修改，请缩小选区。');}
      for(let i=ids.length-2;i>=0;i--)b.MergeParagraphs(h,ids[i],ids[i+1]);
      b.CommitTransaction(transaction);transaction=null;editor.reconcile();showReview();
      const bytes=captureBytes();send('snapshot',{bytes,requestId,aiEditId:plan.id});return;
    }
    const pattern=plan.before_text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    const matches=JSON.parse(b.Grep(h,pattern,JSON.stringify({scope:1,contextChars:0})));
    const match=matches.find(m=>m.enclosingAnchor.id===anchor&&m.span.start===selection.from&&m.span.length===plan.before_text.length);
    if(!match)throw new Error('选中文字无法安全定位，请重新选择后再试。');
    transaction=b.BeginTransaction(h);b.SetTrackedChanges(h,tracking?1:0);b.SetRevisionAuthor(h,author+' · AI');
    const replacement=plan.after_text.replace(/\r\n?/g,'\n');
    const result=JSON.parse(b.ReplaceTextAtSpan(h,anchor,match.span.start,match.span.length,replacement.replace(/[\n\t]/g,'')));
    const target=result.modified?.find(r=>['p','h','li'].includes(r.kind))?.id||anchor;
    let offset=match.span.start;for(const char of replacement){if(char==='\n'||char==='\t')b.InsertKeyboardControl(h,target,offset,char==='\n'?'br':'tab');offset+=char.length;}
    b.CommitTransaction(transaction);transaction=null;editor.reconcile();showReview();
    const bytes=captureBytes();send('snapshot',{bytes,requestId,aiEditId:plan.id});
  }catch(error){if(transaction)bridge().RollbackTransaction(transaction);editor.reconcile();send('request-error',{requestId,message:error instanceof Error?error.message:'修改未写入，请重新选择后重试。'});}
  finally{bridge().SetTrackedChanges(editor.handle,tracking?1:0);bridge().SetRevisionAuthor(editor.handle,author);batching=false;}
}
function highlightDraftRange(value,locate){
  if(!CSS.highlights)return;CSS.highlights.delete('draft-proposal');
  if(!value||!editor||fatal)return;
  const blocks=[...paper.querySelectorAll('[data-anchor][contenteditable="true"]')];
  const clean=node=>{const clone=node.cloneNode(true);clone.querySelectorAll('del,[class$="para-mark-del"],[data-list-marker],a.footnote-ref,a.endnote-ref').forEach(n=>n.remove());clone.querySelectorAll?.('br').forEach(n=>n.replaceWith('\n'));clone.querySelectorAll?.('[data-docx-tab]').forEach(n=>n.replaceWith('\t'));return (clone.textContent||'').replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,'');};
  if(blocks.length!==value.texts.length||blocks.some((b,i)=>clean(b)!==value.texts[i]))return;
  const point=(block,at)=>{const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT);let node;while(node=walker.nextNode()){if(node.parentElement.closest('del,[class$="para-mark-del"],[data-list-marker],a.footnote-ref,a.endnote-ref'))continue;if(at<=node.textContent.length)return [node,at];at-=node.textContent.length;}return [block,block.childNodes.length];};
  try{const first=blocks[value.start],last=blocks[value.end],range=document.createRange();range.setStart(...point(first,value.from));range.setEnd(...point(last,value.to));CSS.highlights.set('draft-proposal',new Highlight(range));if(locate)first.scrollIntoView({block:'center'});}catch(_){}
}
function installDraftSelection(actionLabel){
  const clean=node=>{const clone=node.cloneNode(true);clone.querySelectorAll?.('del,[class$="para-mark-del"],[data-list-marker],a.footnote-ref,a.endnote-ref').forEach(n=>n.remove());clone.querySelectorAll?.('br').forEach(n=>n.replaceWith('\n'));clone.querySelectorAll?.('[data-docx-tab]').forEach(n=>n.replaceWith('\t'));return (clone.textContent||'').replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,'');};
  const blockOf=node=>(node?.nodeType===1?node:node?.parentElement)?.closest('[data-anchor][contenteditable="true"]');
  let pending=null;
  const action=actionLabel?document.createElement('button'):null;
  if(action){action.type='button';action.className='word-selection-action';action.textContent=actionLabel;action.hidden=true;action.addEventListener('mousedown',event=>event.preventDefault());action.addEventListener('click',()=>{if(pending)send('draft-selection',{selection:pending});action.hidden=true;});document.body.append(action);scrollport.addEventListener('scroll',()=>{action.hidden=true;},{passive:true});}
  document.addEventListener('selectionchange',()=>{
    const selection=getSelection();
    if(!selection?.rangeCount||selection.isCollapsed){if(action)action.hidden=true;return;}
    const range=selection.getRangeAt(0),first=blockOf(range.startContainer),last=blockOf(range.endContainer);
    if(!first||!last||!paper.contains(first)||!paper.contains(last)){if(action)action.hidden=true;return;}
    const blocks=[...paper.querySelectorAll('[data-anchor][contenteditable="true"]')].filter(n=>!n.closest('.page-header,.page-footer,.docx-header,.docx-footer'));
    const start=blocks.indexOf(first),end=blocks.indexOf(last);if(start<0||end<start)return;
    const offset=(block,node,at)=>{const prefix=document.createRange();prefix.selectNodeContents(block);prefix.setEnd(node,at);return clean(prefix.cloneContents()).length;};
    const texts=blocks.map(n=>n.dataset.vlEmptyPlaceholder==='true'&&clean(n)==='\u00a0'?'':clean(n));
    if(texts.join('').length>500000)return;
    pending={texts,start,end,from:offset(first,range.startContainer,range.startOffset),to:offset(last,range.endContainer,range.endOffset)};
    if(!action){send('draft-selection',{selection:pending});return;}
    const rect=range.getBoundingClientRect();action.hidden=false;
    action.style.left=`${Math.max(8,Math.min(rect.left,innerWidth-action.offsetWidth-8))}px`;
    action.style.top=`${Math.max(8,rect.top-action.offsetHeight-8)}px`;
  });
}
parent.postMessage({type:'word-editor-loaded'},location.origin);

window.installWordGuides({paper,scrollport,surface:reviewSurface,button:document.getElementById('show-formatting-marks')});
