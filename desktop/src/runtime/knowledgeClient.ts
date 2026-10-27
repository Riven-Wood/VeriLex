import { runtimeRequest } from './api';
export interface KnowledgeLibrary {
  kind?: 'ima'; id: string; name: string; root: string; status: string; error: string | null;
  excluded: string[]; directories: string[]; count: number; synced_at: string | null;
}
export interface KnowledgeMatch { match_start: number; match_end: number; read_start: number; snippet: string }
export interface KnowledgeDocument { kind?: 'folder'; read_start?: number; match_start?: number | null; match_end?: number | null; id: string; path: string; version: string; status: string; error: string | null; snippet: string }
export interface KnowledgePage { external?: boolean; notice?: string; open_url?: string; id: string; path: string; version: string; text: string; start: number; end: number; total_chars: number; has_more: boolean }
const base = '/v1/knowledge';
const idPath = (id: string) => `${base}/${encodeURIComponent(id)}`;
export const knowledgeApi = {
  quote: (id: string, threadId: string) => runtimeRequest<{source_state?: string; text: string; source_name: string; line_start: number; line_end: number}>(`${base}/quotes/${encodeURIComponent(id)}?${new URLSearchParams({thread_id: threadId})}`),
  list: () => runtimeRequest<KnowledgeLibrary[]>(base),
  associate: (root: string) => runtimeRequest<{id: string}>(base, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({root})}),
  update: (id: string, changes: {name?: string; root?: string; excluded?: string[]}) => runtimeRequest(idPath(id), {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(changes)}),
  detach: (id: string) => runtimeRequest(idPath(id), {method: 'DELETE'}),
  sync: (id: string) => runtimeRequest(`${idPath(id)}/sync`, {method: 'POST'}),
  documents: (id: string, q: string, offset: number, folderId = '') => runtimeRequest<{items: KnowledgeDocument[]; has_more: boolean; next_offset?: number | null}>(`${idPath(id)}/documents?${new URLSearchParams({q, offset: String(offset), ...(folderId ? {folder_id: folderId} : {})})}`),
  matches: (id: string, doc: KnowledgeDocument, q: string, start: number) => runtimeRequest<{items: KnowledgeMatch[]; has_more: boolean; next_start: number | null}>(`${idPath(id)}/documents/${encodeURIComponent(doc.id)}/matches?${new URLSearchParams({version: doc.version, q, start: String(start)})}`),
  read: (id: string, doc: KnowledgeDocument, start: number) => runtimeRequest<KnowledgePage>(`${idPath(id)}/documents/${encodeURIComponent(doc.id)}?${new URLSearchParams({version: doc.version, start: String(start)})}`),
};
export type KnowledgeApi = typeof knowledgeApi;
