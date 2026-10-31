import {useEditorDiscussion} from "./DiscussionContext";
import type {EditorSelection} from "./LitigationDocumentEditor";
import {openCaseDatabase} from '../../platform/caseDatabase';
import {useEffect,useRef,useState,type ReactNode} from 'react';
import type {JSONContent} from '@tiptap/core';
import {litigationClient} from '../../views/LitigationClient';
import type {WorkKind} from '../../views/LitigationClient';
import {editorClient,type EditableProduct,type EditableProductSummary} from './client';
import {blankDocument} from './schema';
import {LitigationDocumentEditor} from './LitigationDocumentEditor';
import {WordDocumentEditor,type WordEditorHandle} from '../wordEditor/WordDocumentEditor';
import {retainedWordSession,encodeWord,WordSession,type WordDocument} from '../../runtime/wordDocumentClient';
import {wordSelection} from './wordSelection';

const templates:Record<string,string[]>={
  '起诉状':['原告：［填写］','被告：［填写］','诉讼请求','［填写具体请求］','事实与理由','［填写事实及理由］','此致','［受理法院］','具状人：［填写］','日期：［填写］'],
  '答辩状':['答辩人：［填写］','被答辩人：［填写］','答辩意见','［逐项回应诉讼请求］','事实与理由','［填写事实及理由］','此致','［受理法院］','答辩人：［填写］','日期：［填写］'],
  '上诉状':['上诉人：［填写］','被上诉人：［填写］','原审案号：［填写］','上诉请求','［填写请求］','上诉理由','［填写理由］','此致','［受理法院］','上诉人：［填写］','日期：［填写］'],
  '代理词':['案号：［填写］','代理意见','［填写争议问题、证据与论证］','代理人：［填写］','日期：［填写］'],
  '质证意见':['证据名称及编号：［填写］','质证意见','［填写意见与理由］','日期：［填写］'],
  '财产保全申请书':['申请人：［填写］','被申请人：［填写］','申请事项','［填写保全范围］','事实与理由','［填写理由］','担保及财产线索','［填写］','申请人：［填写］','日期：［填写］'],
  '证据保全申请书':['申请人：［填写］','申请事项','［填写证据及保全方式］','事实与理由','［填写理由］','申请人：［填写］','日期：［填写］'],
  '管辖权异议申请书':['申请人：［填写］','案号：［填写］','申请事项','［填写请求］','事实与理由','［填写理由］','申请人：［填写］','日期：［填写］'],
  '庭审提纲':['本次庭审事项','［填写］','事实与证据','［填写］','发问与回应','［填写］','待核对事项','［填写］'],
};
const templateBody=(title:string):JSONContent=>({type:'doc',content:[{type:'heading',attrs:{level:1,textAlign:'center'},content:[{type:'text',text:title}]},...templates[title].map(text=>({type:'paragraph',content:[{type:'text',text}]}))]});

