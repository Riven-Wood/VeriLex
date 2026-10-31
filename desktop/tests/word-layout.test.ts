import {readFileSync} from 'node:fs';
import {afterEach,describe,expect,it,vi} from 'vitest';

const source=readFileSync('src/documents/word-layout.js','utf8');
let dispose:undefined|(()=>void);
const normal={alignment:'left',spacingBeforeTwips:0,spacingAfterTwips:120,leftIndentTwips:0,rightIndentTwips:0,firstLineIndentTwips:0,hangingIndentTwips:0,lineSpacing:240,lineSpacingRule:'auto'};
function setup(){
 document.body.innerHTML='<button id="paragraph-open">段落</button><button id="table-open">表格</button><p id="status"></p><main id="paper"><p data-anchor="a" contenteditable="true" tabindex="0">第一段正文</p><p data-anchor="b" contenteditable="true" tabindex="0">第二段正文</p><table data-anchor="table"><tbody><tr><td data-anchor="c0"><p data-anchor="p0" contenteditable="true" tabindex="0">甲</p></td><td data-anchor="c1"><p data-anchor="p1" contenteditable="true" tabindex="0">乙</p></td><td data-anchor="c2"><p data-anchor="p2" contenteditable="true" tabindex="0">丙</p></td></tr><tr><td data-anchor="c3"><p data-anchor="p3" contenteditable="true" tabindex="0">丁</p></td><td data-anchor="c4"><p data-anchor="p4" contenteditable="true" tabindex="0">戊</p></td><td data-anchor="c5"><p data-anchor="p5" contenteditable="true" tabindex="0">己</p></td></tr></tbody></table></main><section id="paragraph-panel"></section><section id="table-panel"></section>';
 const paper=document.querySelector<HTMLElement>('#paper')!,first=paper.querySelector<HTMLElement>('[data-anchor="a"]')!;
 let version=1;
 const metadata={anchor:{id:'table'},columns:[0,1,2].map(columnIndex=>({columnIndex})),rows:[0,1].map(rowIndex=>({rowIndex,cells:[0,1,2].map(columnIndex=>({anchor:{id:`c${rowIndex*3+columnIndex}`},rowIndex,columnIndex,rowSpan:1,columnSpan:1,verticalMerge:'none'}))}))};
 const api={GetVersion:vi.fn(()=>String(version)),GetFormatting:vi.fn((_:number,id:string)=>JSON.stringify({effectiveParagraph:{...normal,...(id==='b'?{spacingBeforeTwips:240,leftIndentTwips:720}:{} )}})),SetParagraphFormat:vi.fn((_handle:number,_id:string,_op:string)=>'{"success":true}'),GetTableMetadata:vi.fn(()=>JSON.stringify({success:true,metadata})),InsertTableRow:vi.fn(()=>'{"success":true}'),InsertTableColumn:vi.fn(()=>'{"success":true}'),DeleteTableRow:vi.fn(()=>'{"success":true}'),DeleteTableColumn:vi.fn(()=>'{"success":true}'),MergeCells:vi.fn(()=>'{"success":true}'),UnmergeCells:vi.fn(()=>'{"success":true}'),SplitCell:vi.fn(()=>'{"success":true}')};
 const editor={handle:5,activeBlock:first,exports:{DocxSessionBridge:api},anchorIdOf:(node:HTMLElement)=>node.dataset.anchor,reconcile:vi.fn(),insertTable:vi.fn()};
 const options={status:document.querySelector<HTMLElement>('#status')!,tracking:vi.fn(()=>false),prepare:vi.fn(()=>true),runCommand:vi.fn((action:()=>void)=>{try{action();return true;}catch{options.status.textContent='操作未完成，可以继续编辑。';return false;}})};
 function select(start='a',end=start,startOffset=1,endOffset=2){const range=document.createRange(),a=paper.querySelector(`[data-anchor="${start}"]`)!,b=paper.querySelector(`[data-anchor="${end}"]`)!;range.setStart(a.firstChild!,Math.min(startOffset,a.textContent!.length));range.setEnd(b.firstChild!,Math.min(endOffset,b.textContent!.length));getSelection()!.removeAllRanges();getSelection()!.addRange(range);document.dispatchEvent(new Event('selectionchange'));}
 select();const install=new Function(source+';return window.installWordLayout;')();dispose=install(editor,paper,options).dispose;
 const click=(id:string)=>document.getElementById(id)!.click();
 const input=(id:string,value:string)=>{const field=document.getElementById(id) as HTMLInputElement;field.value=value;field.dispatchEvent(new Event(field.tagName==='SELECT'?'change':'input',{bubbles:true}));};
 return {paper,first,editor,api,options,metadata,select,click,input,changeVersion:()=>version++};
}
afterEach(()=>{dispose?.();dispose=undefined;vi.restoreAllMocks();});
describe('Word paragraph and table panels',()=>{
 it('applies only dirty paragraph fields across the captured paragraphs after input steals focus',()=>{
  const h=setup();h.select('a','b');h.click('paragraph-open');
  expect((document.getElementById('paragraph-before') as HTMLInputElement).value).toBe('');
  h.input('paragraph-after','9');getSelection()!.removeAllRanges();h.click('paragraph-apply');
  expect(h.api.SetParagraphFormat.mock.calls).toEqual([[5,'a','{"spacingAfter":180}'],[5,'b','{"spacingAfter":180}']]);
  expect(h.options.runCommand).toHaveBeenCalledOnce();expect(document.getElementById('paragraph-panel')!.hidden).toBe(true);
  expect(getSelection()!.toString()).toBe('一段正文第二');
 });
 it('removes only the selected paragraph bottom border',()=>{
  const h=setup();h.click('paragraph-open');h.click('paragraph-remove-bottom-border');h.click('paragraph-apply');
  expect(JSON.parse(h.api.SetParagraphFormat.mock.calls[0][2])).toEqual({bottomBorder:{style:'nil',size:0,space:0}});
 });
 it('resolves saved anchors after a formatting repaint and restores the caret',()=>{
  const h=setup();h.click('paragraph-open');h.input('paragraph-before','4');
  h.editor.reconcile.mockImplementation(()=>{const node=h.first.cloneNode(true);h.first.replaceWith(node);});h.click('paragraph-apply');
  expect(getSelection()!.anchorNode?.isConnected).toBe(true);expect(getSelection()!.toString()).toBe('一');
 });
 it('converts units and sends mutually exclusive hanging indent and fixed spacing',()=>{
  const h=setup();h.click('paragraph-open');h.input('paragraph-left','2.54');h.input('paragraph-special','hanging');h.input('paragraph-special-value','1.27');h.input('paragraph-line-rule','exact');h.input('paragraph-line-value','28');h.click('paragraph-apply');
  expect(JSON.parse(h.api.SetParagraphFormat.mock.calls[0][2])).toEqual({leftIndent:1440,hangingIndent:720,lineSpacingRule:'exact',lineSpacing:560});
 });
 it('applies two-sided alignment without changing spacing',()=>{
  const h=setup();h.click('paragraph-open');h.input('paragraph-alignment','justify');h.click('paragraph-apply');expect(h.api.SetParagraphFormat).toHaveBeenCalledWith(5,'a','{"alignment":"justify"}');
 });
 it('does not format the untouched paragraph at a selection ending at its first position',()=>{
  const h=setup();h.select('a','b',1,0);h.click('paragraph-open');h.input('paragraph-after','8');h.click('paragraph-apply');expect(h.api.SetParagraphFormat).toHaveBeenCalledOnce();expect(h.api.SetParagraphFormat).toHaveBeenCalledWith(5,'a','{"spacingAfter":160}');
 });
 it('preserves character-based indentation and automatic paragraph spacing without presenting them as zero',()=>{
  const h=setup();h.api.GetFormatting.mockReturnValue(JSON.stringify({effectiveParagraph:{...normal,firstLineIndentCharacterUnits:200,leftIndentCharacterUnits:100,spacingBeforeAuto:true}}));h.click('paragraph-open');
  expect((document.getElementById('paragraph-special') as HTMLSelectElement).value).toBe('first');
  expect((document.getElementById('paragraph-special-value') as HTMLInputElement).value).toBe('2');
  expect((document.getElementById('paragraph-special-value-unit') as HTMLSelectElement).value).toBe('char');
  expect((document.getElementById('paragraph-left') as HTMLInputElement).value).toBe('1');
  for(const id of ['paragraph-before']){const input=document.getElementById(id) as HTMLInputElement;expect(input.value).toBe('');expect(input.placeholder).toBe('原文设置');}
  h.input('paragraph-after','5');h.click('paragraph-apply');expect(h.api.SetParagraphFormat).toHaveBeenCalledWith(5,'a','{"spacingAfter":100}');
 });
 it('leaves unresolved list/table inheritance blank while showing explicit direct values',()=>{
  const h=setup();h.api.GetFormatting.mockReturnValue(JSON.stringify({effectiveParagraph:{...normal,spacingAfterTwips:160},directParagraph:{spacingAfterTwips:160,spacingAfterLineUnits:0,spacingAfterAuto:false},unresolvedInheritedFormatting:true}));h.click('paragraph-open');
  expect((document.getElementById('paragraph-left') as HTMLInputElement).placeholder).toBe('原文设置');expect((document.getElementById('paragraph-after') as HTMLInputElement).value).toBe('8');expect((document.getElementById('paragraph-line-rule') as HTMLSelectElement).value).toBe('original');
  h.input('paragraph-after','9');h.click('paragraph-apply');expect(h.api.SetParagraphFormat).toHaveBeenCalledWith(5,'a','{"spacingAfter":180}');
 });
 it('does not trust twip-only direct values when inherited character and line units are unresolved',()=>{
  const h=setup();h.api.GetFormatting.mockReturnValue(JSON.stringify({effectiveParagraph:{...normal,leftIndentTwips:720,firstLineIndentTwips:720},directParagraph:{leftIndentTwips:720,firstLineIndentTwips:720,spacingAfterTwips:120,lineSpacing:360},unresolvedInheritedFormatting:true}));h.click('paragraph-open');
  for(const id of ['paragraph-left','paragraph-special-value','paragraph-after','paragraph-line-value'])expect((document.getElementById(id) as HTMLInputElement).placeholder).toBe('原文设置');
  h.click('paragraph-cancel');h.api.GetFormatting.mockReturnValue(JSON.stringify({effectiveParagraph:{...normal,leftIndentTwips:720,firstLineIndentTwips:720,lineSpacing:360},directParagraph:{leftIndentTwips:720,leftIndentCharacterUnits:0,firstLineIndentTwips:720,firstLineIndentCharacterUnits:0,hangingIndentCharacterUnits:0,lineSpacing:360,lineSpacingRule:'auto'},unresolvedInheritedFormatting:true}));h.click('paragraph-open');
  expect((document.getElementById('paragraph-left') as HTMLInputElement).value).toBe('1.27');expect((document.getElementById('paragraph-special') as HTMLSelectElement).value).toBe('first');expect((document.getElementById('paragraph-line-value') as HTMLInputElement).value).toBe('1.5');
 });
 it('keeps the selection at its logical position when preparing strips the empty-cell placeholder',()=>{
  const h=setup();h.first.textContent='\u00a0第一段正文';h.first.dataset.vlEmptyPlaceholder='true';h.select('a','a',2,3);
  h.options.prepare.mockImplementation(()=>{h.first.textContent='第一段正文';h.first.dataset.vlEmptyPlaceholder='false';return true;});h.click('paragraph-open');h.click('paragraph-cancel');expect(getSelection()!.toString()).toBe('一');
 });
 it('normalizes direction marks in captured and restored offsets without losing pending original focus',()=>{
  const h=setup();h.first.textContent='\u200e第\u200f一段正文';h.select('a','a',3,4);
  h.options.prepare.mockImplementation(()=>{expect(h.editor.activeBlock).toBe(h.first);h.first.textContent='第一段正文';return true;});h.click('paragraph-open');h.click('paragraph-cancel');expect(getSelection()!.toString()).toBe('一');
  h.select('a','b');h.options.prepare.mockImplementation(()=>{expect(h.editor.activeBlock).toBe(h.first);return true;});h.click('paragraph-open');expect(h.options.prepare).toHaveBeenCalledTimes(2);
 });
 it('cancel, Escape and outside click close without applying any formatting',()=>{
  const h=setup();h.click('paragraph-open');h.input('paragraph-before','7');document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));expect(document.activeElement?.id).toBe('paragraph-open');expect(document.getElementById('paragraph-panel')!.hidden).toBe(true);
  h.click('paragraph-open');h.first.dispatchEvent(new Event('pointerdown',{bubbles:true}));expect(document.getElementById('paragraph-panel')!.hidden).toBe(true);expect(h.api.SetParagraphFormat).not.toHaveBeenCalled();
 });
 it('rejects invalid input and keeps a failed mutation panel open',()=>{
  const h=setup();h.click('paragraph-open');h.input('paragraph-before','-1');h.click('paragraph-apply');expect(h.options.runCommand).not.toHaveBeenCalled();
  h.input('paragraph-before','2');h.api.SetParagraphFormat.mockReturnValue('{"success":false}');h.click('paragraph-apply');expect(document.getElementById('paragraph-panel')!.hidden).toBe(false);expect(h.editor.reconcile).not.toHaveBeenCalled();
 });
 it('does not apply stale panel settings after document changes',()=>{
  const h=setup();h.click('paragraph-open');h.input('paragraph-after','7');h.changeVersion();h.click('paragraph-apply');expect(h.api.SetParagraphFormat).not.toHaveBeenCalled();expect(document.querySelector('#paragraph-panel .layout-error')!.textContent).toContain('正文已变化');
 });
 it('uses the selected cell rather than a stale activeBlock and states the merge rectangle',()=>{
  const h=setup();h.select('p1','p1',0,0);h.click('table-open');expect(document.querySelector('#table-merge-range')!.textContent).toContain('第 1 行、第 2 列');
  h.input('table-merge-rows','2');h.input('table-merge-columns','2');h.click('table-merge');expect(h.api.MergeCells).toHaveBeenCalledWith(5,'c1',2,2,'append');expect(document.getElementById('table-panel')!.hidden).toBe(true);
 });
 it('validates merge boundaries before mutation and keeps the dialog open',()=>{
  const h=setup();h.select('p2','p2',0,0);h.click('table-open');h.input('table-merge-columns','2');h.click('table-merge');expect(h.api.MergeCells).not.toHaveBeenCalled();expect(document.getElementById('table-panel')!.hidden).toBe(false);
 });
 it('counts physical cells to the right rather than grid columns when a cell is already merged',()=>{
  const h=setup();h.metadata.rows[0].cells.splice(1,1);h.metadata.rows[0].cells[0].columnSpan=2;h.select('p0','p0',0,0);h.click('table-open');expect((document.getElementById('table-merge-columns') as HTMLInputElement).max).toBe('2');h.input('table-merge-columns','3');h.click('table-merge');expect(h.api.MergeCells).not.toHaveBeenCalled();h.input('table-merge-columns','2');h.click('table-merge');expect(h.api.MergeCells).toHaveBeenCalledWith(5,'c0',1,2,'append');
 });
 it('inserts rows and columns with canonical anchors and explicit direction',()=>{
  const h=setup();h.select('p0','p0',0,0);h.click('table-open');h.click('table-row-before');expect(h.api.InsertTableRow).toHaveBeenCalledWith(5,'c0','before');
  h.select('p0','p0',0,0);h.click('table-open');h.click('table-column-after');expect(h.api.InsertTableColumn).toHaveBeenCalledWith(5,'c0','after');
 });
 it('restores a connected neighboring caret after deleting the current row',()=>{
  const h=setup();h.select('p0','p0',0,0);h.click('table-open');
  expect(document.querySelector<HTMLElement>('.layout-table-insert')!.hidden).toBe(true);expect(document.activeElement?.id).toBe('table-row-before');
  h.editor.reconcile.mockImplementation(()=>h.paper.querySelector('tr')!.remove());h.click('table-delete-row');expect(h.editor.activeBlock.isConnected).toBe(true);expect(getSelection()!.anchorNode?.isConnected).toBe(true);expect(h.editor.activeBlock.dataset.anchor).toBe('p3');
 });
 it('splits to the requested rows and columns and only unmerges a merged cell',()=>{
  const h=setup();h.select('p0','p0',0,0);h.click('table-open');expect((document.getElementById('table-unmerge') as HTMLButtonElement).disabled).toBe(true);
  h.input('table-split-rows','2');h.input('table-split-columns','3');h.click('table-split');expect(h.api.SplitCell).toHaveBeenCalledWith(5,'c0',2,3);
  h.metadata.rows[0].cells[0].columnSpan=2;h.select('p0','p0',0,0);h.click('table-open');h.click('table-unmerge');expect(h.api.UnmergeCells).toHaveBeenCalledWith(5,'c0');
 });
 it('never falls back to an old table selection when the current selection is a deleted row',()=>{
  const h=setup();h.select('p0','p0',0,0);const row=h.paper.querySelector('tr')!;row.className='docx-rev-row-del';row.querySelectorAll('[contenteditable]').forEach(node=>node.setAttribute('contenteditable','false'));h.select('p0','p0',0,0);h.click('table-open');expect(document.getElementById('table-panel')!.hidden).toBe(true);expect(h.api.GetTableMetadata).not.toHaveBeenCalled();expect(h.options.status.textContent).toContain('正文');
 });
 it('shows the structural revision explanation only when tracking is enabled',()=>{
  const h=setup();h.select('p0','p0',0,0);h.click('table-open');expect(document.getElementById('table-tracking-hint')!.hidden).toBe(true);h.click('table-cancel');h.options.tracking.mockReturnValue(true);h.click('table-open');expect(document.getElementById('table-tracking-hint')!.hidden).toBe(false);
 });
 it('does not mutate or open when preparing pending document input fails',()=>{
  const h=setup();h.options.prepare.mockReturnValue(false);h.click('paragraph-open');expect(document.getElementById('paragraph-panel')!.hidden).toBe(true);expect(h.api.GetFormatting).not.toHaveBeenCalled();
 });
});

