import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { Plus, Square, Eraser, RotateCcw } from 'lucide-react';
import '@xterm/xterm/css/xterm.css';
import { listWorkspaces } from '../runtime/api';
import type { WorkspaceRecord } from '../runtime/contracts';
import { closeTerminal, createTerminal, listTerminals, readTerminal, resizeTerminal, writeTerminal, type TerminalSession } from '../runtime/terminalClient';

// Keep the user's selection while switching panels; never retain terminal output here.
let lastWorkspaceId = '';
const lastSessionIds = new Map<string, string>();

const message = (error: unknown) => error instanceof Error ? error.message : '终端连接失败，请重试。';

function TerminalScreen({ session, onStatus }: { session: TerminalSession; onStatus: (session: TerminalSession) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const terminal = useRef<Terminal | null>(null);
  const send = useRef<(data: string) => void>(() => {});
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (!host.current) return;
    const abort = new AbortController();
    let active = true, ready = false, failed = false, cursor = 0, sequence = 0;
    let queue = Promise.resolve();
    let resizeTimer: ReturnType<typeof setTimeout> | undefined;
    let lastSize = '';
    setError(null); setConnected(false);
    const term = new Terminal({ fontFamily: 'Menlo, Monaco, monospace', fontSize: 12, cursorBlink: true, scrollback: 3000, allowProposedApi: false, disableStdin: true, screenReaderMode: true });
    terminal.current = term;
    const fit = new FitAddon(); term.loadAddon(fit); term.open(host.current);
    if (term.textarea) term.textarea.setAttribute('aria-label', '终端输入');
    const theme = () => {
      const style = getComputedStyle(document.documentElement);
      term.options.theme = { background: style.getPropertyValue('--vl-canvas').trim() || '#ffffff', foreground: style.getPropertyValue('--vl-ink').trim() || '#20242c', cursor: '#1683ff', selectionBackground: '#1683ff55' };
    };
    theme();
    const themeObserver = new MutationObserver(theme);
    themeObserver.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme', 'style', 'class']});
    const fail = (reason: unknown) => {
      if (!active) return;
      failed = true; ready = false; term.options.disableStdin = true;
      setConnected(false); setError(message(reason));
    };
    send.current = (data) => {
      if (!ready || failed) return;
      // Serialize small Unicode-safe chunks. Never replay an ambiguous input.
      const chars = Array.from(data);
      for (let i = 0; i < chars.length; i += 128) {
        const text = chars.slice(i, i + 128).join('');
        queue = queue.then(async () => {
          if (!active || failed || !ready) return;
          const result = await writeTerminal(session.workspaceId, session.id, text, sequence + 1);
          sequence = result.inputSequence;
        }).catch(fail);
      }
    };
    term.attachCustomKeyEventHandler(event => {
      // WebKit can report keyCode=0 for native shortcuts. Use the semantic key
      // for Ctrl+C; Command+C remains the browser's normal copy action.
      if (event.ctrlKey && !event.metaKey && event.key.toLowerCase() === 'c') {
        if (event.type === 'keydown') send.current('\x03');
        event.preventDefault();
        return false;
      }
      return true;
    });
    const input = term.onData(data => send.current(data));
    const resize = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (!active || !host.current?.clientHeight || !host.current.clientWidth) return;
        fit.fit();
        const columns = Math.max(10, Math.min(400, term.cols)), rows = Math.max(2, Math.min(200, term.rows));
        const size = `${columns}:${rows}`;
        if (ready && lastSize !== size) {
          lastSize = size;
          void resizeTerminal(session.workspaceId, session.id, columns, rows).catch(fail);
        }
      }, 80);
    };
    const observer = new ResizeObserver(resize); observer.observe(host.current); resize();
    void (async () => {
      // Reconcile sequence before enabling input, including after reconnect.
      const result = await listTerminals(session.workspaceId, abort.signal);
      const current = result.sessions.find(item => item.id === session.id);
      if (!current) throw new Error('终端会话已结束或后台已重启，请新建终端。');
      sequence = current.inputSequence;
      while (active && !failed) {
        const output = await readTerminal(session.workspaceId, session.id, cursor, abort.signal);
        if (!active) return;
        if (output.truncated) term.write('\r\n[较早输出已超出保留范围]\r\n');
        if (output.data) {
          const bytes = Uint8Array.from(atob(output.data), char => char.charCodeAt(0));
          await new Promise<void>(resolve => term.write(bytes, resolve));
        }
        cursor = output.cursor;
        onStatusRef.current(output);
        if (!ready && output.status === 'running') {
          ready = true; term.options.disableStdin = false; setConnected(true); resize();
        }
        if (output.status !== 'running' && !output.data) {
          ready = false; term.options.disableStdin = true; setConnected(false);
          term.write(`\r\n[${output.status === 'closed' ? '终端已关闭' : `进程已退出，退出码 ${output.exitCode ?? '未知'}`}，可新建终端]\r\n`);
          return;
        }
      }
    })().catch(fail);
    return () => {
      active = false; ready = false; abort.abort(); clearTimeout(resizeTimer);
      input.dispose(); observer.disconnect(); themeObserver.disconnect(); term.dispose(); terminal.current = null;
    };
  }, [session.id, session.workspaceId, revision]);
  return <>
    <div className="terminal-actions">
      <span role="status">{connected ? '运行中' : error ? '连接中断' : session.status === 'running' ? '连接中…' : '已结束'}</span>
      <button disabled={!connected} title="中断当前命令（Ctrl+C）" aria-label="中断当前命令" onClick={() => send.current('\x03')}><Square size={13}/></button>
      <button title="清屏" aria-label="清屏" onClick={() => terminal.current?.clear()}><Eraser size={14}/></button>
    </div>
    {error && <div className="terminal-error" role="alert">{error}<button onClick={() => setRevision(value => value + 1)}><RotateCcw size={13}/>重新连接</button></div>}
    <div ref={host} className="terminal-screen" onClick={() => terminal.current?.focus()}/>
  </>;
}

