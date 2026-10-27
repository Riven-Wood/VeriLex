import {runtimeRequest} from './api';

export interface WorkspaceFileEntry {name: string; path: string; directory: boolean}
export interface WorkspaceDirectory {
  entries: WorkspaceFileEntry[]; path: string; snapshot: string;
  next_offset: number | null; truncated: boolean;
}
export function listWorkspaceFiles(workspace: string, path: string, offset = 0, snapshot = '', signal?: AbortSignal) {
  const query = new URLSearchParams({path, offset: String(offset), snapshot});
  return runtimeRequest<WorkspaceDirectory>(`/v1/workspaces/${encodeURIComponent(workspace)}/files?${query}`, {signal});
}
