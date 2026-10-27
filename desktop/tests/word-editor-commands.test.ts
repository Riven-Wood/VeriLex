import {readFileSync} from 'node:fs';
import {describe,it,expect,vi} from 'vitest';
const source=readFileSync('src/documents/word-editor.js','utf8');
const commandSource=source.slice(source.indexOf('function runEditingCommand('),source.indexOf("document.getElementById('tools').addEventListener('click'"));
function harness(){
 document.body.innerHTML='<p contenteditable="true">已输入正文</p><div id="status"></div>';
 const api={BeginTransaction:vi.fn(()=>8),CommitTransaction:vi.fn(),RollbackTransaction:vi.fn(),GetVersion:vi.fn(()=>1)};
 const editor={activeBlock:document.querySelector('p'),handle:7,exports:{DocxSessionBridge:api},reconcile:vi.fn(),replacing:false,selectedBlocks:vi.fn(()=>[document.querySelector('p')]),undo:vi.fn()};
 const flush=vi.fn(),fail=vi.fn(),changed=vi.fn();
 const run=new Function('editor','status','flushFocused','fail','changed','captureReadingPosition','restoreReadingPosition',`let composing=false,fatal=false;${commandSource};return runEditingCommand;`)(editor,document.querySelector('#status'),flush,fail,changed,vi.fn(()=>({})),vi.fn());
 return {run,editor,api,flush,fail,changed};
}
describe('Word toolbar command recovery',()=>{
 it('no focused paragraph leaves the document unchanged without the removed instruction banner',()=>{const h=harness();h.editor.activeBlock!.remove();const action=vi.fn();expect(h.run(action)).toBe(false);expect(action).not.toHaveBeenCalled();expect(h.flush).not.toHaveBeenCalled();expect(document.querySelector('#status')!.textContent).toBe('');});
 it('marks an edit dirty immediately while coalescing background snapshots',()=>{
  vi.useFakeTimers();try{const send=vi.fn(),snapshot=vi.fn();const code=source.slice(source.indexOf('function changed()'),source.indexOf("window.addEventListener('message'"));const changed=new Function('send','snapshot','status',`let composing=false,batching=false,fatal=false,queued=false,saveTimer,searchControls;${code};return changed;`)(send,snapshot,{textContent:''});changed();changed();expect(send.mock.calls).toEqual([['dirty']]);vi.advanceTimersByTime(599);expect(snapshot).not.toHaveBeenCalled();vi.advanceTimersByTime(1);expect(snapshot).toHaveBeenCalledOnce();}finally{vi.useRealTimers();}
 });
 it('single-paragraph formatting uses its own engine history without a duplicate package snapshot',()=>{const h=harness();h.run(()=>{},{singleFormat:true});expect(h.api.BeginTransaction).not.toHaveBeenCalled();expect(h.changed).toHaveBeenCalledOnce();});
 it('a rendering failure after atomic formatting restores that operation',()=>{const h=harness();h.api.GetVersion.mockReturnValueOnce(1).mockReturnValue(2);h.run(()=>{throw Error('render failed')},{singleFormat:true});expect(h.editor.undo).toHaveBeenCalledOnce();expect(h.editor.reconcile).toHaveBeenCalledOnce();expect(h.fail).not.toHaveBeenCalled();});
 it('formatting multiple paragraphs retains all-or-nothing recovery',()=>{const h=harness();h.editor.selectedBlocks.mockReturnValue([h.editor.activeBlock,h.editor.activeBlock]);h.run(()=>{throw Error('second paragraph rejected')},{singleFormat:true});expect(h.api.RollbackTransaction).toHaveBeenCalledWith(8);expect(h.editor.undo).not.toHaveBeenCalled();});
 it('rejected command rolls back all its edits and keeps save available',()=>{const h=harness();h.run(()=>{throw Error('unsupported')});expect(h.api.RollbackTransaction).toHaveBeenCalledWith(8);expect(h.api.CommitTransaction).not.toHaveBeenCalled();expect(h.fail).not.toHaveBeenCalled();expect(h.editor.reconcile).toHaveBeenCalledOnce();expect(h.editor.replacing).toBe(false);expect(document.querySelector('#status')!.textContent).toContain('可继续编辑和保存');h.run(()=>{});expect(h.changed).toHaveBeenCalledOnce();expect(h.api.CommitTransaction).toHaveBeenCalledWith(8);});
 it('flush failure keeps original unsaved input protected',()=>{const h=harness();h.flush.mockImplementation(()=>{throw Error('sync failed')});h.run(()=>{});expect(h.fail).toHaveBeenCalledOnce();expect(h.api.BeginTransaction).not.toHaveBeenCalled();});
 it('row insertion outside a table gives a usable instruction without pausing',()=>{const h=harness(),action=vi.fn();h.run(action,{tableCell:true});expect(action).not.toHaveBeenCalled();expect(h.fail).not.toHaveBeenCalled();expect(document.querySelector('#status')!.textContent).toContain('单元格');});
 it('history runs without adding an undo transaction',()=>{const h=harness(),action=vi.fn();h.run(action,{history:true});expect(action).toHaveBeenCalledOnce();expect(h.api.BeginTransaction).not.toHaveBeenCalled();expect(h.changed).toHaveBeenCalledOnce();});
 it.each([['unresolved_structural_revision','尚未处理的修订'],['tracked_operation_unsupported','复杂标记']])('panels interpret real serialized engine code %s and remain editable',(code,message)=>{const h=harness();expect(h.run(()=>{})).toBe(true);const error=Object.assign(new Error('unsupported'),{code});expect(h.run(()=>{throw error})).toBe(false);expect(document.querySelector('#status')!.textContent).toContain(message);expect(h.fail).not.toHaveBeenCalled();});
});
