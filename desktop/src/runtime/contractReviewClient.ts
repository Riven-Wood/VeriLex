import { runtimeRequest } from './api';

export interface ContractReviewHandle {
  review_id: string;
  thread_id: string;
  filename: string;
  upload_id: string;
}

export interface ContractOpinion {
  id: string;
  clause_label: string;
  clause_excerpt: string;
  anchor_text: string;
  occurrence: number | null;
  level: string;
  category: string;
  body: string;
  suggestion: string;
  replacement_text?: string | null;
  adopted: boolean;
  edited_text: string | null;
  citations: { id: string; document?: string; source_kind: string; source_call_id?: string; source_label: string; verified_at: string }[];
}

export interface ContractReviewSummary {
  id: string;
  thread_id: string;
  filename: string;
  skill: string;
  status: string;
  created_at: string;
  updated_at: string;
  opinion_count: number;
}

export interface ContractReviewDetail extends ContractReviewSummary {
  party: string;
  strictness: string;
  signature: string;
  retrieval_enabled: boolean;
  requirements?: string;
  opinions: ContractOpinion[];
}

export interface ContractReviewExport {
  filename: string;
  content: string;
}

export interface ReviewSkillEntry {
  name: string;
  description: string;
}

export type ReviewParty = 'party_a' | 'party_b' | 'neutral';
export type ReviewStrictness = 'loose' | 'normal' | 'strict';

export interface StartReviewOptions {
  skill: string;
  party: ReviewParty;
  strictness: ReviewStrictness;
  signature: string;
  retrieval: boolean;
  requirements?: string;
  providerId: string | null;
  model: string;
  documentId?: string;
  documentRevision?: number;
}

const path = (id: string) => `/v1/contract-reviews/${encodeURIComponent(id)}`;

export const contractReviewApi = {
  remove: (id: string) => runtimeRequest(path(id), {method:'DELETE'}),
  create: async (file: File, options: StartReviewOptions) => {
    const params = new URLSearchParams({
      filename: file.name,
      model: options.model,
      skill: options.skill,
      party: options.party,
      strictness: options.strictness,
      signature: options.signature,
      retrieval: String(options.retrieval),
    });
    if (options.documentId) { params.set('document_id', options.documentId); params.set('document_revision', String(options.documentRevision)); }
    if (options.providerId) params.set('provider_id', options.providerId);
    const body = new FormData();
    body.append("file", file);
    body.append("requirements", options.requirements ?? "");
    return runtimeRequest<ContractReviewHandle>(
      `/v1/contract-reviews?${params.toString()}`,
      { method: 'POST', body },
    );
  },
  list: () => runtimeRequest<ContractReviewSummary[]>('/v1/contract-reviews'),
  get: (id: string) => runtimeRequest<ContractReviewDetail>(path(id)),
  patchOpinion: (reviewId: string, opinionId: string, body: { adopted?: boolean; edited_text?: string }) =>
    runtimeRequest<ContractOpinion>(`${path(reviewId)}/opinions/${encodeURIComponent(opinionId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  stop: (reviewId: string) =>
    runtimeRequest<ContractReviewSummary>(`${path(reviewId)}/stop`, { method: 'POST' }),
  export: (reviewId: string) =>
    runtimeRequest<ContractReviewExport>(`${path(reviewId)}/export`, { method: 'POST' }),
  report: (reviewId: string) =>
    runtimeRequest<ContractReviewExport>(`${path(reviewId)}/report`, { method: 'POST' }),
  listSkills: async (): Promise<ReviewSkillEntry[]> => {
    const rows = await runtimeRequest<
      { name: string; description: string; category: string; valid: boolean; enabled: boolean }[]
    >('/v1/skills');
    return rows
      .filter((row) => row.valid && row.enabled && row.category === 'contract-review')
      .map(({ name, description }) => ({ name, description }));
  },
};

export const DEFAULT_REVIEW_SKILL = '通用合同审查';
