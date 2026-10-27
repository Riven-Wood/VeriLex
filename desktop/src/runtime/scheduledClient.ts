import { runtimeRequest } from './api';
export interface Schedule { kind: 'daily' | 'weekly' | 'once'; timeOfDay?: string | null; weekday?: number | null; fireAt?: string | null }
export interface ScheduledTaskInput { name: string; instruction: string; schedule: Schedule; providerId: string; model: string; workspaceId: string | null; knowledgeLibraryIds: string[]; maxMinutes: number }
export interface ScheduledTask extends ScheduledTaskInput { id: string; enabled: boolean; nextRun: string | null; lastStatus: string | null; runCount: number; scheduleRunCount?: number; createdAt: string }
export interface ScheduledRun { id: string; taskId: string; threadId: string | null; turnId: string | null; triggerKind: 'manual' | 'catchup' | 'schedule'; status: string; startedAt: string; finishedAt: string | null; error: string | null }
const path = (id: string) => `/v1/schedules/${encodeURIComponent(id)}`;
const body = (method: string, value: unknown) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
export const scheduledApi = {
  list: () => runtimeRequest<ScheduledTask[]>('/v1/schedules'),
  create: (value: ScheduledTaskInput) => runtimeRequest<ScheduledTask>('/v1/schedules', body('POST', value)),
  update: (id: string, value: ScheduledTaskInput) => runtimeRequest<ScheduledTask>(path(id), body('PATCH', value)),
  enable: (id: string, enabled: boolean) => runtimeRequest<ScheduledTask>(`${path(id)}/${enabled ? 'enable' : 'disable'}`, {method:'POST'}),
  remove: (id: string) => runtimeRequest<void>(path(id), {method:'DELETE'}),
  run: (id: string) => runtimeRequest<ScheduledRun>(`${path(id)}/run`, {method:'POST'}),
  runs: (id: string) => runtimeRequest<ScheduledRun[]>(`${path(id)}/runs`),
};
export type ScheduledApi = typeof scheduledApi;
