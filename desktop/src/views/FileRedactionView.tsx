import { useEffect,useRef,useState } from 'react';
import { FileText, FolderOpen, ShieldCheck } from 'lucide-react';
import { getCurrentWebview } from '@tauri-apps/api/webview';
import { invoke } from '@tauri-apps/api/core';
import { redactionApi,REDACTION_TYPES,DEFAULT_REDACTION_TYPES,type RedactionApi,type RedactionJob,type RedactionSummary,type RedactionVariant } from '../runtime/redactionClient';
import { saveRedaction,openRedaction,chooseRedactionDirectory } from '../platform/redactionExport';
import './FileRedactionView.css';

const location=(key:string)=>{const [part,p]=key.split(':');const section=part.includes('header')?'页眉':part.includes('footer')?'页脚':part.includes('footnote')?'脚注':part.includes('endnote')?'尾注':'正文/表格';return `${section} · 第 ${Number(p)+1} 段`;};
export function FileRedactionView({api=redactionApi}:{api?:RedactionApi}) {
 const [file,setFile]=useState<File|null>(null),[job,setJob]=useState<RedactionJob|null>(null);
 const [recent,setRecent]=useState<RedactionSummary[]>([]),[kinds,setKinds]=useState(DEFAULT_REDACTION_TYPES),[custom,setCustom]=useState('');
 const [markdown,setMarkdown]=useState(false),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [confirm,setConfirm]=useState<'permanent'|'delete'|null>(null),[saved,setSaved]=useState<string|null>(null),[visible,setVisible]=useState(100);
 const [query,setQuery]=useState(''),[reviewFilter,setReviewFilter]=useState('all');
 const [outputDirectory,setOutputDirectory]=useState<string|null>(null);
 const desktop=Reflect.has(globalThis,'__TAURI_INTERNALS__');
 const input=useRef<HTMLInputElement>(null),lock=useRef(false),mounted=useRef(true);
 const refresh=async()=>{const items=await api.list();if(mounted.current)setRecent(items);};
 useEffect(()=>{mounted.current=true;void refresh().catch(()=>setError('最近记录暂时无法加载，请重新打开页面。'));return()=>{mounted.current=false;};},[api]);
 const accept=(files:File[])=>{
  if(lock.current)return;
  setError('');setNotice('');setSaved(null);
  if(files.length!==1){setError('一次请选择一份 Word 文件。');return;}
  const selected=files[0];
  if(!/\.docx?$/i.test(selected.name)){setError('请选择 .doc 或 .docx 文件，暂不支持 PDF 或扫描件。');return;}
  if(selected.size>20*1024*1024){setError('文件不能超过 20 MB，请拆分后重试。');return;}
  setFile(selected);setJob(null);setConfirm(null);setVisible(100);setQuery('');setReviewFilter('all');
 };
 const acceptRef=useRef(accept);acceptRef.current=accept;
 useEffect(()=>{
  if(!Reflect.has(globalThis,'__TAURI_INTERNALS__'))return;
  let dispose:(()=>void)|undefined;let cancelled=false;
  void getCurrentWebview().onDragDropEvent(event=>{
   if(event.payload.type!=='drop'||lock.current)return;
   const paths=event.payload.paths;
   if(paths.length!==1){setError('一次请选择一份 Word 文件。');return;}
   const name=paths[0].split(/[\\/]/).pop()??'';
   if(!/\.docx?$/i.test(name)){setError('请选择 .doc 或 .docx 文件，暂不支持 PDF 或扫描件。');return;}
   lock.current=true;setBusy('正在读取文件…');
   void invoke<ArrayBuffer|number[]>('read_dropped_file',{path:paths[0]}).then(payload=>{
    const bytes=payload instanceof ArrayBuffer?new Uint8Array(payload):Uint8Array.from(payload);
    if(bytes.length<8)throw new Error();
    lock.current=false;if(!cancelled)acceptRef.current([new File([bytes.slice(8)],name)]);
   }).catch(()=>{if(!cancelled)setError('无法读取拖入的文件，请使用“选择文件”。');}).finally(()=>{lock.current=false;if(!cancelled)setBusy('');});
  }).then(fn=>{if(cancelled)fn();else dispose=fn;}).catch(()=>setError('拖入暂不可用，请使用“选择文件”。'));
  return()=>{cancelled=true;dispose?.();};
 },[]);
 const run=async(label:string,work:()=>Promise<void>)=>{
  if(lock.current)return;lock.current=true;setBusy(label);setError('');setNotice('');setSaved(null);
  try{await work();}catch(e){if(mounted.current)setError(e instanceof Error?e.message:'操作失败，请重试。');}
  finally{lock.current=false;if(mounted.current)setBusy('');}
 };
 const useJob=(value:RedactionJob)=>{if(!mounted.current)return;if(value.id!==job?.id){setQuery('');setReviewFilter('all');}setJob(value);setFile(null);setKinds(value.kinds);setCustom(value.custom.join('\n'));setVisible(100);setConfirm(null);};
 const words=()=>custom.split(/\r?\n/).map(w=>w.trim()).filter(Boolean);
 const process=()=>run('正在本机识别并脱敏…',async()=>{const result=file?await api.create(file,kinds,words()):job?await api.rerun(job,kinds,words()):null;if(result){useJob(result);await refresh();}});
 const exportFile=(variant:RedactionVariant)=>run('正在自检并准备保存…',async()=>{
  if(!job||changes)return;setConfirm(null);const result=await api.export(job,variant);if(!mounted.current)return;const path=await saveRedaction(result,outputDirectory);
  if(mounted.current){setSaved(path);setNotice(path?'已保存新文件。':'已取消保存。');}
 });
 const chooseDirectory=()=>run('正在选择输出目录…',async()=>{const path=await chooseRedactionDirectory();if(path&&mounted.current)setOutputDirectory(path);});
 const items=job?.mapping??[];
 const search=query.trim().toLocaleLowerCase();
 const filtered=items.filter(item=>(reviewFilter==='all'||(reviewFilter==='kept'?item.keep:!item.keep))&&
  [item.old,item.placeholder,REDACTION_TYPES[item.kind]??'自定义',item.context_before??'',item.context_after??''].some(value=>value.toLocaleLowerCase().includes(search)));
 useEffect(()=>setVisible(100),[query,reviewFilter]);
 const changes=!!job&&(JSON.stringify(kinds)!==JSON.stringify(job.kinds)||JSON.stringify(words())!==JSON.stringify(job.custom));
 useEffect(()=>{if(changes)setConfirm(null);},[changes,custom,kinds]);
 return <section className="file-redaction-view" aria-label="文件脱敏" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();accept(Array.from(e.dataTransfer.files));}}>
  <div className="redaction-page"><header className="redaction-page-heading"><div><h1>文件脱敏</h1><p>识别敏感文字，核对后保存新文件。</p></div><span className="redaction-local"><ShieldCheck size={15} aria-hidden="true"/>本机处理</span></header>
  <div className="redaction-card redaction-input-card">
  <input ref={input} aria-label="选择 Word 文件" type="file" accept=".doc,.docx" hidden disabled={!!busy} onChange={e=>{accept(Array.from(e.target.files??[]));e.target.value='';}}/>
  <div className="redaction-file"><span className="redaction-file-icon"><FileText size={24} aria-hidden="true"/></span><div className="redaction-file-name"><small>当前文件</small><strong>{file?.name??job?.filename??'尚未选择文件'}</strong>{!file&&!job&&<small className="redaction-file-hint">可拖入 Word 文件 · .doc / .docx · 最大 20 MB</small>}</div><button className={!file&&!job?'primary-button':''} disabled={!!busy} onClick={()=>input.current?.click()}>{file||job?'选择其他文件':'选择文件'}</button></div>
  <div className="redaction-output" aria-label="输出目录">
   <FolderOpen size={20} aria-hidden="true"/>
   <div className="redaction-output-path"><strong>输出目录</strong><span title={outputDirectory??undefined}>{outputDirectory??(desktop?'保存时选择位置':'浏览器下载位置')}</span></div>
   {desktop&&<div className="redaction-directory-actions"><button disabled={!!busy} onClick={()=>void chooseDirectory()}>{outputDirectory?'更换目录':'选择目录'}</button>{outputDirectory&&<button className="redaction-text-button" disabled={!!busy} onClick={()=>setOutputDirectory(null)}>保存时选择</button>}</div>}
  </div>

   <section className="redaction-options" aria-label="脱敏选项"><h2>脱敏选项</h2>
    <p className="redaction-disclosure">可能漏检或误识别，可添加自定义词。图片、图章和手写中的文字不在识别范围。</p>
    <fieldset disabled={!!busy||!!job&&!job.restorable}><legend>识别类型</legend><div className="redaction-types">{Object.entries(REDACTION_TYPES).map(([key,label])=><label key={key}><input type="checkbox" checked={kinds.includes(key)} onChange={e=>setKinds(current=>e.target.checked?[...current,key]:current.filter(k=>k!==key))}/>{label}</label>)}</div>
     <label className="redaction-custom">自定义词（每行一个）<textarea rows={2} value={custom} maxLength={20100} onChange={e=>setCustom(e.target.value)}/></label>
    </fieldset>
    {markdown&&<p className="redaction-disclosure">节省 token：智能体读起来更省用量，同样的对话能装下更多材料。可能丢失结构性内容：表格、脚注等可能变形或丢失，文件不再是原来的样子，不适合直接对外提交。</p>}
    <div className="redaction-start-row">
     <label><input type="checkbox" checked={markdown} disabled={!!busy} onChange={e=>setMarkdown(e.target.checked)}/>先转成 Markdown 再脱敏</label>
     {(!job||file||job.restorable)&&<button className={!job||file?'primary-button':''} disabled={!!busy||!file&&!job} onClick={()=>void process()}>{!job||file?'开始脱敏':changes?'应用选项并重新处理':'重新识别'}</button>}
    </div>
   </section>
  <p className="redaction-note">旧版 .doc 会在本机转换，复杂排版可能变化；Word 结果统一为 .docx。</p>
  </div>
  {busy&&<p role="status" aria-live="polite">{busy}</p>}
  {error&&<p className="redaction-error" role="alert">{error}</p>}
  {notice&&<p role="status">{notice}</p>}
  {job&&<div className="redaction-card redaction-result-card">
   {job.restorable?<>
    <p className="redaction-disclosure">请逐条核对识别结果；图片、图章和手写文字不在识别范围。</p>
    <div className="redaction-review-heading"><h2>改动清单</h2><span>共 {items.length} 处 · 保留 {items.filter(i=>i.keep).length} 处</span></div>
    <p className="redaction-disclosure">作者等文档属性、批注和修订历史已清理；还原仅恢复被替换文字，不恢复这些历史信息。</p>
    {items.length===0&&<p>未识别到所选类型的敏感文字。请核对文件，也可添加自定义词。</p>}
    {items.length>0&&<div className="redaction-review-tools">
     <input type="search" aria-label="搜索改动清单" placeholder="搜索原文、类型或上下文" value={query} onChange={e=>setQuery(e.target.value)} maxLength={200}/>
     <select aria-label="筛选改动状态" value={reviewFilter} onChange={e=>setReviewFilter(e.target.value)}>
      <option value="all">全部</option><option value="redacted">待脱敏</option><option value="kept">已保留</option>
     </select>
     <span>显示 {filtered.length} / {items.length} 处</span>
    </div>}
    {items.length>0&&filtered.length===0&&<p>没有符合筛选条件的改动。</p>}
    <ol className="redaction-items">{filtered.slice(0,visible).map(item=><li key={item.id}>
     <div><span className="redaction-kind">{REDACTION_TYPES[item.kind]??'自定义'}</span><small>{location(item.key)} · 字符 {item.source_start+1}</small></div>
     <p><span>{item.old}</span><span aria-hidden="true"> → </span><strong>{item.keep?'保留原文':item.placeholder}</strong></p>
     {(item.context_before||item.context_after)&&<p className="redaction-context" aria-label="原文上下文">{item.context_before}<mark>{item.old}</mark>{item.context_after}</p>}
     <button disabled={!!busy||changes} aria-label={`${item.keep?'重新脱敏':'还原此处'} ${item.old} ${location(item.key)} 字符${item.source_start+1}`} onClick={()=>void run('正在更新这一处…',async()=>{useJob(await api.update(job,item));})}>{item.keep?'重新脱敏':'还原此处'}</button>
    </li>)}</ol>
    {visible<filtered.length&&<button onClick={()=>setVisible(v=>v+100)}>显示更多（剩余 {filtered.length-visible} 处）</button>}
   </>:<p role="status">对照表缺失或损坏，已无法还原或重新处理。仍可保存全占位符脱敏副本。</p>}
   <div className="redaction-exports">
    <button className="primary-button" disabled={!!busy||changes} onClick={()=>void exportFile(markdown&&job.restorable?'markdown':'placeholder')}>保存{markdown&&job.restorable?' Markdown ':'脱敏'}版</button>
    {job.restorable&&<><button disabled={!!busy||changes} onClick={()=>setConfirm('permanent')}>生成永久抹除版</button><button disabled={!!busy||changes} onClick={()=>void exportFile('restore')}>整份还原文字</button><button disabled={!!busy} onClick={()=>setConfirm('delete')}>删除对照表</button></>}
   </div>
   {changes&&<p>选项已修改，请先点“应用选项并重新处理”。</p>}
   {confirm&&<div role="alertdialog" aria-label={confirm==='delete'?'删除对照表确认':'永久抹除说明'} className="redaction-confirm">
    <p>{confirm==='delete'?'删除后，本任务无法还原或重新处理，仅保留全占位符副本；你自行保存的文件不受影响。':'这份新文件会真正删除待脱敏文字，已确认保留的位置仍保留。此文件无法通过占位符还原；本机历史对照表不会一并删除。'}</p>
    <button autoFocus disabled={!!busy} onClick={()=>setConfirm(null)}>取消</button>
    <button disabled={!!busy||confirm==='permanent'&&changes} onClick={()=>confirm==='permanent'?void exportFile('permanent'):void run('正在删除对照表…',async()=>{useJob(await api.deleteMapping(job));await refresh();})}>{confirm==='delete'?'确认删除':'确认生成'}</button>
   </div>}
  </div>}
  {saved&&desktop&&<div className="redaction-saved"><p>已保存到 <span>{saved}</span></p><div className="redaction-exports"><button onClick={()=>void openRedaction(saved).catch(()=>setError('无法打开文件，请在保存位置用 Word/WPS 打开。'))}>用默认应用打开</button><button onClick={()=>void openRedaction(saved,true).catch(()=>setError('无法显示文件，请前往保存位置查看。'))}>在访达中显示</button></div></div>}
  {recent.length>0&&<details className="redaction-recent" open><summary>最近脱敏</summary><ul>{recent.map(item=><li key={item.id}><button disabled={!!busy} onClick={()=>void run('正在打开处理记录…',async()=>useJob(await api.get(item.id)))}>{item.filename}</button><span>{new Date(item.created_at).toLocaleDateString('zh-CN')} · {item.restorable?'可查看对照表':'已无法还原'}</span></li>)}</ul></details>}
 </div></section>;
}
