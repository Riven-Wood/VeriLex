import {useEffect, useRef, useState} from 'react';
import {LitigationGuide} from './LitigationGuide';
import {openCaseDatabase} from '../platform/caseDatabase';
import {litigationClient, type LitigationDocument, type LitigationDocumentProposal, type LitigationRecord, type LitigationTemplate} from './LitigationClient';

type Props = {
  workspaceId: string; visible: boolean; record: LitigationRecord<LitigationDocument>; disabled: boolean; legacyDisabled?: boolean; stale?: boolean;
  selectedMaterials: string[]; onStart: (message: string) => void; onOpenSource?: (path: string, page?: number, citation?: import("./LitigationClient").LitigationSource) => void;
  onSaved: (record: LitigationRecord<LitigationDocument>) => void;
};
function reviewDraft(record: LitigationRecord<LitigationDocument>): LitigationRecord<LitigationDocument> {
  if (!record.data || record.data.confirmed) return record;
  return {...record,data:{...record.data,fields:record.data.fields.map(f=>({...f,pending:f.pending.length ? f.pending : ['请逐项核对']}))}};
}
export function LitigationDocuments({workspaceId, visible, record, disabled, legacyDisabled = disabled, stale = false, selectedMaterials, onStart, onOpenSource, onSaved}: Props) {
  const [templates,setTemplates]=useState<LitigationTemplate[]>([]);
  const [selected,setSelected]=useState(record.data?.template_id ?? '');
  const [draft,setDraft]=useState(()=>reviewDraft(record));
  const [dirty,setDirty]=useState(false);
  const [error,setError]=useState('');
  const [templateLinkError,setTemplateLinkError]=useState('');
  const [saving,setSaving]=useState(false);
  const [reload,setReload]=useState(0);
  const live=useRef(true);
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
  useEffect(()=>{
    if (!visible || templates.length) return;
    let active=true;
    void litigationClient.documentTemplates(workspaceId).then(result=>{if(active){setTemplates(result.templates);setError('');}}).catch(reason=>{if(active)setError(reason instanceof Error ? reason.message : '样式读取失败');});
    return()=>{active=false;};
  },[workspaceId,visible,templates.length,reload]);
  useEffect(()=>{
    if(!dirty && record.revision>=draft.revision){setDraft(reviewDraft(record));if(record.data)setSelected(record.data.template_id);}
  },[record,dirty,draft.revision]);
  const data=draft.data;
  const definition=templates.find(t=>t.id===(data?.template_id ?? selected));
  const locked=legacyDisabled || saving;
  const changed=dirty && record.revision!==draft.revision;
  const incomplete=data?.fields.some(f=>!f.value.trim() || f.pending.length>0) ?? true;
  const update=(index:number,change:Partial<LitigationDocument['fields'][number]>)=>{
    if(!data)return;
    setDirty(true);setDraft({...draft,data:{...data,confirmed:false,fields:data.fields.map((f,i)=>i===index ? {...f,...change}:f)}});
  };
  const exportWord=()=>onStart('请只调用litigation_export，kind=documents，导出我刚在文书页核对确认的文书。先读取documents确认版本，不重新抽取、不改写已核对内容；检查失败时说明需修改的项目。');
  return <section className="litigation-documents" aria-label="案件文书">
    <LitigationGuide kind="documents" materials={selectedMaterials} disabled={disabled || saving} onStart={onStart}/>
    <div className="litigation-template-reference">
    <button type="button" onClick={()=>{
      setTemplateLinkError('');
      void openCaseDatabase('templates').catch(()=>{
        if(live.current)setTemplateLinkError('系统浏览器没有打开，请再次点击“最高法文书模板”重试。');
      });
    }}>最高法文书模板</button>
    <span>需要参考格式？在浏览器中查看官方样式。</span></div>
    {templateLinkError && <p role="alert">{templateLinkError}</p>}
    <details className="litigation-legacy-documents"><summary>固定样式与已有文书记录</summary>
    {error && <p role="alert">{error} {!templates.length && <button type="button" onClick={()=>setReload(n=>n+1)}>重试读取样式</button>}</p>}
    <label>文书样式<select value={selected} disabled={locked || !templates.length} onChange={event=>setSelected(event.target.value)}><option value="">根据案件匹配样式</option>{templates.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
    <button type="button" disabled={locked || !templates.length || dirty} onClick={()=>onStart(`请准备本案文书要素。${selected ? `用户已选择template_id=${selected}，先用litigation_read kind=documents、template_id=${selected}读取该样式字段规则。` : '先读取案件信息和documents中的可用样式目录，结合案由和当事人类型匹配；只有唯一适用样式时才准备要素，存在歧义时列出2至3个可用候选和适用区别，请我选择后再准备，不猜测当事人类型。确定后传template_id读取完整字段规则。'}仅使用这些选中材料：${JSON.stringify(selectedMaterials)}。先litigation_read读取case、facts、evidence、issues、strategy和documents，按documents返回的template字段及schema（含当事人类型和第三人人数限制），以litigation_save kind=documents保存。缺失留空并标待确认；调解知悉情况及意愿留空，由当事人亲自选择；不得自动确认或导出，保存后等我在文书页逐项核对。`)}>{data ? '重新准备要素' : '准备文书要素'}</button>
    {dirty && !changed && <button type="button" disabled={locked} onClick={()=>{setDraft(reviewDraft(record));setDirty(false);setError('');}}>放弃未保存修改</button>}
    <p>当前支持单原告、单被告。民间借贷要素式支持个人和组织，可选择无第三人或一名第三人的样式；其他情形请使用上方文书起草，不再扩充固定样式。</p>
    {data && definition && <form className="litigation-review" aria-label="文书要素核对" onSubmit={event=>{
      event.preventDefault();if(locked || incomplete || changed || stale)return;
      if(data.confirmed && !dirty){exportWord();return;}
      setSaving(true);setError('');
      const payload:LitigationDocumentProposal={template_id:data.template_id,plaintiff_count:data.plaintiff_count,defendant_count:data.defendant_count,plaintiff_type:data.plaintiff_type,defendant_type:data.defendant_type,third_party_type:data.third_party_type,third_party_count:data.third_party_count,fields:data.fields,laws:data.laws,summary:'用户逐项核对文书要素'};
      void litigationClient.confirmDocument(workspaceId,payload,draft.revision).then(saved=>{
        if(!live.current)return;setDraft(saved);setDirty(false);onSaved(saved);exportWord();
      }).catch(reason=>{if(live.current)setError(reason instanceof Error ? reason.message : '保存失败，请重试');}).finally(()=>{if(live.current)setSaving(false);});
    }}>
      <h3>{definition.name} · {stale ? '需更新' : data.confirmed && !dirty ? '已核对' : '待核对'}</h3>
      {stale && <p role="status">案件资料已有更新，请点“重新准备要素”，再核对并出稿。</p>}
      <p>AI 草稿，须律师审查。核对内容后生成 Word，可用本机 Word 或 WPS 打开。</p>
      {changed && <p role="status">文书已有更新，你的编辑已保留。<button type="button" disabled={locked} onClick={()=>{setDraft(reviewDraft(record));setDirty(false);setError('');}}>重新载入要素</button></p>}
      {data.fields.map((field,index)=>{
        const rule=definition.fields.find(f=>f.key===field.key);if(!rule)return null;
        return <fieldset key={field.key} disabled={locked}>
          <label>{rule.label}{(rule.choices || ['sex','measure'].includes(rule.kind)) ? <select aria-label={rule.label} value={field.value} onChange={event=>update(index,{value:event.target.value,pending:['请核对修改']})}><option value="">待补</option>{(rule.choices ?? (rule.kind==='sex' ? ['男','女']:['查封','扣押','冻结'])).map(v=><option key={v}>{v}</option>)}</select> : <textarea rows={['claims','reasons','evidence','defendant','applicant','property','guarantee'].includes(field.key) ? 3 : 1} required={rule.required} aria-label={rule.label} maxLength={16000} value={field.value} placeholder={rule.kind==='date' ? 'YYYY-MM-DD' : rule.hint} onChange={event=>update(index,{value:event.target.value,pending:['请核对修改']})}/>}</label>
          {rule.hint && <small>{rule.hint}</small>}
          {field.sources.length>0 && <details><summary>材料出处 · {field.sources.length} 处</summary><ul>{field.sources.map((s,i)=><li key={i}>{onOpenSource ? <button type="button" onClick={()=>onOpenSource(s.path,s.page??undefined,s)}>{s.path}{s.page ? ` · 第${s.page}页` : ''}</button> : s.path}<blockquote>{s.quote}</blockquote></li>)}</ul></details>}
          {!field.sources.length && <small>无材料摘录，请核对或补充。</small>}
          {field.pending.length>0 && <p>{field.pending.join('；')}</p>}
          <label><input type="checkbox" checked={field.pending.length===0 && !!field.value.trim()} disabled={!field.value.trim()} onChange={event=>update(index,{pending:event.target.checked ? []:['请核对']})}/>已核对{rule.label}</label>
        </fieldset>;
      })}
      <p>出稿前检查：漏填与待确认项、旧内容残留、样式格式。全部通过后交付 Word 和核验说明。</p>
      <button type="submit" disabled={locked || incomplete || changed || stale}>{data.confirmed && !dirty ? '导出 Word' : '确认要素并生成 Word'}</button>
    </form>}
    </details>
  </section>;
}
