import {useEffect, useRef, useState} from 'react';
import {ArrowUp, File, Folder, RefreshCw} from 'lucide-react';
import {listWorkspaces} from '../runtime/api';
import type {WorkspaceRecord} from '../runtime/contracts';
import {listWorkspaceFiles, type WorkspaceDirectory} from '../runtime/workspaceFilesClient';
import './WorkspaceFilesPanel.css';

export interface WorkspaceBrowseLocation {workspaceId: string | null; path: string}
interface Props {
  location: WorkspaceBrowseLocation;
  onLocation: (location: WorkspaceBrowseLocation) => void;
  onOpen: (workspaceId: string, path: string) => void;
  revision?: number;
}

export function WorkspaceFilesPanel({location, onLocation, onOpen, revision = 0}: Props) {
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[] | null>(null);
  const [listing, setListing] = useState<WorkspaceDirectory | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const generation = useRef(0);
  const onLocationRef = useRef(onLocation); onLocationRef.current = onLocation;
  const {workspaceId, path} = location;

  useEffect(() => {
    const abort = new AbortController();
    const epoch = ++generation.current;
    setBusy(true); setError(''); setListing(null);
    void (async () => {
      const records = (await listWorkspaces(abort.signal)).filter(item => item.allow_read);
      if (generation.current !== epoch) return;
      setWorkspaces(records);
      const selected = records.find(item => item.id === workspaceId);
      if (!selected) {
        if (workspaceId) setError('此目录的读取授权已收回，请选择其他已授权目录。');
        else if (records.length) onLocationRef.current({workspaceId: records[0].id, path: ''});
        return;
      }
      const result = await listWorkspaceFiles(selected.id, path, 0, '', abort.signal);
      if (generation.current === epoch) setListing(result);
    })().catch(reason => {
      if (generation.current === epoch) setError(reason instanceof Error ? reason.message : '文件夹读取失败，请重试。');
    }).finally(() => {if (generation.current === epoch) setBusy(false);});
    return () => {generation.current++; abort.abort();};
  }, [workspaceId, path, revision, reload]);

  const more = async () => {
    if (!workspaceId || !listing || listing.next_offset === null || busy) return;
    const epoch = generation.current;
    setBusy(true); setError('');
    try {
      const next = await listWorkspaceFiles(workspaceId, path, listing.next_offset, listing.snapshot);
      if (generation.current === epoch) setListing({...next, entries: [...listing.entries, ...next.entries]});
    } catch (reason) {
      if (generation.current === epoch) {setListing(null); setError(reason instanceof Error ? reason.message : '文件夹读取失败，请刷新。');}
    } finally {if (generation.current === epoch) setBusy(false);}
  };

  return <section className="workspace-files" aria-label="工作区文件浏览">
    <div className="workspace-files-toolbar">
      <select aria-label="浏览已授权工作区" value={workspaces?.some(item => item.id === workspaceId) ? workspaceId ?? '' : ''}
        onChange={event => onLocation({workspaceId: event.target.value, path: ''})}>
        <option value="" disabled>选择已授权目录</option>
        {workspaces?.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}
      </select>
      <button type="button" aria-label="刷新文件列表" title="刷新" onClick={() => setReload(value => value + 1)} disabled={busy}><RefreshCw size={15}/></button>
    </div>
    {workspaceId && <div className="workspace-files-location">
      <button type="button" aria-label="返回上一级文件夹" disabled={!path} onClick={() => onLocation({workspaceId, path: path.split('/').slice(0, -1).join('/')})}><ArrowUp size={15}/></button>
      <button type="button" className="workspace-files-root" onClick={() => onLocation({workspaceId, path: ''})}>根目录</button>
      {path && <span title={path}>/ {path}</span>}
    </div>}
    {error && <p className="workspace-files-note" role="alert">{error} <button type="button" disabled={busy} onClick={() => setReload(value => value + 1)}>重试</button></p>}
    {busy && <p className="workspace-files-note" role="status">正在读取文件夹…</p>}
    {workspaces?.length === 0 && !busy && !error && <p className="workspace-files-note">尚无可读取的工作区。请先在对话输入框旁选择工作目录。</p>}
    {listing && <>
      {!listing.entries.length && <p className="workspace-files-note">文件夹内没有可显示的文件。</p>}
      <ul className="workspace-files-list">{listing.entries.map(entry => <li key={entry.path}>
        <button type="button" title={entry.name} onClick={() => entry.directory
          ? onLocation({workspaceId, path: entry.path})
          : workspaceId && onOpen(workspaceId, entry.path)}>
          {entry.directory ? <Folder size={16}/> : <File size={16}/>}<span>{entry.name}</span>
          {entry.directory && <small>文件夹</small>}
        </button>
      </li>)}</ul>
      {listing.next_offset !== null && <button type="button" className="workspace-files-more" disabled={busy} onClick={() => void more()}>显示更多</button>}
      {listing.truncated && <p className="workspace-files-note">本层超过 10,000 项，仅浏览其中部分。可进入子文件夹缩小范围。</p>}
      <p className="workspace-files-note workspace-files-footnote">不显示隐藏项目、依赖目录和符号链接。</p>
    </>}
  </section>;
}
