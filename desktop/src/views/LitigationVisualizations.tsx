import {useEffect,useId,useRef,useState} from 'react';
import {CalendarDays,GitBranch,GitCompareArrows,History,RefreshCw,Plus,MoreHorizontal,Download} from 'lucide-react';
import {runtimeRequest} from '../runtime/api';
import {DiagramSources} from '../components/preview/DiagramSources';
import {diagramFilename,diagramPng,saveDiagramExport} from '../platform/diagramExport';
import {diagramViewport,type DiagramViewport} from '../platform/diagramViewport';
import type {LitigationSource} from './LitigationClient';
import './LitigationVisualizations.css';

export const DIAGRAM_TYPES=[
  {kind:'timeline',label:'时间轴',description:'按时间顺序串联签约、付款、交付等关键事实，直观看清案件经过和先后关系。',icon:History},
  {kind:'chronicle',label:'大事记',description:'将日期与具体经过逐项整理成表，适合全面核对案件事实；内容较多时自动分页。',icon:CalendarDays},
  {kind:'relationship',label:'关系图',description:'用节点和连线展示当事人、标的及其法律关系，适合理清交易结构和各方联系。',icon:GitBranch},
  {kind:'confrontation',label:'对抗图',description:'围绕争议焦点，分栏对照一方主张、需要审查的问题和另一方抗辩，帮助梳理庭审攻防。',icon:GitCompareArrows},
] as const;
interface Diagram {path:string;title:string;kind:string;pages:string[];updated_at:number}
interface Listing {diagrams:Diagram[];errors:{path:string;notice:string}[];truncated:boolean}
interface Preview {path:string;content:string;file_hash:string;viewport?:DiagramViewport}
export function LitigationVisualizations({workspaceId,visible,busy,canWrite,ready,refreshKey,onGenerate,onOpenSource}: {
  workspaceId:string;visible:boolean;busy:boolean;canWrite:boolean;ready:boolean;refreshKey:string|number;
  onGenerate:(title:string,message:string)=>void;
  onOpenSource?:(path:string,page?:number,citation?:LitigationSource)=>void;
}) {
  const descriptionId=useId();
  const [describedKind,setDescribedKind]=useState<string|null>(null);
  const describedType=DIAGRAM_TYPES.find(type=>type.kind===describedKind);
  useEffect(()=>{
    if(!describedKind)return;
    const dismiss=(event:KeyboardEvent)=>{if(event.key==='Escape'){setDescribedKind(null);event.stopPropagation();}};
    document.addEventListener('keydown',dismiss,true);
    return()=>document.removeEventListener('keydown',dismiss,true);
  },[describedKind]);
  const [kind,setKind]=useState<typeof DIAGRAM_TYPES[number]['kind']>('timeline');
  const [request,setRequest]=useState(''),[listing,setListing]=useState<Listing|null>(null),[error,setError]=useState('');
  const [selected,setSelected]=useState(''),[page,setPage]=useState(0),[reload,setReload]=useState(0),[zoom,setZoom]=useState('width');
  const [preview,setPreview]=useState<Preview|null>(null),[previewError,setPreviewError]=useState('');
  const [exporting,setExporting]=useState(false),[notice,setNotice]=useState(''),[exportError,setExportError]=useState('');
  const [creating,setCreating]=useState(false);
  const canvas=useRef<HTMLDivElement>(null);
  const [canvasSize,setCanvasSize]=useState({width:800,height:500});
  const [redrawing,setRedrawing]=useState(false),[redrawRequest,setRedrawRequest]=useState('');
  const previousPaths=useRef<string[]|null>(null);
  const currentWorkspace=useRef(workspaceId);currentWorkspace.current=workspaceId;
  const key=`verilex:litigation-diagram:${workspaceId}`;
  const base=`/v1/litigation/workspaces/${encodeURIComponent(workspaceId)}`;
  useEffect(()=>{
    setExporting(false);setCreating(false);setRedrawing(false);setRequest('');setRedrawRequest('');setListing(null);setSelected('');setPage(0);setPreview(null);setNotice('');setError('');setPreviewError('');setExportError('');previousPaths.current=null;
  },[workspaceId]);
  useEffect(()=>{
    if(!visible)return;let live=true;let timer:ReturnType<typeof setTimeout>;
    const read=async()=>{try {
      const next=await runtimeRequest<Listing>(`${base}/diagrams`);if(!live)return;
      if(!Array.isArray(next.diagrams)||!Array.isArray(next.errors))throw new Error('图表列表格式无效，请刷新重试。');
      const fresh=previousPaths.current&&next.diagrams.find(d=>!previousPaths.current!.includes(d.path));
      let saved='';try{saved=localStorage.getItem(key)||'';}catch{/* Optional view preference. */}
      setListing(next);setError('');
      setSelected(old=>fresh?.path||(next.diagrams.some(d=>d.path===old)?old:next.diagrams.some(d=>d.path===saved)?saved:next.diagrams[0]?.path||''));
      if(fresh){setCreating(false);setPage(0);}
      previousPaths.current=next.diagrams.map(d=>d.path);
    }catch(e){if(live)setError(e instanceof Error?e.message:'图表列表读取失败。');}
    finally{if(live)timer=setTimeout(read,busy?1500:5000);}};
    void read();return()=>{live=false;clearTimeout(timer);};
  },[base,visible,busy,refreshKey,reload,key]);
  const diagram=listing?.diagrams.find(d=>d.path===selected);
  const path=diagram?.pages[Math.min(page,diagram.pages.length-1)];
  useEffect(()=>{setPage(0);setZoom('width');setRedrawing(false);setNotice('');setExportError('');if(selected)try{localStorage.setItem(key,selected);}catch{/* Optional preference. */}},[selected,key]);
  useEffect(()=>{
    if(!visible||!path){setPreview(null);return;}let live=true;setPreview(null);setPreviewError('');
    void runtimeRequest<Preview>(`${base}/diagram-preview?path=${encodeURIComponent(path)}`).then(async value=>{const viewport=await diagramViewport(value.content);if(live)setPreview({...value,viewport});}).catch(e=>{if(live)setPreviewError(e instanceof Error?e.message:'图表读取失败。');});
    return()=>{live=false;};
  },[base,path,visible,reload,diagram?.updated_at]);
  useEffect(()=>{
    const node=canvas.current;if(!node||!visible)return;
    const measure=()=>setCanvasSize({width:Math.max(1,node.clientWidth-8),height:Math.max(1,node.clientHeight-8)});
    const observer=new ResizeObserver(measure);observer.observe(node);measure();return()=>observer.disconnect();
  },[!!preview,visible]);
  const dimensions=preview?.viewport;
  const scale=dimensions?(zoom==='width'?canvasSize.width/dimensions.width:zoom==='readable'?Math.max(canvasSize.width/dimensions.width,16/dimensions.minFont):zoom==='fit'?Math.min(canvasSize.width/dimensions.width,canvasSize.height/dimensions.height):Number(zoom)/100):1;
  const active=DIAGRAM_TYPES.find(t=>t.kind===kind)!;
  const generate=()=>{
    if(redrawing&&diagram&&path&&preview){
      onGenerate('重画当前图',`请按以下要求重画「${diagram.title}」：${redrawRequest.trim()}。先读取原图及来源索引，调用 litigation_visualize，diagram.kind 为 redraw，source_diagram_path 为 ${path}，source_diagram_hash 为 ${preview.file_hash}。保留原意与真实出处，输出新文件，不覆盖原图。`);
      return;
    }
    const message=`请依据本案选中的材料和已整理的事实生成${active.label}。先读取必要材料并核对原文出处，材料不足时说明缺口，不补造事实。调用 litigation_visualize，diagram.kind 为 ${kind}，保留来源索引和全部分页。${request.trim()?`本次出图要求：${request.trim()}`:''}`;
    onGenerate(`生成${active.label}`,message);
  };
  const exportDiagram=async(format:'svg'|'png'|'zip')=>{
    if(!diagram||!path||!preview||preview.path!==path)return;
    const exportingWorkspace=workspaceId;setExporting(true);setNotice('');setExportError('');
    try{
      // Re-read at the action boundary so external file edits are validated as well.
      const result=await runtimeRequest<{content:string}>(`${base}/${format==='zip'?'diagram-archive':'diagram-preview'}?path=${encodeURIComponent(path)}`);
      if(currentWorkspace.current!==exportingWorkspace)return;
      const bytes=format==='png'?await diagramPng(result.content):Uint8Array.from(atob(result.content),c=>c.charCodeAt(0));
      if(currentWorkspace.current!==exportingWorkspace)return;
      const filename=diagramFilename(diagram.title,format,format!=='zip'&&diagram.pages.length>1?page+1:undefined);
      const saved=await saveDiagramExport(filename,bytes);
      if(saved&&currentWorkspace.current===exportingWorkspace)setNotice(`已保存：${saved}`);
    }catch(e){if(currentWorkspace.current===exportingWorkspace)setExportError(e instanceof Error?e.message:'导出失败，请重试。');}
    finally{if(currentWorkspace.current===exportingWorkspace)setExporting(false);}
  };
  return <section className="litigation-visualizations" aria-label="诉讼可视化">
    {!diagram&&<header className="litigation-visual-heading"><h2>选择图表类型</h2><button type="button" aria-label="刷新图表" onClick={()=>setReload(v=>v+1)}><RefreshCw size={14}/></button></header>}
    {error&&<p role="alert">{error}<button type="button" onClick={()=>setReload(v=>v+1)}>重试</button></p>}
    {!listing&&!error&&<p role="status">正在读取图表…</p>}
    {(!diagram||creating)&&<div className="litigation-visual-create">
      {!redrawing&&<div className="litigation-visual-types" role="group" aria-label="图表类型"
        onMouseLeave={()=>setDescribedKind(null)} onBlur={()=>setDescribedKind(null)}>
        {DIAGRAM_TYPES.map(type=><button type="button" key={type.kind} aria-pressed={kind===type.kind}
          aria-describedby={describedKind===type.kind?descriptionId:undefined}
          onMouseEnter={()=>setDescribedKind(type.kind)} onFocus={()=>setDescribedKind(type.kind)}
          onClick={()=>setKind(type.kind)}><type.icon size={18}/><span><strong>{type.label}</strong></span></button>)}
        {describedType&&<div id={descriptionId} role="tooltip" className="litigation-visual-description"
          data-align={describedKind==='relationship'||describedKind==='confrontation'?'end':'start'}>
          <strong>{describedType.label}</strong><p>{describedType.description}</p>
        </div>}
      </div>}
      <div className="litigation-visual-request"><label>{redrawing?'修改要求':'出图要求（选填）'}<textarea aria-label={redrawing?'修改要求':'出图要求'} rows={2} value={redrawing?redrawRequest:request} onChange={e=>redrawing?setRedrawRequest(e.target.value):setRequest(e.target.value)} maxLength={6000} placeholder={redrawing?'例如：保留事实，突出验收与付款节点':`例如：${kind==='relationship'?'梳理当事人、合同和资金往来关系':kind==='confrontation'?'围绕是否完成交付，对照双方主张与证据':'重点呈现签约、交付、验收和付款的经过'}`}/></label></div>
      <div className="litigation-visual-create-actions"><small>生成的图表保存在本案「文书起草」中</small><button type="button" className="litigation-primary" disabled={busy||!canWrite||!ready||!!error||!listing||(redrawing&&(!redrawRequest.trim()||!preview))} onClick={generate}>{redrawing?'重画当前图':`生成${active.label}`}</button></div>
    </div>}
    {listing?.truncated&&<p role="status">图表较多，仅列出前1000个文件；其余可在案件文件夹中查看。</p>}
    {!!listing?.errors.length&&<details><summary>有 {listing.errors.length} 份图表需要核对</summary>{listing.errors.map(item=><p key={item.path}>{item.path}：{item.notice}</p>)}</details>}
    {diagram&&<>
      <div className="litigation-visual-toolbar">
        <select aria-label="选择图表" value={selected} onChange={e=>setSelected(e.target.value)}>{listing!.diagrams.map(item=><option key={item.path} value={item.path}>{item.title} · {DIAGRAM_TYPES.find(t=>t.kind===item.kind)?.label||'重画图表'}</option>)}</select>
        <select aria-label="图表缩放" value={zoom} onChange={e=>setZoom(e.target.value)}><option value="width">铺满宽度</option><option value="readable">放大阅读</option><option value="fit">完整图表</option>{['50','75','100','150','200'].map(value=><option key={value} value={value}>{value}%</option>)}</select>
        <button type="button" className="litigation-visual-icon" aria-label={creating?'收起出图要求':'生成新图'} title={creating?'收起出图要求':'生成新图'} onClick={()=>{setRedrawing(false);setCreating(v=>!v);}}><Plus size={15}/></button>
        <details className="litigation-product-menu" onClick={e=>{if((e.target as HTMLElement).closest('button'))e.currentTarget.removeAttribute('open');}} onKeyDown={e=>{if(e.key==='Escape'){e.currentTarget.removeAttribute('open');e.currentTarget.querySelector('summary')?.focus();}}}>
          <summary aria-label="图表操作" title="图表操作"><MoreHorizontal size={15}/></summary><div>
            <button type="button" disabled={busy||!canWrite||!ready||!preview} onClick={()=>{setRedrawing(true);setCreating(true);}}>修改当前图</button>
            <button type="button" onClick={()=>setReload(v=>v+1)}><RefreshCw size={14}/>刷新图表</button>
          </div>
        </details>
        <details className="litigation-product-menu" onClick={e=>{if((e.target as HTMLElement).closest('button'))e.currentTarget.removeAttribute('open');}} onKeyDown={e=>{if(e.key==='Escape'){e.currentTarget.removeAttribute('open');e.currentTarget.querySelector('summary')?.focus();}}}>
          <summary><Download size={14}/>导出</summary><div>{diagram.pages.length>1&&<small>PNG / SVG 保存当前页</small>}
            <button type="button" disabled={exporting||!preview||!!previewError} onClick={()=>void exportDiagram('png')}>导出 PNG</button>
            <button type="button" disabled={exporting||!preview||!!previewError} onClick={()=>void exportDiagram('svg')}>导出 SVG</button>
            {diagram.pages.length>1&&<button type="button" disabled={exporting||!preview||!!previewError} onClick={()=>void exportDiagram('zip')}>导出整组 SVG</button>}
          </div>
        </details>
      </div>
      {diagram.pages.length>1&&<div className="litigation-visual-pages"><button type="button" disabled={page===0} onClick={()=>setPage(p=>p-1)}>上一页</button><span>{page+1} / {diagram.pages.length} 页</span><button type="button" disabled={page>=diagram.pages.length-1} onClick={()=>setPage(p=>p+1)}>下一页</button></div>}
      {exporting&&<p role="status">正在导出…</p>}{notice&&<p role="status" className="litigation-visual-notice">{notice}</p>}{exportError&&<p role="alert">{exportError}</p>}
      {previewError?<p role="alert">{previewError}<button type="button" onClick={()=>setReload(v=>v+1)}>重试</button></p>:preview?<div ref={canvas} className="litigation-visual-canvas" tabIndex={0} aria-label="图表预览，可滚动查看"><img alt={diagram.title} src={`data:image/svg+xml;base64,${preview.viewport!.content}`} style={{width:preview.viewport!.width*scale,height:preview.viewport!.height*scale}}/></div>:<p role="status">正在打开图表…</p>}
      {path&&<DiagramSources workspaceId={workspaceId} path={path} onPage={next=>{const i=diagram.pages.indexOf(next);if(i>=0)setPage(i);}} onOpen={source=>onOpenSource?.(source.path,source.page??undefined,source)}/>}
    </>}
  </section>;
}
