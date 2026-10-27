import {readFileSync} from 'node:fs';
import {afterEach,describe,it,expect,vi} from 'vitest';
let dispose:(()=>void)|undefined;
function setup(){
  const html=readFileSync('src/documents/word-editor.html','utf8');
  document.body.innerHTML=html.slice(html.indexOf('<body>')+6,html.indexOf('</body>'));
  const paper=document.getElementById('paper')!;
  paper.innerHTML='<p data-anchor="a" contenteditable="true">甲方甲方</p><p data-anchor="b" contenteditable="true">甲方</p>';
  let version=1,calls=0,failAt=0;
  const matches=[{text:'甲方',enclosingAnchor:{id:'a'},span:{start:0,length:2}},{text:'甲方',enclosingAnchor:{id:'a'},span:{start:2,length:2}},{text:'甲方',enclosingAnchor:{id:'b'},span:{start:0,length:2}}];
  const api={GetVersion:()=>version,Grep:vi.fn(()=>JSON.stringify(matches)),ReplaceTextAtSpan:vi.fn((_:number,id:string,start:number,length:number,text:string)=>{if(++calls===failAt)throw Error('unsupported');const block=paper.querySelector(`[data-anchor="${id}"]`)!;block.textContent=block.textContent!.slice(0,start)+text+block.textContent!.slice(start+length);return '{"success":true}';})};
  const editor={handle:1,exports:{DocxSessionBridge:api},activeBlock:paper.firstElementChild,anchorIdOf:(node:HTMLElement)=>node.dataset.anchor,reconcile:()=>{}};
  const controls=new Function(readFileSync('src/documents/word-search.js','utf8')+';return window.installWordSearch;')()(editor,paper,{prepare:()=>true,closePanels:()=>{},runCommand:(action:()=>void)=>{const before=paper.innerHTML;try{action();version++;return true;}catch{paper.innerHTML=before;return false;}}});dispose=controls.dispose;
  Object.defineProperty(Range.prototype,'getBoundingClientRect',{configurable:true,value:()=>({top:0,height:20})});
  const query=document.getElementById('search-query') as HTMLInputElement,replace=document.getElementById('search-replacement') as HTMLInputElement;
  query.value='甲方';replace.value='委托方';controls.open();
  return{paper,api,query,replace,controls,count:document.getElementById('search-count')!,click:(id:string)=>document.getElementById(id)!.click(),change:()=>version++,fail:()=>{failAt=2;}};
}
afterEach(()=>{dispose?.();vi.restoreAllMocks();});
describe('Word search actions',()=>{
 it('replaces overlapping-offset targets from the end so a longer replacement stays correct',()=>{const h=setup();h.click('search-replace-all');expect(h.paper.textContent).toBe('委托方委托方委托方');expect(h.count.textContent).toContain('已替换 3 处');});
 it('uses the current occurrence when selecting next and leaves other occurrences alone',()=>{const h=setup();h.click('search-next');h.click('search-replace');expect(h.paper.textContent).toBe('甲方委托方甲方');});
 it('does not apply stale offsets after the document changes',()=>{const h=setup();h.change();h.click('search-replace-all');expect(h.paper.textContent).toBe('甲方甲方甲方');expect(h.count.textContent).toContain('核对');});
 it('does not use old matches while a newly typed query is still debouncing',()=>{const h=setup();h.query.value='乙方';h.click('search-replace-all');expect(h.paper.textContent).toBe('甲方甲方甲方');});
 it('uses literal punctuation in the engine query and conveys case sensitivity',()=>{const h=setup();h.query.value='A+B.(x)';h.controls.refresh();expect(h.api.Grep.mock.calls.at(-1)).toEqual([1,'A\\+B\\.\\(x\\)',JSON.stringify({scope:1,contextChars:0,regexOptions:1})]);});
 it('shows failure after the command transaction rejects and preserves all original text',()=>{const h=setup();h.fail();h.click('search-replace-all');expect(h.paper.textContent).toBe('甲方甲方甲方');expect(h.count.textContent).toContain('已撤回');});
 it('allows an empty replacement to delete the matched text',()=>{const h=setup();h.replace.value='';h.click('search-replace-all');expect(h.paper.textContent).toBe('');});
});
