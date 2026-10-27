import { afterEach, expect, test, vi } from 'vitest';
import { caseCalendarApi, type CaseCalendarInput } from '../src/runtime/caseCalendarClient';

afterEach(() => vi.unstubAllGlobals());
test('calendar mutations use authenticated runtime requests, encoded identifiers, and revisions', async () => {
  const fetch = vi.fn(async (_url: string, _options: RequestInit) => new Response(JSON.stringify({}), { status: 200 }));
  vi.stubGlobal('fetch', fetch); vi.stubGlobal('__VERILEX_API_TOKEN__', 'calendar-test-token');
  const input: CaseCalendarInput = { title: '虚构日程', kind: 'other', date: '2024-02-29', time: null, timeZone: 'Asia/Shanghai', caseName: '', location: '', notes: '', reminderMinutes: null };
  await caseCalendarApi.create(input); await caseCalendarApi.update('event/id', input, 7); await caseCalendarApi.remove('event/id', 8); await caseCalendarApi.listReminders(); await caseCalendarApi.ackReminder('event/id', 8);
  expect(fetch.mock.calls[0][0]).toContain('/v1/case-calendar/events');
  expect(new Headers(fetch.mock.calls[0][1].headers).get('X-VeriLex-Token')).toBe('calendar-test-token');
  expect(fetch.mock.calls[1][0]).toContain('/events/event%2Fid'); expect(JSON.parse(String(fetch.mock.calls[1][1].body))).toEqual({ ...input, revision: 7 });
  expect(fetch.mock.calls[2][0]).toContain('/events/event%2Fid?revision=8'); expect(fetch.mock.calls[2][1].method).toBe('DELETE');
  expect(fetch.mock.calls[3][0]).toContain('/v1/case-calendar/reminders'); expect(fetch.mock.calls[4][0]).toContain('/reminders/event%2Fid/ack');
  expect(JSON.parse(String(fetch.mock.calls[4][1].body))).toEqual({ revision: 8 });
});
