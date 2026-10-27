import { invoke } from '@tauri-apps/api/core';
import type { RedactionExport } from '../runtime/redactionClient';
import { saveDocumentExport } from './documentExport';
export async function saveRedaction(result:RedactionExport,directory:string|null=null):Promise<string|null> {
 if(directory&&Reflect.has(globalThis,'__TAURI_INTERNALS__')) {
  return invoke<string|null>('save_redaction',{defaultName:result.filename,content:Array.from(Uint8Array.from(atob(result.content),c=>c.charCodeAt(0))),directory});
 }
 return saveDocumentExport(result, 'save_redaction');
}
export const openRedaction=(path:string,reveal=false)=>invoke('open_redaction_export',{path,reveal});

export const chooseRedactionDirectory=()=>invoke<string|null>('choose_redaction_directory');
