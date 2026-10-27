import {editorClient} from '../components/editor/client';
import {EditorProposals} from "../components/editor/EditorProposals";
import {useMaterialCoverage} from '../components/preview/materialCoverageState';
import {useEditorDiscussion,OrdinaryDiscussion,LitigationMaterialsContext} from "../components/editor/DiscussionContext";
import { useCallback, useEffect, useState, useRef, type CSSProperties, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, File, FileQuestion, FileWarning, Folder, MessageSquare, Maximize2, Minimize2, RefreshCw, X, Pencil, Crosshair, Check } from 'lucide-react';
import type { WorkspaceRecord } from '../runtime/contracts';
import type { ThreadSummary } from '../app/navigation';
import { PreviewRail, type PreviewTarget } from '../components/PreviewRail';
import { runtimeRequest,openWorkspacePath } from '../runtime/api';
import { litigationClient, litigationWorkClient, type WorkBinding, type WorkRequest, type LitigationFile } from './LitigationClient';
import { LitigationWorkspaceView, type LitigationPage, type LitigationMaterialControls } from './LitigationWorkspaceView';
import './LitigationWorkbench.css';

const PAGES: {id: LitigationPage; label: string}[] = [
  {id: 'facts', label: '事实梳理'},
  {id: 'evidence', label: '证据整理'},
  {id: 'documents', label: '文书起草'},
  {id: 'discussion', label: '案件讨论'},
];
interface DeskState { page: LitigationPage; paths: string[]; file: string | null; aiOpen: boolean }
const deskKey = (id: string) => `verilex:litigation-desk:v1:${id}`;
function readDesk(id: string): DeskState {
  const fallback: DeskState = {page: 'facts', paths: [], file: null, aiOpen: false};
  try {
    const value = JSON.parse(localStorage.getItem(deskKey(id)) ?? 'null');
    if (!value) return fallback;
    if (['overview','issues','strategy'].includes(value.page)) value.page = 'facts';
    if (!PAGES.some(p => p.id === value.page)) return fallback;
    const paths = Array.isArray(value.paths) ? value.paths.filter((p: unknown): p is string => typeof p === 'string').slice(-12) : [];
    return {page: value.page, paths, file: paths.includes(value.file) ? value.file : null, aiOpen: value.aiOpen === true};
  } catch { return fallback; }
}

const WIDTH_KEY = 'verilex:litigation-widths:v1';
const WIDTH_LIMITS = { files: [170, 480, 200], assistant: [280, 720, 320] } as const;
type Panel = keyof typeof WIDTH_LIMITS;
function clampWidth(panel: Panel, width: number) {
  return Math.round(Math.max(WIDTH_LIMITS[panel][0], Math.min(WIDTH_LIMITS[panel][1], width)));
}
function readWidths(): Record<Panel, number> {
  let saved: Partial<Record<Panel, number>> = {};
  try { saved = JSON.parse(localStorage.getItem(WIDTH_KEY) ?? '{}') ?? {}; } catch { /* Optional preference. */ }
  return Object.fromEntries((['files', 'assistant'] as const).map(panel =>
    [panel, typeof saved[panel] === 'number' && Number.isFinite(saved[panel])
      ? clampWidth(panel, saved[panel]!) : WIDTH_LIMITS[panel][2]])) as Record<Panel, number>;
}
function PanelResizer({panel, width, onChange}: {panel: Panel; width: number; onChange: (width: number) => void}) {
  const stop = useRef<(() => void) | null>(null);
  useEffect(() => () => stop.current?.(), []);
  return <div className={`litigation-resizer litigation-resizer-${panel}`} role="separator" tabIndex={0}
    aria-label={panel === 'files' ? '调整案件工作区宽度' : '调整讨论栏宽度'} aria-orientation="vertical"
    aria-valuemin={WIDTH_LIMITS[panel][0]} aria-valuemax={WIDTH_LIMITS[panel][1]} aria-valuenow={width}
    onDoubleClick={() => onChange(WIDTH_LIMITS[panel][2])}
    onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      onChange(event.key === 'Home' ? WIDTH_LIMITS[panel][0] : event.key === 'End' ? WIDTH_LIMITS[panel][1]
        : clampWidth(panel, width + (event.key === 'ArrowRight' ? 16 : -16) * (panel === 'files' ? 1 : -1)));
    }}
    onPointerDown={event => {
      if (event.button !== 0) return;
      event.preventDefault(); stop.current?.();
      const startX = event.clientX;
      const startWidth = event.currentTarget.parentElement?.getBoundingClientRect().width || width;
      const pointerId = event.pointerId;
      const cursor = document.body.style.cursor, selection = document.body.style.userSelect;
      document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none';
      const move = (next: PointerEvent) => {
        if (next.pointerId !== pointerId) return;
        onChange(clampWidth(panel, startWidth + (next.clientX - startX) * (panel === 'files' ? 1 : -1)));
      };
      const finish = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', finish);
        window.removeEventListener('pointercancel', finish);
        window.removeEventListener('blur', finish);
        document.body.style.cursor = cursor; document.body.style.userSelect = selection;
        stop.current = null;
      };
      stop.current = finish;
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', finish);
      window.addEventListener('pointercancel', finish);
      window.addEventListener('blur', finish);
    }}/>;
}

