import {LitigationDrafting} from '../components/editor/LitigationDrafting';
import { useEffect, useRef, useState } from 'react';
import {ArrowRight, FolderOpen} from 'lucide-react';
import type { WorkspaceRecord } from '../runtime/contracts';
import { litigationClient, type LitigationCaseInfo, type LitigationFact, type LitigationKind, type LitigationMaterial, type LitigationSnapshot, type LitigationSource } from './LitigationClient';
import './LitigationWorkspaceView.css';
import {LitigationGuide, guidePrompt, LITIGATION_WORK, ACTIVE_WORK_KINDS} from './LitigationGuide';
import {LitigationWorkProduct} from './LitigationWorkProduct';
import type {WorkRequest} from './LitigationClient';
import {LitigationFactHistory} from './LitigationFactHistory';
import {LitigationDocuments} from './LitigationDocuments';
import {LitigationElementsReview} from './LitigationElementsReview';

const STRENGTH: Record<string,string> = {strong:'强',medium:'中',weak:'弱',missing:'缺失'};
const EMPTY_INFO: LitigationCaseInfo = { title: '', cause: '', plaintiff: '', defendant: '', position: '', theory: '' };
const CASE_LABELS = {title:'案件名称',cause:'案由',plaintiff:'指控／请求方',defendant:'被指控／被请求方',position:'我方立场',theory:'一句话案件理论'} as const;
const WORKFLOWS: {kind: LitigationKind; name: string; description: string}[] = [
  {kind:'facts', name:'事实梳理', description:'按时间整理事实、原文出处和待确认事项'},
  {kind:'evidence', name:'证据整理', description:'按证明目的分组，分开生成目录与内部评估'},
];

export type LitigationPage = 'overview' | LitigationKind | 'documents' | 'discussion';

export interface LitigationMaterialControls {
  hint?:string;
  workspaceId:string|null; materials:LitigationMaterial[]; selected:string[]; locked:boolean;
  toggle:(path:string)=>void; all:()=>void; clear:()=>void;
}

export interface LitigationWorkspaceViewProps {
  contentVisible?: boolean;
  page?: LitigationPage;
  openDocumentRequest?: {id:string;sequence:number}|null;
  onMaterialsChange?:(controls:LitigationMaterialControls)=>void;
  onStartWork?: (request: WorkRequest) => void;
  selectedSection?: string | null;
  focusKey?: number;
  onNavigate?: (page: LitigationPage) => void;
  onCaseTitleChange?: (title: string) => void;
  workspaces: readonly WorkspaceRecord[];
  selectedWorkspaceId: string | null;
  onSelectWorkspace: (id: string) => void;
  onChooseWorkspace: () => void;
  onStartWorkflow: (message: string) => void;
  busy: boolean;
  awaitingApproval?: boolean;
  refreshKey?: string | number;
  onOpenSource?: (path: string, page?: number, citation?: import("./LitigationClient").LitigationSource) => void;
}

