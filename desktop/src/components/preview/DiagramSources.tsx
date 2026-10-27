import {useEffect, useState} from 'react';
import {runtimeRequest} from '../../runtime/api';
import type {LitigationSource} from '../../views/LitigationClient';

export function DiagramSources({workspaceId, path, onOpen, onPage}: {
  workspaceId: string; path: string; onOpen: (source: LitigationSource) => void; onPage: (path:string) => void;
}) {
  const [value, setValue] = useState<{sources: LitigationSource[]; notice?: string; pages?: string[]} | null>(null);
  useEffect(() => {
    let live = true;
    setValue(null);
    void runtimeRequest<NonNullable<typeof value>>(`/v1/litigation/workspaces/${encodeURIComponent(workspaceId)}/diagram-sources?path=${encodeURIComponent(path)}`)
      .then(next => {if (live) setValue(next);})
      .catch(() => {if (live) setValue({sources: [], notice: '图表出处暂时无法读取。'});});
    return () => {live = false;};
  }, [workspaceId, path]);
  if (!value) return null;
  return <div className="preview-diagram-sources">
    {(value.pages?.length??0)>1&&<nav aria-label="图表分页">{value.pages!.map((page,index)=><button key={page} aria-current={page===path?'page':undefined} disabled={page===path} onClick={()=>onPage(page)}>第 {index+1} 页</button>)}</nav>}
    <details><summary>图表出处（{value.sources.length}）</summary>
    {value.notice && <p>{value.notice}</p>}
    {value.sources.map((source, index) => <p key={index}><button onClick={() => onOpen(source)}>{source.path}{source.page ? ` · 第 ${source.page} 页` : ''}</button><br/>{source.quote}</p>)}
  </details></div>;
}
