import { runtimeRequest } from './api';

export interface TerminalSession {
  id: string; workspaceId: string; status: 'running' | 'exited' | 'closed';
  exitCode: number | null; columns: number; rows: number; inputSequence: number;
}
export interface TerminalOutput extends TerminalSession { data: string; cursor: number; truncated: boolean }
const base = (workspace: string) => `/v1/workspaces/${encodeURIComponent(workspace)}/terminals`;
const session = (workspace: string, id: string) => `${base(workspace)}/${encodeURIComponent(id)}`;
const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
export const listTerminals = (workspace: string, signal?: AbortSignal) => runtimeRequest<{supported: boolean; sessions: TerminalSession[]}>(base(workspace), {signal});
export const createTerminal = (workspace: string, id: string) => runtimeRequest<TerminalSession>(base(workspace), json({id}));
export const readTerminal = (workspace: string, id: string, after: number, signal?: AbortSignal) => runtimeRequest<TerminalOutput>(`${session(workspace, id)}/output?after=${after}`, {signal});
export const writeTerminal = (workspace: string, id: string, text: string, sequence: number) => runtimeRequest<{inputSequence: number}>(`${session(workspace, id)}/input`, json({text, sequence}));
export const resizeTerminal = (workspace: string, id: string, columns: number, rows: number) => runtimeRequest<TerminalSession>(`${session(workspace, id)}/resize`, json({columns, rows}));
export const closeTerminal = (workspace: string, id: string) => runtimeRequest<TerminalSession>(session(workspace, id), {method: 'DELETE'});
