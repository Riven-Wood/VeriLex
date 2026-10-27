import { useEffect, useId, useRef, useState } from 'react';
import { DetailDialog } from '../components/DetailDialog';
import { filterCommands, shortcutLabel, type BoundCommand } from './registry';
import './commands.css';
export function CommandPalette({commands,onClose,onRun}: {commands:readonly BoundCommand[];onClose:()=>void;onRun:(command:BoundCommand)=>void}) {
  const [query,setQuery]=useState('');
  const [index,setIndex]=useState(0);
  const input=useRef<HTMLInputElement>(null);
  const composing=useRef(false);
  const listId=useId();
  const results=filterCommands(commands,query);
  const selected=Math.min(index,Math.max(0,results.length-1));
  useEffect(()=>{input.current?.focus();},[]);
  useEffect(()=>{document.getElementById(`${listId}-${selected}`)?.scrollIntoView?.({block:'nearest'});},[selected,listId]);
  return <DetailDialog title="命令面板" onClose={onClose} className="command-palette">
    <div className="command-search"><input ref={input} role="combobox" aria-label="搜索命令" aria-autocomplete="list" aria-expanded="true" aria-controls={listId} aria-activedescendant={results.length?`${listId}-${selected}`:undefined} placeholder="搜索操作，例如：文件、设置" value={query} maxLength={100}
      onChange={e=>{setQuery(e.target.value);setIndex(0);}}
      onCompositionStart={()=>{composing.current=true;}} onCompositionEnd={()=>{composing.current=false;}}
      onKeyDown={e=>{
        if(composing.current||e.nativeEvent.isComposing||e.keyCode===229)return;
        if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setIndex(results.length?(selected+(e.key==='ArrowDown'?1:-1)+results.length)%results.length:0);}
        if(e.key==='Enter'){e.preventDefault();if(results[selected])onRun(results[selected]);}
      }}/></div>
    <div className="command-results" id={listId} role="listbox" aria-label="可用命令">
      {results.map((command,i)=><div id={`${listId}-${i}`} key={command.id} role="option" aria-selected={i===selected} className="command-result" onMouseMove={()=>setIndex(i)} onMouseDown={e=>e.preventDefault()} onClick={()=>onRun(command)}>
        <span>{command.title}{command.scope&&<small>{command.scope}</small>}</span><kbd>{shortcutLabel(command)}</kbd>
      </div>)}
    </div>
    {!results.length&&<p className="command-empty" role="status">没有匹配的命令</p>}
    <footer className="command-hint">↑ ↓ 选择 · Enter 执行 · Esc 关闭</footer>
  </DetailDialog>;
}