export function LitigationWorkspaceView({contentVisible=true,openDocumentRequest,workspaces, selectedWorkspaceId, onSelectWorkspace, onChooseWorkspace, onStartWorkflow, busy, awaitingApproval = false, refreshKey, onOpenSource, page:requestedPage, onCaseTitleChange, onNavigate, onStartWork, selectedSection, focusKey, onMaterialsChange}: LitigationWorkspaceViewProps) {
  const page = requestedPage==='issues'||requestedPage==='strategy' ? 'facts' : requestedPage;
  const [snapshot, setSnapshot] = useState<LitigationSnapshot | null>(null);
  const [materials, setMaterials] = useState<LitigationMaterial[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [draftControls,setDraftControls]=useState<LitigationMaterialControls|null>(null);
  const [evidenceControls,setEvidenceControls]=useState<LitigationMaterialControls|null>(null);
  const [info, setInfo] = useState<LitigationCaseInfo>(EMPTY_INFO);
  const [editingFact, setEditingFact] = useState<LitigationFact | null>(null);
  const [error, setError] = useState('');
  const [dirtyInfo, setDirtyInfo] = useState(false);
  const dirtyInfoRef = useRef(false); dirtyInfoRef.current = dirtyInfo;
  const infoRevision = useRef(0);
  const factRevision = useRef(0);
  const loadedWorkspace = useRef<string | null>(null);
  const materialsInitialized = useRef(false);
  const [saving, setSaving] = useState(false);
  const [reload, setReload] = useState(0);
  const [localActive, setActive] = useState<LitigationKind>('facts');
  const active = page && page !== 'overview' && page !== 'documents' && page !== 'discussion' ? page : localActive;
  const showResults = !page || !['overview', 'documents', 'discussion'].includes(page);
  const [exportKind, setExportKind] = useState<LitigationKind | null>(null);
  const draggedEvidence = useRef<{id: string; revision: number} | null>(null);
  const requestEpoch = useRef(0);
  const currentWorkspace = useRef(selectedWorkspaceId);
  currentWorkspace.current = selectedWorkspaceId;

  useEffect(() => {
    const epoch = ++requestEpoch.current;
    const switched = loadedWorkspace.current !== selectedWorkspaceId;
    loadedWorkspace.current = selectedWorkspaceId;
    setError('');
    if (switched) {materialsInitialized.current=false;setSnapshot(null); setMaterials([]); setSelected([]); setEditingFact(null); setExportKind(null);setInfo(EMPTY_INFO);setDirtyInfo(false);dirtyInfoRef.current=false;}
    if (!selectedWorkspaceId) return;
    void Promise.all([litigationClient.snapshot(selectedWorkspaceId), litigationClient.materials(selectedWorkspaceId)]).then(([next, listing]) => {
      if (requestEpoch.current !== epoch) return;
      setSnapshot(next);
      if(!dirtyInfoRef.current){setInfo(next.case.data ?? EMPTY_INFO);infoRevision.current=next.case.revision;}
      setMaterials(listing.materials);

      materialsInitialized.current = true;
      setSelected(old => old.filter(path=>listing.materials.some(m=>m.path===path && m.supported)));
      if (listing.truncated) setError('材料较多，当前仅显示已扫描的部分；请缩小工作区材料范围后刷新。');
    }).catch(reason => { if (requestEpoch.current === epoch) setError(reason instanceof Error ? reason.message : '案件读取失败，请重试。'); });
    return () => { requestEpoch.current++; };
  }, [selectedWorkspaceId, refreshKey, reload]);

  const perform = async (operation: () => Promise<unknown>) => {
    const workspaceId = selectedWorkspaceId;
    setSaving(true); setError('');
    try { await operation(); if (currentWorkspace.current === workspaceId) { setEditingFact(null); setReload(v => v+1); } return true; }
    catch (reason) { if (currentWorkspace.current === workspaceId) setError(reason instanceof Error ? reason.message : '保存失败，请重试。'); return false; }
    finally { setSaving(false); }
  };
  const sources = (items: LitigationSource[]) => items.map((source, index) => <li key={`${source.path}-${index}`}>
    {onOpenSource ? <button type="button" className="litigation-source" onClick={() => onOpenSource(source.path,source.page??undefined,source)}>{source.path}{source.page ? ` · 第${source.page}页` : ' · 文件'}</button> : <span>{source.path}{source.page ? ` · 第${source.page}页` : ' · 文件'}</span>}
    <blockquote>{source.quote}</blockquote>
  </li>);
  const startStructured = (kind: LitigationKind, replaceFacts = false) => {
    if(dirtyInfo){setError("请先保存案件信息，再开始处理。");return;}
    setActive(kind);
    const name = WORKFLOWS.find(item => item.kind === kind)!.name;
    onStartWorkflow(`请更新本案件的旧版结构化“${name}”记录。仅使用以下选中材料（JSON路径列表）：${JSON.stringify(selected)}。先读取litigation_read的case和${kind}记录，读取材料原文后按返回schema保存结构化产物；材料只读，${replaceFacts ? '我已在工作台选择整体重新梳理，请确认facts中的regeneration_revision，以mode=replace保存新事实，旧版已保留。' : '事实默认增量合并，保留已核对条目及冲突。'}${kind === 'issues' ? '先读elements；若未确认，保存要件清单供我在工作台核对，并结束本回合。已确认则严格按清单核验法条与证据并保存焦点。' : ''}`);
  };
  const pending = snapshot?.facts.data?.facts.filter(f => f.status === 'pending' || f.pending.length > 0).length ?? 0;
  const locked = busy || saving || !snapshot;
  const staleDependencies = (dependencies: Record<string,number> | undefined) => !!snapshot && !!dependencies && Object.entries(dependencies).some(([kind,revision])=>snapshot[kind as keyof LitigationSnapshot]?.revision !== revision);
  const elementsStale = !!snapshot?.elements?.data && (!snapshot.elements.data.confirmed || snapshot.elements.data.case_revision !== snapshot.case.revision);
  const issuesStale = !!snapshot?.issues.data && (staleDependencies(snapshot.issues.data.dependencies) || elementsStale || (snapshot.issues.data.elements_revision !== undefined && snapshot.issues.data.elements_revision !== snapshot.elements?.revision));
  const strategyStale = !!snapshot?.strategy.data && (issuesStale || staleDependencies(snapshot.strategy.data.dependencies) || (snapshot.strategy.data.issues_revision !== undefined && snapshot.strategy.data.issues_revision !== snapshot.issues.revision));
  const activeStale = active === 'issues' ? issuesStale : active === 'strategy' ? strategyStale : false;
  const workspace = workspaces.find(item => item.id === selectedWorkspaceId);
  useEffect(() => {onCaseTitleChange?.(snapshot?.case.data?.title || workspace?.display_name || '诉讼工作');}, [snapshot?.case.data?.title, workspace?.display_name, onCaseTitleChange]);

  useEffect(()=>{
    if(page==='documents'&&onStartWork){if(draftControls?.workspaceId===selectedWorkspaceId)onMaterialsChange?.(draftControls);else onMaterialsChange?.({workspaceId:selectedWorkspaceId,materials:[],selected:[],locked:true,toggle:()=>{},all:()=>{},clear:()=>{}});return;}
    if(page==='evidence'){if(evidenceControls?.workspaceId===selectedWorkspaceId)onMaterialsChange?.(evidenceControls);else onMaterialsChange?.({workspaceId:selectedWorkspaceId,materials:[],selected:[],locked:true,toggle:()=>{},all:()=>{},clear:()=>{}});return;}
    onMaterialsChange?.({workspaceId:selectedWorkspaceId,materials,selected,locked,
      toggle:path=>{if(!locked&&materials.some(m=>m.path===path&&m.supported))setSelected(old=>old.includes(path)?old.filter(p=>p!==path):[...old,path]);},
      all:()=>{if(!locked)setSelected(materials.filter(m=>m.supported).map(m=>m.path));},
      clear:()=>{if(!locked)setSelected([]);},
    });
  },[onMaterialsChange,selectedWorkspaceId,materials,selected,locked,page,evidenceControls,draftControls,!!onStartWork]);
  return <section className={`litigation-workspace${page ? " litigation-workspace-embedded" : ""}`} aria-label="诉讼工作">
    {!page && <><header className="litigation-header"><div><h1>诉讼工作</h1><p>整理案件事实、制作证据册与目录、起草文书</p></div><button type="button" onClick={onChooseWorkspace} disabled={busy}>选择工作区</button></header>
    <label className="litigation-workspace-picker">案件工作区<select value={selectedWorkspaceId ?? ''} onChange={event => onSelectWorkspace(event.target.value)} disabled={busy}>
      <option value="" disabled>选择装有案件材料的文件夹</option>{workspaces.filter(w => w.allow_read).map(w => <option key={w.id} value={w.id}>{w.display_name}</option>)}
    </select></label></>}
    {error && <p role="alert" className="litigation-error">{error} <button type="button" onClick={() => setReload(v => v+1)}>重新读取</button></p>}
    {!selectedWorkspaceId ? <p className="litigation-empty">选择已有工作区，或点“选择工作区”打开案件材料文件夹。</p> : <>
      {page === 'overview' && onNavigate && <section className="litigation-start" aria-label="开始案件工作">
        <div className="litigation-start-heading"><span className="litigation-kicker"><FolderOpen size={15}/>案件工作台</span><h2>这次要处理什么？</h2><p>选择一项工作开始，或点击左侧文件查看材料。</p></div>
        <div className="litigation-action-list">{ACTIVE_WORK_KINDS.map(kind=>{
          const work=LITIGATION_WORK[kind]; const Icon=work.icon;
          return <button type="button" key={kind} onClick={()=>onNavigate(kind)}><span className="litigation-action-icon"><Icon size={21} strokeWidth={1.6}/></span><span><strong>{work.title}</strong><small>{work.outcome}</small></span><ArrowRight size={17}/></button>;
        })}</div>
      </section>}
      <div hidden={!!page && page !== 'overview'}><details className="litigation-case-info" open={page && !onNavigate ? true : undefined}><summary>案件信息{info.title ? ` · ${info.title}` : workspace ? ` · ${workspace.display_name}` : ''}</summary>
        <button type="button" disabled={locked || dirtyInfo || !selected.length || !workspace?.allow_write} onClick={()=>onStartWorkflow(`请识别本案件的案件名称、案由及双方主体，写明诉讼身份。plaintiff字段兼容记录指控／请求方（如公诉机关、自诉人、原告），defendant记录被指控／被请求方（如被告人、被告）；按材料身份填写，不将刑事案件套成民事案件。仅使用以下选中材料（JSON路径列表）：${JSON.stringify(selected)}。先用litigation_read读取case与case_suggestions，读取原文后按case_suggestions的schema保存候选与出处。不要修改既有案件信息；案由说明推断依据，拿不准先问我；我方立场和案件理论由我填写。保存后让我在案件概况核对。`)}>从所选材料识别案件信息</button>
        {snapshot?.case_suggestions?.data && <div className="litigation-case-suggestions" aria-label="案件信息候选"><h3>材料识别结果 · 待你核对</h3>
          {snapshot.case_suggestions.data.case_revision !== snapshot.case.revision && <p className="litigation-pending">案件信息已有更新，请重新识别后再采用候选。</p>}
          {snapshot.case_suggestions.data.fields.map(field=><article key={field.key}><strong>{CASE_LABELS[field.key]}：{field.value}</strong>{field.reason && <p>{field.reason}</p>}<details><summary>查看识别依据</summary><ul>{sources(field.sources)}</ul></details><button type="button" disabled={locked || !workspace?.allow_write || snapshot.case_suggestions?.data?.case_revision !== snapshot.case.revision || info[field.key] === field.value} onClick={()=>{setInfo(old=>({...old,[field.key]:field.value}));setDirtyInfo(true);}}>采用{CASE_LABELS[field.key]}</button></article>)}
          {!!snapshot.case_suggestions.data.gaps.length && <><h4>仍待确认</h4><ul>{snapshot.case_suggestions.data.gaps.map((gap,index)=><li key={index}>{gap}</li>)}</ul></>}
          <p>采用后仍可在下方修改，点“保存案件信息”才会生效。我方立场和案件理论由你填写。</p>
        </div>}
        <form onSubmit={event => {event.preventDefault(); if (snapshot) void perform(() => litigationClient.saveCase(selectedWorkspaceId, info, infoRevision.current).then(result=>{setDirtyInfo(false);dirtyInfoRef.current=false;return result;}));}}>
          {([['title','案件名称'],['cause','案由'],['plaintiff','指控／请求方'],['defendant','被指控／被请求方'],['position','我方立场'],['theory','一句话案件理论']] as const).map(([key, label]) => <label key={key}>{label}<input value={info[key]} onChange={event => {setInfo({...info,[key]:event.target.value});setDirtyInfo(true);}} disabled={locked} /></label>)}
          <button type="submit" disabled={locked || !workspace?.allow_write || !dirtyInfo}>保存案件信息</button>
        </form>
      </details>
      </div>
      {!onMaterialsChange&&<details className="litigation-materials" open={!page} hidden={page === 'overview' && !!onNavigate}><summary>本次材料 · 已选 {selected.length} 份</summary>
        {!materials.length && <p>工作区内尚未找到支持的材料。放入文字、Word 或文字版 PDF 后点“刷新”。</p>}
        <div className="litigation-material-actions"><button type="button" disabled={locked} onClick={() => setSelected(materials.filter(m => m.supported).map(m => m.path))}>全选可读材料</button><button type="button" disabled={locked} onClick={() => setSelected([])}>取消全选</button><button type="button" disabled={busy} onClick={() => setReload(v=>v+1)}>刷新</button></div>
        {materials.some(material => selected.includes(material.path) && /\.pdf$/i.test(material.path)) && <p className="litigation-reading-note">PDF 仅读取文字层；图片、扫描页和乱码仍需核对。</p>}
        <ul>{materials.map(material => <li key={material.path}><label><input type="checkbox" checked={selected.includes(material.path)} disabled={!material.supported || locked} onChange={event => setSelected(old => event.target.checked ? [...old,material.path] : old.filter(path => path !== material.path))} />{material.path}</label>{material.reason && <span>{material.reason}</span>}</li>)}</ul>
      </details>
      }
      {page === 'overview' && !!(snapshot?.facts.data || snapshot?.evidence.data) && <p className="litigation-overview-status">已保存事实 {snapshot?.facts.data?.facts.length ?? 0} 条 · 证据 {snapshot?.evidence.data?.evidence.length ?? 0} 项 · 待核对事实 {pending} 条</p>}
      {snapshot && !onStartWork && <div hidden={page !== 'documents'}><LitigationDocuments key={selectedWorkspaceId} workspaceId={selectedWorkspaceId} visible={page === 'documents'} record={snapshot.documents ?? {revision:0,data:null}} stale={staleDependencies(snapshot.documents?.data?.dependencies)} disabled={locked} legacyDisabled={locked || dirtyInfo || !workspace?.allow_write} selectedMaterials={selected} onStart={onStartWorkflow} onOpenSource={onOpenSource} onSaved={record=>{requestEpoch.current++;setSnapshot(old=>old ? {...old,documents:record}:old);}} /></div>}
      {!page && <><div className="litigation-workflow-grid">{WORKFLOWS.map(item => <button type="button" key={item.kind} disabled={locked} onClick={() => {setActive(item.kind); onStartWorkflow(guidePrompt(item.kind, item.description, '', selected));}}><strong>{item.name}</strong><span>{item.description}</span></button>)}</div>
      <div className="litigation-result-tabs" role="tablist" aria-label="案件产物">{WORKFLOWS.map(item => <button type="button" role="tab" aria-selected={active===item.kind} key={item.kind} onClick={() => setActive(item.kind)}>{item.name}{snapshot?.[item.kind].data ? ' · 已生成' : ''}</button>)}</div>
      </>}
      {onStartWork && page && ACTIVE_WORK_KINDS.filter(kind=>kind!=='documents').map(kind=><div key={`${selectedWorkspaceId}:${kind}`} hidden={page!==kind}><LitigationWorkProduct workspaceId={selectedWorkspaceId} kind={kind} visible={page===kind} materialFiles={materials} onEvidenceControls={setEvidenceControls} materials={selected} busy={busy} awaitingApproval={awaitingApproval} refreshKey={refreshKey??''} onStart={onStartWork} onOpenSource={onOpenSource} selectedSection={selectedSection} focusKey={focusKey} ready={!locked} canWrite={!!workspace?.allow_write}/></div>)}
      {onStartWork&&page&&<div className="litigation-drafting-host" hidden={page!=='documents'}><LitigationDrafting openDocumentRequest={openDocumentRequest} key={selectedWorkspaceId} workspaceId={selectedWorkspaceId} materials={materials} visible={contentVisible&&page==='documents'} busy={busy} canWrite={!!workspace?.allow_write} onStart={onStartWork} onControlsChange={setDraftControls}/></div>}
      <div hidden={!showResults}>
      {page && !onStartWork && WORKFLOWS.map(item => <div key={`${selectedWorkspaceId}:${item.kind}`} hidden={active !== item.kind}><LitigationGuide kind={item.kind} materials={selected} disabled={locked} onStart={onStartWorkflow}/></div>)}
      {snapshot?.[active].data && <div className="litigation-page-heading"><h3>已保存的分析记录</h3><button type="button" disabled={locked || dirtyInfo || !workspace?.allow_write} onClick={()=>startStructured(active)}>更新已保存记录</button></div>}
      <div className="litigation-results" role="tabpanel" aria-label={WORKFLOWS.find(w=>w.kind===active)?.name}>
        {activeStale && <p role="status" className="litigation-pending">案件资料已有更新，下方是旧版结果。请先更新争议焦点{active === 'strategy' ? '，再更新策略备忘' : '并核对要件'}后导出。</p>}
        {snapshot?.elements?.data && <div hidden={active !== 'issues'}><LitigationElementsReview key={selectedWorkspaceId} record={snapshot.elements} disabled={locked || dirtyInfo || !workspace?.allow_write} onConfirm={async(data, revision)=>{
          let saved: Awaited<ReturnType<typeof litigationClient.confirmElements>> | null = null;
          await perform(async()=>{
            saved = await litigationClient.confirmElements(selectedWorkspaceId,data,revision);
            if(currentWorkspace.current===selectedWorkspaceId){
              requestEpoch.current++; const record = saved;
              setSnapshot(old=>old ? {...old,elements:record}:old); startStructured('issues');
            }
          });
          return saved;
        }} /></div>}
        {!snapshot?.[active].data ? null : <><div className="litigation-result-heading"><p>AI 草稿，须律师审查 · 待确认事实 {pending} 条</p><button type="button" disabled={locked || activeStale} onClick={() => setExportKind(active)}>导出{active === 'evidence' ? '目录与内部评估' : '产物'}</button></div>
          {active === 'facts' && snapshot.facts.data && <>
            <div className="litigation-material-actions"><button type="button" disabled={locked || dirtyInfo || !selected.length || !workspace?.allow_write} onClick={()=>{
              void perform(async()=>{const facts=await litigationClient.regenerateFacts(selectedWorkspaceId,snapshot.facts.revision);if(currentWorkspace.current===selectedWorkspaceId){requestEpoch.current++;setSnapshot(old=>old ? {...old,facts}:old);startStructured('facts',true);}});
            }}>整体重新梳理</button><LitigationFactHistory key={selectedWorkspaceId} workspaceId={selectedWorkspaceId} revision={snapshot.facts.revision} disabled={locked || !workspace?.allow_write} onRestore={(revision,expected)=>perform(async()=>{
              const facts=await litigationClient.restoreFacts(selectedWorkspaceId,revision,expected);
              if(currentWorkspace.current===selectedWorkspaceId){requestEpoch.current++;setSnapshot(old=>old ? {...old,facts}:old);}
            })} /></div>
            {snapshot.facts.data.facts.map(fact => <article className="litigation-fact" key={fact.id}><div><strong>{fact.date || '日期待确认'}</strong><span className={!fact.pending.length && fact.user_note ? 'litigation-user-supplied' : ''}>{fact.pending.length ? '待确认' : fact.status==='confirmed' ? '用户已核对' : fact.user_note ? '用户已补充' : '有材料支持'}</span><span>重要性：{{high:'高',medium:'中',low:'低'}[fact.importance]}</span></div><p>{fact.text}</p><ul>{sources(fact.sources)}</ul>{fact.pending.length>0 && <p className="litigation-pending">{fact.pending.join('；')}</p>}{fact.user_note && <p>用户核对：{fact.user_note}</p>}<button type="button" disabled={locked} onClick={() => {factRevision.current=snapshot.facts.revision;setEditingFact({...fact});}}>核对或修改</button></article>)}
            <h3>缺口报告</h3><ul>{snapshot.facts.data.gaps.map((gap,index)=><li key={index}>{gap}</li>)}</ul><h3>法律关系小结</h3><p>{snapshot.facts.data.relationships}</p>
            {snapshot.facts.data.conflicts.map((conflict,index) => <article className="litigation-pending" key={index}><h3>新旧事实冲突 · {conflict.id}</h3><p>已保留：{conflict.previous.text}</p><p>新提议：{conflict.proposed.text}</p></article>)}
          </>}
          {active === 'evidence' && snapshot.evidence.data && <>{snapshot.evidence.data.evidence.map((item,index,array) => <article className="litigation-fact" key={item.id} onDragOver={event=>{if(draggedEvidence.current && !locked) event.preventDefault();}} onDrop={event=>{
            event.preventDefault();const drag=draggedEvidence.current;draggedEvidence.current=null;
            if(!drag || locked || drag.id===item.id) return;
            const source=array.findIndex(e=>e.id===drag.id);if(source<0) return;
            const ordered=[...array];const [moved]=ordered.splice(source,1);ordered.splice(index,0,moved);
            void perform(()=>litigationClient.arrangeEvidence(selectedWorkspaceId,ordered.map(e=>({id:e.id,group:e.group})),drag.revision));
          }}><button type="button" draggable={!locked} disabled={locked} aria-label={`拖动${item.name}调整顺序`} onDragStart={event=>{draggedEvidence.current={id:item.id,revision:snapshot.evidence.revision};event.dataTransfer.setData('text/plain',item.id);event.dataTransfer.effectAllowed='move';}} onDragEnd={()=>{draggedEvidence.current=null;}}>拖动排序</button><strong>{index+1}. {item.name} · {item.group}</strong><p>{item.kind} · 证明目的：{item.purpose}</p><ul>{sources(item.sources)}</ul><details><summary>内部评估 · 请律师复核</summary><p>证明力：{STRENGTH[item.strength] ?? item.strength}</p><p>{item.assessment}</p></details><label>分组<input aria-label={`${item.name}分组`} defaultValue={item.group} disabled={locked} onBlur={event => {if (event.target.value.trim() && event.target.value !== item.group) void perform(() => litigationClient.arrangeEvidence(selectedWorkspaceId, array.map(e=>({id:e.id,group:e.id===item.id ? event.target.value.trim():e.group})), snapshot.evidence.revision));}} /></label><button type="button" disabled={locked || index===0} onClick={() => {const ordered=[...array];[ordered[index-1],ordered[index]]=[ordered[index],ordered[index-1]];void perform(()=>litigationClient.arrangeEvidence(selectedWorkspaceId,ordered.map(e=>({id:e.id,group:e.group})),snapshot.evidence.revision));}}>上移</button><button type="button" disabled={locked || index===array.length-1} onClick={()=>{const ordered=[...array];[ordered[index],ordered[index+1]]=[ordered[index+1],ordered[index]];void perform(()=>litigationClient.arrangeEvidence(selectedWorkspaceId,ordered.map(e=>({id:e.id,group:e.group})),snapshot.evidence.revision));}}>下移</button></article>)}<h3>证据缺口</h3><ul>{snapshot.evidence.data.gaps.map((v,i)=><li key={i}>{v}</li>)}</ul><h3>证据矛盾</h3><ul>{snapshot.evidence.data.contradictions.map((v,i)=><li key={i}>{v}</li>)}</ul><h3>补证建议</h3><ul>{snapshot.evidence.data.suggestions.map((v,i)=><li key={i}>{v}</li>)}</ul></>}
          {active === 'issues' && snapshot.issues.data?.issues.map((issue,index) => <article className="litigation-fact" key={index}><h3>{issue.title}</h3>{issue.elements.map((element,i)=><div key={i}><h4>{element.name}</h4><p>支持程度：{STRENGTH[element.strength] ?? element.strength} · 请律师复核</p>{element.gap && <p className="litigation-pending">缺口：{element.gap}</p>}<p>支持证据</p><ul>{sources(element.supporting)}</ul><p>反驳证据</p><ul>{sources(element.opposing)}</ul></div>)}{issue.laws.map((law,i)=><blockquote key={i}>法条摘录 · {law.clause}<p>{law.quote}</p></blockquote>)}</article>)}
          {active === 'strategy' && snapshot.strategy.data && ([['favorable','有利因素'],['unfavorable','不利因素'],['defenses','对方可能的抗辩'],['actions','补证与行动建议'],['opposing_counsel','对方会怎么打我们']] as const).map(([key,title])=><div key={key}><h3>{title}</h3><ul>{snapshot.strategy.data![key].map((item,i)=><li key={i}>{item}</li>)}</ul></div>)}
        </>}
      </div>
      </div>
      {editingFact && <form className="litigation-review" aria-label="核对事实" onSubmit={event => {event.preventDefault();if (snapshot) void perform(()=>litigationClient.reviewFact(selectedWorkspaceId,editingFact,factRevision.current));}}><h3>核对事实</h3><label>日期<input value={editingFact.date} onChange={event=>setEditingFact({...editingFact,date:event.target.value})} /></label><label>事实内容<textarea value={editingFact.text} onChange={event=>setEditingFact({...editingFact,text:event.target.value})} required /></label><label>仍待确认的事项（每行一项）<textarea value={editingFact.pending.join('\n')} onChange={event=>setEditingFact({...editingFact,pending:event.target.value.split('\n').filter(Boolean)})} /></label><label>核对说明<textarea value={editingFact.user_note} onChange={event=>setEditingFact({...editingFact,user_note:event.target.value})} required /></label><button type="submit" disabled={saving || busy}>保存核对</button><button type="button" onClick={()=>setEditingFact(null)}>取消</button></form>}
      {exportKind && <div className="litigation-review" role="dialog" aria-label="导出核验状态"><h3>导出前核对</h3><p>待确认事实 {pending} 条；事实冲突 {snapshot?.facts.data?.conflicts.length ?? 0} 条。</p><p>导出保留待确认标记与“AI 草稿，须律师审查”。证据目录与内部评估分别保存。</p><button type="button" disabled={locked} onClick={()=>{onStartWorkflow(`请调用litigation_export导出本案件已保存的${exportKind}产物，保留核验汇总与全部待确认标记。`);setExportKind(null);}}>生成导出文件</button><button type="button" onClick={()=>setExportKind(null)}>返回核对</button></div>}
    </>}
  </section>;
}
