/* Compact paragraph/table controls for the embedded Word editor. Formatting
 * targets are canonical anchors, never the selection inside a toolbar input. */
'use strict';
window.installWordLayout = function(editor, paper, options) {
  const api=editor.exports.DocxSessionBridge;
  const paragraphPanel=document.getElementById('paragraph-panel'),tablePanel=document.getElementById('table-panel');
  const paragraphButton=document.getElementById('paragraph-open'),tableButton=document.getElementById('table-open');
  const editable='[data-anchor][contenteditable="true"]';
  const chrome='del, [data-list-marker], [class$="para-mark-del"], a.footnote-ref, a.endnote-ref, a[class$="-backref"]';
  const listeners=[],dirty=new Set();
  let current=null,bookmark=null,lastSelection=null,version=null,tableContext=null,restoring=false;
  const on=(node,type,handler,capture=false)=>{node.addEventListener(type,handler,capture);listeners.push(()=>node.removeEventListener(type,handler,capture));};
  const parse=value=>typeof value==='string'?JSON.parse(value):value;
  const byId=id=>[...paper.querySelectorAll(editable)].find(node=>editor.anchorIdOf(node)===id);
  const blockOf=node=>(node?.nodeType===1?node:node?.parentElement)?.closest(editable);
  const readVersion=()=>api.GetVersion(editor.handle);
  function element(tag,text,className){const node=document.createElement(tag);if(text)node.textContent=text;if(className)node.className=className;return node;}
  function button(text,id,action){const node=element('button',text);node.type='button';node.id=id;on(node,'click',action);return node;}
  function announce(message){options.status.textContent=message;}
  function error(panel,message){panel.querySelector('.layout-error').textContent=message;}
  const directionMarks=/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g;
  function contentText(fragment){
    const copy=fragment.cloneNode(true);copy.querySelectorAll?.(chrome).forEach(node=>node.remove());
    copy.querySelectorAll?.('br').forEach(node=>node.replaceWith('\n'));
    copy.querySelectorAll?.('[data-docx-tab]').forEach(node=>node.replaceWith('\t'));
    return copy.textContent.replace(directionMarks,'');
  }
  function offsetIn(block,node,offset){
    const range=document.createRange();range.selectNodeContents(block);range.setEnd(node,offset);
    const text=contentText(range.cloneContents());
    return text.length-(block.dataset.vlEmptyPlaceholder==='true'&&text.includes('\u00a0')?1:0);
  }
  function pointAt(block,offset){
    if(block.dataset.vlEmptyPlaceholder==='true'){
      const placeholder=contentText(block).indexOf('\u00a0');if(placeholder>=0&&offset>=placeholder)offset++;
    }
    let left=offset,answer=null;
    function walk(node){
      if(answer)return;
      if(node.nodeType===1&&node.matches(chrome))return;
      if(node.nodeType===3){
        const text=node.data.replace(directionMarks,'');
        if(left<=text.length){
          let raw=0,seen=0;while(raw<node.data.length){const mark=/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/.test(node.data[raw]);if(!mark&&seen===left)break;if(!mark)seen++;raw++;}answer=[node,raw];
        }else left-=text.length;return;
      }
      if(node.nodeType===1&&(node.tagName==='BR'||node.hasAttribute('data-docx-tab'))){
        if(left<=1){const parent=node.parentNode,index=[...parent.childNodes].indexOf(node);answer=[parent,index+(left?1:0)];}else left--;return;
      }
      [...node.childNodes].forEach(walk);
    }
    walk(block);return answer||[block,block.childNodes.length];
  }
  function capture(){
    const selection=getSelection();if(!selection?.rangeCount)return null;
    const range=selection.getRangeAt(0);
    if(!paper.contains(range.startContainer)||!paper.contains(range.endContainer))return null;
    let first=blockOf(range.startContainer),last=blockOf(range.endContainer);
    const blocks=[...paper.querySelectorAll(editable)];
    const intersecting=blocks.filter(block=>range.intersectsNode(block));
    first=first||intersecting[0];last=last||intersecting.at(-1);
    if(!first||!last)return null;
    const readOnly=node=>node.closest('[contenteditable="false"]')||[...(node.closest('tr')?.classList||[])].some(name=>name.endsWith('rev-row-del'));
    if(readOnly(first)||readOnly(last))return null;
    const ids=blocks.slice(blocks.indexOf(first),blocks.indexOf(last)+1).map(node=>editor.anchorIdOf(node));
    if(ids.some(id=>!id))return null;
    const focused=blockOf(selection.focusNode),active=focused&&editor.anchorIdOf(focused);
    const end=last.contains(range.endContainer)?offsetIn(last,range.endContainer,range.endOffset):offsetIn(last,last,last.childNodes.length);
    return {ids,targetIds:ids.length>1&&end===0?ids.slice(0,-1):ids,start:first.contains(range.startContainer)?offsetIn(first,range.startContainer,range.startOffset):0,end,
      backwards:selection.anchorNode!==range.startContainer||selection.anchorOffset!==range.startOffset,
      active:ids.includes(active)?active:ids[0]};
  }
  function restore(saved,focus=true){
    if(!saved)return false;
    let first=byId(saved.ids[0]),last=byId(saved.ids.at(-1));
    if(!first||!last||saved.ids.some(id=>!byId(id)))return false;
    restoring=true;
    try{
      const selection=getSelection();
      editor.activeBlock=byId(saved.active)||first;
      if(focus)editor.activeBlock.focus({preventScroll:true});
      first=byId(saved.ids[0]);last=byId(saved.ids.at(-1));if(!first||!last)return false;
      const start=pointAt(first,saved.start),end=pointAt(last,saved.end);
      const range=document.createRange();range.setStart(...start);range.setEnd(...end);selection.removeAllRanges();selection.addRange(range);
      if(saved.backwards&&selection.setBaseAndExtent)selection.setBaseAndExtent(...end,...start);
      return true;
    }finally{restoring=false;}
  }
  function close({restoreSelection=true,focusButton=false,focusDocument=true}={}){
    const old=current;if(!old)return;
    current=null;old.panel.hidden=true;old.button.setAttribute('aria-expanded','false');
    if(restoreSelection)restore(bookmark,!focusButton&&focusDocument);
    if(focusButton)old.button.focus({preventScroll:true});
  }
  function validTarget(panel){
    if(paper.inert||readVersion()!==version||!restore(bookmark)){
      error(panel,'正文已变化，请关闭面板并重新选择要编辑的位置。');return false;
    }
    return true;
  }
  function mutate(panel,action,{preserve=true}={}){
    if(!validTarget(panel))return false;
    let succeeded=false;
    try{succeeded=options.runCommand(action)===true;}catch{error(panel,'这处操作未完成，正文保持原状。');return false;}
    if(!succeeded){
      if(paper.inert){close({restoreSelection:false});return false;}
      error(panel,options.status.textContent||'这处操作未完成，请调整设置后重试。');options.status.textContent='';version=readVersion();return false;
    }
    close({restoreSelection:preserve});
    if(!preserve&&editor.activeBlock?.isConnected)editor.activeBlock.focus({preventScroll:true});
    lastSelection=capture();return true;
  }
  function checked(result){const value=parse(result);if(!value?.success)throw new Error(value?.error?.code||'layout_edit_rejected');return value;}
  function rebuild(){
    const block=byId(bookmark.active)||byId(bookmark.ids[0]);
    const index=editor.blockIndex?editor.blockIndex(block):[...paper.querySelectorAll(editable)].indexOf(block);
    editor.reconcile(index,false);
    const remaining=[...paper.querySelectorAll(editable)];
    const target=byId(bookmark.active)||remaining[Math.min(Math.max(index,0),remaining.length-1)];
    if(target){
      editor.activeBlock=target;target.focus({preventScroll:true});
      const range=document.createRange();range.selectNodeContents(target);range.collapse(true);getSelection().removeAllRanges();getSelection().addRange(range);
    }
  }
  function panelShell(panel,title){
    panel.replaceChildren();panel.hidden=true;panel.setAttribute('role','dialog');panel.setAttribute('aria-label',title);
    panel.append(element('h3',title,'layout-panel-heading'));
    const hint=element('p','','layout-hint'),fields=element('div','','layout-fields'),failure=element('p','','layout-error');
    failure.setAttribute('role','alert');panel.append(hint,fields,failure);return {hint,fields};
  }
  function numberField(parent,id,label,{min=0,max=1000,step='any',value=''}={}){
    const wrap=element('label',label,'layout-field'),input=element('input');input.type='number';input.id=id;input.min=String(min);input.max=String(max);input.step=String(step);input.value=String(value);wrap.htmlFor=id;wrap.append(input);parent.append(wrap);return input;
  }
  function selectField(parent,id,label,values){
    const wrap=element('label',label,'layout-field'),select=element('select');select.id=id;wrap.htmlFor=id;
    values.forEach(([value,text])=>{const option=element('option',text);option.value=value;select.append(option);});wrap.append(select);parent.append(wrap);return select;
  }
  function readNumber(input,{integer=false,min=Number(input.min),max=Number(input.max)}={}){
    const value=Number(input.value);
    if(input.value.trim()===''||!Number.isFinite(value)||value<min||value>max||(integer&&!Number.isInteger(value)))throw new Error(`${input.parentElement.firstChild.textContent}请输入 ${min}–${max}${integer?' 的整数':''}。`);
    return value;
  }
  const paragraph=panelShell(paragraphPanel,'段落设置');
  const fields={
    alignment:selectField(paragraph.fields,'paragraph-alignment','对齐方式',[['','混合，保持原样'],['original','原文设置'],['left','左对齐'],['center','居中'],['right','右对齐'],['justify','两端对齐'],['distributed','分散对齐']]),
    before:numberField(paragraph.fields,'paragraph-before','段前'),
    after:numberField(paragraph.fields,'paragraph-after','段后'),
    left:numberField(paragraph.fields,'paragraph-left','左侧缩进',{min:-50,max:50}),
    right:numberField(paragraph.fields,'paragraph-right','右侧缩进',{min:-50,max:50}),
    special:selectField(paragraph.fields,'paragraph-special','特殊缩进',[['','混合，保持原样'],['original','原文设置'],['none','无'],['first','首行缩进'],['hanging','悬挂缩进']]),
    specialValue:numberField(paragraph.fields,'paragraph-special-value','缩进值',{max:50}),
    lineRule:selectField(paragraph.fields,'paragraph-line-rule','行距',[['','混合，保持原样'],['original','原文设置'],['single','单倍行距'],['oneHalf','1.5 倍行距'],['double','双倍行距'],['auto','多倍行距'],['exact','固定值（磅）'],['atLeast','最小值（磅）']]),
    lineValue:numberField(paragraph.fields,'paragraph-line-value','行距值',{min:.1,max:1000}),
  };
  const units={};
  for(const name of ['before','after','left','right','specialValue']) {
    const input=fields[name],unit=element('select');unit.id=`${input.id}-unit`;unit.setAttribute('aria-label',`${input.parentElement.firstChild.textContent}单位`);
    const spacing=['before','after'].includes(name);
    for(const [value,label] of spacing?[['pt','磅'],['line','行']]:[['cm','厘米'],['char','字符']]) {const item=element('option',label);item.value=value;unit.append(item);}
    input.parentElement.append(unit);units[name]=unit;
    on(unit,'change',()=>{dirty.add(name);error(paragraphPanel,'');});
  }
  fields.direction=selectField(paragraph.fields,'paragraph-direction','文字方向',[['','混合，保持原样'],['false','从左向右'],['true','从右向左']]);
  fields.outline=selectField(paragraph.fields,'paragraph-outline','大纲级别',[['','混合，保持原样'],['9','正文文本'],...Array.from({length:9},(_,i)=>[String(i),`${i+1} 级`])]);
  for(const [title,names] of [['常规',['alignment','outline','direction']],['缩进',['left','special','right','specialValue']],['间距',['before','lineRule','after','lineValue']]]) {
    const group=element('fieldset','','paragraph-group');group.append(element('legend',title));const grid=element('div','','layout-fields');group.append(grid);for(const name of names)grid.append(fields[name].parentElement);paragraph.fields.append(group);
  }
  const borders=element('fieldset','','paragraph-group');borders.append(element('legend','段落边框'));
  const removeBottom=button('移除下边框','paragraph-remove-bottom-border',()=>{dirty.add('removeBottomBorder');removeBottom.textContent='已选择移除下边框';error(paragraphPanel,'');});borders.append(removeBottom);paragraph.fields.append(borders);
  const paging=element('div','','layout-fields'),tabsPage=element('div'),tabbar=element('div','','paragraph-tabs');
  tabbar.setAttribute('role','tablist');tabbar.setAttribute('aria-label','段落设置类别');
  paragraphPanel.insertBefore(tabbar,paragraph.fields);paragraph.fields.after(paging,tabsPage);
  for(const [i,label,body] of [[0,'缩进和间距',paragraph.fields],[1,'换行和分页',paging],[2,'制表位',tabsPage]]) {
    body.id=`paragraph-page-${i}`;body.setAttribute('role','tabpanel');body.hidden=i!==0;
    const tab=button(label,`paragraph-tab-${i}`,()=>{for(const item of tabbar.children){item.setAttribute('aria-selected',String(item===tab));document.getElementById(item.getAttribute('aria-controls')).hidden=item!==tab;}});
    tab.setAttribute('role','tab');tab.setAttribute('aria-controls',body.id);tab.setAttribute('aria-selected',String(i===0));body.setAttribute('aria-labelledby',tab.id);tabbar.append(tab);
  }
  for(const [name,label] of [['keepNext','与下段同页'],['keepLines','段中不分页'],['widowControl','孤行控制'],['pageBreakBefore','段前分页']])
    fields[name]=selectField(paging,`paragraph-${name}`,label,[['','混合，保持原样'],['true','开启'],['false','关闭']]);
  paging.append(element('p','分页设置会随 Word 文件保存。','layout-hint'));
  const tabHint=element('p','设置按 Tab 键后文字对齐的位置。未设置的部分沿用文档默认制表间隔。','layout-hint'),tabList=element('div','','paragraph-tab-stops'),tabFields=element('div','','layout-fields');tabsPage.append(tabHint,tabList,tabFields);
  const tabPosition=numberField(tabFields,'paragraph-tab-position','位置（厘米）',{min:0,max:55.88});
  const tabAlignment=selectField(tabFields,'paragraph-tab-alignment','对齐方式',[['left','左对齐'],['center','居中'],['right','右对齐'],['decimal','小数点对齐']]);
  const tabLeader=selectField(tabFields,'paragraph-tab-leader','前导符',[['none','无'],['dot','点线 ……'],['hyphen','短线 ----'],['underscore','下划线 ____']]);
  let stops=[];
  function renderStops(){tabList.replaceChildren();for(const stop of stops){const row=element('div','','paragraph-tab-row');row.append(element('span',`${+(stop.position*2.54/1440).toFixed(2)} 厘米 · ${{left:'左对齐',center:'居中',right:'右对齐',decimal:'小数点对齐'}[stop.alignment]||stop.alignment}`),button('删除','',()=>{stops=stops.filter(value=>value!==stop);dirty.add('tabStops');renderStops();}));tabList.append(row);}if(!stops.length)tabList.append(element('p','未设置自定义制表位','layout-hint'));}
  const tabActions=element('div','','layout-actions');tabActions.append(button('添加 / 更新','paragraph-tab-add',()=>{try{const position=Math.round(readNumber(tabPosition)*1440/2.54);stops=stops.filter(value=>value.position!==position);stops.push({position,alignment:tabAlignment.value,leader:tabLeader.value});stops.sort((a,b)=>a.position-b.position);dirty.add('tabStops');renderStops();error(paragraphPanel,'');}catch(reason){error(paragraphPanel,reason.message);}}),button('全部清除','paragraph-tab-clear',()=>{stops=[];dirty.add('tabStops');renderStops();}));tabsPage.append(tabActions);
  const preview=element('div','','paragraph-preview');preview.setAttribute('aria-label','段落排版预览');preview.textContent='合同条款示例：双方应当依照约定履行各自义务。段落设置将应用到当前选中的正文。';paragraphPanel.append(preview);
  function updatePreview(){preview.style.textAlign=fields.alignment.value==='distributed'?'justify':fields.alignment.value==='justify'?'justify':fields.alignment.value||'left';preview.style.direction=fields.direction.value==='true'?'rtl':'ltr';preview.style.lineHeight=({single:'1.2',oneHalf:'1.8',double:'2.4'})[fields.lineRule.value]||(fields.lineRule.value==='auto'?fields.lineValue.value:'1.5');preview.style.textIndent=fields.special.value==='none'?'0':`${fields.special.value==='hanging'?'-':''}${Math.min(8,Number(fields.specialValue.value)||0)}em`;}
  on(paragraphPanel,'input',updatePreview);on(paragraphPanel,'change',updatePreview);
  for(const name of ['alignment','special','lineRule'])fields[name].querySelector('[value="original"]').disabled=true;
  for(const [name,input]of Object.entries(fields)){
    const mark=()=>{dirty.add(name);error(paragraphPanel,'');};on(input,'input',mark);on(input,'change',mark);
  }
  on(fields.special,'change',()=>{fields.specialValue.disabled=!fields.special.value||['none','original'].includes(fields.special.value);});
  on(fields.lineRule,'change',()=>{fields.lineValue.disabled=!fields.lineRule.value||['original','single','oneHalf','double'].includes(fields.lineRule.value);fields.lineValue.value=({single:'1',oneHalf:'1.5',double:'2',auto:'1'})[fields.lineRule.value]||(fields.lineValue.disabled?'':'12');dirty.add('lineValue');});
  function common(formats,read){const values=formats.map(read);return values.every(value=>value===values[0])?values[0]:null;}
  function showNumber(input,value,scale=1){input.value=value==null?'':String(Math.round(value/scale*1000)/1000);input.placeholder=value==null?'混合，保持原样':'';}
  function populateParagraph(){
    const inspections=bookmark.targetIds.map(id=>parse(api.GetFormatting(editor.handle,id)));
    const formats=inspections.map(value=>value?.effectiveParagraph);
    if(formats.some(value=>!value))throw new Error('无法读取这处段落格式，请重新选择正文。');
    const unresolved=explicit=>inspections.some(value=>value.unresolvedInheritedFormatting===true&&!explicit(value.directParagraph||{}));
    fields.alignment.value=unresolved(value=>value.alignment!=null)?'original':common(formats,value=>value.alignment)||'';
    for(const [name,key,scale,alternate,auto]of [['before','spacingBeforeTwips',20,'spacingBeforeLineUnits','spacingBeforeAuto'],['after','spacingAfterTwips',20,'spacingAfterLineUnits','spacingAfterAuto'],['left','leftIndentTwips',1440/2.54,'leftIndentCharacterUnits'],['right','rightIndentTwips',1440/2.54,'rightIndentCharacterUnits']]){
      const original=unresolved(value=>value[key]!=null&&value[alternate]===0&&(!auto||value[auto]===false))||formats.some(value=>Boolean(value[alternate])||Boolean(auto&&value[auto]));
      showNumber(fields[name],original?null:common(formats,value=>value[key]),scale);
      if(original)fields[name].placeholder='原文设置';
    }
    const specialOriginal=unresolved(value=>(value.firstLineIndentTwips!=null||value.hangingIndentTwips!=null)&&value.firstLineIndentCharacterUnits===0&&value.hangingIndentCharacterUnits===0)||formats.some(value=>Boolean(value.firstLineIndentCharacterUnits)||Boolean(value.hangingIndentCharacterUnits));
    fields.special.value=specialOriginal?'original':common(formats,value=>value.hangingIndentTwips?'hanging':value.firstLineIndentTwips?'first':'none')||'';
    showNumber(fields.specialValue,specialOriginal?null:common(formats,value=>value.hangingIndentTwips||value.firstLineIndentTwips||0),1440/2.54);
    if(specialOriginal)fields.specialValue.placeholder='原文设置';
    fields.specialValue.disabled=!fields.special.value||['none','original'].includes(fields.special.value);
    const lineOriginal=unresolved(value=>value.lineSpacing!=null&&value.lineSpacingRule!=null);
    fields.lineRule.value=lineOriginal?'original':common(formats,value=>value.lineSpacingRule)||'';
    showNumber(fields.lineValue,lineOriginal||!fields.lineRule.value?null:common(formats,value=>value.lineSpacing/(value.lineSpacingRule==='auto'?240:20)));
    if(!lineOriginal&&fields.lineRule.value==='auto')fields.lineRule.value=({'1':'single','1.5':'oneHalf','2':'double'})[fields.lineValue.value]||'auto';
    if(lineOriginal)fields.lineValue.placeholder='原文设置';fields.lineValue.disabled=lineOriginal||!fields.lineRule.value||['single','oneHalf','double'].includes(fields.lineRule.value);
    for(const [name,key,alternate] of [['before','spacingBeforeTwips','spacingBeforeLineUnits'],['after','spacingAfterTwips','spacingAfterLineUnits'],['left','leftIndentTwips','leftIndentCharacterUnits'],['right','rightIndentTwips','rightIndentCharacterUnits']]) {
      units[name].value=['before','after'].includes(name)?'pt':'cm';
      if(!unresolved(value=>value[alternate]!=null)&&formats.every(value=>value[alternate])) {units[name].value=['before','after'].includes(name)?'line':'char';showNumber(fields[name],common(formats,value=>value[alternate]),100);}
    }
    units.specialValue.value='cm';
    if(!unresolved(value=>value.firstLineIndentCharacterUnits!=null||value.hangingIndentCharacterUnits!=null)&&formats.every(value=>value.firstLineIndentCharacterUnits||value.hangingIndentCharacterUnits)) {
      units.specialValue.value='char';fields.special.value=common(formats,value=>value.hangingIndentCharacterUnits?'hanging':'first')||'';showNumber(fields.specialValue,common(formats,value=>value.hangingIndentCharacterUnits||value.firstLineIndentCharacterUnits),100);fields.specialValue.disabled=!fields.special.value;
    }
    for(const name of ['keepNext','keepLines','widowControl','pageBreakBefore']) {const value=common(formats,value=>value[name]??(name==='widowControl'));fields[name].value=unresolved(value=>value[name]!=null)||value==null?'':String(value);}
    fields.direction.value=unresolved(value=>value.rightToLeft!=null)?'':String(common(formats,value=>value.rightToLeft??false)??'');
    fields.outline.value=unresolved(value=>value.outlineLevel!=null)?'':String(common(formats,value=>value.outlineLevel??9)??'');
    const sharedStops=common(formats,value=>JSON.stringify(value.tabStops||[]));stops=sharedStops?JSON.parse(sharedStops):[];tabHint.textContent=sharedStops?'设置按 Tab 键后文字对齐的位置。未设置的部分沿用文档默认制表间隔。':'所选段落的制表位不同，未调整时保持各自设置。';renderStops();updatePreview();
    removeBottom.textContent='移除下边框';
    paragraph.hint.textContent=bookmark.targetIds.length>1?`应用到所选 ${bookmark.targetIds.length} 个段落；只改变你调整的项目。`:'应用到当前段落。';dirty.clear();
  }
  function applyParagraph(){
    const op={};
    try{
      if(dirty.has('alignment')&&['left','center','right','justify','distributed'].includes(fields.alignment.value))op.alignment=fields.alignment.value;
      for(const [name,key,scale,alternate]of [['before','spacingBefore',20,'spacingBeforeLineUnits'],['after','spacingAfter',20,'spacingAfterLineUnits'],['left','leftIndent',1440/2.54,'leftIndentCharacterUnits'],['right','rightIndent',1440/2.54,'rightIndentCharacterUnits']])if(dirty.has(name)){const relative=['line','char'].includes(units[name].value);op[relative?alternate:key]=Math.round(readNumber(fields[name])*(relative?100:scale));}
      if(dirty.has('special')||dirty.has('specialValue')){
        if(!fields.special.value)throw new Error('请选择首行缩进、悬挂缩进或无。');
        const key=fields.special.value==='hanging'?'hangingIndent':'firstLineIndent',relative=units.specialValue.value==='char';op[key+(relative?'CharacterUnits':'')]=fields.special.value==='none'?0:Math.round(readNumber(fields.specialValue)*(relative?100:1440/2.54));
      }
      if(dirty.has('lineRule')||dirty.has('lineValue')){
        if(!fields.lineRule.value)throw new Error('请选择行距类型。');
        op.lineSpacingRule=['single','oneHalf','double'].includes(fields.lineRule.value)?'auto':fields.lineRule.value;op.lineSpacing=Math.round(readNumber(fields.lineValue,{max:fields.lineRule.value==='auto'?10:1000})*(op.lineSpacingRule==='auto'?240:20));
      }
      for(const name of ['keepNext','keepLines','widowControl','pageBreakBefore'])if(dirty.has(name)&&fields[name].value!=='')op[name]=fields[name].value==='true';
      if(dirty.has('direction')&&fields.direction.value!=='')op.rightToLeft=fields.direction.value==='true';
      if(dirty.has('outline')&&fields.outline.value!=='')op.outlineLevel=Number(fields.outline.value);
      if(dirty.has('tabStops'))op.tabStops=stops;
      if(dirty.has('removeBottomBorder'))op.bottomBorder={style:'nil',size:0,space:0};
    }catch(reason){error(paragraphPanel,reason.message);return;}
    if(!Object.keys(op).length){close();return;}
    mutate(paragraphPanel,()=>{for(const id of bookmark.targetIds)checked(api.SetParagraphFormat(editor.handle,id,JSON.stringify(op)));rebuild();});
  }
  const paragraphActions=element('div','','layout-actions'),apply=button('应用','paragraph-apply',applyParagraph);apply.classList.add('primary');paragraphActions.append(button('取消','paragraph-cancel',()=>close()),apply);paragraphPanel.append(paragraphActions);
  const table=panelShell(tablePanel,'表格设置');
  const insertGroup=element('div','','layout-table-insert');tablePanel.insertBefore(insertGroup,table.fields);insertGroup.append(table.fields);
  const insertRows=numberField(table.fields,'table-insert-rows','新表格行数',{min:1,max:100,step:1,value:2});
  const insertColumns=numberField(table.fields,'table-insert-columns','新表格列数',{min:1,max:63,step:1,value:2});
  const insert=button('插入表格','table-insert',()=>{
    try{const rows=readNumber(insertRows,{integer:true}),columns=readNumber(insertColumns,{integer:true});if(rows*columns>1000)throw new Error('单次插入最多 1000 个单元格。');mutate(tablePanel,()=>editor.insertTable(rows,columns),{preserve:false});}catch(reason){error(tablePanel,reason.message);}
  });insertGroup.append(insert);
  const cellGroup=element('div','','layout-table-cell');tablePanel.append(cellGroup);
  const trackingHint=element('p','结构调整会以删除旧表、插入新表记录修订。','layout-hint');trackingHint.id='table-tracking-hint';cellGroup.append(trackingHint);
  const cellActions=element('div','','layout-actions layout-table-actions');cellGroup.append(cellActions);
  const structureButtons=[];
  function tableOperation(fn){return mutate(tablePanel,()=>{checked(fn());rebuild();},{preserve:false});}
  for(const [id,label,method,position]of [
    ['row-before','上方插行','InsertTableRow','before'],['row-after','下方插行','InsertTableRow','after'],
    ['column-before','左侧插列','InsertTableColumn','before'],['column-after','右侧插列','InsertTableColumn','after'],
    ['delete-row','删除当前行','DeleteTableRow'],['delete-column','删除当前列','DeleteTableColumn'],
  ]){
    const control=button(label,`table-${id}`,()=>tableOperation(()=>api[method](editor.handle,tableContext.cell.anchor.id,...(position?[position]:[]))));control.classList.add('layout-table-action');structureButtons.push(control);cellActions.append(control);
  }
  const mergeHint=element('p','','layout-hint'),mergeFields=element('div','','layout-fields');mergeHint.id='table-merge-range';cellGroup.append(element('h3','合并单元格','layout-panel-heading'),mergeHint,mergeFields);
  const mergeRows=numberField(mergeFields,'table-merge-rows','向下合并行数',{min:1,max:100,step:1,value:1});
  const mergeColumns=numberField(mergeFields,'table-merge-columns','向右单元格数',{min:1,max:63,step:1,value:2});
  const merge=button('合并此范围','table-merge',()=>{
    try{
      const rows=readNumber(mergeRows,{integer:true}),columns=readNumber(mergeColumns,{integer:true}),{cell,metadata,mergeColumnCount}=tableContext;
      if(rows*columns<2)throw new Error('请至少选择 2 个单元格。');
      if(cell.rowIndex+rows>metadata.rows.length||columns>mergeColumnCount)throw new Error('合并范围超出表格，请减少行数或单元格数。');
      tableOperation(()=>api.MergeCells(editor.handle,cell.anchor.id,rows,columns,'append'));
    }catch(reason){error(tablePanel,reason.message);}
  });cellGroup.append(merge);
  const splitFields=element('div','','layout-fields');cellGroup.append(element('h3','拆分单元格','layout-panel-heading'),splitFields);
  const splitRows=numberField(splitFields,'table-split-rows','拆分为行数',{min:1,max:20,step:1,value:1});
  const splitColumns=numberField(splitFields,'table-split-columns','拆分为列数',{min:1,max:20,step:1,value:2});
  const split=button('拆分当前单元格','table-split',()=>{
    try{const rows=readNumber(splitRows,{integer:true}),columns=readNumber(splitColumns,{integer:true});if(rows*columns<2)throw new Error('拆分后至少需要 2 个单元格。');tableOperation(()=>api.SplitCell(editor.handle,tableContext.cell.anchor.id,rows,columns));}catch(reason){error(tablePanel,reason.message);}
  });
  const unmerge=button('取消当前合并','table-unmerge',()=>tableOperation(()=>api.UnmergeCells(editor.handle,tableContext.cell.anchor.id)));
  const splitActions=element('div','','layout-actions');splitActions.append(split,unmerge);cellGroup.append(splitActions);
  cellGroup.append(element('p','合并保留各格内容；拆分后原内容保留在左上格。','layout-hint'));
  const tableActions=element('div','','layout-actions');tableActions.append(button('关闭','table-cancel',()=>close()));tablePanel.append(tableActions);
  function populateTable(){
    tableContext=null;const block=byId(bookmark.active)||byId(bookmark.ids[0]),cell=block?.closest('td,th'),tableNode=cell?.closest('table');
    cellGroup.hidden=!cell;insertGroup.hidden=Boolean(cell);
    trackingHint.hidden=options.tracking?.()!==true;
    table.hint.textContent=cell?'操作当前单元格所在的表格。':'在当前段落处插入表格。';
    if(!cell)return;
    if([...cell.closest('tr').classList].some(name=>name.endsWith('rev-row-del')))throw new Error('这行已标记删除，请先处理该修订。');
    const result=parse(api.GetTableMetadata(editor.handle,editor.anchorIdOf(tableNode)));
    if(!result?.success||!result.metadata)throw new Error('无法读取当前表格，请重新点击单元格。');
    const anchor=editor.anchorIdOf(cell),metadata=result.metadata;
    const info=metadata.rows.flatMap(row=>row.cells).find(item=>item.anchor.id===anchor);
    if(!info)throw new Error('当前单元格已变化，请重新选择。');
    const rowCells=metadata.rows.find(row=>row.rowIndex===info.rowIndex).cells;
    const remainingCells=rowCells.slice(rowCells.findIndex(item=>item.anchor.id===anchor));
    const continuedIndex=remainingCells.findIndex(item=>item.verticalMerge==='continue');
    const mergeColumnCount=continuedIndex<0?remainingCells.length:continuedIndex;
    tableContext={cell:info,metadata,mergeColumnCount};merge.disabled=mergeColumnCount===0;
    mergeHint.textContent=`从当前第 ${info.rowIndex+1} 行、第 ${info.columnIndex+1} 列开始，向右下合并（包含当前格）。`;
    mergeRows.max=String(metadata.rows.length-info.rowIndex);mergeColumns.max=String(Math.max(1,mergeColumnCount));
    mergeRows.value='1';mergeColumns.value=Number(mergeColumns.max)>1?'2':'1';
    structureButtons.forEach(control=>control.disabled=false);
    document.getElementById('table-delete-row').textContent=metadata.rows.length===1?'删除最后一行（整个表格）':'删除当前行';
    document.getElementById('table-delete-column').textContent=metadata.columns.length===1?'删除最后一列（整个表格）':'删除当前列';
    unmerge.disabled=!(info.rowSpan>1||info.columnSpan>1);split.disabled=typeof api.SplitCell!=='function';
    split.title=split.disabled?'当前编辑器暂不支持指定行列拆分':'';
  }
  function open(panel,trigger,populate){
    if(current?.panel===panel){close();return;}
    const selection=getSelection(),selectionInPaper=selection?.rangeCount&&paper.contains(selection.getRangeAt(0).startContainer);
    const selected=capture()||(selectionInPaper?null:lastSelection);
    close({restoreSelection:false});
    if(!selected||selected.ids.some(id=>!byId(id))){announce('请先点击或选中正文中的段落。');return;}
    if(options.prepare()!==true)return;
    bookmark=selected;
    if(!restore(bookmark)){announce('正文位置已变化，请重新选择。');return;}
    version=readVersion();error(panel,'');
    try{populate();}catch(reason){announce(reason.message);return;}
    current={panel,button:trigger};panel.hidden=false;trigger.setAttribute('aria-expanded','true');
    [...panel.querySelectorAll('input:not(:disabled),select:not(:disabled),button:not(:disabled)')].find(node=>!node.closest('[hidden]'))?.focus({preventScroll:true});
  }
  for(const [trigger,panel,populate]of [[paragraphButton,paragraphPanel,populateParagraph],[tableButton,tablePanel,populateTable]]){
    trigger.setAttribute('aria-controls',panel.id);trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-haspopup','dialog');
    on(trigger,'pointerdown',()=>{lastSelection=capture()||lastSelection;});on(trigger,'click',()=>open(panel,trigger,populate));
  }
  on(document,'selectionchange',()=>{if(!restoring&&!current){const selected=capture(),selection=getSelection();if(selected)lastSelection=selected;else if(selection?.rangeCount&&paper.contains(selection.getRangeAt(0).startContainer))lastSelection=null;}});
  on(document,'pointerdown',event=>{
    if(!current||current.panel.contains(event.target)||current.button.contains(event.target))return;
    const inPaper=paper.contains(event.target);close({restoreSelection:!inPaper});
    if(inPaper)lastSelection=null;
  },true);
  on(document,'keydown',event=>{if(current&&event.key==='Escape'&&!event.isComposing){event.preventDefault();event.stopPropagation();close({focusButton:true});}},true);
  for(const panel of [paragraphPanel,tablePanel])on(panel,'keydown',event=>{
    if(event.key==='Enter'&&!event.isComposing&&event.target.tagName!=='BUTTON'&&event.target.tagName!=='SELECT'&&panel===paragraphPanel){event.preventDefault();applyParagraph();}
  });
  const fontPanel=element('section','','layout-panel');fontPanel.id='font-panel';paragraphPanel.after(fontPanel);
  const fontDialog=panelShell(fontPanel,'字体与字号');
  const cloneOptions=id=>[...document.getElementById(id)?.options||[]].map(option=>[option.value,option.value?option.text:'保持原样']);
  const fontFamily=selectField(fontDialog.fields,'dialog-font','字体',cloneOptions('font'));
  const fontSize=selectField(fontDialog.fields,'dialog-size','字号',cloneOptions('size'));
  const fontActions=element('div','','layout-actions');fontActions.append(button('取消','font-cancel',()=>close()),button('应用','font-apply',()=>{if(!fontFamily.value&&!fontSize.value){close();return;}mutate(fontPanel,()=>{if(fontFamily.value){editor.setFontFamily(fontFamily.value);restore(bookmark);}if(fontSize.value)editor.setFontSize(Number(fontSize.value));});}));fontPanel.append(fontActions);
  function openFont(){open(fontPanel,document.getElementById('font')||paragraphButton,()=>{fontFamily.value='';fontSize.value='';fontDialog.hint.textContent='应用到选中的文字；没有选中文字时应用到当前段落。';});}
  lastSelection=capture();
  return {captureSelection:capture,restoreSelection:restore,openFont,close,dispose(){close({restoreSelection:false});fontPanel.remove();listeners.forEach(remove=>remove());}};
};