describe('WPS-style paragraph controls',()=>{
 it('writes character and line units without flattening them into physical units',()=>{
  const h=setup();h.click('paragraph-open');h.input('paragraph-left-unit','char');h.input('paragraph-left','2');h.input('paragraph-before-unit','line');h.input('paragraph-before','0.5');h.input('paragraph-special','first');h.input('paragraph-special-value-unit','char');h.input('paragraph-special-value','2');h.input('paragraph-line-rule','oneHalf');h.click('paragraph-apply');
  expect(JSON.parse(h.api.SetParagraphFormat.mock.calls[0][2])).toEqual({leftIndentCharacterUnits:200,spacingBeforeLineUnits:50,firstLineIndentCharacterUnits:200,lineSpacingRule:'auto',lineSpacing:360});
 });
 it('writes explicit off values for inherited pagination without touching other settings',()=>{
  const h=setup();h.click('paragraph-open');h.click('paragraph-tab-1');h.input('paragraph-keepNext','false');h.input('paragraph-pageBreakBefore','false');h.click('paragraph-apply');expect(JSON.parse(h.api.SetParagraphFormat.mock.calls[0][2])).toEqual({keepNext:false,pageBreakBefore:false});
 });
 it('updates and clears custom tab stops only when edited',()=>{
  const h=setup();h.click('paragraph-open');h.click('paragraph-tab-2');h.input('paragraph-tab-position','2.54');h.input('paragraph-tab-alignment','right');h.input('paragraph-tab-leader','dot');h.click('paragraph-tab-add');h.click('paragraph-apply');expect(JSON.parse(h.api.SetParagraphFormat.mock.calls[0][2])).toEqual({tabStops:[{position:1440,alignment:'right',leader:'dot'}]});
  h.click('paragraph-open');h.click('paragraph-tab-clear');h.click('paragraph-apply');expect(JSON.parse(h.api.SetParagraphFormat.mock.calls[1][2])).toEqual({tabStops:[]});
 });
});
