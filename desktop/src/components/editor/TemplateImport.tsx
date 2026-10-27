import {useEffect,useRef,useState} from 'react';
import {invoke} from '@tauri-apps/api/core';
import {getCurrentWebview} from '@tauri-apps/api/webview';
import {Upload} from 'lucide-react';
import {DetailDialog} from '../DetailDialog';
import {editorClient} from './client';

const LIMIT=20*1024*1024;
export function TemplateImport({workspaceId,onClose,onSaved}:{workspaceId:string;onClose:()=>void;onSaved:(template:{path:string;name:string})=>void}){
 const [file,setFile]=useState<File|null>(null),[name,setName]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[dragging,setDragging]=useState(false);
 const input=useRef<HTMLInputElement>(null),zone=useRef<HTMLDivElement>(null),alive=useRef(true),pending=useRef(false);
 const choose=(files:File[])=>{
  if(pending.current)return;
  setError('');setFile(null);setName('');
  if(files.length!==1){setError('请一次选择一个 Word 模板。');return;}
  const next=files[0];
  if(!/\.docx$/i.test(next.name)){setError('请选择 .docx 文件；旧版 .doc 请先用 Word/WPS 另存为 .docx。');return;}
  if(!next.size||next.size>LIMIT){setError('模板文件不能为空，且不能超过 20 MiB。');return;}
  setFile(next);setName(next.name.replace(/\.docx$/i,'').slice(0,80));
 };
 useEffect(()=>{
  alive.current=true;let cancelled=false,unlisten:(()=>void)|undefined;
  if(Reflect.has(globalThis,'__TAURI_INTERNALS__'))void getCurrentWebview().onDragDropEvent(event=>{
   if(cancelled)return;
   const payload=event.payload;
   if(payload.type==='leave'){setDragging(false);return;}
   const rect=zone.current?.getBoundingClientRect(),ratio=window.devicePixelRatio||1;
   const inside=!!rect&&payload.position.x/ratio>=rect.left&&payload.position.x/ratio<=rect.right&&payload.position.y/ratio>=rect.top&&payload.position.y/ratio<=rect.bottom;
   setDragging(inside&&payload.type!=='drop');
   if(!inside||payload.type!=='drop'||pending.current)return;
   if(payload.paths.length!==1){setError('请一次选择一个 Word 模板。');return;}
   const path=payload.paths[0],filename=path.split(/[\\/]/).pop()??'';
   if(!/\.docx$/i.test(filename)){setError('请选择 .docx 文件；旧版 .doc 请先用 Word/WPS 另存为 .docx。');return;}
   pending.current=true;setBusy(true);setError('');
   void invoke<ArrayBuffer|number[]>('read_dropped_file',{path}).then(raw=>{
    if(cancelled)return;
    const bytes=new Uint8Array(raw);if(bytes.byteLength<8)throw Error('文件读取失败，请重新选择。');
    pending.current=false;choose([new File([bytes.slice(8)],filename,{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'})]);
   }).catch(e=>{if(!cancelled)setError(e instanceof Error?e.message:'文件读取失败，请从文件夹重新选择。');}).finally(()=>{pending.current=false;if(!cancelled)setBusy(false);});
  }).then(fn=>{if(cancelled)fn();else unlisten=fn;}).catch(()=>{if(!cancelled)setError('拖放暂不可用，请从文件夹选择文件。');});
  return()=>{alive.current=false;cancelled=true;unlisten?.();};
 },[workspaceId]);
 const validName=!!name.trim().replace(/\.docx$/i,'').replace(/[. ]/g,'')&&!/[\\/:*?"<>|\x00-\x1f\u202a-\u202e\u2066-\u2069]/.test(name);
 const save=async()=>{
  if(!file||!validName||pending.current)return;
  pending.current=true;setBusy(true);setError('');
  try{const saved=await editorClient.importTemplate(workspaceId,file,name.trim());if(alive.current)onSaved(saved);}
  catch(e){if(alive.current)setError(e instanceof Error?e.message:'模板保存失败，请重试。');}
  finally{pending.current=false;if(alive.current)setBusy(false);}
 };
 return <DetailDialog title="上传模板" className="drafting-upload-dialog" busy={busy} onClose={onClose}>
  <div className="drafting-upload-body" data-template-drop ref={zone}>
   <div className={`drafting-upload-drop ${dragging?'is-dragging':''}`} onDragOver={e=>{e.preventDefault();e.stopPropagation();if(!busy)setDragging(true);}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setDragging(false);}} onDrop={e=>{e.preventDefault();e.stopPropagation();setDragging(false);choose(Array.from(e.dataTransfer.files));}}>
    <Upload size={24}/><span>{file?file.name:'将 Word 模板拖到这里'}</span>
    <button type="button" disabled={busy} onClick={()=>input.current?.click()}>从文件夹选择</button>
    <input ref={input} type="file" hidden accept=".docx" aria-label="选择模板文件" onChange={e=>{choose(Array.from(e.target.files??[]));e.target.value='';}}/>
    <small>支持 .docx，最大 20 MiB</small>
   </div>
   <label>模板名称<input aria-label="模板名称" value={name} maxLength={80} disabled={busy} onChange={e=>setName(e.target.value)} placeholder="默认使用文件名称"/></label>
   <p>保留原有正文和格式。保存后可在“文书类型”中选择。</p>
   {error&&<p role="alert" className="evidence-error">{error}</p>}
   <div className="drafting-upload-actions"><button disabled={busy} onClick={onClose}>取消</button><button className="litigation-primary" disabled={busy||!file||!validName} onClick={()=>void save()}>{busy?'正在保存…':'保存为模板'}</button></div>
  </div>
 </DetailDialog>;
}
