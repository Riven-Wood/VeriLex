import {useEffect, useState} from 'react';
import type {LitigationElements, LitigationRecord} from './LitigationClient';

export function LitigationElementsReview({record, disabled, onConfirm}: {
  record: LitigationRecord<LitigationElements>; disabled: boolean;
  onConfirm: (data: Pick<LitigationElements,'issues'|'summary'>, revision: number) => Promise<LitigationRecord<LitigationElements> | null>;
}) {
  const [draft, setDraft] = useState(record);
  const [dirty, setDirty] = useState(false);
  useEffect(()=>{if(!dirty && record.revision >= draft.revision) setDraft(record);},[record,dirty,draft.revision]);
  if(!draft.data) return null;
  const data = draft.data;
  return <form className="litigation-review" aria-label="核对要件清单" onSubmit={event=>{
    event.preventDefault();void onConfirm({issues:data.issues,summary:'用户核对要件清单'},draft.revision).then(saved=>{if(saved){setDraft(saved);setDirty(false);}});
  }}>
    <h3>构成要件清单 · {draft.data?.confirmed ? '已核对' : '待核对'}</h3>
    {dirty && draft.revision !== record.revision && <p role="status">清单已有更新，你的编辑已保留。<button type="button" disabled={disabled} onClick={()=>{setDraft(record);setDirty(false);}}>重新载入清单</button></p>}
    {data.issues.map((issue,index)=><fieldset key={index} disabled={disabled}><legend>焦点 {index+1}</legend>
      <label>焦点名称<input required maxLength={300} value={issue.title} onChange={event=>{setDirty(true);setDraft({...draft,data:{...data,issues:data.issues.map((item,i)=>i===index ? {...item,title:event.target.value}:item)}});}} /></label>
      <label>构成要件（每行一项）<textarea required value={issue.elements.join('\n')} onChange={event=>{setDirty(true);setDraft({...draft,data:{...data,issues:data.issues.map((item,i)=>i===index ? {...item,elements:event.target.value.split('\n')}:item)}});}} /></label>
    </fieldset>)}
    <button type="submit" disabled={disabled || data.issues.some(i=>!i.title.trim() || i.elements.some(e=>!e.trim()))}>确认清单并继续分析</button>
  </form>;
}
