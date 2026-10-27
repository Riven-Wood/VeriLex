import type {JSONContent} from '@tiptap/core';
import {runtimeRequest} from '../../runtime/api';
import type {WorkKind} from '../../views/LitigationClient';
export interface EditableProductSummary {path:string;id:string;title:string;kind:WorkKind;revision:number;updated_at:string;external_update:boolean}
export interface EditableProduct extends EditableProductSummary {
  document_type?:string;native_word?:boolean;document:JSONContent|null;file_hash:string;path:string;editor_receipt?:{proposal_id:string;action:string};
  recovery?:{base_hash:string;document:JSONContent;updated_at:string};
  draft?:{base_hash:string;document:JSONContent;updated_at:string};draft_current?:boolean;draft_error?:string;
  external_content?:{paragraphs:{id:string;text:string;editable:boolean}[];retained_objects:string[]};
}
const base=(workspace:string)=>`/v1/litigation/workspaces/${encodeURIComponent(workspace)}/editor-products`;
const json=(method:string,body:unknown)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export interface EditorProposal {id:string;product_id:string;thread_id:string;status:'pending'|'accepted'|'discarded'|'undone'|'partial';changes?:{selection:{from:number;to:number;text:string}|null;replacement:string}[];applied?:number[];preview_before?:JSONContent;preview_changes?:{selection:{from:number;to:number;text:string}|null;replacement:string}[];before:JSONContent;after:JSONContent;selection:{text:string}|null;replacement:string}
export const editorClient={
  openWord:async(w:string,id:string,base_hash:string)=>runtimeRequest<{product:EditableProduct;content:string}>(`${base(w)}/${id}/word`,json('POST',{base_hash})),
  saveWord:async(w:string,id:string,base_hash:string,content:string)=>runtimeRequest<EditableProduct>(`${base(w)}/${id}/word`,json('PUT',{base_hash,content})),
  export:async(w:string,id:string,base_hash:string,document:JSONContent,title:string,format:'docx'|'pdf')=>runtimeRequest<{filename:string;content:string;path:string;product:EditableProduct}>(`${base(w)}/${id}/export/${format}`,json('POST',{base_hash,document,title})),
  fromWork:async(w:string,kind:WorkKind,expected_revision:number)=>runtimeRequest<EditableProduct>(`/v1/litigation/workspaces/${encodeURIComponent(w)}/editor-from-work/${kind}`,json('POST',{expected_revision})),
  proposals:async(w:string,t:string)=>runtimeRequest<{proposals:EditorProposal[]}>(`/v1/litigation/workspaces/${encodeURIComponent(w)}/editor-discussions/${encodeURIComponent(t)}/proposals`),
  decide:async(w:string,t:string,id:string,action:'accept'|'discard'|'undo',document:JSONContent,change_ids?:number[])=>runtimeRequest<EditableProduct>(`/v1/litigation/workspaces/${encodeURIComponent(w)}/editor-discussions/${encodeURIComponent(t)}/proposals/${id}`,json('POST',{action,document,change_ids})),
  templates:async(w:string)=>runtimeRequest<{templates:{path:string;name:string}[]}>(`/v1/litigation/workspaces/${encodeURIComponent(w)}/editor-templates`),
  importTemplate:async(w:string,file:File,title:string)=>runtimeRequest<{path:string;name:string}>(`/v1/litigation/workspaces/${encodeURIComponent(w)}/editor-templates?${new URLSearchParams({filename:file.name,title})}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file}),
  saveTemplate:async(w:string,id:string,base_hash:string,document:JSONContent,title:string)=>runtimeRequest<{path:string;name:string}>(`${base(w)}/${id}/template`,json('POST',{base_hash,document,title})),
  importCopy:async(w:string,path:string,title:string,kind:WorkKind,text_only=false,document_type?:string)=>runtimeRequest<EditableProduct>(`/v1/litigation/workspaces/${encodeURIComponent(w)}/editor-import`,json('POST',{path,title,kind,text_only,document_type})),
  list:async(w:string,kind:WorkKind)=>runtimeRequest<{products:EditableProductSummary[];errors:{path:string;notice:string}[]}>(`${base(w)}?kind=${kind}`),
  read:async(w:string,id:string)=>runtimeRequest<EditableProduct>(`${base(w)}/${id}`),
  create:async(w:string,title:string,kind:WorkKind,document:JSONContent,document_type?:string)=>runtimeRequest<EditableProduct>(base(w),json('POST',{title,kind,document,document_type})),
  rename:async(w:string,id:string,base_hash:string,title:string)=>runtimeRequest<EditableProduct>(`${base(w)}/${id}/rename`,json('POST',{base_hash,title})),
  delete:async(w:string,id:string,base_hash:string)=>runtimeRequest<{id:string;path:string}>(`${base(w)}/${id}`,json('DELETE',{base_hash})),
  save:async(w:string,id:string,base_hash:string,document:JSONContent,title:string)=>runtimeRequest<EditableProduct>(`${base(w)}/${id}`,json('PUT',{base_hash,document,title})),
  draft:async(w:string,id:string,base_hash:string,document:JSONContent)=>runtimeRequest(`${base(w)}/${id}/draft`,json('PUT',{base_hash,document})),
  history:async(w:string,id:string)=>runtimeRequest<{versions:EditableProduct[]}>(`${base(w)}/${id}/history`),
};
