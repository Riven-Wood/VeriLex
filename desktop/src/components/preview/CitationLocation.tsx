import {useEffect,useState} from 'react';
import {runtimeRequest} from '../../runtime/api';
import type {LitigationSource} from '../../views/LitigationClient';
export function useCitationLocation(workspaceId:string|undefined,source:LitigationSource|undefined){
 const [value,setValue]=useState<{status:string;notice:string;quote:string;text?:string;page?:number;locator?:{file_hash:string;index:number}}|null>(null);
 useEffect(()=>{setValue(null);if(!workspaceId||!source)return;let live=true;void runtimeRequest<NonNullable<typeof value>>(`/v1/litigation/workspaces/${encodeURIComponent(workspaceId)}/source-location`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(source)}).then(next=>{if(live)setValue(next);}).catch(()=>{if(live)setValue({status:'failed',notice:'来源位置暂时无法确认，请核对原摘录。',quote:source.quote});});return()=>{live=false;};},[workspaceId,source]);
 return value;
}
