import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ChevronDown, ChevronRight, FolderOpen, Plug, Plus } from 'lucide-react';

export function KnowledgeCreateMenu({busy, onAssociate, onIma}: {
  busy: boolean; onAssociate: () => void; onIma: () => void;
}) {
  const [page, setPage] = useState<'root' | 'external' | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  function close() { setPage(null); trigger.current?.focus(); }
  useEffect(() => {
    if (!page) return;
    panel.current?.querySelector<HTMLButtonElement>('button')?.focus();
    function outside(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setPage(null);
    }
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [page]);
  return <div className="knowledge-create" ref={root} onBlur={event => {
    if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node)) setPage(null);
  }} onKeyDown={event => { if (event.key === 'Escape' && page) { event.preventDefault(); event.stopPropagation(); close(); } }}>
    <button ref={trigger} className="knowledge-add" disabled={busy} aria-expanded={page !== null} aria-controls="knowledge-create-options" onClick={() => setPage(page ? null : 'root')}><Plus size={16} aria-hidden="true"/>建立知识库<ChevronDown size={14} aria-hidden="true"/></button>
    {page && <div ref={panel} id="knowledge-create-options" className="knowledge-create-panel" role="group" aria-label={page === 'root' ? '建立知识库方式' : '选择外部知识库'}>
      {page === 'root' ? <>
        <button aria-label="关联文件夹" disabled={busy} onClick={() => { close(); onAssociate(); }}><FolderOpen size={18} aria-hidden="true"/><span><strong>关联文件夹</strong><small>读取本机 Markdown 资料</small></span></button>
        <button aria-label="接入外部工具" disabled={busy} onClick={() => setPage('external')}><Plug size={18} aria-hidden="true"/><span><strong>接入外部工具</strong><small>ima、Obsidian 等知识库</small></span><ChevronRight size={15} aria-hidden="true"/></button>
      </> : <>
        <button className="knowledge-create-back" onClick={() => setPage('root')}><ArrowLeft size={14} aria-hidden="true"/>返回</button>
        <button aria-label="ima" disabled={busy} onClick={() => { close(); onIma(); }}><span className="knowledge-source-mark">i</span><span><strong>ima</strong><small>连接账号凭据，选择线上知识库</small></span></button>
        <button aria-label="Obsidian" disabled={busy} onClick={() => { close(); onAssociate(); }}><span className="knowledge-source-mark">O</span><span><strong>Obsidian</strong><small>选择本机知识库文件夹（Vault）</small></span></button>
        <button disabled={busy} onClick={() => { close(); onAssociate(); }}><FolderOpen size={18} aria-hidden="true"/><span><strong>其他 Markdown 笔记库</strong><small>选择已保存在本机的资料文件夹</small></span></button>
      </>}
    </div>}
  </div>;
}
