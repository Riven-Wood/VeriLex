import { runtimeRequest } from './api';
export interface ReviewMarkup {review_id:string;applied:number;skipped:number}
export interface WordDocument { id: string; filename: string; content: string; revision: number; updated_at: string; review_markups?:Record<string,{applied:number;skipped:number}> }
export interface WordMarkupPlan {review_id:string;document_id:string;revision:number;source:string;author:string;opinions:{id:string;anchor_text:string;occurrence:number|null;replacement_text:string|null;comment:string}[]}
export interface WordSelection {texts:string[];start:number;end:number;from:number;to:number}
export interface WordAiEdit {id:string;review_id:string;document_id:string;revision:number;selection:WordSelection;before_text:string;after_text:string;reason:string;status:string}
const root = '/v1/contract-reviews/documents';
export const wordDocumentApi = {
  remove: async (id: string) => { await runtimeRequest(`${root}/${encodeURIComponent(id)}`, {method:'DELETE'}); sessions.delete(id); },
  list: () => runtimeRequest<{id:string;filename:string}[]>(root),
  open: (file: File) => runtimeRequest<WordDocument>(`${root}?${new URLSearchParams({filename:file.name})}`, {method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file}),
  get: (id: string) => runtimeRequest<WordDocument>(`${root}/${encodeURIComponent(id)}`),
  forReview: (id: string) => runtimeRequest<WordDocument>(`/v1/contract-reviews/${encodeURIComponent(id)}/word-document`),
  markupPlan: (id:string)=>runtimeRequest<WordMarkupPlan|{already_applied:true;document_id:string;applied:number;skipped:number}>(`/v1/contract-reviews/${encodeURIComponent(id)}/word-markup-plan`),
  aiEdits: (id:string)=>runtimeRequest<WordAiEdit[]>(`/v1/contract-reviews/${encodeURIComponent(id)}/word-ai-edits`),
  aiConflict: (id:string,editId:string)=>runtimeRequest(`/v1/contract-reviews/${encodeURIComponent(id)}/word-ai-edits/${encodeURIComponent(editId)}/conflict`,{method:'POST'}),
  save: (id: string, revision: number, bytes: Uint8Array, markup?:ReviewMarkup, aiEditId?:string) => runtimeRequest<{id:string;revision:number}>(`${root}/${encodeURIComponent(id)}?${new URLSearchParams({revision:String(revision),...(markup?{review_id:markup.review_id,applied:String(markup.applied),skipped:String(markup.skipped)}:{}),...(aiEditId?{ai_edit_id:aiEditId}:{})})}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:bytes.slice().buffer}),
};
export const decodeWord = (content: string) => Uint8Array.from(atob(content), c=>c.charCodeAt(0));
export function encodeWord(bytes: Uint8Array) { let text='';for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(text); }

// Retain pending bytes across view changes. A failed write keeps the local copy
// and blocks review/export until it is explicitly retried or exported by the user.
export class WordSession {
  document: WordDocument;
  bytes: Uint8Array;
  dirty = false;
  error = '';
  private generation = 0;
  private saving: Promise<void> | null = null;
  private pendingMarkup: ReviewMarkup | undefined;
  private pendingAiEdit: string | undefined;
  private listeners = new Set<()=>void>();
  constructor(document: WordDocument, private saveDocument: typeof wordDocumentApi.save = (...args)=>wordDocumentApi.save(...args)) { this.document=document;this.bytes=decodeWord(document.content); }
  subscribe = (listener: ()=>void) => {this.listeners.add(listener);return ()=>{this.listeners.delete(listener);};};
  private emit(){this.listeners.forEach(fn=>fn());}
  update(bytes: Uint8Array, markup?:ReviewMarkup, aiEditId?:string) {
    if(!markup && !aiEditId && bytes.length===this.bytes.length && bytes.every((value,i)=>value===this.bytes[i]))return;
    if(markup)this.pendingMarkup=markup;
    if(aiEditId)this.pendingAiEdit=aiEditId;
    this.bytes=bytes.slice();this.generation++;this.dirty=true;this.emit();
    if(!this.error)void this.flush().catch(()=>{});
  }
  async flush(): Promise<void> {
    if(this.saving){await this.saving; if(this.dirty)return this.flush();return;}
    if(!this.dirty)return;
    this.error='';const generation=this.generation;const bytes=this.bytes.slice();const markup=this.pendingMarkup, aiEdit=this.pendingAiEdit;
    const save=aiEdit?this.saveDocument(this.document.id,this.document.revision,bytes,undefined,aiEdit):markup?this.saveDocument(this.document.id,this.document.revision,bytes,markup):this.saveDocument(this.document.id,this.document.revision,bytes);
    this.saving=save.then(result=>{
      this.document={...this.document,revision:result.revision,content:encodeWord(bytes),review_markups:{...this.document.review_markups,...(markup?{[markup.review_id]:{applied:markup.applied,skipped:markup.skipped}}:{})}};
      if(markup===this.pendingMarkup)this.pendingMarkup=undefined;
      if(aiEdit===this.pendingAiEdit)this.pendingAiEdit=undefined;
      if(generation===this.generation)this.dirty=false;
    }).catch(error=>{this.error=error instanceof Error?error.message:'本机保存失败，请重试或另存 Word。';throw error;}).finally(()=>{this.saving=null;this.emit();});
    await this.saving;
    if(this.dirty)await this.flush();
  }
}
const sessions=new Map<string,WordSession>();
// Failed saves survive navigation; do not permit a later app close to silently
// discard a pending copy just because its editor is no longer mounted.
if (typeof window !== 'undefined') window.addEventListener('beforeunload', event => {
  if ([...sessions.values()].some(session => session.dirty)) { event.preventDefault(); event.returnValue=''; }
});
export function wordSession(document: WordDocument){let session=sessions.get(document.id);if(!session){session=new WordSession(document);sessions.set(document.id,session);}return session;}

// Legacy workbench sessions use their own save API but need the same pending
// byte retention and close protection as contract documents.
export function retainedWordSession(key:string, document:WordDocument, save:ConstructorParameters<typeof WordSession>[1]) {
  let session=sessions.get(key);
  if (!session || (!session.dirty && session.document.revision !== document.revision)) {
    session=new WordSession(document,save);
    sessions.set(key,session);
  }
  return session;
}
