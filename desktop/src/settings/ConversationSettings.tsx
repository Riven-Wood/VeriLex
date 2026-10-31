import {useState} from 'react';
import type {ThreadSummary} from '../app/navigation';
import {SettingsGroup} from './SettingsControls';

export interface ConversationSettingsProps {
  status: "loading" | "ready" | "error";
  retry: ()=>Promise<void>;
  archived: readonly ThreadSummary[];
  trash: readonly ThreadSummary[];
  unarchive: (id:string)=>Promise<void>;
  trashArchived: (id:string)=>Promise<void>;
  restore: (id:string)=>Promise<void>;
  remove: (id:string)=>Promise<void>;
}

export function ConversationSettings({status, retry, archived, trash, unarchive, trashArchived, restore, remove}:ConversationSettingsProps) {
  const [selected,setSelected]=useState<Set<string>>(()=>new Set());
  const [busy,setBusy]=useState<string|null>(null);
  const [error,setError]=useState('');
  const run=async(id:string,action:(id:string)=>Promise<void>)=>{
    if(busy)return;
    setBusy(id);setError('');
    try {await action(id);setSelected(current=>{const next=new Set(current);next.delete(id);return next;});}
    catch{setError('操作未完成，请重试。');}
    finally {setBusy(null);}
  };
  const selectedTrash=trash.filter(thread=>selected.has(thread.id));
  const removeSelected=async()=>{
    if(busy || !selectedTrash.length)return;
    setBusy('batch');setError('');
    let failed=false;
    for(const thread of selectedTrash){
      try {await remove(thread.id);setSelected(current=>{const next=new Set(current);next.delete(thread.id);return next;});}
      catch {failed=true;}
    }
    if(failed)setError('部分对话删除失败，请重试。');
    setBusy(null);
  };
  return <div className="conversation-settings">
    {error && <p role="alert" className="preference-error">{error}</p>}
    {status === 'loading' && <p role="status">正在读取对话…</p>}
    {status === 'error' && <p role="alert">对话列表读取失败。<button type="button" onClick={()=>void retry()}>重试</button></p>}
    <SettingsGroup title="已归档的对话" id="archived-conversations">
      {archived.length ? archived.map(thread=><div className="conversation-settings-row" key={thread.id}>
        <span title={thread.title}>{thread.title}</span><div>
          <button type="button" disabled={!!busy} aria-label={`恢复归档：${thread.title}`} onClick={()=>void run(thread.id,unarchive)}>恢复</button>
          <button type="button" disabled={!!busy} aria-label={`移入回收站：${thread.title}`} onClick={()=>void run(thread.id,trashArchived)}>移入回收站</button>
        </div>
      </div>) : status === "ready" ? <p className="conversation-settings-empty">没有已归档的对话。</p> : null}
    </SettingsGroup>
    <SettingsGroup title="回收站" id="trashed-conversations">
      {!!trash.length && <div className="conversation-settings-row conversation-settings-select-all">
        <label><input type="checkbox" aria-label="全选回收站对话" disabled={!!busy} checked={selectedTrash.length===trash.length} onChange={event=>setSelected(event.target.checked ? new Set(trash.map(thread=>thread.id)) : new Set())}/> 全选</label>
        <button type="button" disabled={!!busy || !selectedTrash.length} onClick={()=>void removeSelected()}>彻底删除所选</button>
      </div>}
      {trash.length ? trash.map(thread=><div className="conversation-settings-row" key={thread.id}>
        <label className="conversation-settings-choice"><input type="checkbox" aria-label={`选择：${thread.title}`} disabled={!!busy} checked={selected.has(thread.id)} onChange={event=>setSelected(current=>{const next=new Set(current);if(event.target.checked)next.add(thread.id);else next.delete(thread.id);return next;})}/><span title={thread.title}>{thread.title}</span></label><div>
          <button type="button" disabled={!!busy} aria-label={`恢复回收站：${thread.title}`} onClick={()=>void run(thread.id,restore)}>恢复</button>
          <button type="button" disabled={!!busy} aria-label={`彻底删除：${thread.title}`} onClick={()=>void run(thread.id,remove)}>彻底删除</button>
        </div>
      </div>) : status === "ready" ? <p className="conversation-settings-empty">回收站为空。</p> : null}
    </SettingsGroup>
  </div>;
}
