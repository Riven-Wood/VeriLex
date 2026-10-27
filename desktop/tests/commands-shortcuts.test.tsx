import {afterEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {APP_COMMANDS,bindCommands,filterCommands,matchCommand} from '../src/commands/registry';
import {CommandPalette} from '../src/commands/CommandPalette';
afterEach(cleanup);
const commands=bindCommands({'settings':vi.fn(),'new-thread':vi.fn(),'terminal':vi.fn()});
function key(target:HTMLElement,options:KeyboardEventInit={}){const e=new KeyboardEvent('keydown',{key:'n',metaKey:true,bubbles:true,cancelable:true,...options});target.dispatchEvent(e);return e;}
describe('window command ownership',()=>{
 it('binds only usable actions and filters Chinese/English terms',()=>{expect(commands).toHaveLength(3);expect(filterCommands(commands,'shell')[0].id).toBe('terminal');expect(filterCommands(commands,'新 对话')[0].id).toBe('new-thread');expect(bindCommands({browser:vi.fn()})).toEqual([]);});
 it('uses unique combinations and exact platform modifiers',()=>{const keys=APP_COMMANDS.filter(c=>c.key).map(c=>`${c.key}:${!!c.shift}`);expect(new Set(keys).size).toBe(keys.length);expect(matchCommand(key(document.body),commands,true)?.id).toBe('new-thread');for(const options of [{shiftKey:true},{altKey:true},{ctrlKey:true},{repeat:true},{isComposing:true},{keyCode:229}])expect(matchCommand(key(document.body,options),commands,true)).toBeUndefined();expect(matchCommand(key(document.body,{metaKey:false,ctrlKey:true}),commands,false)?.id).toBe('new-thread');});
 it('leaves inputs, rich editors, terminal and dialogs alone',()=>{for(const html of ['<input/>','<textarea/>','<div contenteditable="true"><span>x</span></div>','<div class="xterm"><span>x</span></div>','<div role="dialog"><button>x</button></div>']){const root=document.createElement('div');root.innerHTML=html;document.body.append(root);expect(matchCommand(key(root.querySelector('span,button,input,textarea')!),commands,true)).toBeUndefined();root.remove();}const dialog=document.createElement('dialog');dialog.open=true;document.body.append(dialog);expect(matchCommand(key(document.body),commands,true)).toBeUndefined();dialog.remove();});
 it('respects an already handled event',()=>{const e=key(document.body);e.preventDefault();expect(matchCommand(e,commands,true)).toBeUndefined();});
});
describe('palette',()=>{
 it('searches, navigates, executes and does not execute Chinese composition',()=>{
  HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  const run=vi.fn();render(<CommandPalette commands={commands} onClose={vi.fn()} onRun={run}/>);
  const input=screen.getByRole('combobox');expect(document.activeElement).toBe(input);
  fireEvent.change(input,{target:{value:'terminal'}});expect(screen.getAllByRole('option')).toHaveLength(1);
  fireEvent.compositionStart(input);fireEvent.keyDown(input,{key:'Enter'});expect(run).not.toHaveBeenCalled();fireEvent.compositionEnd(input);fireEvent.keyDown(input,{key:'Enter'});expect(run.mock.calls[0][0].id).toBe('terminal');
  fireEvent.change(input,{target:{value:''}});fireEvent.keyDown(input,{key:'ArrowUp'});expect(screen.getAllByRole('option').at(-1)?.getAttribute('aria-selected')).toBe('true');
  fireEvent.change(input,{target:{value:'不存在'}});fireEvent.keyDown(input,{key:'Enter'});expect(run).toHaveBeenCalledTimes(1);expect(screen.getByRole('status').textContent).toContain('没有匹配');
 });
});
