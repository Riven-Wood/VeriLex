import { type ReactNode, forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { encodeWord, wordDocumentApi, wordSession, type WordAiEdit, type WordDocument, type WordMarkupPlan, type WordSession } from '../../runtime/wordDocumentClient';
import { saveContractReview, wordCopyFilename } from '../../platform/contractReviewExport';
import { createWordContextMenu } from '../../platform/wordContextMenu';
import { installWordHistoryMenu } from '../../platform/wordHistoryMenu';
import './WordDocumentEditor.css';
import { Save } from 'lucide-react';
export interface WordEditorHandle { exportCopy:()=>Promise<void>; isDirty:()=>boolean; locate:(range:unknown)=>void; history: (command:'undo'|'redo')=>void; flush: () => Promise<{document:WordDocument;bytes:Uint8Array}>; applyAiEdit:(edit:WordAiEdit)=>Promise<void> }
interface Props { quiet?:boolean; historyToolbar?:boolean; headerActions?:ReactNode; document: WordDocument; author?:string; review?:{id:string;status:string;opinion_count:number}; onReady?: (ready:boolean)=>void; onChanged?: ()=>void; session?:WordSession; label?:string; tracking?:boolean; editable?:boolean; onSelection?:(value:unknown)=>void; selectionActionLabel?:string; hideHeader?:boolean; onStatusChange?:(status:string)=>void; highlight?:unknown }
export const WordDocumentEditor=forwardRef<WordEditorHandle,Props>(function WordDocumentEditor({quiet=false,historyToolbar=true,headerActions,document: source,author='VeriLex',review,onReady,onChanged,session:providedSession,label='合同 Word 编辑器',tracking=true,editable=true,onSelection,selectionActionLabel,hideHeader=false,onStatusChange,highlight},ref){
  const session=useMemo(()=>providedSession??wordSession(source),[source.id,providedSession]);
  const frame=useRef<HTMLIFrameElement>(null);
  const channel=useMemo(()=>crypto.randomUUID(),[source.id]);
  const [ready,setReady]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [applying,setApplying]=useState(false),[markupError,setMarkupError]=useState('');
  const [exporting,setExporting]=useState(false);
  const exportLock=useRef(false);
  const [restartVersion,setRestartVersion]=useState(0),[reopening,setReopening]=useState(false);
  const failure=useRef('');
  const attempted=useRef(new Set<string>()),applyingRef=useRef(false);
  const [,render]=useState(0);
  const requests=useRef(new Map<string,{resolve:()=>void;reject:(error:Error)=>void;timer:number}>());
  const callbacks=useRef({onReady,onChanged,onSelection});callbacks.current={onReady,onChanged,onSelection};
  const pendingInput=useRef(false);
  const flushFlight=useRef<ReturnType<WordEditorHandle['flush']>|null>(null);
  useEffect(()=>session.subscribe(()=>render(n=>n+1)),[session]);
  useEffect(()=>{
    const closeControls=(event:PointerEvent)=>{
      if(event.target!==frame.current)frame.current?.contentWindow?.postMessage({channel,type:'close-controls'},location.origin);
    };
    document.addEventListener('pointerdown',closeControls,true);
    return ()=>document.removeEventListener('pointerdown',closeControls,true);
  },[channel]);
  const request=(type='snapshot',plan?:WordMarkupPlan|WordAiEdit)=>new Promise<void>((resolve,reject)=>{
    if(!ready||failure.current){reject(new Error(failure.current||'请等待 Word 编辑器打开。'));return;}
    const requestId=crypto.randomUUID();
    const timer=window.setTimeout(()=>{requests.current.delete(requestId);reject(new Error('编辑器没有响应，请重试。'));},20000);
    requests.current.set(requestId,{resolve,reject,timer});
    frame.current?.contentWindow?.postMessage({channel,type,requestId,plan},location.origin);
  });
  const snapshot=()=>request();
  useImperativeHandle(ref,()=>({exportCopy:()=>exportCopy(),isDirty:()=>pendingInput.current||session.dirty,applyAiEdit:async edit=>{
    if(applyingRef.current||pendingInput.current||session.dirty||session.document.id!==edit.document_id||session.document.revision!==edit.revision)throw new Error('文档已改变，AI 修改未写入，请重新选择后再试。');
    applyingRef.current=true;if(frame.current)frame.current.inert=true;
    try{await request('apply-ai-edit',edit);await session.flush();}
    finally{applyingRef.current=false;if(frame.current)frame.current.inert=!editable;}
  },locate:range=>{frame.current?.contentWindow?.postMessage({channel,type:'draft-highlight',range,locate:true},location.origin);},history:command=>{
    if(!ready||failure.current||frame.current?.inert)return;
    frame.current?.focus();frame.current?.contentWindow?.postMessage({channel,type:'history-action',command},location.origin);
  },flush:()=>{
    if(applyingRef.current)return Promise.reject(new Error('正在写入修订与批注，请稍候。'));
    if(flushFlight.current)return flushFlight.current;
    const iframe=frame.current,wasInert=Boolean(iframe?.inert);
    if(iframe)iframe.inert=true;
    // Keep the document still from snapshot through disk persistence. Repeated
    // navigation requests share the flight and cannot unlock it prematurely.
    const flight=(async()=>{await snapshot();await session.flush();return {document:session.document,bytes:session.bytes.slice()};})()
      .finally(()=>{if(iframe&&!applyingRef.current)iframe.inert=wasInert;flushFlight.current=null;});
    flushFlight.current=flight;return flight;
  }}));
  useEffect(()=>{
    let menu:ReturnType<typeof createWordContextMenu>|undefined,disposed=false;
    const nativeContextMenu=Reflect.has(globalThis,'__TAURI_INTERNALS__');
    const historyMenu=nativeContextMenu&&/Mac/.test(navigator.platform)?installWordHistoryMenu(command=>{
      if(disposed)return;
      if(document.activeElement===frame.current){if(!failure.current&&!frame.current?.inert)frame.current?.contentWindow?.postMessage({channel,type:'history-action',command},location.origin);}
      else document.execCommand(command);
    }):undefined;
    void historyMenu?.catch(()=>{if(!disposed)setNotice('系统撤销菜单暂不可用，请使用编辑区的撤销、重做或键盘快捷键。');});
    const sendAction=(command:string)=>{if(!disposed&&!failure.current&&!frame.current?.inert)frame.current?.contentWindow?.postMessage({channel,type:'context-action',command},location.origin);};
    const receive=(event:MessageEvent)=>{
      if(event.source!==frame.current?.contentWindow||event.origin!==location.origin)return;
      if(event.data?.type==='word-editor-loaded'){
        frame.current.contentWindow?.postMessage({type:'open',channel,bytes:session.bytes,author,nativeContextMenu,tracking,label,selectionEvents:Boolean(callbacks.current.onSelection),selectionActionLabel,historyToolbar},location.origin);return;
      }
      if(event.data?.channel!==channel)return;
      const data=event.data;
      if(data.type==='context-menu'&&nativeContextMenu&&!failure.current&&!frame.current?.inert){
        if(!Number.isFinite(data.x)||!Number.isFinite(data.y))return;
        const rect=frame.current.getBoundingClientRect(),x=rect.left+Math.max(0,Math.min(data.x,rect.width)),y=rect.top+Math.max(0,Math.min(data.y,rect.height));
        menu??=createWordContextMenu(sendAction);
        void menu.then(value=>{if(!disposed)return value.popup(x,y,{table:data.table===true});}).catch(()=>{menu=undefined;if(!disposed)frame.current?.contentWindow?.postMessage({channel,type:'context-fallback'},location.origin);});
      }
      else if(data.type==='ready'&&!failure.current){setReady(true);callbacks.current.onReady?.(true);}
      else if(data.type==='draft-selection'){callbacks.current.onSelection?.(data.selection);}
      else if(data.type==='dirty'){if(!pendingInput.current){pendingInput.current=true;render(n=>n+1);}callbacks.current.onChanged?.();}
      else if(data.type==='error'){
        failure.current=data.message||'编辑器暂时无法继续编辑。';setError(failure.current);setNotice('');callbacks.current.onReady?.(false);
        requests.current.forEach(req=>{clearTimeout(req.timer);req.reject(new Error(failure.current));});requests.current.clear();
      }
      else if(data.type==='snapshot'&&!failure.current){
        pendingInput.current=false;render(n=>n+1);session.update(new Uint8Array(data.bytes),data.markup,data.aiEditId);
        if(data.requestId){const req=requests.current.get(data.requestId);if(req){clearTimeout(req.timer);req.resolve();requests.current.delete(data.requestId);}}
      }else if(data.type==='snapshot-error'||data.type==='request-error'){
        const req=requests.current.get(data.requestId);if(req){clearTimeout(req.timer);req.reject(new Error(data.message||'请完成当前输入后再保存。'));requests.current.delete(data.requestId);}
      }else if(data.type==='save'&&!failure.current&&!pendingInput.current)void session.flush().then(()=>{if(!quiet&&!failure.current&&!pendingInput.current)setNotice('已保存到本机。');}).catch(()=>{});
    };
    const leave=(event:BeforeUnloadEvent)=>{if(pendingInput.current||session.dirty){event.preventDefault();event.returnValue='';}};
    window.addEventListener('message',receive);window.addEventListener('beforeunload',leave);
    return ()=>{disposed=true;void historyMenu?.then(restore=>restore()).catch(()=>{});void menu?.then(value=>value.close()).catch(()=>{});window.removeEventListener('message',receive);window.removeEventListener('beforeunload',leave);callbacks.current.onReady?.(false);requests.current.forEach(req=>{clearTimeout(req.timer);req.reject(new Error('文档已关闭。'));});requests.current.clear();void session.flush().catch(()=>{});};
  },[channel,session]);
  useEffect(()=>{if(frame.current)frame.current.inert=!editable||applyingRef.current;},[editable]);
  useEffect(()=>{if(ready)frame.current?.contentWindow?.postMessage({channel,type:'draft-highlight',range:highlight},location.origin);},[ready,highlight,channel]);
  const applyReview=async()=>{
    if(!review||applyingRef.current)return;
    applyingRef.current=true;setApplying(true);setMarkupError('');callbacks.current.onReady?.(false);
    if(frame.current)frame.current.inert=true;
    try{
      await snapshot();await session.flush();
      const plan=await wordDocumentApi.markupPlan(review.id);
      if('already_applied' in plan){session.document.review_markups={...session.document.review_markups,[review.id]:{applied:plan.applied,skipped:plan.skipped}};render(n=>n+1);return;}
      if(plan.document_id!==source.id||plan.revision!==session.document.revision)throw new Error('当前文档已更新，请重新审查后再写入。');
      await request('apply-review',plan);await session.flush();
    }catch(cause){setMarkupError(cause instanceof Error?cause.message:'修订与批注未能写入，请重试。');}
    finally{applyingRef.current=false;setApplying(false);if(frame.current)frame.current.inert=false;callbacks.current.onReady?.(!failure.current);}
  };
  useEffect(()=>{
    if(!ready||error||!review||review.status!=='completed'||!review.opinion_count||session.document.review_markups?.[review.id]||attempted.current.has(review.id))return;
    attempted.current.add(review.id);void applyReview();
  },[ready,review?.id,review?.status,review?.opinion_count]);
  const markup=review?session.document.review_markups?.[review.id]:undefined;
  const exportCopy=async()=>{
    if(exportLock.current||applyingRef.current)return;
    exportLock.current=true;
    const recovering=Boolean(failure.current);setExporting(true);setNotice('');
    try{
      // A paused editor cannot produce a new snapshot. Export only the last
      // successful bytes, without treating the failed input as saved.
      if(!recovering)await snapshot();
      const path=await saveContractReview({filename:wordCopyFilename(source.filename),content:encodeWord(session.bytes)});
      setNotice(quiet&&!recovering?'':path===null?'已取消另存。':recovering?'可用副本已另存，不包含本次失败的输入。':'Word 副本已保存。');
    }catch(cause){setNotice(cause instanceof Error?cause.message:typeof cause==='string'?cause:'另存失败。');}
    finally{exportLock.current=false;setExporting(false);}
  };
  const reopenCopy=async()=>{
    if(!window.confirm('将丢弃本次未能保存的输入，重新打开最近成功生成的内容。建议先点击“另存可用副本”保留副本。确定重新打开吗？'))return;
    setReopening(true);
    // Let an earlier disk write settle before mounting another editor on the
    // retained bytes. Only this explicit confirmation discards failed input.
    try { if(flushFlight.current)await flushFlight.current.catch(()=>{}); }
    finally {
      failure.current='';pendingInput.current=false;setError('');setNotice('');setMarkupError('');setReady(false);
      callbacks.current.onReady?.(false);setRestartVersion(value=>value+1);setReopening(false);
    }
  };
  const saveStatus=error?'编辑已暂停':!ready?'正在打开…':session.error?'保存未成功':session.dirty||pendingInput.current?'正在保存…':'已自动保存';
  useEffect(()=>{onStatusChange?.(saveStatus);},[saveStatus,onStatusChange]);
  return <div className="word-document-editor">
    <div className="word-document-header" hidden={hideHeader&&!error&&!session.error}>{headerActions}<strong title={source.filename}>{source.filename}</strong><span role="status">{error?'编辑已暂停':!ready?'正在打开…':session.error?'保存未成功':session.dirty||pendingInput.current?'正在保存…':'已保存到本机'}</span>
      {session.error&&<button onClick={()=>void session.flush().catch(()=>{})}>重试保存</button>}
      <button className={error?undefined:"word-export-icon"} aria-label={error?"另存可用副本":"另存 Word"} title={error?"另存可用副本":"另存 Word"} disabled={(!ready&&!error)||applying||exporting||reopening} onClick={()=>void exportCopy()}>{error?'另存可用副本':<Save size={17}/>}</button>
      {error&&<button disabled={applying||exporting||reopening} onClick={()=>void reopenCopy()}>重新打开可用副本</button>}</div>
    {applying&&!quiet&&<p role="status" className="word-document-notice">正在写入 AI 修订与批注…</p>}
    {markup&&!quiet&&<p role="status" className="word-document-notice">已写入 {markup.applied} 条审查意见。可在正文右键处理新增修订，在“修订与批注”中处理其余修改。{markup.skipped>0&&`另有 ${markup.skipped} 条因定位不唯一、重叠或内容复杂未写入，请在意见栏核对。`}</p>}
    {quiet&&markup&&markup.skipped>0&&<p role="alert" className="word-document-error">有 {markup.skipped} 条意见未能写入文档，请核对审查成果。</p>}
    {markupError&&!error&&<p role="alert" className="word-document-error">{markupError}<button disabled={applying} onClick={()=>void applyReview()}>重试写入</button></p>}
    {(error||session.error)&&<p className="word-document-error" role="alert">{error||session.error}{error&&' 另存可用副本仅保留最近成功生成的文档，不包含本次失败的输入。'}</p>}
    {notice&&notice!==error&&notice!==session.error&&<p role="status" className="word-document-notice">{notice}<button onClick={()=>setNotice('')}>关闭</button></p>}
    <iframe key={restartVersion} ref={frame} title={label} src="/documents-renderer/word-editor.html" sandbox="allow-scripts allow-same-origin" />
  </div>;
});
