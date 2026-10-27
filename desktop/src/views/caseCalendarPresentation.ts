import type { CaseCalendarEvent, CaseCalendarKind } from '../runtime/caseCalendarClient';

export const BEIJING_TIME_ZONE = 'Asia/Shanghai';
export const CASE_CALENDAR_KINDS: Record<CaseCalendarKind, string> = {
  hearing: '开庭', meeting: '会议', interview: '会见', mediation: '调解', filing: '立案', investigation: '调查取证',
  'evidence-deadline': '举证截止', 'appeal-deadline': '上诉截止', 'enforcement-deadline': '申请执行截止',
  'preservation-expiry': '保全到期', deadline: '其他期限', other: '其他',
};
export function calendarKindLabel(item: Pick<CaseCalendarEvent, 'kind' | 'customKind'>): string {
  return item.kind === 'other' && item.customKind?.trim() ? item.customKind.trim() : CASE_CALENDAR_KINDS[item.kind];
}
export function beijingToday(now = new Date()): string {
  // UTC accessors after a fixed +08:00 shift never depend on device settings.
  return new Date(now.getTime() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

export type CalendarView = 'month' | 'week' | 'list';
export const weekdays = ['一', '二', '三', '四', '五', '六', '日'];
const pad = (n: number) => String(n).padStart(2, '0');
export const dateKey = (d: Date) => `${String(d.getFullYear()).padStart(4, '0')}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export function parseDay(value: string): Date {
  const [year, month, day] = value.split('-').map(Number);
  const result = new Date(0);
  result.setFullYear(year, month - 1, day);
  result.setHours(12, 0, 0, 0);
  return result;
}
export function validDay(value?: string): value is string {
  return Boolean(value && /^\d{4}-\d{2}-\d{2}$/.test(value) && value >= '1900-01-01' && value <= '9999-12-31' && dateKey(parseDay(value)) === value);
}
export function addDays(value: string, amount: number): string {
  const day = parseDay(value);
  day.setDate(day.getDate() + amount);
  return dateKey(day);
}

// Adapted behavior from the owner's Legal Work Space calendarNavigation.ts and
// its tests (reviewed 2026-10-12): shared anchor, Monday-first weeks, natural
// month clamping and cross-year titles. Native date arithmetic; no date-fns copy.
export function moveCalendarMonth(value: string, direction: -1 | 1): string {
  const date = parseDay(value);
  const day = date.getDate();
  date.setDate(1);
  date.setMonth(date.getMonth() + direction);
  const lastDay = new Date(date.getFullYear(), date.getMonth() + 1, 0, 12).getDate();
  date.setDate(Math.min(day, lastDay));
  return dateKey(date);
}
export function calendarWeekDays(value: string): string[] {
  const start = addDays(value, -(parseDay(value).getDay() + 6) % 7);
  return Array.from({ length: 7 }, (_, i) => addDays(start, i));
}
export function calendarMonthDays(value: string): string[] {
  const first = `${value.slice(0, 7)}-01`;
  const date = parseDay(first);
  date.setMonth(date.getMonth() + 1, 0);
  const start = calendarWeekDays(first)[0];
  const last = calendarWeekDays(dateKey(date))[6];
  const days: string[] = [];
  for (let day = start; parseDay(day) <= parseDay(last); day = addDays(day, 1)) days.push(day);
  return days;
}
export function dayLabel(value: string): string {
  const [year, month, day] = value.split('-').map(Number);
  return `${year}年${month}月${day}日`;
}
export function shortDayLabel(value: string): string {
  return `${Number(value.slice(5, 7))}月${Number(value.slice(8))}日`;
}
export function calendarTitle(value: string, view: CalendarView): string {
  if (view !== 'week') return `${Number(value.slice(0, 4))}年${Number(value.slice(5, 7))}月`;
  const days = calendarWeekDays(value).filter(validDay);
  const start = days[0], end = days[days.length - 1];
  const endLabel = start.slice(0, 4) !== end.slice(0, 4) ? dayLabel(end)
    : start.slice(0, 7) !== end.slice(0, 7) ? shortDayLabel(end) : `${Number(end.slice(8))}日`;
  return `${dayLabel(start)}—${endLabel}`;
}
export function navigateCalendar(value: string, view: CalendarView, direction: -1 | 1): string {
  const next = view === 'week' ? addDays(value, direction * 7) : moveCalendarMonth(value, direction);
  return parseDay(next) < parseDay('1900-01-01') ? '1900-01-01'
    : parseDay(next) > parseDay('9999-12-31') ? '9999-12-31' : next;
}
export function sortCalendarEvents(items: CaseCalendarEvent[]): CaseCalendarEvent[] {
  return [...items].sort((a, b) => a.date.localeCompare(b.date) || (a.time ?? '').localeCompare(b.time ?? '') || a.title.localeCompare(b.title, 'zh-CN') || a.id.localeCompare(b.id));
}
