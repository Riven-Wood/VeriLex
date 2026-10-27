import { runtimeRequest } from './api';
export const REDACTION_TYPES: Record<string,string> = {person:'姓名',address:'住址',phone:'电话',email:'邮箱',wechat:'微信号',identity:'身份证',bank:'银行/支付账号',plate:'车牌',health:'健康状况',certificate:'权属证书编号',credit:'统一社会信用代码',case:'案号',money:'金额',date:'日期',organization:'公司/机构'};
export const DEFAULT_REDACTION_TYPES = Object.keys(REDACTION_TYPES).filter(k=>!['case','money','date','organization'].includes(k));
export interface RedactionItem {id:string;key:string;source_start:number;source_end:number;start:number;end:number;old:string;kind:string;placeholder:string;keep:boolean;context_before?:string;context_after?:string}
export interface RedactionSummary {id:string;filename:string;created_at:string;revision:number;restorable:boolean}
export interface RedactionJob extends RedactionSummary {mapping:RedactionItem[]|null;kinds:string[];custom:string[]}
export type RedactionVariant='placeholder'|'permanent'|'restore'|'markdown';
export interface RedactionExport {filename:string;content:string}
const path=(id:string)=>`/v1/redaction/${encodeURIComponent(id)}`;
const post=<T>(url:string,data:unknown,method='POST')=>runtimeRequest<T>(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function base64(file:File):Promise<string> { return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('无法读取文件，请重新选择。'));reader.readAsDataURL(file);}); }
export const redactionApi={
 list:()=>runtimeRequest<RedactionSummary[]>('/v1/redaction'),
 get:(id:string)=>runtimeRequest<RedactionJob>(path(id)),
 create:async(file:File,kinds:string[],custom:string[])=>post<RedactionJob>('/v1/redaction',{filename:file.name,content:await base64(file),kinds,custom}),
 update:(job:RedactionJob,item:RedactionItem)=>post<RedactionJob>(path(job.id),{revision:job.revision,item_id:item.id,keep:!item.keep},'PATCH'),
 rerun:(job:RedactionJob,kinds:string[],custom:string[])=>post<RedactionJob>(`${path(job.id)}/rerun`,{revision:job.revision,kinds,custom}),
 export:(job:RedactionJob,variant:RedactionVariant)=>post<RedactionExport>(`${path(job.id)}/export`,{revision:job.revision,variant}),
 deleteMapping:(job:RedactionJob)=>post<RedactionJob>(`${path(job.id)}/delete-mapping`,{revision:job.revision}),
};
export type RedactionApi=typeof redactionApi;