export function EditableProducts({workspaceId,kind,visible,canWrite,onActive,onOpenSource,openId,heading,actions,onLocate}: {
  heading?:ReactNode;actions?:ReactNode;onLocate?:()=>void;
  openId?:string;workspaceId:string;kind:WorkKind;visible:boolean;canWrite:boolean;onActive:(active:boolean)=>void;
  onOpenSource?:(path:string,page?:number)=>void;
}) {
  const discussion=useEditorDiscussion();
  const selection=useRef<EditorSelection|null>(null);
  const [list,setList]=useState<EditableProductSummary[]>([]);const [product,setProduct]=useState<EditableProduct|null>(null);
  const [choosing,setChoosing]=useState(false);const [category,setCategory]=useState<'builtin'|'personal'|'blank'>('builtin');
  const [personal,setPersonal]=useState<{path:string;name:string}[]>([]);const [importPath,setImportPath]=useState('');const [importError,setImportError]=useState('');
  const [error,setError]=useState('');const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);const [title,setTitle]=useState('');
  const locked=busy||!!discussion?.applying;
  const [epoch,setEpoch]=useState(0);const [history,setHistory]=useState<EditableProduct[]|null>(null);
  const [old,setOld]=useState<EditableProduct|null>(null);
  const oldRef=useRef(old);oldRef.current=old;
  const current=useRef<EditableProduct|null>(null);current.current=product;
  const wordRef=useRef<WordEditorHandle>(null);
  const [native,setNative]=useState<{document:WordDocument;session:WordSession;tracking:boolean}|null>(null);
  const [wordReady,setWordReady]=useState(false);
  const wordReadyRef=useRef(wordReady);wordReadyRef.current=wordReady;
  const isNative=!!product?.native_word && kind!=='documents';
  const flushWord=async()=>{
    if(current.current?.native_word && kind!=='documents' && !oldRef.current) {
      if(!wordRef.current || !wordReadyRef.current)throw Error('Word 编辑器尚未准备好，请稍候。');
      await wordRef.current.flush();
      const active=current.current;
      if(active){const saved=await editorClient.read(workspaceId,active.id);if(current.current?.id===active.id){current.current=saved;latest.current=saved.document??blankDocument();cached.current.set(saved.id,{product:saved,document:latest.current});setProduct(saved);}}
    }
  };
  const latest=useRef<JSONContent>(blankDocument());
  const cached=useRef(new Map<string,{product:EditableProduct;document:JSONContent}>());
  const savedKey=`verilex:editor-product:${workspaceId}:${kind}`;
  const install=(next:EditableProduct,activate=true)=>{setProduct(next);current.current=next;setTitle(next.title);setOld(null);setEpoch(v=>v+1);
    latest.current=next.draft_current&&next.draft?next.draft.document:next.document??blankDocument();
    cached.current.set(next.id,{product:next,document:latest.current});
    selection.current=null;
    discussion?.setSelection(null);
    if(next.document&&!next.external_update){const target={path:next.path,id:next.id,title:next.title,kind:next.kind,
      flush:async()=>{if(current.current?.id===next.id)await flushWord();},
      capture:()=>{const active=current.current?.id===next.id;const cache=cached.current.get(next.id);const value=active?current.current:cache?.product;return value?.document&&!value.external_update&&!(active&&oldRef.current)&&(!value.native_word||kind==='documents'||value.projection_complete===true)&&!(active&&wordRef.current?.isDirty())?{product_id:value.id,base_hash:value.file_hash,document:active?latest.current:cache!.document,selection:active?selection.current:null}:null;},
      clearSelection:()=>{if(current.current?.id===next.id)selection.current=null;},
      locate:()=>{onLocate?.();const cache=cached.current.get(next.id);if(cache)install({...cache.product,draft:{base_hash:cache.product.file_hash,document:cache.document,updated_at:''},draft_current:true},false);},
      apply:(value:EditableProduct)=>{cached.current.set(value.id,{product:value,document:value.document??blankDocument()});if(current.current?.id===value.id)install(value,false);}}
    ;discussion?.register(target);if(activate)discussion?.setTarget(target); }
    localStorage.setItem(savedKey,next.id);onActive(true);
  };
  const reload=async()=>{const result=await editorClient.list(workspaceId,kind);setList(result.products);setError(result.errors[0]?.notice??'');return result.products;};
  useEffect(()=>{
    if(!isNative || !product){setNative(null);setWordReady(false);return;}
    let live=true;setWordReady(false);setNative(null);
    const value=product;
    const request=old?editorClient.openWordHistory(workspaceId,value.id,old.revision,old.file_hash):editorClient.openWord(workspaceId,value.id,value.file_hash);
    void request.then(opened=>{
      if(!live)return;
      const next=opened.product;
      const document:WordDocument={id:next.id,filename:next.title+'.docx',content:opened.content,revision:next.revision,updated_at:next.updated_at};
      if(old){
        const session=new WordSession(document,async()=>{throw Error('历史版本只读。');});
        setNative({document,session,tracking:opened.tracking_enabled??false});
        return;
      }
      let hash=next.file_hash;
      const session=retainedWordSession(`legacy:${workspaceId}:${next.id}`,document,async(_id,_revision,bytes)=>{
        const saved=await editorClient.saveWord(workspaceId,next.id,hash,encodeWord(bytes));hash=saved.file_hash;
        return {id:saved.id,revision:saved.revision};
      });
      current.current=next;latest.current=next.document??blankDocument();setProduct(next);
      const unsubscribe=session.subscribe(()=>{
        if(!live||session.dirty||current.current?.id!==next.id||session.document.revision===current.current.revision)return;
        void editorClient.read(workspaceId,next.id).then(saved=>{
          if(!live||current.current?.id!==next.id)return;
          current.current=saved;latest.current=saved.document??blankDocument();cached.current.set(saved.id,{product:saved,document:latest.current});setProduct(saved);
        }).catch(cause=>{if(live)setError(cause instanceof Error?cause.message:'Word 已保存，但状态未能刷新。');});
      });
      setNative({document:session.document,session,tracking:opened.tracking_enabled??false});
      return unsubscribe;
    }).then(unsubscribe=>{if(!live)unsubscribe?.();else dispose=unsubscribe;}).catch(cause=>{if(live)setError(cause instanceof Error?cause.message:'无法打开 Word 文档。');});
    let dispose:(()=>void)|undefined;
    return()=>{live=false;dispose?.();};
  },[workspaceId,product?.id,isNative,epoch,old?.revision]);
  useEffect(()=>{
    if(!visible)return;let live=true;
    void editorClient.list(workspaceId,kind).then(async result=>{
      if(!live)return;setList(result.products);
      if(result.errors.length)setError(result.errors[0].notice);
      const id=localStorage.getItem(savedKey);
      if(!current.current&&id&&result.products.some(p=>p.id===id)){
        const value=await editorClient.read(workspaceId,id);if(live)install(value,false);
      }
    }).catch(e=>{if(live)setError(e instanceof Error?e.message:'文书列表读取失败');});
    return()=>{live=false;};
  },[workspaceId,kind,visible]);
  useEffect(()=>{
    if(!visible)return;
    let live=true;
    const refresh=async()=>{
      const previous=current.current;if(!previous||wordRef.current?.isDirty())return;
      try {const next=await editorClient.read(workspaceId,previous.id);
        if(live&&current.current?.id===previous.id&&current.current.file_hash===previous.file_hash&&next.file_hash!==previous.file_hash)install(next,false);
        else if(live&&current.current?.id===previous.id&&next.external_update&&next.file_hash===current.current.file_hash) {
          setProduct(next);current.current=next;
        }
      }catch(e){if(live)setError(e instanceof Error?e.message:'读取失败');}
    };
    window.addEventListener('focus',refresh);const timer=setInterval(()=>void refresh(),5000);
    return()=>{live=false;clearInterval(timer);window.removeEventListener('focus',refresh);};
  },[workspaceId,visible]);
  useEffect(()=>{if(!openId)return;let live=true;void (async()=>{await flushWord();const next=await editorClient.read(workspaceId,openId);if(live){install(next);void reload();}})().catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[workspaceId,openId]);
  const perform=async(action:()=>Promise<unknown>)=>{setBusy(true);setError('');setNotice('');try{await flushWord();await action();}catch(e){setError(e instanceof Error?e.message:'操作失败，请重试');}finally{setBusy(false);}};
  const create=async(name:string,body:JSONContent)=>{const next=await editorClient.create(workspaceId,name,kind,body);install(next);setChoosing(false);await reload();};
  const save=async(doc:JSONContent)=>{
    const base=current.current;if(!base)throw new Error('请选择文书');
    const next=await editorClient.save(workspaceId,base.id,base.file_hash,doc,title);
    if(current.current?.id===base.id){current.current=next;cached.current.set(next.id,{product:next,document:doc});setProduct(next);await reload();}
  };
  return <section className="litigation-editable-products" aria-label="可编辑成果">
    <div className="litigation-editable-product-header">
      {!product&&heading}
      {list.length>0&&<select aria-label="选择成果文书" disabled={locked} value={product?.id??''} onChange={e=>{
        const id=e.target.value;
        void perform(async()=>{const value=current.current;if(value?.document&&!value.external_update&&!value.native_word)await editorClient.draft(workspaceId,value.id,value.file_hash,latest.current);if(!id){setProduct(null);current.current=null;onActive(false);localStorage.removeItem(savedKey);return;}install(await editorClient.read(workspaceId,id));});
      }}><option value="">已有分析成果</option>{list.map(p=><option key={p.id} value={p.id}>{p.title}</option>)}</select>}
      <button type="button" disabled={!canWrite||locked} onClick={()=>setChoosing(!choosing)}>{kind==='documents'?'新建文书':'新建成果'}</button>
      {product&&<>{discussion?.target?.id!==product.id&&<button type="button" disabled={!product.document||!!old||(isNative&&product.projection_complete!==true)} onClick={()=>discussion?.activate(product.id)}>继续处理此文书</button>}<details className="litigation-product-menu" onClick={e=>{if((e.target as HTMLElement).closest('button'))e.currentTarget.open=false;}} onKeyDown={e=>{if(e.key==='Escape'){e.currentTarget.open=false;e.currentTarget.querySelector('summary')?.focus();}}}><summary>更多</summary><div><button type="button" disabled={!canWrite||locked||!product.document||!!old} onClick={()=>void perform(async()=>{const value=current.current!;const next=await editorClient.copy(workspaceId,value.id,value.file_hash,latest.current,title+'（副本）');install(next);await reload();})}>另存为</button>
        <button type="button" disabled={locked||!product.document||!!old} onClick={()=>void perform(async()=>{if(canWrite&&!isNative)await save(latest.current);onOpenSource?.(current.current!.path);})}>查看 Word</button>
        <button type="button" disabled={!canWrite||locked||!product.document||!!old} title="模板会保留当前文档内容" onClick={()=>void perform(async()=>{const value=current.current!;await editorClient.saveTemplate(workspaceId,value.id,value.file_hash,latest.current,title);setNotice('已保存为模板，模板保留当前文档内容。');})}>保存为我的模板</button>
        <button type="button" onClick={()=>void perform(async()=>setHistory((await editorClient.history(workspaceId,product.id)).versions))}>历史</button></div></details></>}
      {actions}
    </div>
    {notice&&<p role="status">{notice}</p>}
    {error&&<p role="alert">{error}<button type="button" onClick={()=>void perform(reload)}>重试</button></p>}
    {choosing&&<section className="litigation-new-document" aria-label="新建文书">
      <div role="tablist" aria-label="新建方式"><button type="button" role="tab" aria-selected={category==='builtin'} onClick={()=>setCategory('builtin')}>内置模板</button><button type="button" role="tab" aria-selected={category==='personal'} onClick={()=>{setCategory('personal');void perform(async()=>{const [saved,materials]=await Promise.all([editorClient.templates(workspaceId),litigationClient.materials(workspaceId)]);setPersonal([...saved.templates,...materials.materials.filter(m=>/\.docx$/i.test(m.path)&&!saved.templates.some(t=>t.path===m.path)).map(m=>({path:m.path,name:m.name}))]);});}}>我的模板</button><button type="button" role="tab" aria-selected={category==='blank'} onClick={()=>setCategory('blank')}>空白文档</button></div>
      {category==='builtin'?<div className="litigation-template-list">{Object.keys(templates).map(name=><button type="button" disabled={locked} key={name} onClick={()=>void perform(()=>create(name,templateBody(name)))}>{name}</button>)}</div>:category==='personal'?<div><label>本案 Word 模板<select aria-label="选择Word模板" value={importPath} onChange={e=>{setImportPath(e.target.value);setImportError('');}}><option value="">选择文件</option>{personal.map(p=><option value={p.path} key={p.path}>{p.name}</option>)}</select></label><p>从案件文件夹选择 Word，原件保留；模板内容会复制到新文书。</p><button type="button" disabled={!importPath||locked} onClick={()=>void perform(async()=>{try{const next=await editorClient.importCopy(workspaceId,importPath,importPath.split('/').pop()!.replace(/\.docx$/i,''),kind);install(next);setChoosing(false);await reload();}catch(e){setImportError(e instanceof Error?e.message:'读取失败');}})}>使用模板</button>{importError&&<div role="alert">{importError}<p>仅提取正文会丢失原有格式、图片和表格排版。</p><button type="button" disabled={locked} onClick={()=>void perform(async()=>{const next=await editorClient.importCopy(workspaceId,importPath,'正文副本',kind,true);install(next);setChoosing(false);await reload();})}>仅提取正文到新文书</button></div>}</div>:<button type="button" disabled={locked} onClick={()=>void perform(()=>create('未命名文书',blankDocument()))}>创建空白文档</button>}
      <button type="button" onClick={()=>void perform(()=>openCaseDatabase('templates'))}>查看最高人民法院文书模板</button>
      <button type="button" onClick={()=>setChoosing(false)}>取消</button>
    </section>}
    {history&&<div><label>历史版本<select aria-label="编辑历史版本" value={old?.revision??''} onChange={e=>{const revision=Number(e.target.value);const choose=()=>{setOld(history.find(r=>r.revision===revision)??null);setEpoch(v=>v+1);};if(isNative)void perform(async()=>choose());else choose();}}><option value="">当前版本</option>{history.map((r,i)=><option value={r.revision} key={`${r.revision}:${i}`}>第 {r.revision} 版</option>)}</select></label><button type="button" onClick={()=>{setHistory(null);setOld(null);setEpoch(v=>v+1);}}>关闭历史</button></div>}
    {product&&<>
      <div className="litigation-editable-product-header"><input aria-label="文书名称" value={title} maxLength={200} disabled={!canWrite||!!old||locked} onChange={e=>setTitle(e.target.value)}/><small>第 {old?.revision??product.revision} 版{old?' · 只读':''}</small></div>
      {product.external_update&&!old?<div role="status"><p>已读取外部更新的 Word。原文件中的图片、批注等内容保留在预览中。</p><button type="button" onClick={()=>onOpenSource?.(product.path)}>查看最新 Word</button><button type="button" disabled={!canWrite||locked} onClick={()=>void perform(()=>create(title+'（正文副本）',{type:'doc',content:(product.external_content?.paragraphs??[]).map(p=>({type:'paragraph',...(p.text?{content:[{type:'text',text:p.text}]}:{})}))}))}>仅提取正文到新文书</button></div>:
      isNative ? <>{product.projection_complete!==true&&<p role="status">此 Word 包含复杂内容，已在原格式编辑器中打开；自动分析暂不使用可能不完整的正文。</p>}{native?<WordDocumentEditor key={`${product.id}:${epoch}`} ref={wordRef} document={native.document} session={native.session} label="Word 编辑器" tracking={native.tracking} hideHeader editable={canWrite&&!locked&&!old} onReady={setWordReady} onChanged={()=>{selection.current=null;discussion?.setSelection(null);}} onSelection={value=>{const selected=!old&&product.projection_complete===true?wordSelection(latest.current,value):null;selection.current=selected;discussion?.setSelection(selected);}}/>:<p role="status">正在打开 Word 文档…</p>}</> : <LitigationDocumentEditor positionKey={`verilex:editor-position:${workspaceId}:${product.id}`} onUndoAI={product.editor_receipt?.action==='accept'&&discussion?.threadId&&!old?()=>void perform(async()=>{
        const value=current.current;if(!value?.editor_receipt||!discussion.threadId)return;
        discussion.setApplying(true);
        try{await editorClient.draft(workspaceId,value.id,value.file_hash,latest.current);const next=await editorClient.decide(workspaceId,discussion.threadId,value.editor_receipt.proposal_id,'undo',latest.current);install(next,false);}finally{discussion.setApplying(false);}
      }):undefined} key={`${product.id}:${epoch}:${old?.revision??''}`} document={old?.document??latest.current} editable={canWrite&&!old&&!discussion?.applying} proposal={!old&&discussion?.preview?.product_id===product.id?discussion.preview:null}
        onSelection={value=>{selection.current=value;discussion?.setSelection(value);}}
        onChange={doc=>{latest.current=doc;if(current.current)cached.current.set(current.current.id,{product:current.current,document:doc});selection.current=null;discussion?.setSelection(null);}}
        onDraft={doc=>editorClient.draft(workspaceId,product.id,product.file_hash,doc)}
        onSave={save}/>}
      {product.recovery&&<details><summary>外部更新前的恢复草稿</summary><button type="button" disabled={!canWrite||locked} onClick={()=>void perform(()=>create(title+'（恢复草稿）',product.recovery!.document))}>恢复为新文书</button></details>}
      {product.draft&&!product.draft_current&&JSON.stringify(product.draft.document)!==JSON.stringify(product.document)&&<details><summary>保存的恢复草稿</summary><p>恢复为独立副本，保留当前文件。</p><button type="button" disabled={!canWrite||locked} onClick={()=>void perform(()=>create(title+'（恢复草稿）',product.draft!.document))}>恢复为新文书</button></details>}
    </>}
  </section>;
}
