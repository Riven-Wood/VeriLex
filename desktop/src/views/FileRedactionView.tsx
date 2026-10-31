import { useEffect,useRef,useState } from 'react';
import { FileText, FolderOpen, ShieldCheck } from 'lucide-react';
import { getCurrentWebview } from '@tauri-apps/api/webview';
import { invoke } from '@tauri-apps/api/core';
import { redactionApi,REDACTION_TYPES,DEFAULT_REDACTION_TYPES,type RedactionApi,type RedactionReplacement } from '../runtime/redactionClient';
import { saveRedaction,openRedaction,chooseRedactionDirectory,defaultRedactionDirectory,listRedactionExports,deleteRedactionExport,type RedactionHistoryEntry } from '../platform/redactionExport';
import './FileRedactionView.css';

export function FileRedactionView({api=redactionApi}:{api?:RedactionApi}) {
 const [file,setFile]=useState<File|null>(null);
 const [recent,setRecent]=useState<RedactionHistoryEntry[]>([]),[kinds,setKinds]=useState(DEFAULT_REDACTION_TYPES);
 const [custom,setCustom]=useState<RedactionReplacement[]>([]);
 const [customDraft,setCustomDraft]=useState(''),[replacementDraft,setReplacementDraft]=useState('');
 const [markdown,setMarkdown]=useState(false),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [outputDirectory,setOutputDirectory]=useState<string|null>(null),[outputName,setOutputName]=useState('');
 const [deletePath,setDeletePath]=useState<string|null>(null);
 const isMarkdownFile=!!file&&/\.md$/i.test(file.name);
 const markdownOutput=markdown||isMarkdownFile;
 const desktop=Reflect.has(globalThis,'__TAURI_INTERNALS__');
 const [directoryLoading,setDirectoryLoading]=useState(desktop);
 const [directoryUnavailable,setDirectoryUnavailable]=useState(false);
 const input=useRef<HTMLInputElement>(null),lock=useRef(false),mounted=useRef(true);
 const refresh=async()=>{if(!desktop)return;const entries=await listRedactionExports();if(mounted.current)setRecent(entries);};
 useEffect(()=>{mounted.current=true;void refresh().catch(()=>setError('脱敏历史暂时无法加载，请重新打开页面。'));return()=>{mounted.current=false;};},[]);
 useEffect(()=>{if(!desktop)return;let cancelled=false;void defaultRedactionDirectory().then(path=>{if(!cancelled)setOutputDirectory(path);}).catch(()=>{if(!cancelled){setDirectoryUnavailable(true);setError('无法找到桌面，请选择输出目录。');}}).finally(()=>{if(!cancelled)setDirectoryLoading(false);});return()=>{cancelled=true;};},[]);
 const accept=(files:File[])=>{
  if(lock.current)return;
  setError('');setNotice('');
  if(files.length!==1){setError('一次请选择一份 Word 或 Markdown 文件。');return;}
  const selected=files[0];
  if(!/\.(?:docx?|md)$/i.test(selected.name)){setError('请选择 .doc、.docx 或 .md 文件，暂不支持 PDF 或扫描件。');return;}
  if(selected.size>20*1024*1024){setError('文件不能超过 20 MB，请拆分后重试。');return;}
  setOutputName('');setFile(selected);
 };
 const acceptRef=useRef(accept);acceptRef.current=accept;
 useEffect(()=>{
  if(!Reflect.has(globalThis,'__TAURI_INTERNALS__'))return;
  let dispose:(()=>void)|undefined;let cancelled=false;
  void getCurrentWebview().onDragDropEvent(event=>{
   if(event.payload.type!=='drop'||lock.current)return;
   const paths=event.payload.paths;
   if(paths.length!==1){setError('一次请选择一份 Word 或 Markdown 文件。');return;}
   const name=paths[0].split(/[\\/]/).pop()??'';
   if(!/\.(?:docx?|md)$/i.test(name)){setError('请选择 .doc、.docx 或 .md 文件，暂不支持 PDF 或扫描件。');return;}
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
  if(lock.current)return;lock.current=true;setBusy(label);setError('');setNotice('');
  try{await work();}catch(e){if(mounted.current)setError(e instanceof Error?e.message:'操作失败，请重试。');}
  finally{lock.current=false;if(mounted.current)setBusy('');}
 };
 const addCustom=()=>{
  const old=customDraft.trim();if(!old)return;
  if(custom.some(item=>item.old===old)){setError('这项文字已经添加，请移除原条目后再添加。');return;}
  if(custom.length>=100){setError('最多添加100项。');return;}
  setCustom(current=>[...current,{old,new:replacementDraft}]);setCustomDraft('');setReplacementDraft('');setError('');
 };
 const exportFile=()=>run('正在本机处理并准备导出…',async()=>{
  if(!file)return;
  if(customDraft.trim()||replacementDraft){throw new Error('请先添加当前自定义删除项，或清空输入后再导出。');}
  const stem=outputName.trim().replace(/\.(docx|md)$/i,'');
  if(outputName.trim()&&!stem)throw new Error('请填写文件名。');
  if(stem&&(stem.startsWith('.')||/[\\/:*?"<>|\u0000-\u001f\u007f]/.test(stem)))throw new Error('文件名不能以点开头，也不能包含路径或特殊符号。');
  const filename=stem?`${stem}${markdownOutput?'.md':'.docx'}`:markdownOutput?'脱敏版.md':'脱敏修订版.docx';
  if(new TextEncoder().encode(filename).length>240)throw new Error('文件名过长，请缩短后重试。');
  const result=await api.process(file,kinds,custom,markdownOutput?'markdown':'word');
  if(!mounted.current)return;
  const path=await saveRedaction({...result,filename},outputDirectory);
  if(!mounted.current)return;
  setNotice(path?(desktop?'已导出新文件。':'已发起浏览器下载。'):'已取消保存。');
  if(path)await refresh();
 });
 const chooseDirectory=()=>run('正在选择输出目录…',async()=>{const path=await chooseRedactionDirectory();if(path&&mounted.current){setOutputDirectory(path);setDirectoryUnavailable(false);}else if(directoryUnavailable)throw new Error('无法找到桌面，请选择输出目录。');});
 const deleteHistory=(path:string)=>run('正在删除记录…',async()=>{await deleteRedactionExport(path);if(mounted.current){setRecent(current=>current.filter(item=>item.path!==path));setDeletePath(null);}});
 return <section className="file-redaction-view" aria-label="文件脱敏" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();accept(Array.from(e.dataTransfer.files));}}>
  <div className="redaction-page"><header className="redaction-page-heading"><div><h1>文件脱敏</h1><p>选择处理方式，导出新文件。</p></div><span className="redaction-local"><ShieldCheck size={15} aria-hidden="true"/>本机处理</span></header>
  <div className="redaction-card redaction-input-card">
  <input ref={input} aria-label="选择 Word 或 Markdown 文件" type="file" accept=".doc,.docx,.md" hidden disabled={!!busy} onChange={e=>{accept(Array.from(e.target.files??[]));e.target.value='';}}/>
  <div className="redaction-file"><span className="redaction-file-icon"><FileText size={24} aria-hidden="true"/></span><div className="redaction-file-name"><small>当前文件</small><strong>{file?.name??'尚未选择文件'}</strong>{!file&&<small className="redaction-file-hint">可拖入 Word 或 Markdown 文件 · .doc / .docx / .md · 最大 20 MB</small>}</div><button className={!file?'primary-button':''} disabled={!!busy} onClick={()=>input.current?.click()}>{file?'选择其他文件':'选择文件'}</button></div>
  <div className="redaction-output" aria-label="输出目录">
   <FolderOpen size={20} aria-hidden="true"/>
   <div className="redaction-output-path"><strong>输出目录{desktop&&<small>（默认保存到桌面）</small>}</strong><span title={outputDirectory??undefined}>{outputDirectory??(desktop?(directoryLoading?'正在读取桌面…':'请选择目录'):'浏览器下载位置')}</span></div>
   {desktop&&<div className="redaction-directory-actions"><button disabled={!!busy||directoryLoading} onClick={()=>void chooseDirectory()}>{outputDirectory?'更换目录':'选择目录'}</button></div>}
  </div>

  <label className="redaction-filename">导出文件名<input aria-label="导出文件名" value={outputName} disabled={!!busy} onChange={e=>setOutputName(e.target.value)} placeholder="留空使用默认名称"/><small>无需填写后缀，按导出格式自动补齐。</small></label>

   <section className="redaction-options" aria-label="脱敏选项"><h2>脱敏选项</h2>
    <p className="redaction-disclosure">可能漏检或误识别，可添加自定义删除项。图片、图章和手写中的文字不在识别范围。</p>
    <fieldset disabled={!!busy}><legend>识别类型</legend><div className="redaction-types">{Object.entries(REDACTION_TYPES).map(([key,label])=><label key={key}><input type="checkbox" checked={kinds.includes(key)} onChange={e=>setKinds(current=>e.target.checked?[...current,key]:current.filter(k=>k!==key))}/>{label}</label>)}</div>
     <div className="redaction-custom">
      <h3>自定义删除</h3>
      <div className="redaction-custom-entry">
       <label>要脱敏的文字<input value={customDraft} maxLength={200} placeholder="输入原文字" onChange={e=>setCustomDraft(e.target.value)}/></label>
       <label>脱敏后的替代文字<input value={replacementDraft} maxLength={200} placeholder="留空表示删除" onChange={e=>setReplacementDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.nativeEvent.isComposing){e.preventDefault();addCustom();}}}/></label>
       <button disabled={!customDraft.trim()||!!busy} onClick={addCustom}>添加</button>
      </div>
      {custom.length>0&&<ul className="redaction-custom-list">{custom.map(item=><li key={item.old}><span>{item.old} → {item.new||'（删除）'}</span><button aria-label={`移除 ${item.old}`} onClick={()=>setCustom(current=>current.filter(value=>value.old!==item.old))}>移除</button></li>)}</ul>}
     </div>
    </fieldset>
    <div className="redaction-start-row">
     <label><input type="checkbox" checked={markdownOutput} disabled={!!busy||isMarkdownFile} onChange={e=>setMarkdown(e.target.checked)}/>导出为 Markdown</label>
     <button className="primary-button" disabled={!!busy||!file||directoryLoading||directoryUnavailable} onClick={()=>void exportFile()}>{markdownOutput?'导出 Markdown 文件':'导出 Word 修订版'}</button>
    </div>
    <p className="redaction-disclosure">{isMarkdownFile?'Markdown 按原始文本处理；图片和链接指向的文件不会被处理。':markdown?'Word 转为 Markdown 后，表格、脚注等排版可能丢失。':'Word 保留删除和替换的修订记录，被删除的原文仍可查看或恢复。对外分享前请在 Word 中接受全部修订。'}</p>
   </section>
  <p className="redaction-note">旧版 .doc 会在本机转换，复杂排版可能变化；Word 结果统一为 .docx。</p>
  </div>
  {busy&&<p role="status" aria-live="polite">{busy}</p>}
  {error&&<p className="redaction-error" role="alert">{error}</p>}
  {notice&&<p role="status">{notice}</p>}
  {recent.length>0&&<section className="redaction-saved" aria-label="脱敏历史"><h2>脱敏历史</h2><ul className="redaction-history">{recent.map(item=><li key={item.path}>
   <div><strong>{item.filename}</strong><time>{new Date(item.saved_at).toLocaleString('zh-CN')}</time><p>{item.path}</p></div>
   <div className="redaction-history-actions"><button disabled={!!busy} onClick={()=>void openRedaction(item.path).catch(e=>setError(e instanceof Error?e.message:String(e)))}>用默认应用打开</button><button disabled={!!busy} onClick={()=>void openRedaction(item.path,true).catch(e=>setError(e instanceof Error?e.message:String(e)))}>在访达显示</button>{deletePath===item.path?<><button disabled={!!busy} onClick={()=>void deleteHistory(item.path)}>确认删除记录</button><button disabled={!!busy} onClick={()=>setDeletePath(null)}>取消</button></>:<button disabled={!!busy} aria-label={`删除 ${item.filename} 的记录`} onClick={()=>setDeletePath(item.path)}>删除记录</button>}</div>
  </li>)}</ul></section>}
 </div></section>;
}
