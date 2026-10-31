import { runtimeRequest } from '../runtime/api';

export type LitigationKind = 'facts' | 'evidence' | 'issues' | 'strategy';
export interface LitigationSource { path: string; page: number | null; quote: string; locator?:{version:1;file_hash:string;kind:string;part:string;key:string;index:number}|null }
export interface LitigationFact {
  id: string; date: string; text: string; sources: LitigationSource[];
  importance: 'high' | 'medium' | 'low'; pending: string[];
  status: 'pending' | 'supported' | 'confirmed'; user_note: string;
}
export interface LitigationCaseInfo { title: string; cause: string; plaintiff: string; defendant: string; position: string; theory: string }
export interface LitigationCaseSuggestion { fields: {key: 'title'|'cause'|'plaintiff'|'defendant'; value: string; sources: LitigationSource[]; reason: string}[]; gaps: string[]; summary: string; case_revision: number }
export interface LitigationRecord<T> { revision: number; data: T | null; updated_at?: string }
export interface LitigationEvidence { id: string; group: string; name: string; kind: string; purpose: string; sources: LitigationSource[]; strength: string; assessment: string }
export interface LitigationIssue { title: string; elements: { name: string; supporting: LitigationSource[]; opposing: LitigationSource[]; strength: string; gap: string }[]; laws: { bbbs: string; clause: string; quote: string }[] }
export interface LitigationElements { issues: { title: string; elements: string[] }[]; summary: string; confirmed: boolean; case_revision: number }
export interface LitigationVersion { revision: number; updated_at?: string; summary: string }
export interface LitigationDocumentField { key: string; value: string; sources: LitigationSource[]; pending: string[] }
export interface LitigationDocumentProposal { template_id: string; plaintiff_count: number; defendant_count: number; plaintiff_type?: 'natural' | 'organization' | null; defendant_type?: 'natural' | 'organization' | null; third_party_type?: 'natural' | 'organization' | null; third_party_count?: number; fields: LitigationDocumentField[]; laws: {bbbs: string; clause: string; quote: string}[]; summary: string }
export interface LitigationDocument extends LitigationDocumentProposal { confirmed: boolean; dependencies: Record<string, number> }
export interface LitigationTemplate { id: string; name: string; source_url: string; version: string; fields: {key: string; label: string; hint: string; required: boolean; kind: string; choices?: string[]}[] }
export interface LitigationSnapshot {
  case_suggestions?: LitigationRecord<LitigationCaseSuggestion>;
  documents?: LitigationRecord<LitigationDocument>;
  case: LitigationRecord<LitigationCaseInfo>;
  elements?: LitigationRecord<LitigationElements>;
  facts: LitigationRecord<{facts: LitigationFact[]; gaps: string[]; relationships: string; conflicts: {id: string; previous: LitigationFact; proposed: LitigationFact}[] }>;
  evidence: LitigationRecord<{evidence: LitigationEvidence[]; gaps: string[]; contradictions: string[]; suggestions: string[] }>;
  issues: LitigationRecord<{issues: LitigationIssue[]; dependencies?: Record<string,number>; elements_revision?: number}>;
  strategy: LitigationRecord<{favorable: string[]; unfavorable: string[]; defenses: string[]; actions: string[]; opposing_counsel: string[]; dependencies?: Record<string,number>; issues_revision?: number}>;
}
export interface LitigationFile { path: string; name: string; directory: boolean }
export interface LitigationMaterial { path: string; name: string; supported: boolean; book_supported?: boolean; reason: string }
const base = (id: string) => `/v1/litigation/workspaces/${encodeURIComponent(id)}`;
export const litigationClient = {
  documentTemplates: (id: string) => runtimeRequest<{templates: LitigationTemplate[]}>(`${base(id)}/document-templates`),
  confirmDocument: (id: string, data: LitigationDocumentProposal, expected_revision: number) => runtimeRequest<LitigationRecord<LitigationDocument>>(`${base(id)}/documents/confirmation`, {
    method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({data, expected_revision}),
  }),
  files: (id: string, path = '') => runtimeRequest<{entries: LitigationFile[]; truncated: boolean}>(`${base(id)}/files?path=${encodeURIComponent(path)}`),
  snapshot: (id: string) => runtimeRequest<LitigationSnapshot>(base(id)),
  materials: (id: string) => runtimeRequest<{materials: LitigationMaterial[]; truncated: boolean}>(`${base(id)}/materials`),
  saveCase: (id: string, data: LitigationCaseInfo, expected_revision: number) => runtimeRequest(`${base(id)}`, {
    method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({data, expected_revision}),
  }),
  reviewFact: (id: string, data: LitigationFact, expected_revision: number) => runtimeRequest(`${base(id)}/facts/${encodeURIComponent(data.id)}`, {
    method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify({data, expected_revision}),
  }),
  arrangeEvidence: (id: string, order: {id: string; group: string}[], expected_revision: number) => runtimeRequest(`${base(id)}/evidence/order`, {
    method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({order, expected_revision}),
  }),
  regenerateFacts: (id: string, expected_revision: number) => runtimeRequest<LitigationSnapshot['facts']>(`${base(id)}/facts/regeneration`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({expected_revision}),
  }),
  factVersions: (id: string) => runtimeRequest<{versions: LitigationVersion[]}>(`${base(id)}/facts/versions`),
  factVersion: (id: string, revision: number) => runtimeRequest<LitigationSnapshot['facts']>(`${base(id)}/facts/versions/${revision}`),
  restoreFacts: (id: string, revision: number, expected_revision: number) => runtimeRequest<LitigationSnapshot['facts']>(`${base(id)}/facts/restore`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({revision, expected_revision}),
  }),
  confirmElements: (id: string, data: Pick<LitigationElements,'issues'|'summary'>, expected_revision: number) => runtimeRequest<LitigationRecord<LitigationElements>>(`${base(id)}/elements/confirmation`, {
    method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({data, expected_revision}),
  }),
};

export type WorkKind = LitigationKind | 'documents';
export interface WorkSection { id: string; title: string; body: string; sources: LitigationSource[]; pending: string[] }
export interface WorkProduct { title: string; sections: WorkSection[]; last_thread_id?: string }
export interface WorkBinding { project_chart_kind?: 'timeline'|'chronicle'|'relationship'|'confrontation'; cross_revision?: number; cross_entry_id?: string; evidence_revision?: number; kind: WorkKind; section_id: string | null; source_path?: string | null; materials: string[]; expected_revision: number }
export interface WorkRequest extends WorkBinding { title: string; message?: string; projectWorkspaceId?: string; editorContext?: import("../components/editor/DiscussionContext").EditorTurnContext }
export const litigationWorkClient = {
  read: (id: string, kind: WorkKind) => runtimeRequest<LitigationRecord<WorkProduct>>(`${base(id)}/work-products/${kind}`),
  versions: (id: string, kind: WorkKind) => runtimeRequest<{versions: LitigationRecord<WorkProduct>[]}>(`${base(id)}/work-products/${kind}/versions`),
  links: (id: string) => runtimeRequest<{links: Record<string, WorkBinding>}>(`${base(id)}/work-discussions`),
  bind: (id: string, threadId: string, binding: WorkBinding) => runtimeRequest<WorkBinding>(`${base(id)}/work-discussions/${encodeURIComponent(threadId)}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(binding)}),
};
