import { runtimeRequest } from './api';
import type { ContractReviewExport } from './contractReviewClient';

export type ContractEditorTurnContext = {
  review_id: string;
  revision: number;
  paragraph_id: string;
  start: number;
  end: number;
  selected_text: string;
} | {mode:'word';review_id:string;document_id:string;revision:number;selection:import('./wordDocumentClient').WordSelection;selected_text:string};
export interface ContractEditorParagraph {
  id: string;
  text: string;
  original: string;
  editable: boolean;
  in_table?: boolean;
  readonly_reason?: string | null;
}
export interface ContractEditorProposal {
  id: string;
  turn_id: string;
  paragraph_id: string;
  before: string;
  after: string;
  reason: string;
  status: 'pending' | 'accepted' | 'rejected' | 'undone';
  base_revision: number;
  can_accept: boolean;
  can_undo: boolean;
}
export interface ContractEditorState {
  review_id: string;
  revision: number;
  file_hash: string;
  mode: 'paragraph_text';
  paragraphs: ContractEditorParagraph[];
  retained_objects: string[];
  notice: string;
  proposals: ContractEditorProposal[];
  opinion_locations: { opinion_id: string; paragraph_id: string | null; status: 'current' | 'changed' | 'unresolved' }[];
  updated_at: string;
}
export interface ContractParagraphEdit { paragraph_id: string; original: string; text: string }
const base = (id: string) => `/v1/contract-reviews/${encodeURIComponent(id)}/editor`;
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
export const contractEditorApi = {
  get: (id: string) => runtimeRequest<ContractEditorState>(base(id)),
  save: (id: string, expected_revision: number, edits: ContractParagraphEdit[]) =>
    runtimeRequest<ContractEditorState>(base(id), json('PATCH', { expected_revision, edits })),
  decide: (id: string, proposal: string, action: 'accept' | 'reject' | 'undo', expected_revision: number) =>
    runtimeRequest<ContractEditorState>(`${base(id)}/proposals/${encodeURIComponent(proposal)}/${action}`, json('POST', { expected_revision })),
  export: (id: string, expected_revision: number) =>
    runtimeRequest<ContractReviewExport>(`${base(id)}/export`, json('POST', { expected_revision })),
};