function MaterialIcon({workspaceId,path}:{workspaceId:string;path:string}) {
  const coverage=useMaterialCoverage(workspaceId,path);
  if((path.startsWith('文书起草/')||path.startsWith('工作成果/'))||coverage?.status==='text_available') return <File size={15}/>;
  return <span title={coverage ? `最近检查：${coverage.gaps.join(' ')}` : '尚未检查材料读取范围；打开文件后检查。'}>{coverage ? <FileWarning size={15}/> : <FileQuestion size={15}/>}</span>;
}
function MaterialCheckbox({path,controls}:{path:string;controls:LitigationMaterialControls|null}) {
  const material=controls?.materials.find(m=>m.path===path);
  return material?<><input type="checkbox" aria-label={`选取材料 ${path}`} title={material.reason||(/\.pdf$/i.test(path)?'用于本次分析；PDF只读取文字层，扫描页需核对':'用于本次分析')} checked={controls!.selected.includes(path)} disabled={controls!.locked||!material.supported} onChange={()=>controls!.toggle(path)}/>{controls!.selected.includes(path)&&<span className="evidence-selection-number" aria-label={`选取顺序 ${controls!.selected.indexOf(path)+1}`}>{controls!.selected.indexOf(path)+1}</span>}</>:null;
}
function FileBranch({workspaceId, controls, path = '', revision, activePath, onOpen}: {
  workspaceId: string; controls:LitigationMaterialControls|null; path?: string; revision: string; activePath: string | null; onOpen: (path: string) => void;
}) {
  const [entries, setEntries] = useState<LitigationFile[]>([]);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [truncated, setTruncated] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setLoading(true); setError('');
    litigationClient.files(workspaceId, path).then(result => {
      if (!current) return;
      setEntries(result.entries); setTruncated(result.truncated);
    }).catch(() => {if (current) setError('文件夹读取失败');})
      .finally(() => {if (current) setLoading(false);});
    return () => {current = false;};
  }, [workspaceId, path, revision, retry]);
  return <ul className="litigation-file-branch" aria-label={path || '案件文件'}>
    {loading && entries.length === 0 && <li className="litigation-file-note" role="status">正在读取…</li>}
    {error && <li role="alert">{error} <button type="button" onClick={() => setRetry(v => v + 1)}>重试</button></li>}
    {!loading && !error && entries.length === 0 && <li className="litigation-file-note">文件夹为空</li>}
    {entries.map(entry => <li key={entry.path}>
      <div className="litigation-file-entry">{!entry.directory&&<MaterialCheckbox path={entry.path} controls={controls}/>}
      <button type="button" className="litigation-file-row" title={entry.path}
        aria-current={!entry.directory && activePath === entry.path ? 'page' : undefined}
        aria-expanded={entry.directory ? expanded.includes(entry.path) : undefined}
        onClick={() => entry.directory
          ? setExpanded(old => old.includes(entry.path) ? old.filter(p => p !== entry.path) : [...old, entry.path])
          : onOpen(entry.path)}>
        {entry.directory ? <>{expanded.includes(entry.path) ? <ChevronDown size={12}/> : <ChevronRight size={12}/>}<Folder size={15}/></> : <MaterialIcon workspaceId={workspaceId} path={entry.path}/>}
        <span>{entry.name}</span>
      </button>
      </div>
      {entry.directory && expanded.includes(entry.path) && <FileBranch workspaceId={workspaceId} controls={controls} path={entry.path} revision={revision} activePath={activePath} onOpen={onOpen}/>}
    </li>)}
    {truncated && <li className="litigation-file-note">本层文件较多，仅显示前 1000 项。</li>}
  </ul>;
}

