import {useEffect,useState} from 'react';
import {runtimeRequest} from '../../runtime/api';
import {publishCoverage} from './materialCoverageState';
export function MaterialCoverage({workspaceId,path}:{workspaceId:string;path:string}){
 const [value,setValue]=useState<{status:string;gaps:string[];notice:string}|null>(null),[closed,setClosed]=useState(false);
 useEffect(()=>{setValue(null);setClosed(false);if(path.startsWith('文书起草/')||path.startsWith('工作成果/'))return;let live=true;void runtimeRequest<{status:string;gaps:string[];notice:string}>(`/v1/litigation/workspaces/${encodeURIComponent(workspaceId)}/material-inspection?path=${encodeURIComponent(path)}`).then(v=>{if(live){setValue(v);publishCoverage(workspaceId,path,v);}}).catch(()=>{if(live){const failed={status:'failed',gaps:['无法检查此材料的读取范围。'],notice:''};setValue(failed);publishCoverage(workspaceId,path,failed);}});return()=>{live=false;};},[workspaceId,path]);
 if(!value||closed||value.status==='text_available')return null;
 return <div className="preview-file-meta"><details><summary>ⓘ 材料读取范围有待核对</summary>{value.gaps.map((gap,i)=><p key={i}>{gap}</p>)}<small>{value.notice}</small></details><button aria-label="关闭材料读取提示" onClick={()=>setClosed(true)}>×</button></div>;
}
