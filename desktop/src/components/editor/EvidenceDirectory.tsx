import {useEffect,useRef,useState} from 'react';
import {useEvidenceCardDrag} from './useEvidenceCardDrag';
import {ArrowDown,ArrowUp,GripVertical,X} from 'lucide-react';
import {runtimeRequest} from '../../runtime/api';
import type {LitigationMaterial,WorkRequest} from '../../views/LitigationClient';
import type {LitigationMaterialControls} from '../../views/LitigationWorkspaceView';
interface Entry {name:string;group?:string;content?:string;purpose?:string;source?:string;path:string;first:number;last:number|null;file_hash?:string;page_count?:number|null;manual?:boolean;manual_fields?:string[];status?:'pending'|'ready'|'unread';gaps?:string[]}
interface EvidenceGroup {id:string;name:string;purpose:string;numbering:'continue'|'restart';start_number:number}
interface Inventory {revision:number;data:{title:string;entries:Entry[];groups?:EvidenceGroup[]}}
interface Props {workspaceId:string;canWrite:boolean;visible:boolean;busy:boolean;materials:LitigationMaterial[];workRevision:number;onStart:(request:WorkRequest)=>void;onOpen?:(path:string)=>void;onControlsChange?:(controls:LitigationMaterialControls)=>void}
export function EvidenceDirectory({workspaceId,canWrite,visible,busy,materials,workRevision,onStart,onOpen,onControlsChange}:Props){
 const [record,setRecord]=useState<Inventory|null>(null),[error,setError]=useState(''),[working,setWorking]=useState(''),[dirty,setDirty]=useState(false),[outputs,setOutputs]=useState<{pdf?:string;docx?:string}>({});
 const [tab,setTab]=useState<'book'|'directory'>('book');
 const [selected,setSelected]=useState<number[]>([]);
 const [activePath,setActivePath]=useState<string|null>(null);
 const cardsRef=useRef<HTMLDivElement>(null);
 const current=useRef(record);current.current=record;
 const dirtyRef=useRef(dirty);dirtyRef.current=dirty;
 const workingRef=useRef(false),edits=useRef(0),dragged=useRef<number|null>(null);
 const base=`/v1/litigation/workspaces/${encodeURIComponent(workspaceId)}/evidence-directory`;
 const locked=!canWrite||busy||!!working||!record;
 const entries=record?.data.entries??[],groups=record?.data.groups??[];
 const grouped=groups.length>1;
 const selectable=materials.map(file=>({...file,supported:file.book_supported??file.supported,reason:file.book_supported?'按勾选顺序加入证据册和目录':file.reason}));
 const updateData=(patch:Partial<Inventory['data']>)=>{edits.current++;setRecord(old=>old?{...old,data:{...old.data,...patch}}:old);setDirty(true);setOutputs({});setError('');};
 const arrange=(items:Entry[],sections:EvidenceGroup[])=>{const order=new Map(sections.map((group,index)=>[group.id,index]));return [...items].sort((a,b)=>(order.get(a.group??'')??sections.length)-(order.get(b.group??'')??sections.length));};
 const update=(next:Entry[])=>{setSelected([]);updateData({entries:grouped?arrange(next,groups):next.map(entry=>({...entry,group:groups[0]?.id??''}))});};
 const remove=(indices:number[])=>{if(locked)return;if(indices.some(index=>entries[index]?.path===activePath))setActivePath(null);update(entries.filter((_,index)=>!indices.includes(index)));};
 const change=(index:number,patch:Partial<Entry>,manual=false)=>updateData({entries:entries.map((entry,i)=>i===index?{...entry,...patch,...(manual?{manual:true,manual_fields:[...new Set([...(entry.manual_fields??[]),...Object.keys(patch)])]}: {})}:entry)});
 const move=(from:number,to:number)=>{if(locked||from===to||to<0||to>=entries.length)return;const next=[...entries],destination=entries[to].group??'';const item={...next.splice(from,1)[0],group:destination};next.splice(to,0,item);update(next);};
 const groupChange=(id:string,patch:Partial<EvidenceGroup>)=>updateData({groups:groups.map(group=>group.id===id?{...group,...patch}:group)});
 const assign=(id:string)=>{update(entries.map((entry,index)=>selected.includes(index)?{...entry,group:id}:entry));};
 const createGroup=()=>{
  const base=groups.length?groups:[{id:crypto.randomUUID(),name:'第1组证据',purpose:'',numbering:'continue' as const,start_number:1}];
  const group:EvidenceGroup={id:crypto.randomUUID(),name:`第${base.length+1}组证据`,purpose:'',numbering:'continue',start_number:1};const next=[...base,group];
  const items=entries.map((entry,index)=>({...entry,group:selected.includes(index)?group.id:!grouped?base[0].id:entry.group}));
  setSelected([]);updateData({groups:next,entries:arrange(items,next)});
 };
 const moveGroup=(index:number,direction:number)=>{const next=[...groups];next.splice(index+direction,0,next.splice(index,1)[0]);setSelected([]);updateData({groups:next,entries:arrange(entries,next)});};
 const removeGroup=(id:string)=>{const next=groups.filter(group=>group.id!==id);setSelected([]);updateData({groups:next,entries:arrange(entries.map(entry=>entry.group===id?{...entry,group:''}:entry),next)});};
 const cardDrag=useEvidenceCardDrag(cardsRef,!locked&&visible&&tab==='directory',(path,target)=>{
  const next=[...entries],from=next.findIndex(entry=>entry.path===path);if(from<0)return;
  const item={...next.splice(from,1)[0],group:target.group};
  const destination=target.path?next.findIndex(entry=>entry.path===target.path):-1;
  if(target.path&&destination<0)return;
  next.splice(destination<0?next.length:destination+(target.place==='after'?1:0),0,item);
  if(JSON.stringify(next)!==JSON.stringify(entries))update(next);
 });
 const dragging=cardDrag.dragPath!==null;
 const add=(path:string):Entry=>({name:materials.find(f=>f.path===path)?.name??path.split('/').pop()??path,path,first:1,last:null,status:'pending',manual:false,content:'',purpose:'',source:''});
 useEffect(()=>{
  if(!visible)return;
  onControlsChange?.({workspaceId,materials:selectable,selected:entries.map(e=>e.path),locked,
   toggle:path=>{if(locked)return;update(entries.some(e=>e.path===path)?entries.filter(e=>e.path!==path):[...entries,add(path)]);},
   all:()=>{if(!locked)update([...entries,...selectable.filter(m=>m.supported&&!entries.some(e=>e.path===m.path)).map(m=>add(m.path))]);},
   clear:()=>{if(!locked)update([]);},
  });
 },[workspaceId,materials,record,locked,onControlsChange,visible,tab]);
 useEffect(()=>{
  if(!visible)return;let live=true;let timer:ReturnType<typeof setTimeout>;
  const load=async()=>{try{const value=await runtimeRequest<Inventory>(base);if(live&&!dirtyRef.current&&!workingRef.current){setRecord(old=>!old||value.revision>old.revision?value:old);}}
   catch(e){if(live)setError(e instanceof Error?e.message:'证据清单读取失败');}
   finally{if(live)timer=setTimeout(load,busy?1000:5000);}};
  void load();return()=>{live=false;clearTimeout(timer);};
 },[base,visible,busy]);
 const save=async()=>{
  const value=current.current;if(!value)throw Error('清单尚未读取');
  if(!dirtyRef.current)return value;
  const generation=edits.current;
  const saved=await runtimeRequest<Inventory>(base,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:value.revision,data:value.data})});
  current.current=saved;setRecord(old=>generation===edits.current?saved:old?{...old,revision:saved.revision}:saved);
  if(generation===edits.current){dirtyRef.current=false;setDirty(false);}
  return saved;
 };
 useEffect(()=>{
  if(!dirty||busy||working||error||dragging)return;
  const timer=setTimeout(()=>{workingRef.current=true;setWorking('正在保存清单…');void save().catch(e=>setError(e.message)).finally(()=>{workingRef.current=false;setWorking('');});},500);
  return()=>clearTimeout(timer);
 },[record,dirty,busy,working,error,dragging]);
 const run=async(kind:'prepare'|'pdf'|'docx'|'ai'|'save')=>{
  if(workingRef.current||busy)return;workingRef.current=true;setWorking(kind==='save'?'正在保存清单…':'正在转换文件并核对页数…');setError('');
  try{
   let value=await save();if(kind==='save')return;
   if(kind==='pdf'||kind==='docx'){
    const result=await runtimeRequest<{path:string;record:Inventory}>(`${base}/export/${kind}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:value.revision})});
    current.current=result.record;setRecord(result.record);setOutputs(old=>({...old,[kind]:result.path}));
   }else{
    value=await runtimeRequest<Inventory>(`${base}/prepare`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:value.revision})});current.current=value;setRecord(value);
    if(kind==='ai')onStart({kind:'evidence',section_id:null,materials:value.data.entries.map(e=>e.path),expected_revision:workRevision,evidence_revision:value.revision,title:'生成证据目录',message:'请按我选定的顺序填写证据目录。先 litigation_work_read 读取案件背景，再用 litigation_evidence_next 依次读取每份文件；有 next_start 必须续读至末尾，再 litigation_evidence_save 填写证据内容、证明目的、证据来源。循环到 done。来源指文件记载的形成或提供主体，缺失标待核对；扫描页未识别如实标注，保留人工修改。只填写独立目录，不生成PDF证据册，不另建其他成果。'});
   }
  }catch(e){setError(e instanceof Error?e.message:'处理失败，请重试。');}
  finally{workingRef.current=false;setWorking('');}
 };
 let cursor=1;
 const pageRanges=entries.map(entry=>{if(!entry.page_count){cursor=NaN;return '待计算';}const count=(entry.last??entry.page_count)-entry.first+1;const start=cursor;cursor+=count;return Number.isFinite(start)?(count===1?`${start}`:`${start}—${cursor-1}`):'待计算';});
 let evidenceNumber=1,previousGroup:string|undefined;
 const numbers=entries.map(entry=>{const id=entry.group??'',group=grouped?groups.find(item=>item.id===id):undefined;if(id!==previousGroup&&group?.numbering==='restart')evidenceNumber=group.start_number;previousGroup=id;return evidenceNumber++;});
 const sections=grouped?[...groups.map(group=>({group,rows:entries.map((entry,index)=>({entry,index})).filter(row=>row.entry.group===group.id)})),{group:null,rows:entries.map((entry,index)=>({entry,index})).filter(row=>!row.entry.group)}]:[{group:groups[0]??null,rows:entries.map((entry,index)=>({entry,index}))}];
 const activeEvidence=entries.some(entry=>entry.path===activePath)?activePath:null;
 const output=outputs[tab==='book'?'pdf':'docx'];
 const pending=entries.filter(e=>(!e.manual||!!e.manual_fields?.length)&&!['content','purpose','source'].every(f=>e.manual_fields?.includes(f))&&(!e.status||e.status==='pending')).length;
 return <section className="litigation-evidence-directory" aria-label="证据册与证据目录">
  <div className="litigation-facts-tabs" role="tablist" aria-label="证据成果"><button role="tab" aria-selected={tab==='book'} onClick={()=>setTab('book')}>证据册</button><button role="tab" aria-selected={tab==='directory'} onClick={()=>setTab('directory')}>证据目录</button></div>
  <div className="evidence-toolbar"><span>{entries.length} 份材料{Number.isFinite(cursor)&&entries.length?` · ${cursor-1} 页`:''}</span><span className="evidence-save-state" role="status">{working|| (dirty?'尚未保存':record?'已保存':'正在读取…')}</span>
   {tab==='book'?<><button disabled={locked||!entries.length} onClick={()=>void run('prepare')}>核对页数</button><button className="litigation-primary" disabled={locked||!entries.length} onClick={()=>void run('pdf')}>生成 PDF 证据册</button></>:<><button disabled={locked||!pending} onClick={()=>void run('ai')}>AI 填写目录{pending?`（${pending}）`:''}</button><button className="litigation-primary" disabled={locked||!entries.length} onClick={()=>void run('docx')}>导出 Word 目录</button></>}
  </div>
  {error&&<div className="evidence-error" role="alert">{error} {dirty?<><button disabled={locked} onClick={()=>void run('save')}>重试保存</button><button disabled={locked} onClick={()=>{void runtimeRequest<Inventory>(base).then(v=>{setRecord(v);setDirty(false);dirtyRef.current=false;setError('');}).catch(e=>setError(e.message));}}>放弃未保存修改并重新读取</button></>:<button disabled={!!working} onClick={()=>{void runtimeRequest<Inventory>(base).then(v=>{setRecord(v);setError('');}).catch(e=>setError(e.message));}}>重新读取</button>}</div>}
  {!entries.length&&record&&<p className="litigation-empty">在左侧依次勾选文件。选中顺序就是装册顺序，可在这里拖动调整。</p>}
  {!!entries.length&&tab==='book'&&<div className="evidence-table-scroll"><table className="evidence-book-table"><thead><tr><th>顺序</th><th>证据文件</th><th>原文件页范围</th><th>本册页码</th><th>调整顺序</th></tr></thead><tbody>
   {entries.map((entry,index)=><tr key={`${entry.path}:${index}`} data-evidence-index={index} onDragOver={e=>{if(!locked)e.preventDefault();}} onDrop={e=>{e.preventDefault();if(dragged.current!==null)move(dragged.current,index);dragged.current=null;}}>
    <td><button className="evidence-drag" disabled={locked} aria-label={`拖动证据 ${index+1}`} onPointerDown={e=>{if(e.button!==0)return;e.preventDefault();dragged.current=index;e.currentTarget.setPointerCapture(e.pointerId);}} onPointerUp={e=>{const row=document.elementFromPoint(e.clientX,e.clientY)?.closest<HTMLTableRowElement>('tr[data-evidence-index]');const from=dragged.current;dragged.current=null;e.currentTarget.releasePointerCapture(e.pointerId);if(from!==null&&row&&e.currentTarget.closest('table')?.contains(row))move(from,Number(row.dataset.evidenceIndex));}} onPointerCancel={()=>{dragged.current=null;}}><GripVertical size={14}/>{index+1}</button></td>
    <td><button className="evidence-file" title={entry.path} onClick={()=>onOpen?.(entry.path)}>{entry.name}</button><small title={entry.path}>{entry.path}{grouped&&entry.group?` · ${groups.find(group=>group.id===entry.group)?.name??entry.group}`:''}{entry.page_count?` · ${entry.page_count} 页`:''}</small></td>
    <td><div className="evidence-range"><input aria-label={`证据 ${index+1} 开始页`} type="number" min={1} disabled={locked} value={entry.first} onChange={e=>change(index,{first:Number(e.target.value)})}/><span>—</span><input aria-label={`证据 ${index+1} 结束页`} type="number" min={entry.first} disabled={locked} placeholder="末页" value={entry.last??''} onChange={e=>change(index,{last:e.target.value?Number(e.target.value):null})}/></div></td>
    <td>{pageRanges[index]}</td><td><div className="evidence-row-actions"><button aria-label={`上移证据 ${index+1}`} disabled={locked||index===0} onClick={()=>move(index,index-1)}><ArrowUp size={14}/></button><button aria-label={`下移证据 ${index+1}`} disabled={locked||index===entries.length-1} onClick={()=>move(index,index+1)}><ArrowDown size={14}/></button><button aria-label={`移除证据 ${index+1}`} disabled={locked} title="从证据清单移除，原文件保留" onClick={()=>remove([index])}><X size={14}/></button></div></td>
   </tr>)}
  </tbody></table></div>}
  {!!entries.length&&tab==='directory'&&<>
   <label className="litigation-directory-title">目录名称<input disabled={locked} value={record!.data.title} onChange={e=>updateData({title:e.target.value})}/></label>
   <div className="evidence-group-toolbar"><span>已选 {selected.length} 项</span><button disabled={locked||!selected.length} title="移除所选证据条目，原文件保留" onClick={()=>remove(selected)}>移除所选</button><button disabled={locked} onClick={createGroup}>新建分组</button><select aria-label="将所选证据移入分组" value="" disabled={locked||!selected.length} onChange={event=>assign(event.target.value)}><option value="" disabled>移入分组…</option>{groups.map(group=><option key={group.id} value={group.id}>{group.name}</option>)}</select>{!!selected.length&&<button disabled={locked} onClick={()=>assign('')}>移出分组</button>}<small>点击卡片编辑 · 拖动调整顺序或分组 · 同步证据册</small></div>
   <div className="evidence-card-board" ref={cardsRef} data-dragging={dragging}>
    <label className="evidence-select-all"><input type="checkbox" aria-label="选择全部证据" disabled={locked} checked={selected.length===entries.length&&!!entries.length} onChange={event=>setSelected(event.target.checked?entries.map((_,i)=>i):[])}/>全选证据</label>
    {sections.map(({group,rows})=><section className="evidence-card-group" key={group?.id??'ungrouped'} data-group-drop={group?.id??''} aria-label={grouped?(group?.name??'未分组证据'):'证据条目'} data-grouped={grouped} data-drop-empty={!cardDrag.edge?.path&&cardDrag.edge?.group===(group?.id??'')&&dragging}>
     {grouped&&(group?<div className="evidence-group-editor"><div className="evidence-group-main"><input aria-label={`${group.name} 分组名称`} disabled={locked} value={group.name} maxLength={200} onChange={event=>groupChange(group.id,{name:event.target.value})}/><label>证明：<textarea rows={1} aria-label={`${group.name} 共同证明目的`} disabled={locked} value={group.purpose} maxLength={2000} placeholder="填写本组共同证明目的" onChange={event=>groupChange(group.id,{purpose:event.target.value})}/></label></div><div className="evidence-group-options"><select aria-label={`${group.name} 编号方式`} disabled={locked} value={group.numbering} onChange={event=>groupChange(group.id,{numbering:event.target.value as EvidenceGroup['numbering']})}><option value="continue">接着上一组编号</option><option value="restart">重新编号</option></select>{group.numbering==='restart'&&<label>从<input aria-label={`${group.name} 起始编号`} type="number" min={1} max={10000} disabled={locked} value={group.start_number} onChange={event=>groupChange(group.id,{start_number:Number(event.target.value)})}/>开始</label>}<span>{rows.length} 项</span><button aria-label={`上移${group.name}`} disabled={locked||groups.indexOf(group)===0} onClick={()=>moveGroup(groups.indexOf(group),-1)}><ArrowUp size={14}/></button><button aria-label={`下移${group.name}`} disabled={locked||groups.indexOf(group)===groups.length-1} onClick={()=>moveGroup(groups.indexOf(group),1)}><ArrowDown size={14}/></button><button aria-label={`删除${group.name}分组`} title="删除分组，证据保留为未分组" disabled={locked} onClick={()=>removeGroup(group.id)}><X size={14}/></button></div></div>:<strong>未分组证据</strong>)}
     <div className="evidence-card-list">{rows.map(({entry,index})=><article className="evidence-card" data-evidence-index={index} data-evidence-path={entry.path} data-dragging={cardDrag.dragPath===entry.path} data-drop-edge={cardDrag.edge?.path===entry.path?cardDrag.edge.place:undefined} data-active={activeEvidence===entry.path} key={entry.path}>
      <div className="evidence-card-heading">
       <button className="evidence-drag" disabled={locked} aria-label={`拖动目录证据 ${index+1}`} title="按住拖动，可跨组移动" onPointerDown={event=>cardDrag.start(event,entry.path)}><GripVertical size={14}/></button>
       <input type="checkbox" aria-label={`选择证据 ${index+1}`} checked={selected.includes(index)} disabled={locked} onChange={event=>setSelected(old=>event.target.checked?[...old,index]:old.filter(i=>i!==index))}/>
       <button className="evidence-card-open" aria-expanded={activeEvidence===entry.path} onClick={()=>setActivePath(activeEvidence===entry.path?null:entry.path)}><span className="evidence-card-number">{numbers[index]}</span><strong>{entry.name}</strong><small>{pageRanges[index]} 页 · {entry.manual?'已人工修改':entry.status==='ready'?'AI 草稿':entry.status==='unread'?'待核对':'待填写'}</small></button>
       <button aria-label={`上移目录证据 ${index+1}`} disabled={locked||index===0} onClick={()=>move(index,index-1)}><ArrowUp size={14}/></button><button aria-label={`下移目录证据 ${index+1}`} disabled={locked||index===entries.length-1} onClick={()=>move(index,index+1)}><ArrowDown size={14}/></button><button aria-label={`移除目录证据 ${index+1}`} title="从证据清单移除，原文件保留" disabled={locked} onClick={()=>remove([index])}><X size={14}/></button>
      </div>
      {activeEvidence===entry.path&&<div className="evidence-card-fields">{(['name','content','purpose','source'] as const).map(field=><label key={field}>{ {name:'证据名称',content:'证据内容',purpose:'证明目的',source:'证据来源'}[field]}<textarea rows={3} disabled={locked} aria-label={`证据 ${index+1} ${{name:'名称',content:'内容',purpose:'证明目的',source:'来源'}[field]}`} value={entry[field]??''} placeholder="待填写" onChange={event=>change(index,{[field]:event.target.value},true)}/></label>)}{!!entry.gaps?.length&&<p className="evidence-gap">{entry.gaps.join(' ')}</p>}<button onClick={()=>onOpen?.(entry.path)}>查看原文件</button></div>}
     </article>)}</div>
     {!rows.length&&<p className="evidence-drop-empty">将证据拖到这里</p>}
    </section>)}
   </div>
   <p className="litigation-directory-note">AI 按文件顺序逐份读取；人工修改会保留。目录页码与独立证据册对应。</p>
  </>}
  {output&&<p className="evidence-output">已生成{tab==='book'?'证据册':'证据目录'}：<button onClick={()=>onOpen?.(output)}>{output.split('/').pop()}</button></p>}
 </section>;
}
