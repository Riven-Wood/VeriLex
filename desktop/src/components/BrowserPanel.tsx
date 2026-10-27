import { useEffect, useRef, useState } from 'react';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { ArrowLeft, ArrowRight, RotateCw, Square, ExternalLink, X, Globe } from 'lucide-react';
import { browserUrl } from '../platform/browserPanel';
import './BrowserPanel.css';

type Status = { url: string; phase: string; notice?: string };
let consumedRequest = 0;
let remembered: Status = { url: '', phase: 'idle' };
// Serialize native mutations: a late resize must never re-show a closed panel.
let commands: Promise<unknown> = Promise.resolve();
function action(name: string, extra: Record<string, unknown> = {}) {
  const operation = commands.catch(() => undefined).then(() => invoke<Status | null>('browser_action', { action: name, ...extra }));
  commands = operation;
  return operation;
}

export default function BrowserPanel({ request }: { request?: {url: string; id: number} }) {
  const [status, setStatus] = useState(remembered);
  const [address, setAddress] = useState(remembered.url);
  const [error, setError] = useState('');
  const [slow, setSlow] = useState(false);
  const viewport = useRef<HTMLDivElement>(null);
  const editing = useRef(false);
  const mounted = useRef(true);
  const opening = useRef(false);
  const native = isTauri();
  const run = async (name: string, extra: Record<string, unknown> = {}) => {
    setError('');
    try { await action(name, extra); return true; }
    catch (e) { if (mounted.current) { if (name === 'open') editing.current = true; setError(typeof e === 'string' ? e : '网页操作失败，请重试。'); } return false; }
  };
  const open = (value: string) => {
    try {
      const url = browserUrl(value);
      editing.current = true; opening.current = true;
      setAddress(url); setSlow(false);
      void run('open', { url }).then(ok => {
        opening.current = false;
        if (ok) editing.current = false;
      });
    } catch (e) { setError(e instanceof Error ? e.message : '请输入完整的网页地址。'); }
  };
  useEffect(() => {
    mounted.current = true;
    if (!native) return;
    let cancelled = false, busy = false;
    const sync = async () => {
      if (busy || cancelled) return;
      busy = true;
      try {
        const result = await action('status');
        if (cancelled) return;
        if (result?.phase) {
          remembered = result; setStatus(result);
          if (!editing.current && !opening.current) setAddress(result.url);
        }
        const rect = viewport.current?.getBoundingClientRect();
        const covered = document.querySelector('[role="dialog"], dialog[open], [aria-modal="true"]');
        if (!rect || rect.width < 20 || rect.height < 20 || covered || document.hidden) await action('hide');
        else await action('bounds', { bounds: [rect.x, rect.y, rect.width, rect.height] });
      } catch (e) {
        if (!cancelled) setError(typeof e === 'string' ? e : '内置浏览器暂不可用。');
      } finally { busy = false; }
    };
    const resize = new ResizeObserver(() => void sync());
    if (viewport.current) resize.observe(viewport.current);
    const mutations = new MutationObserver(() => { if (document.querySelector('[role="dialog"], dialog[open], [aria-modal="true"]')) void action('hide').catch(() => undefined); });
    mutations.observe(document.body, { childList: true, subtree: true });
    void sync();
    const timer = window.setInterval(() => void sync(), 400);
    window.addEventListener('resize', sync);
    return () => {
      mounted.current = false; cancelled = true; clearInterval(timer); resize.disconnect(); mutations.disconnect(); window.removeEventListener('resize', sync);
      void action('hide').catch(() => undefined);
    };
  }, [native]);
  useEffect(() => {
    if (request && request.id !== consumedRequest && native) { consumedRequest = request.id; open(request.url); }
  }, [request, native]);
  useEffect(() => {
    setSlow(false);
    if (status.phase !== 'loading') return;
    const timer = setTimeout(() => setSlow(true), 20000);
    return () => clearTimeout(timer);
  }, [status.phase, status.url]);

  return <section className="browser-panel" aria-label="内置浏览器">
    <div className="browser-navigation">
      <button title="网页后退" aria-label="网页后退" disabled={!status.url} onClick={() => void run('back')}><ArrowLeft size={16}/></button>
      <button title="网页前进" aria-label="网页前进" disabled={!status.url} onClick={() => void run('forward')}><ArrowRight size={16}/></button>
      <button title={status.phase === 'loading' ? '停止加载' : '刷新网页'} aria-label={status.phase === 'loading' ? '停止加载' : '刷新网页'} disabled={!status.url} onClick={() => void run(status.phase === 'loading' ? 'stop' : 'reload')}>{status.phase === 'loading' ? <Square size={14}/> : <RotateCw size={16}/>}</button>
      <button title="系统浏览器打开" aria-label="系统浏览器打开" disabled={!status.url} onClick={() => void run('external', {url: status.url})}><ExternalLink size={16}/></button>
      <button title="关闭当前网页" aria-label="关闭当前网页" disabled={!status.url} onClick={() => { editing.current=false; void run('close'); }}><X size={16}/></button>
    </div>
    <form className="browser-address" onSubmit={event => {event.preventDefault(); open(address);}}>
      <input aria-label="网页地址" placeholder="输入网址，或点击对话中的网页链接" value={address} onFocus={() => {editing.current=true;}} onBlur={() => {if (!opening.current) editing.current=false;}} onChange={event => setAddress(event.target.value)} spellCheck={false}/>
      <button type="submit" disabled={!native || !address.trim()}>打开</button>
    </form>
    {(error || status.notice || slow) && <div className="browser-notice" role="alert">{error || status.notice || '网页尚未完成加载。可停止后刷新，或用系统浏览器打开。'}</div>}
    <div className="browser-status" role="status">{status.phase === 'loading' ? '正在加载网页…' : status.phase === 'stopped' ? '已停止加载' : status.url ? '独立浏览 · 网页不会自动发送给助手' : '网页不会自动发送给助手'}</div>
    <div ref={viewport} className="browser-viewport">
      {!status.url && <div className="browser-empty"><Globe size={28}/><p>{native ? '在这里查看网页' : '请在 VeriLex 桌面应用中使用内置浏览器'}</p><span>点击对话中的网页引用，或在上方输入网址。</span></div>}
    </div>
  </section>;
}
