import { runtimeRequest } from './api';

export type CaseCalendarKind = 'hearing' | 'deadline' | 'meeting' | 'interview' | 'mediation' | 'filing' | 'investigation' | 'evidence-deadline' | 'appeal-deadline' | 'enforcement-deadline' | 'preservation-expiry' | 'other';
export type CaseCalendarReminder = null | 0 | 15 | 60 | 1440 | 4320 | 10080;
export interface CaseCalendarInput {
  title: string;
  kind: CaseCalendarKind;
  customKind?: string;
  date: string;
  time: string | null;
  timeZone: string;
  caseName: string;
  location: string;
  notes: string;
  reminderMinutes: CaseCalendarReminder;
}
export interface CaseCalendarEvent extends CaseCalendarInput {
  id: string;
  revision: number;
  createdAt: string;
  updatedAt: string;
  reminderAt: string | null;
  reminderAcknowledgedAt: string | null;
}
const root = '/v1/case-calendar';
const path = (id: string) => `${root}/events/${encodeURIComponent(id)}`;
const json = (method: string, value: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });

export const caseCalendarApi = {
  list: () => runtimeRequest<CaseCalendarEvent[]>(`${root}/events`),
  create: (value: CaseCalendarInput) => runtimeRequest<CaseCalendarEvent>(`${root}/events`, json('POST', value)),
  update: (id: string, value: CaseCalendarInput, revision: number) => runtimeRequest<CaseCalendarEvent>(path(id), json('PATCH', { ...value, revision })),
  remove: (id: string, revision: number) => runtimeRequest<void>(`${path(id)}?revision=${encodeURIComponent(revision)}`, { method: 'DELETE' }),
  listReminders: () => runtimeRequest<CaseCalendarEvent[]>(`${root}/reminders`),
  ackReminder: (id: string, revision: number) => runtimeRequest<void>(`${root}/reminders/${encodeURIComponent(id)}/ack`, json('POST', { revision })),
};
export type CaseCalendarApi = typeof caseCalendarApi;
