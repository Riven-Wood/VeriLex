import {useEffect, useRef, useState} from 'react';
import {litigationClient, type LitigationSnapshot, type LitigationVersion} from './LitigationClient';

export function LitigationFactHistory({workspaceId, revision, disabled, onRestore}: {
  workspaceId: string; revision: number; disabled: boolean; onRestore: (revision: number, expectedRevision: number) => Promise<boolean>;
}) {
  const [versions, setVersions] = useState<LitigationVersion[] | null>(null);
  const [preview, setPreview] = useState<LitigationSnapshot['facts'] | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const epoch = useRef(0);
  const baseRevision = useRef(revision);
  useEffect(() => () => {epoch.current++;}, []);
  const request = async (work: () => Promise<void>) => {
    setLoading(true); setError('');
    try {await work();} catch (reason) {setError(reason instanceof Error ? reason.message : '版本读取失败，请重试。');}
    finally {setLoading(false);}
  };
  const load = () => {
    const current = ++epoch.current;
    baseRevision.current = revision;
    setPreview(null);
    void request(async () => {
      const result = await litigationClient.factVersions(workspaceId);
      if(current === epoch.current) setVersions(result.versions);
    });
  };
  return <div className="litigation-history">
    <button type="button" disabled={loading} onClick={load}>查看历史版本</button>
    {error && <p role="alert">{error}</p>}
    {versions && <div className="litigation-review" aria-label="事实历史版本">
      <label>选择版本<select aria-label="事实版本" value={preview?.revision ?? ''} disabled={loading} onChange={event => {
        const selected = Number(event.target.value); const current = ++epoch.current;
        void request(async () => {const result = await litigationClient.factVersion(workspaceId, selected); if(current === epoch.current) setPreview(result);});
      }}><option value="" disabled>选择要查看的版本</option>{versions.map(v=><option key={v.revision} value={v.revision}>第 {v.revision} 版 · {v.summary}</option>)}</select></label>
      {preview?.data && <div aria-label="历史事实内容"><p>第 {preview.revision} 版 · AI 草稿，须律师审查</p>
        {preview.data.facts.map(f=><article key={f.id}><strong>{f.date || '日期待确认'}</strong><p>{f.text}</p><p>{f.pending.join('；')}</p><p>{f.user_note}</p>{f.sources.map((s,i)=><blockquote key={i}>{s.path}{s.page ? ` · 第${s.page}页` : ' · 文件'}：{s.quote}</blockquote>)}</article>)}
        <p>缺口：{preview.data.gaps.join('；')}</p><p>法律关系：{preview.data.relationships}</p>
        {preview.data.conflicts.map((c,i)=><p key={i}>冲突：{c.previous.text} / {c.proposed.text}</p>)}
        <button type="button" disabled={disabled || loading || preview.revision === revision} onClick={() => {
          const current = ++epoch.current;
          void request(async () => {const saved = await onRestore(preview.revision,baseRevision.current); if(saved && current===epoch.current){setVersions(null);setPreview(null);}});
        }}>恢复此版并保留现版</button>
      </div>}
      <button type="button" onClick={()=>{epoch.current++;setVersions(null);setPreview(null);}}>收起历史版本</button>
    </div>}
  </div>;
}
