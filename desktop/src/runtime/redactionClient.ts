import { runtimeRequest } from './api';
export const REDACTION_TYPES: Record<string,string> = {person:'姓名',address:'住址',phone:'电话',email:'邮箱',wechat:'微信号',identity:'身份证',bank:'银行/支付账号',plate:'车牌',health:'健康状况',certificate:'权属证书编号',credit:'统一社会信用代码',case:'案号',money:'金额',date:'日期',organization:'公司/机构'};
export const DEFAULT_REDACTION_TYPES = Object.keys(REDACTION_TYPES).filter(k=>!['case','money','date'].includes(k));
export interface RedactionReplacement {old:string;new:string}
export interface RedactionExport {filename:string;content:string}
const post=<T>(url:string,data:unknown,method='POST')=>runtimeRequest<T>(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function base64(file:File):Promise<string> { return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('无法读取文件，请重新选择。'));reader.readAsDataURL(file);}); }
export const redactionApi={
 process:async(file:File,kinds:string[],replacements:RedactionReplacement[],variant:'word'|'markdown')=>post<RedactionExport>('/v1/redaction/process',{filename:file.name,content:await base64(file),kinds,replacements,variant}),
};
export type RedactionApi=typeof redactionApi;
