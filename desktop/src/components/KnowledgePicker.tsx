import { ImaConnection } from './ImaConnection';
import { ChevronRight, Database } from 'lucide-react';
import { useEffect, useState } from 'react';
import { knowledgeApi, type KnowledgeLibrary } from '../runtime/knowledgeClient';

export interface KnowledgeSelection { selected: string[]; onChange: (ids: string[]) => void; disabled: boolean }
export function KnowledgePicker({selected, onChange, disabled, showTrigger = true}: KnowledgeSelection & {showTrigger?: boolean}) {
  const [imaOpen, setImaOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const [open, setOpen] = useState(!showTrigger);
  const [libraries, setLibraries] = useState<KnowledgeLibrary[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open) return;
    let active = true;
    knowledgeApi.list().then(items => {if (active) {setLibraries(items); setError('');}})
      .catch(() => {if (active) setError('暂时无法读取知识库，请重试。');});
    return () => {active = false;};
  }, [open, revision]);
  return <div className="knowledge-picker">
    {showTrigger && <button type="button" role="menuitem" disabled={disabled} aria-label="知识库" aria-expanded={open} onClick={() => setOpen(value => !value)}><Database aria-hidden="true" size={16} />知识库{selected.length ? ` · 已选 ${selected.length}` : ''}<ChevronRight className="knowledge-submenu-arrow" aria-hidden="true" size={14} /></button>}
    {imaOpen && <ImaConnection onClose={() => setImaOpen(false)} onSaved={() => setRevision(n => n + 1)} />}
    {open && <div className="composer-knowledge-options" style={!showTrigger ? {position: "static", width: 280, padding: 4, border: 0, boxShadow: "none"} : undefined} role="group" aria-label="本次对话知识库">
      <p>选中的资料片段会发送给当前模型服务。使用 ima 时，搜索词也会发送给 ima。新对话默认不选库。</p>
      {error ? <p role="alert">{error}</p> : libraries.length === 0 ? <p>从左侧“知识库”关联文件夹，或在下方接入 ima。</p> : libraries.map(library => <label key={library.id} style={{display:'block'}}>
        <input type="checkbox" disabled={disabled || (!selected.includes(library.id) && selected.length >= 10)} checked={selected.includes(library.id)} onChange={event => onChange(event.target.checked ? [...selected, library.id] : selected.filter(id => id !== library.id))} />{library.name}{library.kind === 'ima' ? ' · ima' : ''}
        {library.status !== 'ready' && <small> · 部分资料可能暂时不可用</small>}
      </label>)}
      {selected.some(id => !libraries.some(l => l.id === id)) && <p>部分已选库已不可用，请清除选择后重选。</p>}
      <button type="button" disabled={disabled} onClick={() => setImaOpen(true)}>接入 ima</button>
      <button type="button" disabled={disabled || !selected.length} onClick={() => onChange([])}>清除选择</button>
    </div>}
  </div>;
}
