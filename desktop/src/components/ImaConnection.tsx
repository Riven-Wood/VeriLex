import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ExternalLink, X } from 'lucide-react';
import { imaApi, type ImaChoice } from '../runtime/imaClient';
import { openIma } from '../platform/ima';
import './ImaConnection.css';

export function ImaConnection({onClose, onSaved}: {onClose: () => void; onSaved: () => void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [clientId, setClientId] = useState('');
  const [key, setKey] = useState('');
  const [token, setToken] = useState('');
  const [items, setItems] = useState<ImaChoice[]>([]);
  const [ids, setIds] = useState<string[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [configured, setConfigured] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    imaApi.status().then(s => {if (mounted.current) setConfigured(s.configured);}).catch(() => {});
    return () => {mounted.current = false; previous?.focus();};
  }, []);
  async function act(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError('');
    try {await operation();} catch (e) {if (mounted.current) setError(e instanceof Error ? e.message : '操作未完成，请重试。');}
    finally {if (mounted.current) setBusy(false);}
  }
  async function browse(next = '') {
    await act(async () => {
      const result = await imaApi.choices(token, next ? search : query, next);
      if (!mounted.current) return;
      setItems(previous => next ? [...previous, ...result.items.filter(x => !previous.some(p => p.id === x.id))] : result.items);
      setCursor(result.next_cursor); if (!next) setSearch(query);
    });
  }
  return createPortal(<dialog ref={dialog} className="ima-connection" aria-labelledby="ima-title" onCancel={e => {e.preventDefault(); if (!busy) onClose();}} onPointerDown={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}>
    <header><div><span className="ima-eyebrow">外部知识来源</span><h2 id="ima-title">连接 ima</h2></div><button aria-label="关闭连接面板" disabled={busy} onClick={onClose}><X size={18} /></button></header>
    {!token ? <>
      {configured && <button disabled={busy} onClick={() => void act(async () => {const result = await imaApi.resume(); if (!mounted.current) return; setToken(result.token); setItems(result.items); setCursor(result.next_cursor); setIds(result.selected);})}>调整已接入的知识库</button>}
      <p>将 ima 知识库接入 VeriLex，提问时按需选择。</p>
      <button className="ima-official" disabled={busy} onClick={() => void act(() => openIma(true))}>前往 ima 获取凭据 <ExternalLink size={15} /></button>
      <p className="ima-help">在官网登录并获取 API Key，将 Client ID 和 API Key 分别粘贴到下方。</p>
      <form onSubmit={e => {e.preventDefault(); void act(async () => {
        const result = await imaApi.connect(clientId.trim(), key.trim());
        if (!mounted.current) return;
        setClientId(''); setKey(''); setToken(result.token); setItems(result.items); setCursor(result.next_cursor);
      });}}>
        <label>Client ID<input autoFocus autoComplete="off" spellCheck={false} value={clientId} maxLength={4096} disabled={busy} required onChange={e => setClientId(e.target.value)} /></label>
        <label>API Key<input type="password" autoComplete="off" spellCheck={false} value={key} maxLength={4096} disabled={busy} required onChange={e => setKey(e.target.value)} /></label>
        <p className="ima-help">凭据仅保存在本机，不会发送给模型。VeriLex 只读取 ima 资料。</p>
        {configured && <p className="ima-help">已有连接。完成选库后会替换原连接，未完成时保留原连接。</p>}
        {error && <p role="alert" className="ima-error">{error}</p>}
        <footer>{configured && <button type="button" disabled={busy} onClick={() => void act(async () => {await imaApi.disconnect(); onSaved(); onClose();})}>断开 ima</button>}<button type="submit" className="ima-primary" disabled={busy || !clientId.trim() || !key.trim()}>{busy ? '正在验证连接…' : '连接并选择知识库'}</button></footer>
      </form>
    </> : <>
      <p role="status">凭据验证成功。选择要接入的知识库。</p>
      <form className="ima-search" onSubmit={e => {e.preventDefault(); void browse();}}><input aria-label="搜索 ima 知识库" placeholder="搜索知识库名称" maxLength={256} value={query} onChange={e => setQuery(e.target.value)} disabled={busy} /><button disabled={busy}>搜索</button></form>
      <div className="ima-choices" aria-label="可接入的 ima 知识库">{items.map(item => <label key={item.id}><input type="checkbox" checked={ids.includes(item.id)} disabled={busy || (!ids.includes(item.id) && ids.length >= 50)} onChange={e => setIds(previous => e.target.checked ? [...previous, item.id] : previous.filter(id => id !== item.id))} /><span>{item.name}</span></label>)}
        {!items.length && <p>没有找到知识库。试试输入名称搜索，或先在 ima 创建、加入知识库。</p>}
        {cursor && <button disabled={busy} onClick={() => void browse(cursor)}>加载更多</button>}
      </div>
      <p className="ima-help">保存前会检查读取权限。接入后，在对话「＋ → 知识库」中勾选使用；搜索词会发给 ima，所选资料片段会发给当前模型。</p>
      {error && <p role="alert" className="ima-error">{error}</p>}
      <footer><button disabled={busy} onClick={() => {setToken(''); setIds([]); setItems([]); setQuery(''); setSearch(''); setError('');}}>重新填写凭据</button><button className="ima-primary" disabled={busy || !ids.length} onClick={() => void act(async () => {await imaApi.save(token, ids); onSaved(); onClose();})}>{busy ? '正在检查并保存…' : `完成${ids.length ? ` · 已选 ${ids.length}` : ''}`}</button></footer>
    </>}
  </dialog>, document.body);
}
