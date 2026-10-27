import {LitigationVisualizations} from './LitigationVisualizations';
import {editorClient} from '../components/editor/client';
import type {LitigationMaterialControls} from './LitigationWorkspaceView';
import type {LitigationMaterial} from './LitigationClient';
import {EvidenceDirectory} from '../components/editor/EvidenceDirectory';
import {EditableProducts} from '../components/editor/EditableProducts';
import {useEffect, useRef, useState} from 'react';
import {MessageSquare, History} from 'lucide-react';
import {MarkdownBody} from '../components/timeline/MarkdownBody';
import {LITIGATION_WORK} from './LitigationGuide';
import {litigationWorkClient, type LitigationRecord, type WorkKind, type WorkProduct, type WorkRequest} from './LitigationClient';

export function LitigationWorkProduct({workspaceId,kind,materials,materialFiles=[],onEvidenceControls,busy,awaitingApproval=false,refreshKey,onStart,onOpenSource,selectedSection,canWrite,visible,focusKey=0,ready=true}: {
 materialFiles?:LitigationMaterial[]; onEvidenceControls?:(controls:LitigationMaterialControls)=>void; workspaceId:string; kind:WorkKind; materials:string[]; busy:boolean; awaitingApproval?:boolean; refreshKey:string|number;
 onStart:(request:WorkRequest)=>void; onOpenSource?:(path:string,page?:number,citation?:import("./LitigationClient").LitigationSource)=>void; selectedSection?:string|null; canWrite:boolean; visible:boolean; focusKey?:number; ready?:boolean;
}) {
 const [factsView,setFactsView]=useState<'visual'|'text'>('visual');
 const [editing,setEditing]=useState(false);const [openId,setOpenId]=useState<string>();const [importing,setImporting]=useState(false);
 const [record,setRecord]=useState<LitigationRecord<WorkProduct>|null>(null);
 const [error,setError]=useState(''); const [request,setRequest]=useState('');
 const [versions,setVersions]=useState<LitigationRecord<WorkProduct>[]|null>(null);
 const [historical,setHistorical]=useState<number|null>(null); const [reload,setReload]=useState(0);
 const [options,setOptions]=useState(false);
 const root=useRef<HTMLElement>(null);
 useEffect(()=>{
   if(!visible)return;
   let live=true; let timer:ReturnType<typeof setTimeout>;
   const read=async()=>{try{const next=await litigationWorkClient.read(workspaceId,kind); if(live){setRecord(next);setError('');}}
     catch(e){if(live)setError(e instanceof Error?e.message:'成果读取失败，请重试。');}
     finally{if(live)timer=setTimeout(read,busy?1500:5000);}};
   void read(); return()=>{live=false;clearTimeout(timer);};
 },[workspaceId,kind,refreshKey,busy,reload,visible]);
 useEffect(()=>{setHistorical(null);if(selectedSection||focusKey>0)setFactsView('text');},[focusKey,selectedSection]);
 useEffect(()=>{if(selectedSection) root.current?.querySelector(`[data-section-id="${selectedSection}"]`)?.scrollIntoView?.({block:'nearest'});},[selectedSection,record?.revision,focusKey]);
 const work=LITIGATION_WORK[kind];
 const displayed=historical===null?record:versions?.find(v=>v.revision===historical);
 const start=(sectionId:string|null, title:string, message?:string)=>onStart({kind,section_id:sectionId,materials,expected_revision:record?.revision??0,title,message});
 const locked=busy||!record||!canWrite||!!error||historical!==null||!ready;
 return <section className="litigation-product" ref={root} aria-label={`${work.title}成果`}>
   {kind==='evidence'&&<EvidenceDirectory workspaceId={workspaceId} materials={materialFiles} canWrite={canWrite} visible={visible} busy={busy} workRevision={record?.revision??0} onStart={onStart} onControlsChange={onEvidenceControls} onOpen={onOpenSource}/>}
   {kind==='facts'&&<>
     <div className="litigation-facts-tabs" role="tablist" aria-label="事实梳理视图"><button type="button" role="tab" aria-selected={factsView==='visual'} onClick={()=>setFactsView('visual')}>诉讼可视化</button><button type="button" role="tab" aria-selected={factsView==='text'} onClick={()=>setFactsView('text')}>事实底稿</button></div>
     <div hidden={factsView!=='visual'}><LitigationVisualizations workspaceId={workspaceId} visible={visible&&factsView==='visual'} busy={busy} canWrite={canWrite} ready={ready&&!!record} refreshKey={refreshKey} onGenerate={(title,message)=>start(null,title,message)} onOpenSource={onOpenSource}/></div>
   </>}
   {kind!=='evidence'&&<div hidden={kind==='facts'&&factsView!=='text'}>
   <EditableProducts onLocate={()=>setFactsView('text')} openId={openId} workspaceId={workspaceId} kind={kind} visible={visible} canWrite={canWrite} onActive={setEditing} onOpenSource={onOpenSource}
     heading={<h2 className="litigation-product-title" title={work.description}>{work.title}</h2>}
     actions={<>
       {kind==='strategy'&&<details className="litigation-chart-menu" onClick={e=>{if((e.target as HTMLElement).closest('button'))e.currentTarget.open=false;}} onKeyDown={e=>{if(e.key==='Escape'){e.currentTarget.open=false;e.currentTarget.querySelector('summary')?.focus();}}}><summary>生成图表</summary><div>{[['timeline','时间轴'],['chronicle','大事记'],['relationship','关系图'],['confrontation','对抗图'],['redraw','重画图表']].map(([type,label])=><button key={type} disabled={busy||!canWrite} onClick={()=>start(null,label,`请根据当前已整理并核对的内容生成${label}，使用 litigation_visualize，kind 为 ${type}，保留原文出处，不补造事实。`)}>{label}</button>)}</div></details>}
       {!editing&&record?.data&&<button type="button" disabled={busy} onClick={()=>setOptions(!options)}>{options?'收起处理要求':'继续处理'}</button>}
     </>}/>
   <div hidden={editing}>
   {error&&<p role="alert" className="litigation-error">{error}<button type="button" onClick={()=>setReload(v=>v+1)}>重试</button></p>}
   {!record&&!error&&<p role="status">正在读取案件成果…</p>}
   {(!record?.data||options)&&<div className="litigation-product-start">
     <label>{kind==='documents'?'起草要求':'本次要求'}<textarea value={request} onChange={e=>setRequest(e.target.value)} maxLength={6000} placeholder={work.example} rows={2}/></label>
     <div><small>已选 {materials.length} 份材料</small><button type="button" className="litigation-primary" disabled={locked||(kind==='documents'&&!request.trim())}
       onClick={()=>start(null,work.title,request.trim()||`请${work.title}，将成果整理到工作台。`)}>{record?.data?'更新这份成果':kind==='documents'?'开始起草':`开始${{facts:'事实梳理',evidence:'证据整理',issues:'争议焦点',strategy:'策略备忘'}[kind]}`}</button></div>
   </div>}
   {!canWrite&&<p role="status">当前文件夹仅可读取，请选择可保存成果的案件文件夹。</p>}

   {record?.data&&<>
     <div className="litigation-product-toolbar"><span>第 {record.revision} 版 · 待律师核对</span>
       <button type="button" onClick={()=>{void litigationWorkClient.versions(workspaceId,kind).then(r=>setVersions(r.versions)).catch(e=>setError(String(e)));}}><History size={14}/>历史版本</button>
       <button type="button" disabled={locked||importing} onClick={()=>{setImporting(true);void editorClient.fromWork(workspaceId,kind,record.revision).then(p=>setOpenId(p.id)).catch(e=>setError(e.message)).finally(()=>setImporting(false));}}>编辑正文副本</button>
       <button type="button" disabled={locked} onClick={()=>start(null,work.title)}><MessageSquare size={14}/>讨论整份成果</button>
       {kind==='documents'&&<button type="button" disabled={locked} onClick={()=>start(null,work.title,'请把工作台当前文书生成 Word 文件，保留正文与待补标记，不改写内容。')}>生成 Word</button>}
     </div>
     {versions&&<div className="litigation-version-picker"><label>查看版本<select value={historical??''} onChange={e=>setHistorical(e.target.value?Number(e.target.value):null)}><option value="">当前版本</option>{versions.filter(v=>v.revision!==record.revision).map(v=><option key={v.revision} value={v.revision}>第 {v.revision} 版</option>)}</select></label><button type="button" onClick={()=>{setVersions(null);setHistorical(null);}}>关闭历史版本</button></div>}
     {historical!==null&&<p role="status">正在查看第 {historical} 版，历史内容只读。</p>}
     <h3>{displayed?.data?.title}</h3>
     {displayed?.data?.sections.map(section=><article key={section.id} data-section-id={section.id} className="litigation-product-section" data-selected={selectedSection===section.id}>
       <header><h4>{section.title}</h4>{historical===null&&<button type="button" disabled={locked} onClick={()=>start(section.id,section.title)}><MessageSquare size={14}/>讨论此项</button>}</header>
       <MarkdownBody text={section.body}/>
       {!!section.pending.length&&<ul className="litigation-section-pending" aria-label="待核实事项">{section.pending.map((p,i)=><li key={i}>{p}</li>)}</ul>}
       {!!section.sources.length&&<details><summary>材料出处 · {section.sources.length} 处</summary>{section.sources.map((source,i)=><div key={i}><button type="button" onClick={()=>onOpenSource?.(source.path,source.page??undefined,source)}>{source.path}{source.page?` · 第${source.page}页`:''}</button><blockquote>{source.quote}</blockquote></div>)}</details>}
     </article>)}
   </>}
   </div>
   </div>}
 </section>;
}