export default function TerminalPanel({ preferredWorkspaceId }: { preferredWorkspaceId: string | null }) {
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[] | null>(null);
  const [workspaceId, setWorkspaceId] = useState(lastWorkspaceId);
  const [sessions, setSessions] = useState<TerminalSession[]>([]);
  const [selected, setSelected] = useState('');
  const [supported, setSupported] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => { if (workspaceId) lastWorkspaceId = workspaceId; }, [workspaceId]);
  useEffect(() => {
    const abort = new AbortController();
    void listWorkspaces(abort.signal).then(items => {
      const writable = items.filter(item => item.allow_read && item.allow_write);
      setWorkspaces(writable);
      setWorkspaceId(previous => writable.some(item => item.id === previous) ? previous : writable.find(item => item.id === preferredWorkspaceId)?.id ?? writable[0]?.id ?? '');
      if (!writable.length) setLoading(false);
    }).catch(reason => { if (!abort.signal.aborted) {setError(message(reason)); setLoading(false);} });
    return () => abort.abort();
  }, [preferredWorkspaceId, revision]);
  useEffect(() => {
    const current = ++generation.current;
    if (!workspaceId) return;
    const abort = new AbortController(); setLoading(true); setError(null); setSessions([]); setSelected('');
    void listTerminals(workspaceId, abort.signal).then(result => {
      if (generation.current !== current) return;
      setSupported(result.supported); setSessions(result.sessions);
      setSelected(result.sessions.find(item => item.id === lastSessionIds.get(workspaceId))?.id ?? [...result.sessions].reverse().find(item => item.status === 'running')?.id ?? result.sessions.at(-1)?.id ?? '');
      setLoading(false);
    }).catch(reason => {if (!abort.signal.aborted) { setError(message(reason)); setLoading(false); }});
    return () => { abort.abort(); generation.current++; };
  }, [workspaceId, revision]);
  const active = sessions.find(item => item.id === selected);
  const run = async (action: () => Promise<TerminalSession>) => {
    const current = generation.current;
    setBusy(true); setError(null);
    try {
      const result = await action();
      if (generation.current !== current) return;
      setSessions(items => items.some(item => item.id === result.id) ? items.map(item => item.id === result.id ? result : item) : [...items, result]); setSelected(result.id); lastSessionIds.set(workspaceId, result.id);
    } catch (reason) { if (generation.current === current) setError(message(reason)); }
    finally {setBusy(false);}
  };
  return <div className="terminal-panel">
    <div className="terminal-workspace">
      <select aria-label="终端文件夹" value={workspaceId} disabled={busy || !workspaces?.length} onChange={event => setWorkspaceId(event.target.value)}>
        {!workspaces?.length && <option value="">选择文件夹</option>}
        {workspaces?.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}
      </select>
      <button aria-label="新建终端" title="新建终端" disabled={busy || loading || !workspaceId || !supported} onClick={() => void run(() => createTerminal(workspaceId, crypto.randomUUID()))}><Plus size={16}/></button>
    </div>
    {workspaceId && <div className="terminal-folder" title={workspaces?.find(item => item.id === workspaceId)?.root_path}>{workspaces?.find(item => item.id === workspaceId)?.root_path}</div>}
    {sessions.length > 0 && <div className="terminal-session-picker">
      <select aria-label="终端会话" value={selected} disabled={busy} onChange={event => { setSelected(event.target.value); lastSessionIds.set(workspaceId, event.target.value); }}>{sessions.map((item, index) => <option key={item.id} value={item.id}>终端 {index + 1}{item.status !== 'running' ? ' · 已结束' : ''}</option>)}</select>
      <button disabled={busy || active?.status !== 'running'} onClick={() => active && void run(() => closeTerminal(workspaceId, active.id))}>关闭会话</button>
    </div>}
    {error && <div role="alert" className="terminal-error">{error}<button onClick={() => setRevision(value => value + 1)}>刷新</button></div>}
    {loading ? <div className="terminal-empty" role="status">正在连接…</div> : !workspaces?.length ? <div className="terminal-empty">先在对话输入区选择一个允许读写的文件夹，再打开终端。</div> : !supported ? <div className="terminal-empty">此设备暂不支持终端面板。</div> : active ? <TerminalScreen key={active.id} session={active} onStatus={updated => setSessions(items => items.map(item => item.id === updated.id && item.status !== updated.status ? updated : item))}/> : <div className="terminal-empty"><p>在此文件夹中运行命令</p><button disabled={busy} onClick={() => void run(() => createTerminal(workspaceId, crypto.randomUUID()))}>新建终端</button></div>}
    <div className="terminal-footnote">收起后继续运行 · 退出应用时结束<br/>仅可写入所选文件夹及临时目录 · 不联网</div>
  </div>;
}
