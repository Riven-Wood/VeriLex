import {useEffect,useState} from 'react';
import {useEditorDiscussion} from './DiscussionContext';
import {editorClient,type EditorProposal} from './client';
export function EditorProposals({workspaceId,comments=false}:{workspaceId:string;comments?:boolean}) {
 const context=useEditorDiscussion(),threadId=context?.threadId;
 const [rows,setRows]=useState<EditorProposal[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{setRows([]);setError('');if(!threadId)return;let live=true;
  const read=async()=>{try{const result=await editorClient.proposals(workspaceId,threadId);if(live)setRows(result.proposals);}catch(e){if(live)setError(e instanceof Error?e.message:'建议读取失败');}};
  void read();const timer=setInterval(()=>void read(),2500);return()=>{live=false;clearInterval(timer);};
 },[workspaceId,threadId]);
 useEffect(()=>{context?.setPreview(rows.find(r=>r.product_id===context.target?.id&&(r.status==='pending'||r.status==='partial'))??null);},[rows,context?.target?.id,context?.setPreview]);
 const decide=async(row:EditorProposal,action:'accept'|'discard'|'undo',changeIds?:number[])=>{
  const target=context?.target;
  if(!threadId||!target||target.id!==row.product_id)return;
  setBusy(true);context?.setApplying(true);setError('');
  try{await target.flush?.();const snapshot=target.capture(),document=snapshot?.document;if(!document)throw Error('请等待文书保存完成后再处理建议。');if(action!=='discard'&&snapshot)await editorClient.draft(workspaceId,target.id,snapshot.base_hash,document);
   const product=await editorClient.decide(workspaceId,threadId,row.id,action,document,changeIds);if(action!=='discard')await target.apply(product);
   setRows((await editorClient.proposals(workspaceId,threadId)).proposals);
  }catch(e){setError(e instanceof Error?e.message:'修改未保存，请重试');}finally{setBusy(false);context?.setApplying(false);}
 };
 const visible=rows.filter(r=>r.product_id===context?.target?.id&&((r.status==='pending'||r.status==='partial')||r.status==='accepted'));
 return <>{error&&<p role="alert">{error}</p>}{visible.map(row=><section aria-label={comments?"AI 修改批注":"文书修改建议"} key={row.id} className="litigation-editor-proposal">
  {row.status==='pending'||row.status==='partial'?<><strong>{comments?'AI 批注 · 修改建议':'文书修改建议'}</strong>
   {(row.changes??[{selection:row.selection,replacement:row.replacement}]).map((change,index)=><div key={index} id={`proposal-${row.id}-${index}`}>
    {comments&&change.selection&&<button onClick={()=>context?.target?.locate?.(change.selection)}>定位原文</button>}
    {change.selection&&<p className={comments?'drafting-comment-original':undefined}>{comments?change.selection.text:<del>{change.selection.text}</del>}</p>}<p style={{whiteSpace:'pre-wrap'}}>{comments?change.replacement:<ins>{change.replacement}</ins>}</p>
    {row.applied?.includes(index)?<span>已接受</span>:<button disabled={busy} onClick={()=>void decide(row,'accept',[index])}>接受修改{(row.changes?.length??1)>1?` ${index+1}`:''}</button>}
   </div>)}
   {(row.changes?.length??1)>1&&<button disabled={busy} onClick={()=>void decide(row,'accept')}>全部接受</button>}
   <button disabled={busy} onClick={()=>void decide(row,'discard')}>放弃修改</button>
   {row.status==='partial'&&<button disabled={busy} onClick={()=>void decide(row,'undo')}>撤销此次修改</button>}
   </>:<><span>已接受修改</span><button disabled={busy} onClick={()=>void decide(row,'undo')}>撤销此次修改</button></>}
 </section>)}</>;
}