interface Props {
  workspace: WorkspaceRecord | null;
  onSelectWorkspace: (id: string) => void;
  onChooseWorkspace: () => void;
  workspaces: WorkspaceRecord[];
  thread: ThreadSummary | null;
  threads: ThreadSummary[];
  busy: boolean;
  filesCollapsed: boolean;
  onBack: () => void;
  onStartWorkflow: (message: string) => void;
  onStartWork?: (request: WorkRequest) => void;
  onSelectThread: (id: string) => void;
  onNewDiscussion: () => void;
  onRenameThread?: (id:string,title:string) => Promise<void>;
  renderAssistant: (onPreview: (target: PreviewTarget) => void, ordinary?:boolean) => ReactNode;
  draft: string;
  onDraftChange: (value: string) => void;
  hasModel: boolean;
  onOpenSettings: () => void;
}

export function LitigationWorkbench({workspace, workspaces, onSelectWorkspace, onChooseWorkspace, thread, threads, busy, filesCollapsed,
  onStartWorkflow, onStartWork, onSelectThread, onNewDiscussion, onRenameThread, renderAssistant, draft, onDraftChange, hasModel, onOpenSettings}: Props) {
  const [query,setQuery]=useState(''),[searchResults,setSearchResults]=useState<LitigationFile[]>([]);
  useEffect(()=>{let live=true;if(!workspace||!query.trim()){setSearchResults([]);return;}const timer=setTimeout(()=>{void runtimeRequest<{entries:LitigationFile[]}>(`/v1/litigation/workspaces/${encodeURIComponent(workspace.id)}/file-search?query=${encodeURIComponent(query)}`).then(result=>{if(live)setSearchResults(result.entries);}).catch(()=>{if(live)setFileError('文件搜索失败，请重试。');});},250);return()=>{live=false;clearTimeout(timer);};},[workspace?.id,query]);
  const [widths, setWidths] = useState(readWidths);
  const [focused,setFocused]=useState(false);
  const [showFiles,setShowFiles]=useState(false);
  const filesHidden=focused||(filesCollapsed&&!showFiles);
  useEffect(()=>setShowFiles(false),[filesCollapsed]);
  const fileRequest=useRef(0);
  const [openDocumentRequest,setOpenDocumentRequest]=useState<{id:string;sequence:number}|null>(null);
  useEffect(()=>()=>{fileRequest.current++;},[workspace?.id]);
  const [materialState,setMaterialState]=useState<LitigationMaterialControls|null>(null);
  useEffect(() => {
    try { localStorage.setItem(WIDTH_KEY, JSON.stringify(widths)); } catch { /* Optional preference. */ }
  }, [widths]);
  const resize = (panel: Panel, width: number) => setWidths(old => ({...old, [panel]: width}));
  const [desk, setDesk] = useState(() => readDesk(workspace?.id ?? ''));
  const materialControls=materialState?.workspaceId===workspace?.id?materialState:null;
  const [target, setTarget] = useState<PreviewTarget | null>(() => workspace && desk.file ? {source: 'workspace', workspaceId: workspace.id, path: desk.file} : null);
  const [revision, setRevision] = useState(0);
  const [fileError, setFileError] = useState('');
  useEffect(() => {
    if (!workspace) return;
    try { localStorage.setItem(deskKey(workspace.id), JSON.stringify(desk)); } catch { /* View preferences are optional. */ }
  }, [desk, workspace?.id]);
  useEffect(() => {
    const refresh = () => setRevision(v => v + 1);
    const filesChanged=(event:Event)=>{if((event as CustomEvent).detail?.workspaceId===workspace?.id)refresh();};
    window.addEventListener('focus', refresh);window.addEventListener('verilex:workspace-files-changed',filesChanged);
    return () => {window.removeEventListener('focus', refresh);window.removeEventListener('verilex:workspace-files-changed',filesChanged);};
  }, [workspace?.id]);
  const openPreview = useCallback((next: PreviewTarget) => {
    if (!workspace) return;
    if (next.source === 'workspace' && next.workspaceId !== workspace.id) return;
    fileRequest.current++;setOpenDocumentRequest(null);
    setTarget(next);
    setDesk(old => next.source === 'workspace'
      ? {...old, file: next.path, paths: old.paths.includes(next.path) ? old.paths : [...old.paths, next.path].slice(-12)}
      : {...old, file: null});
  }, [workspace?.id]);
  const openFile = (path: string, page?: number, citation?:import("./LitigationClient").LitigationSource) => workspace && openPreview({source: 'workspace', workspaceId: workspace.id, path, page, citation});
  const closePreview = () => {fileRequest.current++;setOpenDocumentRequest(null);setTarget(null); setDesk(old => ({...old, file: null}));};
  // Only explicit file-list clicks route registered drafts into the editor; citations keep their source preview.
  const openListedFile=async(path:string)=>{
    if(!workspace)return;
    const request=++fileRequest.current;setFileError('');
    try{
      if(onStartWork&&/\.docx$/i.test(path)){
        const result=await editorClient.list(workspace.id,'documents');
        if(request!==fileRequest.current)return;
        const failure=result.errors.find(row=>row.path===path);
        if(failure)throw Error(failure.notice);
        const product=result.products.find(row=>row.path===path);
        if(product){setTarget(null);setDesk(old=>({...old,page:'documents',file:null}));setOpenDocumentRequest({id:product.id,sequence:request});return;}
      }
      if(request===fileRequest.current)openFile(path);
    }catch(error){if(request===fileRequest.current)setFileError(error instanceof Error?error.message:'文件打开失败，请重试。');}
  };
  const run = (message: string) => onStartWorkflow(message);
  const [focusKey,setFocusKey]=useState(0);
  const [links,setLinks]=useState<Record<string,WorkBinding>>({});
  const [linkError,setLinkError]=useState('');
  useEffect(()=>{
    if(!onStartWork || !workspace)return;
    let live=true; let timer:ReturnType<typeof setTimeout>;
    const read=async()=>{try{const result=await litigationWorkClient.links(workspace.id);if(live){setLinks(result.links);setLinkError('');}}
      catch{if(live)setLinkError('讨论关联读取失败，请刷新案件文件后重试。');}
      finally{if(live)timer=setTimeout(read,5000);}};
    void read();return()=>{live=false;clearTimeout(timer);};
  },[workspace?.id,thread?.id,revision,onStartWork]);
  const editorDiscussion=useEditorDiscussion();
  const [leaveError,setLeaveError]=useState('');
  const leaveEditor=async(action:()=>void)=>{try{if(editorDiscussion)await editorDiscussion.flush();setLeaveError('');action();}catch(error){setLeaveError(error instanceof Error?error.message:'文书尚未保存，请先完成保存。');}};
  useEffect(()=>{editorDiscussion?.setReference(desk.file);},[desk.file,editorDiscussion?.setReference]);
  const awaitingApproval = thread?.status === 'awaiting_approval';
  const binding=thread?links[thread.id]:null;
  const [renaming,setRenaming]=useState(false),[discussionName,setDiscussionName]=useState(''),[renameBusy,setRenameBusy]=useState(false),[renameError,setRenameError]=useState('');
  useEffect(()=>{setRenaming(false);setRenameError('');},[thread?.id]);
  const renameDiscussion=async()=>{if(!thread||!onRenameThread||!discussionName.trim()||renameBusy)return;setRenameBusy(true);setRenameError('');try{await onRenameThread(thread.id,discussionName.trim());setRenaming(false);}catch(error){setRenameError(error instanceof Error?error.message:'会话名称保存失败，请重试。');}finally{setRenameBusy(false);}};

  const locateBinding=(selected:WorkBinding)=>{
    setFocusKey(value=>value+1);
    if(selected.source_path)openFile(selected.source_path);
    else if(PAGES.some(p=>p.id===selected.kind)){closePreview();setDesk(old=>({...old,page:selected.kind}));}
  };
  const fullDiscussion=desk.page==='discussion'&&target===null;
  const assistantVisible=fullDiscussion||(desk.aiOpen&&!focused);
  const refreshKey = `${thread?.id ?? ''}:${thread?.updatedAt ?? ''}:${thread?.status ?? ''}:${revision}`;
  return <section className="litigation-desk" aria-label="诉讼工作台" style={{'--litigation-files-width': `${widths.files}px`, '--litigation-assistant-width': `${widths.assistant}px`} as CSSProperties} data-files-collapsed={filesHidden} data-ai-open={assistantVisible} data-focused={focused} data-discussion-page={fullDiscussion}>
    <header className="litigation-desk-header">
        <nav className="litigation-desk-pages" aria-label="案件工作">
          {PAGES.map(page => <button key={page.id} type="button" disabled={!workspace} aria-current={!target && desk.page === page.id ? 'page' : undefined}
            onClick={() => {closePreview(); setDesk(old => ({...old, page: page.id}));}}>{page.label}</button>)}
        </nav>
      {filesHidden&&<button type="button" aria-label="显示案件文件" onClick={()=>{setShowFiles(true);setFocused(false);}}><Folder size={15}/>文件</button>}
      <button type="button" aria-label={focused?'恢复两侧栏':'展开工作区'} title={focused?'恢复两侧栏':'展开工作区，收起两侧栏'} aria-pressed={focused} onClick={()=>setFocused(v=>!v)}>{focused?<Minimize2 size={15}/>:<Maximize2 size={15}/>}</button>
      <button type="button" hidden={fullDiscussion} aria-expanded={desk.aiOpen&&!focused} onClick={() => {setFocused(false);setDesk(old => ({...old, aiOpen: focused||!old.aiOpen}));}}>
        <MessageSquare size={15}/>{desk.aiOpen&&!focused ? '收起讨论' : awaitingApproval ? '查看待确认操作' : '展开讨论'}
      </button>
    </header>
    {leaveError&&<p role="alert" className="evidence-error">{leaveError}</p>}
    <div className="litigation-desk-body">
      <aside className="litigation-files" aria-label="当前工作区文件" hidden={filesHidden}>
        <div className="litigation-workspace-select">
          <label><span className="visually-hidden">案件工作区</span><select aria-label="案件工作区" value={workspace?.id ?? ''} disabled={busy} onChange={event=>{const id=event.target.value;void leaveEditor(()=>onSelectWorkspace(id));}}>
            <option value="" disabled>选择工作区</option>
            {workspaces.filter(item=>item.allow_read).map(item=><option key={item.id} value={item.id}>{item.display_name}</option>)}
          </select></label>
          <button type="button" aria-label="打开其他文件夹" title="打开其他文件夹" disabled={busy} onClick={()=>void leaveEditor(onChooseWorkspace)}><Folder size={14}/></button>
        </div>
        <div className="litigation-files-heading"><strong>案件文件{materialControls?` · 已选 ${materialControls.selected.length}`:''}</strong><button type="button" aria-label="刷新案件文件" disabled={!workspace} onClick={() => setRevision(v => v + 1)}><RefreshCw size={14}/></button></div>
        <input aria-label="搜索案件文件" placeholder="搜索文件名或路径" value={query} onChange={e=>setQuery(e.target.value)} className="litigation-file-search"/>
        {materialControls&&<div className="litigation-material-selection"><span>{materialControls.hint??(desk.page==='evidence'?'按勾选顺序装册':desk.page==='documents'?'勾选文书依据材料':'勾选本次使用的文件')}</span><button type="button" disabled={materialControls.locked} onClick={materialControls.all}>全选</button><button type="button" disabled={materialControls.locked} onClick={materialControls.clear}>清空</button></div>}
        {query.trim()&&<ul className="litigation-file-tree">{searchResults.map(file=><li key={file.path}><div className="litigation-file-entry"><MaterialCheckbox path={file.path} controls={materialControls}/><button onClick={()=>void openListedFile(file.path)}>{file.path}</button></div></li>)}</ul>}
        <div className="litigation-file-scroll" hidden={!!query.trim()}>{workspace ? <FileBranch workspaceId={workspace.id} controls={materialControls} revision={refreshKey} activePath={desk.file} onOpen={openListedFile}/> : <p className="litigation-file-note">选择工作区后显示案件文件。</p>}</div>
        {fileError && <p role="alert">{fileError}</p>}
        <button type="button" className="litigation-folder-open" disabled={!workspace} onClick={() => {
          if (!workspace) return;
          setFileError(''); void openWorkspacePath(workspace.id, {path: '.', mode: 'open'})
            .catch(() => setFileError('无法打开文件夹，请重试。'));
        }}><Folder size={14}/> 打开案件文件夹</button>
        <PanelResizer panel="files" width={widths.files} onChange={value => resize('files', value)}/>
      </aside>
      <div className="litigation-desk-center" hidden={fullDiscussion}>

        {desk.paths.length > 0 && target !== null && <div className="litigation-file-tabs" aria-label="已打开文件">
          {desk.paths.map(path => <div key={path} className="litigation-file-tab" data-active={desk.file === path}>
            <button type="button" title={path} aria-pressed={desk.file === path} onClick={() => openFile(path)}>{path.split('/').pop()}</button>
            <button type="button" aria-label={`关闭 ${path}`} onClick={() => {
              if (desk.file === path) setTarget(null);
              setDesk(old => ({...old, paths: old.paths.filter(p => p !== path), file: old.file === path ? null : old.file}));
            }}><X size={12}/></button>
          </div>)}
        </div>}
        <div className="litigation-page-content" hidden={target !== null}>
          {workspace ? <LitigationWorkspaceView contentVisible={target===null&&!fullDiscussion} openDocumentRequest={openDocumentRequest} onMaterialsChange={setMaterialState} workspaces={workspaces} selectedWorkspaceId={workspace.id}
            onSelectWorkspace={() => undefined} onChooseWorkspace={() => undefined}
            onStartWorkflow={run} onStartWork={onStartWork} selectedSection={binding?.kind===desk.page?binding.section_id:null} focusKey={focusKey} busy={busy} awaitingApproval={awaitingApproval} refreshKey={refreshKey} onOpenSource={openFile}
            page={desk.page} onNavigate={page => setDesk(old => ({...old, page}))}/> : <div className="litigation-no-case"><Folder size={32} strokeWidth={1.3}/><h2>打开一个案件文件夹</h2><p>合同、证据和文书集中在同一处，从这里开始处理案件。</p><button type="button" onClick={()=>void leaveEditor(onChooseWorkspace)}>选择案件文件夹</button><small>也可以在左侧选择已有工作区</small></div>}
        </div>
        {workspace && target?.source === 'workspace' && onStartWork && <div className="litigation-file-discussion"><button type="button" disabled={busy} onClick={()=>{
          const path=target.path;
          void litigationWorkClient.read(workspace.id,'facts').then(record=>{onStartWork({kind:'facts',section_id:null,source_path:path,materials:[path],expected_revision:record.revision,title:path.split('/').pop()??path});}).catch(()=>setFileError('无法建立文件讨论，请重试。'));
        }}>讨论此文件</button></div>}
        {workspace && target && <PreviewRail key={`${revision}:${target.source === 'workspace' ? target.path : target.source}`} embedded open target={target}
          threadId={thread?.backendId ?? null} workspaceId={workspace.id} artifactsRevision={revision}
          onClose={closePreview} onOpenWorkspaceFile={(id, path) => openPreview({source: 'workspace', workspaceId: id, path})}
          onOpenCitation={source => openFile(source.path, source.page ?? undefined, source)}
          onOpenList={() => openPreview({source: 'artifacts-list'})}/>}
      </div>
      <aside className="litigation-assistant" aria-label="案件 AI 助手" hidden={!assistantVisible}>
        {!fullDiscussion&&<PanelResizer panel="assistant" width={widths.assistant} onChange={value => resize('assistant', value)}/>}
        <div className="litigation-assistant-heading">
          {renaming?<form className="litigation-discussion-rename" onSubmit={event=>{event.preventDefault();void renameDiscussion();}}>
            <input autoFocus aria-label="会话名称" maxLength={100} value={discussionName} disabled={renameBusy} onChange={event=>setDiscussionName(event.target.value)} onKeyDown={event=>{if(event.key==='Escape'&&!renameBusy)setRenaming(false);}}/>
            <button type="submit" aria-label="保存会话名称" title="保存名称" disabled={renameBusy||!discussionName.trim()}><Check size={15}/></button><button type="button" aria-label="取消改名" title="取消" disabled={renameBusy} onClick={()=>setRenaming(false)}><X size={15}/></button>
          </form>:<>
            <select className="litigation-discussion-picker" aria-label="当前会话" value={thread?.id??''} disabled={busy||renameBusy} onChange={event=>void leaveEditor(()=>onSelectThread(event.target.value))}>
              <option value="" disabled>新讨论</option>{threads.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
            {thread&&onRenameThread&&<button type="button" aria-label="重命名当前会话" title="重命名当前会话" disabled={busy} onClick={()=>{setDiscussionName(thread.title);setRenameError('');setRenaming(true);}}><Pencil size={14}/></button>}
          </>}
          {!fullDiscussion&&(editorDiscussion?.target||(binding&&(binding.source_path||PAGES.some(p=>p.id===binding.kind))))&&<button type="button" aria-label="定位讨论对象" title={editorDiscussion?.target?`定位：${editorDiscussion.target.title}`:'定位讨论对象'} onClick={()=>{if(editorDiscussion?.target){closePreview();editorDiscussion.target.locate?.();setDesk(old=>({...old,page:PAGES.some(p=>p.id===editorDiscussion.target!.kind)?editorDiscussion.target!.kind as LitigationPage:'documents'}));}else if(binding)locateBinding(binding);}}><Crosshair size={15}/></button>}
          <button type="button" disabled={busy||renameBusy||!workspace} onClick={()=>void leaveEditor(()=>{if(!thread)editorDiscussion?.setTarget(null);onNewDiscussion();})}>新讨论</button>
        </div>
        {renameError&&<p role="alert" className="evidence-error">{renameError}</p>}
        {linkError&&<p role="alert">{linkError}</p>}
        {!fullDiscussion&&editorDiscussion?.target&&(editorDiscussion.reference&&editorDiscussion.reference!==editorDiscussion.target.path||editorDiscussion.selection)&&<div className="litigation-discussion-context">{editorDiscussion.reference&&editorDiscussion.reference!==editorDiscussion.target.path&&<span>参考：{editorDiscussion.reference.split('/').pop()}</span>}{editorDiscussion.selection&&<button type="button" onClick={()=>{editorDiscussion.target?.clearSelection();editorDiscussion.setSelection(null);}}>已选 {editorDiscussion.selection.text.length} 字 ×</button>}</div>}
        {!fullDiscussion&&workspace&&editorDiscussion?.target&&editorDiscussion.target.kind!=='documents'&&<EditorProposals workspaceId={workspace.id}/>}
        <LitigationMaterialsContext.Provider value={workspace?{workspaceId:workspace.id,paths:materialControls?.selected??[]}:null}><OrdinaryDiscussion enabled={fullDiscussion}>{renderAssistant(openPreview,fullDiscussion)}</OrdinaryDiscussion></LitigationMaterialsContext.Provider>
      </aside>
    </div>
  </section>;
}
