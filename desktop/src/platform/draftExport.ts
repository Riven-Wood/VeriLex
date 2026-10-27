import {invoke} from '@tauri-apps/api/core';
export async function saveDraftExport(filename:string,content:string,format:'docx'|'pdf'):Promise<string|null>{
 const bytes=Uint8Array.from(atob(content),c=>c.charCodeAt(0));
 if(bytes.length>20*1024*1024)throw Error('文书超过20 MiB，无法导出。');
 if(Reflect.has(globalThis,'__TAURI_INTERNALS__'))return invoke<string|null>('save_litigation_document',{defaultName:filename,content:Array.from(bytes)});
 const url=URL.createObjectURL(new Blob([bytes],{type:format==='pdf'?'application/pdf':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}));
 const link=document.createElement('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return filename;
}
