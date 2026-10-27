import {useEffect,useRef,useState} from 'react';
import type {JSONContent} from '@tiptap/core';
import {litigationWorkClient,type LitigationMaterial,type WorkRequest} from '../../views/LitigationClient';
import type {LitigationMaterialControls} from '../../views/LitigationWorkspaceView';
import {editorClient,type EditableProduct,type EditableProductSummary} from './client';
import {useEditorDiscussion,type EditorTurnContext} from './DiscussionContext';
import type {EditorSelection} from './LitigationDocumentEditor';
import {WordDocumentEditor,type WordEditorHandle} from '../wordEditor/WordDocumentEditor';
import {WordSession,encodeWord,type WordDocument} from '../../runtime/wordDocumentClient';
import {wordSelection,wordRange} from './wordSelection';
import {EditorProposals} from './EditorProposals';
import {blankDocument} from './schema';
import {openCaseDatabase} from '../../platform/caseDatabase';
import {Plus,Sparkles,Download,ChevronDown,Trash2,ExternalLink,Upload,X} from 'lucide-react';
import {TemplateImport} from './TemplateImport';
import './LitigationDrafting.css';
const TYPES=['起诉状','答辩状','上诉状','代理词','质证意见','财产保全申请书','证据保全申请书','管辖权异议申请书','庭审提纲'];
const hasText=(doc:JSONContent):boolean=>!!doc.text?.trim()||!!doc.content?.some(hasText);
interface Props {openDocumentRequest?:{id:string;sequence:number}|null;workspaceId:string;materials:LitigationMaterial[];visible:boolean;busy:boolean;canWrite:boolean;onStart:(request:WorkRequest)=>void;onControlsChange?:(controls:LitigationMaterialControls)=>void}
export function LitigationDrafting({openDocumentRequest,workspaceId,materials,visible,busy,canWrite,onStart,onControlsChange}:Props){
 const discussion=useEditorDiscussion();
 const key=`verilex:drafting:${workspaceId}`;
 const [settings,setSettings]=useState(()=>{try{return JSON.parse(localStorage.getItem(key)??'null')??{};}catch{return {};}});
 const [type,setType]=useState<string>(typeof settings.type==='string'?settings.type:TYPES[0]);
 const [closedIds,setClosedIds]=useState<string[]>(Array.isArray(settings.closedIds)?settings.closedIds.filter((id:unknown)=>typeof id==='string'):[]);
 const requestedOpen=useRef(openDocumentRequest);requestedOpen.current=openDocumentRequest;
 const handledOpen=useRef<number|null>(null);
 const tabbarRef=useRef<HTMLDivElement>(null);
 const [templateLinkError,setTemplateLinkError]=useState('');
 const [templatesOpen,setTemplatesOpen]=useState(false),[templates,setTemplates]=useState<{path:string;name:string}[]>([]);
 const [templatePath,setTemplatePath]=useState<string>(typeof settings.templatePath==='string'?settings.templatePath:'');

 const crossTemplate=type==='质证意见'&&!templatePath;
 const [filename,setFilename]=useState(''),[deleteTarget,setDeleteTarget]=useState<string|null>(null);
 const filenameError=filename.trim()&&(/[\\/:*?"<>|\x00-\x1f]/.test(filename)||!filename.trim().replace(/\.docx$/i,'').replace(/[. ]/g,''))?'名称不能只包含句点，也不能包含 / \\ : * ? " < > | 等字符。':'';
 const [requirement,setRequirement]=useState<string>(typeof settings.requirement==='string'?settings.requirement:'');
 const [selected,setSelected]=useState<string[]>(Array.isArray(settings.selected)?settings.selected.filter((v:unknown)=>typeof v==='string'):[]);
 const [list,setList]=useState<EditableProductSummary[]>([]),[product,setProduct]=useState<EditableProduct|null>(null);
 const [error,setError]=useState(''),[notice,setNotice]=useState(''),[working,setWorking]=useState(false),[expanded,setExpanded]=useState(true),[epoch,setEpoch]=useState(0);
 const [tabMenu,setTabMenu]=useState<{id:string;x:number;y:number}|null>(null),[renameTarget,setRenameTarget]=useState<EditableProduct|null>(null),[renameValue,setRenameValue]=useState('');
 const menuRef=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(!tabMenu)return;menuRef.current?.querySelector('button')?.focus();const close=(e:PointerEvent)=>{if(!menuRef.current?.contains(e.target as Node))setTabMenu(null);};const key=(e:KeyboardEvent)=>{if(e.key==='Escape')setTabMenu(null);};window.addEventListener('pointerdown',close);window.addEventListener('keydown',key);return()=>{window.removeEventListener('pointerdown',close);window.removeEventListener('keydown',key);};},[tabMenu]);
 const [saveStatus,setSaveStatus]=useState('');
 const [selection,setSelection]=useState<EditorSelection|null>(null);
 const wordRef=useRef<WordEditorHandle>(null);
 const [native,setNative]=useState<{document:WordDocument;session:WordSession}|null>(null),[wordReady,setWordReady]=useState(false);
 const readyRef=useRef(wordReady);readyRef.current=wordReady;
 const current=useRef(product);current.current=product;
 const latest=useRef<JSONContent>(blankDocument()),baseline=useRef<JSONContent>(blankDocument());
 const pendingSelection=useRef<unknown>(null);
 const selectedRef=useRef(selection);selectedRef.current=selection;
 const [,refreshContent]=useState(0);
 const generation=useRef(0),alive=useRef(true),workingRef=useRef(false);
 const locked=!canWrite||working||!!discussion?.applying;
 const flushWord=async()=>{if(current.current){if(!wordRef.current||!readyRef.current){if(!wordRef.current?.isDirty())return;throw Error('文书有尚未保存的输入，请先恢复编辑器并保存。');}await wordRef.current.flush();}};
 useEffect(()=>{try{localStorage.setItem(key,JSON.stringify({type,templatePath,requirement,selected,closedIds,productId:product?.id??settings.productId}));}catch{}},[key,type,templatePath,requirement,selected,closedIds,product?.id,settings.productId]);
 useEffect(()=>{onControlsChange?.({workspaceId,materials,selected,hint:crossTemplate?'勾选对方证据目录、证据册及参照材料':undefined,locked:locked||busy,toggle:path=>{if(!locked&&!busy)setSelected(old=>old.includes(path)?old.filter(p=>p!==path):[...old,path]);},all:()=>{if(!locked&&!busy)setSelected(materials.filter(m=>m.supported).map(m=>m.path));},clear:()=>{if(!locked&&!busy)setSelected([]);}});},[workspaceId,materials,selected,locked,busy,onControlsChange,crossTemplate]);
 const register=(next:EditableProduct,activate:boolean)=>{
  const target={id:next.id,path:next.path,title:next.title,kind:'documents',flush:async()=>{if(current.current?.id===next.id)await flushWord();},capture:():EditorTurnContext|null=>current.current?.id===next.id&&!current.current.external_update&&!wordRef.current?.isDirty()?{product_id:next.id,base_hash:current.current.file_hash,document:latest.current,selection:selectedRef.current}:null,
   clearSelection:()=>{selectedRef.current=null;setSelection(null);},locate:(range?:{from?:number;to?:number;text?:string}|null)=>{wordRef.current?.locate(wordRange(latest.current,range));},apply:(value:EditableProduct)=>install(value,false)};
  discussion?.register(target);if(activate)discussion?.setTarget(target);
 };
 const install=async(next:EditableProduct,activate=true)=>{
  const request=++generation.current,wasReady=readyRef.current;setWordReady(false);
  const opened=await editorClient.openWord(workspaceId,next.id,next.file_hash).catch(error=>{if(alive.current&&request===generation.current){generation.current=request-1;setWordReady(wasReady);}throw error;});
  if(!alive.current||request!==generation.current)return;
  next=opened.product;current.current=next;latest.current=next.document??blankDocument();baseline.current=latest.current;
  const document={id:next.id,filename:next.title+'.docx',content:opened.content,revision:next.revision,updated_at:next.updated_at};
  let savedHash=next.file_hash;
  const session=new WordSession(document,async(_id,_revision,bytes)=>{
    const saved=await editorClient.saveWord(workspaceId,next.id,savedHash,encodeWord(bytes));savedHash=saved.file_hash;
    if(alive.current&&current.current?.id===saved.id&&request===generation.current){current.current=saved;setProduct(saved);latest.current=saved.document??blankDocument();baseline.current=latest.current;register(saved,false);const range=wordSelection(latest.current,pendingSelection.current);selectedRef.current=range;setSelection(range);discussion?.setSelection(range);refreshContent(v=>v+1);}
    return {id:saved.id,revision:saved.revision};
  });
  setNative({document,session});selectedRef.current=null;setSelection(null);setProduct(next);setClosedIds(old=>old.includes(next.id)?old.filter(id=>id!==next.id):old);setEpoch(v=>v+1);setSettings((old:any)=>({...old,productId:next.id}));setDeleteTarget(null);const knownType=TYPES.includes(next.document_type??'')?next.document_type:TYPES.find(name=>next.title.startsWith(name));if(next.document_type||knownType)setType(next.document_type||knownType!);register(next,activate);
 };
 const refreshList=async()=>{const response=await editorClient.list(workspaceId,'documents');if(alive.current){setList(response.products);if(response.errors.length)setError(response.errors[0].notice);}return response.products;};
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;current.current=null;generation.current++;};},[]);
 useEffect(()=>{if(!visible)return;let live=true;
  void refreshList().then(async rows=>{if(!live||current.current||requestedOpen.current)return;const id=settings.productId;if(id&&!closedIds.includes(id)&&rows.some(r=>r.id===id)){const value=await editorClient.read(workspaceId,id);if(live&&!requestedOpen.current){await install(value,false);setExpanded(false);}}}).catch(e=>{if(live)setError(e.message);});
  return()=>{live=false;};
 },[visible,workspaceId]);
 useEffect(()=>{if(!visible)return;let live=true;const poll=async()=>{
  const before=current.current;if(!before||workingRef.current||wordRef.current?.isDirty())return;
  try{const next=await editorClient.read(workspaceId,before.id);if(!live||current.current?.id!==before.id||current.current.file_hash!==before.file_hash||wordRef.current?.isDirty())return;
   if(next.file_hash!==before.file_hash||next.external_update){
    if(JSON.stringify(latest.current)!==JSON.stringify(baseline.current)){
     try{await editorClient.draft(workspaceId,before.id,before.file_hash,latest.current);}catch{}
     if(live)setError('文书已有新版本，当前手动修改已保留在编辑区。请先另存手动副本，再打开新版本。');return;
    }
    await install(next,false);void refreshList();
   }
  }catch(e){if(live)setError(e instanceof Error?e.message:'文书读取失败');}
 };void poll();const timer=setInterval(()=>void poll(),1500);return()=>{live=false;clearInterval(timer);};},[visible,workspaceId]);
 const perform=async(action:()=>Promise<void>)=>{if(workingRef.current)return;workingRef.current=true;setWorking(true);setError('');setNotice('');try{await action();}catch(e){if(alive.current)setError(e instanceof Error?e.message:'操作失败，请重试。');}finally{workingRef.current=false;if(alive.current)setWorking(false);}};
 useEffect(()=>{if(!visible)return;let live=true;void editorClient.templates(workspaceId).then(result=>{if(live)setTemplates(result.templates);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[workspaceId,visible]);
 const create=async()=>{
  if(filenameError)throw Error(filenameError);
  const value=templatePath
   ?await editorClient.importCopy(workspaceId,templatePath,filename.trim()||type,'documents',false,type)
   :await editorClient.create(workspaceId,filename.trim()||type,'documents',blankDocument(),type);
  if(!alive.current)return value;await install(value);await refreshList();window.dispatchEvent(new CustomEvent('verilex:workspace-files-changed',{detail:{workspaceId}}));return current.current!;
 };
 const begin=async(mode:'generate'|'rewrite'|'selection')=>perform(async()=>{
  const request=requirement.trim()||(mode!=='selection'?`请起草${type}。`:'');
  if(!request)throw Error('请填写撰写或修改要求。');
  if(selected.some(path=>!materials.some(m=>m.path===path&&m.supported)))throw Error('部分选中材料不可读取，请在左侧重新选择。');
  if(current.current)await flushWord();let value=current.current;if(!value)value=await create();
  if(!alive.current)return;
  const doc=structuredClone(latest.current),range=mode==='selection'?selectedRef.current:null;
  if(mode==='selection'&&!range)throw Error('请先在正文中选中文字。');
  if(mode==='generate'&&hasText(doc)){if(templatePath)mode='rewrite';else throw Error('已有正文，请选择重新生成全文。');}
  await editorClient.draft(workspaceId,value.id,value.file_hash,doc);
  const work=await litigationWorkClient.read(workspaceId,'documents');
  const context:EditorTurnContext={product_id:value.id,base_hash:value.file_hash,document:doc,selection:range,...(mode==='generate'?{operation:'generate' as const}:{})};
  discussion?.activate(value.id);setExpanded(false);
  onStart({kind:'documents',section_id:null,materials:[...selected],expected_revision:work.revision,title:`${mode==='generate'?'起草':mode==='rewrite'?'重写':'修改'}${value.title}`,editorContext:context,
   message:`文书类型：${type}。${templatePath?'沿用模板正文的文种、标题和格式。':''}${mode==='generate'?'请首次起草完整正文，调用 litigation_editor_generate 直接填入绑定的中央空白编辑区。':mode==='rewrite'?'请重新生成整篇文书，以 litigation_editor_propose 的全文建议提交批注，等待我接受后替换。':'请仅修改本轮选中文字，以 litigation_editor_propose 提交批注建议，不改选区外正文。'}\n${selected.length?`依据材料（JSON路径列表）：${JSON.stringify(selected)}。请围绕这些材料起草。`:''}\n撰写要求：${request}`});
 });
 const exportFile=async(format:'docx'|'pdf')=>perform(async()=>{await flushWord();const value=current.current;if(!value)return;
  const result=await editorClient.export(workspaceId,value.id,value.file_hash,latest.current,value.title,format);
  current.current=result.product;setProduct(result.product);setNotice(`已导出到当前工作区：${result.path}`);window.dispatchEvent(new CustomEvent('verilex:workspace-files-changed',{detail:{workspaceId}}));
 });
 const resetDocument=()=>{generation.current++;current.current=null;setNative(null);setProduct(null);setWordReady(false);setSelection(null);selectedRef.current=null;pendingSelection.current=null;latest.current=blankDocument();baseline.current=latest.current;setSettings((old:any)=>({...old,productId:undefined}));setFilename('');setTemplatePath('');setType(TYPES[0]);setDeleteTarget(null);setTemplatesOpen(false);setExpanded(true);};
 const deleteDocument=()=>perform(async()=>{
  const id=deleteTarget;if(!id||current.current?.id!==id)return;
  if(native)await flushWord();const value=current.current;if(!value||value.id!==id)return;
  await editorClient.delete(workspaceId,id,value.file_hash);
  discussion?.unregister(id);resetDocument();
  setList(old=>old.filter(row=>row.id!==id));
  setNotice(`已删除“${value.title}”。可在“文书起草 / 已删除文书”中找回。`);
  window.dispatchEvent(new CustomEvent('verilex:workspace-files-changed',{detail:{workspaceId}}));
  await refreshList();
 });
 const openRename=(id:string)=>{setTabMenu(null);void perform(async()=>{await flushWord();const value=await editorClient.read(workspaceId,id);setRenameTarget(value);setRenameValue(value.title);});};
 const renameDocument=()=>perform(async()=>{
  if(!renameTarget)return;await flushWord();const value=current.current?.id===renameTarget.id?current.current:renameTarget;
  const saved=await editorClient.rename(workspaceId,value.id,value.file_hash,renameValue);
  discussion?.renameIdentity(saved.id,saved.title,saved.path);
  setRenameTarget(null);
  if(current.current?.id===saved.id)await install(saved,false);
  await refreshList();setNotice(`已重命名为“${saved.title}”，导出将使用此名称。`);
  window.dispatchEvent(new CustomEvent('verilex:workspace-files-changed',{detail:{workspaceId}}));
 });
 const openTabs=list.filter(row=>!closedIds.includes(row.id));
 const openDocument=(id:string)=>perform(async()=>{await flushWord();await install(await editorClient.read(workspaceId,id));setTemplatesOpen(false);setExpanded(false);});
 useEffect(()=>{
  if(!visible||!openDocumentRequest||handledOpen.current===openDocumentRequest.sequence||working||busy||discussion?.applying||renameTarget||deleteTarget)return;
  handledOpen.current=openDocumentRequest.sequence;
  void perform(async()=>{
   if(current.current?.id!==openDocumentRequest.id){
    await flushWord();const next=await editorClient.read(workspaceId,openDocumentRequest.id);
    if(requestedOpen.current?.sequence!==openDocumentRequest.sequence)return;
    await install(next);
   }
   setTemplatesOpen(false);setExpanded(false);await refreshList();
  });
 },[visible,openDocumentRequest,working,busy,discussion?.applying,renameTarget,deleteTarget]);
 const closeTab=(id:string)=>perform(async()=>{
  if(current.current?.id===id){
   await flushWord();
   const index=openTabs.findIndex(row=>row.id===id),next=openTabs[index+1]??openTabs[index-1];
   if(next){await install(await editorClient.read(workspaceId,next.id));setExpanded(false);}
   else{resetDocument();discussion?.setTarget(null);}
  }
  setClosedIds(old=>old.includes(id)?old:[...old,id]);setTabMenu(null);
  requestAnimationFrame(()=>tabbarRef.current?.querySelector<HTMLButtonElement>('[role=tab][aria-selected=true]')?.focus());
 });
 const hasBody=hasText(latest.current);
 return <section className="litigation-drafting" aria-label="文书起草工作区">
  {tabMenu&&<div ref={menuRef} className="drafting-tab-menu" role="menu" aria-label="文书标签菜单" style={{left:tabMenu.x,top:tabMenu.y}}><button role="menuitem" onClick={()=>openRename(tabMenu.id)}>重命名…</button></div>}
  <div className="drafting-tabbar" ref={tabbarRef}>
   <div role="tablist" aria-label="文书标签" className="drafting-tabs" onKeyDown={event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    const tabs=[...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]:not(:disabled)')];const index=tabs.indexOf(event.target as HTMLButtonElement);if(index<0)return;
    event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next]?.focus();tabs[next]?.click();
   }}>
    {openTabs.map(row=><div key={row.id} role="presentation" className={`drafting-document-tab ${product?.id===row.id?'is-active':''}`}><button role="tab" aria-selected={product?.id===row.id} aria-controls="drafting-document-panel" tabIndex={product?.id===row.id?0:-1} title={`${row.title} · 右键重命名`} disabled={locked||busy||!!renameTarget} onContextMenu={event=>{event.preventDefault();if(!locked&&!busy&&!renameTarget)setTabMenu({id:row.id,x:Math.max(8,Math.min(event.clientX,window.innerWidth-150)),y:Math.max(8,Math.min(event.clientY,window.innerHeight-60))});}} onKeyDown={event=>{if(event.key==='Delete'){event.preventDefault();if(!locked&&!busy&&!renameTarget)void closeTab(row.id);return;}if(event.key==='ContextMenu'||(event.shiftKey&&event.key==='F10')){event.preventDefault();const rect=event.currentTarget.getBoundingClientRect();setTabMenu({id:row.id,x:Math.min(rect.left,window.innerWidth-150),y:rect.bottom});}}} onClick={()=>{if(row.id!==product?.id)void openDocument(row.id);}}><span>{row.title}</span></button><button type="button" className="drafting-tab-close" aria-label={`关闭标签：${row.title}`} title="关闭标签，文件保留" disabled={locked||busy||!!renameTarget||!!deleteTarget} onClick={()=>void closeTab(row.id)}><X size={13}/></button></div>)}
    <button role="tab" aria-selected={!product} aria-controls="drafting-document-panel" tabIndex={!product?0:-1} className="drafting-new-tab" disabled={locked||busy||!!renameTarget} onClick={()=>{if(product)void perform(async()=>{await flushWord();resetDocument();discussion?.setTarget(null);});}}><Plus size={14}/>新建文书</button>
   </div>

  </div>
  <div className="drafting-commandbar">
   <button className="drafting-ai-toggle" aria-expanded={expanded||!product} aria-controls="drafting-ai-panel" onClick={()=>{setTemplatesOpen(false);setExpanded(v=>!v);}} disabled={!product}><Sparkles size={14}/>AI辅助起草<ChevronDown size={13}/></button>
   <button className="drafting-template-reference" title="在系统浏览器查看最高人民法院诉讼文书样式" onClick={()=>{setTemplateLinkError('');void openCaseDatabase('templates').catch(()=>{if(alive.current)setTemplateLinkError('系统浏览器没有打开，请再次点击“最高法文书模板”重试。');});}}><ExternalLink size={14}/>最高法文书模板</button>
   {product&&<><span className="drafting-save-state" role="status">{saveStatus}</span><div className="drafting-exports" role="group" aria-label="导出到当前工作区" title="导出到当前工作区；同名文件自动编号。">
    <button disabled={locked||busy||!wordReady||!hasBody||product.external_update} onClick={()=>void exportFile('docx')}><Download size={14}/>导出 Word</button><button disabled={locked||busy||!wordReady||!hasBody||product.external_update} onClick={()=>void exportFile('pdf')} aria-label="导出 PDF">导出 PDF</button>
   </div><button className="drafting-delete" aria-label="删除文书" title="删除当前文书" disabled={locked||busy||!!renameTarget} onClick={()=>setDeleteTarget(product.id)}><Trash2 size={14}/></button></>}
  </div>
  {templateLinkError&&<p role="alert" className="evidence-error">{templateLinkError}</p>}
  {renameTarget&&<form className="drafting-rename" aria-label="重命名文书" onSubmit={event=>{event.preventDefault();void renameDocument();}}><label>文件名称<input autoFocus aria-label="新的文件名称" maxLength={80} value={renameValue} disabled={working} onChange={event=>setRenameValue(event.target.value)} onKeyDown={event=>{if(event.key==='Escape')setRenameTarget(null);}}/></label><span>.docx</span><button type="submit" disabled={locked||busy||!renameValue.trim()}>保存名称</button><button type="button" disabled={working} onClick={()=>setRenameTarget(null)}>取消</button></form>}
  {deleteTarget&&product?.id===deleteTarget&&<div className="drafting-delete-confirm" role="alertdialog" aria-label="删除文书确认">
   <span>删除“{product.title}”？文件将移到“文书起草 / 已删除文书”，已导出的副本保留。</span>
   <button disabled={locked||busy} onClick={()=>void deleteDocument()}>确认删除</button><button disabled={working} onClick={()=>setDeleteTarget(null)}>取消</button>
  </div>}
  {templatesOpen&&visible&&<TemplateImport key={workspaceId} workspaceId={workspaceId} onClose={()=>setTemplatesOpen(false)} onSaved={saved=>{
   setTemplates(old=>[...old.filter(t=>t.path!==saved.path),saved]);setTemplatesOpen(false);
   if(!product){setTemplatePath(saved.path);setType(saved.name);}
   setNotice(`已保存模板“${saved.name}”，可在文书类型中选择。`);
   window.dispatchEvent(new CustomEvent('verilex:workspace-files-changed',{detail:{workspaceId}}));
  }}/>}
  {(expanded||!product)&&<div className="drafting-setup" id="drafting-ai-panel">
   <div className="drafting-source-row"><label>文书类型<select aria-label="文书类型" disabled={locked||busy||!!product} value={!product&&templatePath?`template:${templatePath}`:type} onChange={e=>{
    const value=e.target.value,template=templates.find(t=>`template:${t.path}`===value);
    setTemplatePath(template?.path??'');setType(template?.name??value);
   }}>
    {product&&!TYPES.includes(type)&&<option value={type}>{type}</option>}
    <optgroup label="常用文书">{TYPES.map(name=><option key={name} value={name}>{name}</option>)}</optgroup>
    {!!templates.length&&<optgroup label="已上传模板">{templates.map(t=><option key={t.path} value={`template:${t.path}`}>{t.name}</option>)}</optgroup>}
   </select></label><button className="drafting-upload-toggle" disabled={locked||busy} onClick={()=>setTemplatesOpen(true)}><Upload size={14}/>上传模板</button>
   {!product&&<label className="drafting-filename">文件名称<input aria-label="文件名称" maxLength={80} placeholder={type} value={filename} disabled={locked||busy} aria-invalid={!!filenameError} onChange={e=>setFilename(e.target.value)}/><span>.docx</span></label>}<span>依据材料：左侧已选 {selected.length} 份</span></div>
   {filenameError&&!product&&<p role="alert" className="evidence-error">{filenameError}</p>}
   <label>起草或修改要求<textarea aria-label="起草或修改要求" value={requirement} disabled={locked} rows={3} placeholder={crossTemplate?'选填：说明我方立场或重点质证问题；留空则结合材料按模板起草':'选填：说明我方立场或重点事项；留空则结合材料按文书类型起草'} onChange={e=>setRequirement(e.target.value)}/></label>
   <div className="drafting-actions">{!hasBody?<button className="litigation-primary" disabled={locked||busy||(!product&&!!filenameError)||(!!product&&!wordReady)} onClick={()=>void begin('generate')}>生成正文</button>:<><button disabled={locked||busy||(!product&&!!filenameError)||(!!product&&!wordReady)} onClick={()=>void begin('rewrite')}>重新生成全文</button><button disabled={locked||busy||!selection||!requirement.trim()} onClick={()=>void begin('selection')}>修改选中文字{selection?`（${selection.text.length}字）`:''}</button></>}
    {!product&&<button disabled={locked||busy||!!filenameError} onClick={()=>void perform(async()=>{await create();setExpanded(false);})}>{templatePath?'使用模板新建':'直接手动撰写'}</button>}
   </div>
  </div>}
  {error&&<p role="alert" className="evidence-error">{error}{product&&!native&&<button disabled={locked} onClick={()=>void perform(async()=>{const next=await editorClient.create(workspaceId,product.title+'（手动副本）','documents',latest.current);await install(next);await refreshList();})}>另存手动副本</button>}</p>}
  {notice&&<p role="status" className="drafting-notice">{notice}</p>}
  {product&&<>

   {product.external_update?<p role="alert">这份 Word 已由外部应用更新，请打开原文件核对；可另存手动副本继续撰写。</p>:<div className="drafting-paper-layout" id="drafting-document-panel" role="tabpanel" aria-label={product.title}>{native&&<WordDocumentEditor key={`${product.id}:${epoch}`} ref={wordRef} document={native.document} session={native.session} label="文书 Word 编辑器" highlight={discussion?.preview?.product_id===product.id&&JSON.stringify(discussion.preview.preview_before??discussion.preview.before)===JSON.stringify(latest.current)?wordRange(latest.current,((discussion.preview.preview_changes??discussion.preview.changes)?.[0]?.selection)):null} tracking={false} hideHeader onStatusChange={setSaveStatus} editable={!locked} onReady={setWordReady}
     onChanged={()=>{pendingSelection.current=null;selectedRef.current=null;setSelection(null);discussion?.setSelection(null);}}
     onSelection={value=>{pendingSelection.current=value;const range=wordSelection(latest.current,value);selectedRef.current=range;setSelection(range);discussion?.setSelection(range);}}
    />}
    {visible&&<aside className="drafting-comments" aria-label="AI 修改批注"><EditorProposals workspaceId={workspaceId} comments/></aside>}
   </div>}
  </>}
 </section>;
}
