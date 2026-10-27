import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { CaseCalendarView, calendarMonthDays, moveCalendarMonth } from '../src/views/CaseCalendarView';
import { beijingToday, calendarTitle, calendarWeekDays, navigateCalendar } from '../src/views/caseCalendarPresentation';
import { RuntimeApiError } from '../src/runtime/api';
import type { CaseCalendarApi, CaseCalendarEvent, CaseCalendarInput } from '../src/runtime/caseCalendarClient';

Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.setAttribute('open', ''); } });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const record: CaseCalendarEvent = { id: 'one', revision: 3, title: '虚构开庭安排', kind: 'hearing', date: '2024-03-31', time: '00:15', timeZone: 'Asia/Shanghai', caseName: '虚构甲乙案', location: '模拟法庭', notes: '携带副本', reminderMinutes: 15, reminderAt: '2024-03-31T07:00:00Z', reminderAcknowledgedAt: null, createdAt: '2024-03-01T00:00:00Z', updatedAt: '2024-03-01T00:00:00Z' };
function fixture(seed: CaseCalendarEvent[] = [record]): CaseCalendarApi {
  let rows = [...seed];
  return {
    list: vi.fn(async () => [...rows]),
    create: vi.fn(async (input: CaseCalendarInput) => { const saved = { ...record, ...input, id: 'created', revision: 1 }; rows.push(saved); return saved; }),
    update: vi.fn(async (id, input, revision) => { const saved = { ...record, ...input, id, revision: revision + 1 }; rows = rows.map(row => row.id === id ? saved : row); return saved; }),
    remove: vi.fn(async id => { rows = rows.filter(row => row.id !== id); }),
    listReminders: vi.fn(async () => []), ackReminder: vi.fn(async () => {}),
  };
}
async function openCreate(api: CaseCalendarApi) {
  render(<CaseCalendarView api={api} initialDate="2024-03-31" />);
  await waitFor(() => expect((screen.getByRole('button', { name: '新建日程' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '新建日程' }));
}
async function openDetail(api: CaseCalendarApi) {
  render(<CaseCalendarView api={api} initialDate={record.date} />);
  const agenda = screen.getByLabelText('当天日程');
  fireEvent.click(await within(agenda).findByRole('button', { name: /虚构开庭安排/ }));
  return screen.getByRole('dialog');
}

// The owner's Legal Work Space calendarNavigation.test.ts supplied the reference
// behaviors: March 31 clamps to February 29, calendar dates are unique, anchor stays visible.
test('natural-month navigation clamps leap years and crosses year boundaries', () => {
  expect(moveCalendarMonth('2024-03-31', -1)).toBe('2024-02-29');
  expect(moveCalendarMonth('2023-03-31', -1)).toBe('2023-02-28');
  expect(moveCalendarMonth('2024-12-31', 1)).toBe('2025-01-31');
  const days = calendarMonthDays('2024-03-31');
  expect(days[0]).toBe('2024-02-26'); expect(days.at(-1)).toBe('2024-03-31');
  expect(days).toContain('2024-03-31'); expect(new Set(days).size).toBe(days.length); expect(days.length % 7).toBe(0);
});

test('manual creation persists the selected date and does not require a case', async () => {
  const api = fixture([]); await openCreate(api);
  fireEvent.change(screen.getByLabelText('日程标题'), { target: { value: '  虚构材料截止  ' } });
  fireEvent.click(screen.getByRole('button', { name: '保存日程' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ title: '虚构材料截止', date: '2024-03-31', time: '09:00', timeZone: 'Asia/Shanghai', reminderMinutes: null, caseName: '' }));
  expect(await within(screen.getByLabelText('当天日程')).findByRole('button', { name: /虚构材料截止/ })).toBeTruthy();
  expect(screen.getByText('日程已保存')).toBeTruthy();
});

test('a failed save preserves form values and closing a changed draft requires a decision', async () => {
  const api = fixture([]); api.create = vi.fn(async () => { throw new RuntimeApiError('保存失败，请重试', { code: 'unavailable', retryable: true, status: 503 }); });
  await openCreate(api);
  fireEvent.change(screen.getByLabelText('日程标题'), { target: { value: '未保存的虚构安排' } });
  fireEvent.click(screen.getByRole('button', { name: '保存日程' }));
  expect(await screen.findByText('保存失败，请重试')).toBeTruthy();
  expect((screen.getByLabelText('日程标题') as HTMLInputElement).value).toBe('未保存的虚构安排');
  fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
  expect(screen.getByText('本次填写尚未保存，要放弃吗？')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect((screen.getByLabelText('日程标题') as HTMLInputElement).value).toBe('未保存的虚构安排');
  fireEvent.click(screen.getByRole('button', { name: '取消', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '放弃修改' }));
  expect(screen.queryByRole('dialog')).toBeNull(); expect(screen.queryByText('日程已保存')).toBeNull();
});

test('pending submission is sent once and cannot be dismissed or edited', async () => {
  const api = fixture([]); let resolve!: (item: CaseCalendarEvent) => void;
  api.create = vi.fn(() => new Promise(done => { resolve = done; }));
  await openCreate(api); fireEvent.change(screen.getByLabelText('日程标题'), { target: { value: '虚构安排' } });
  const form = screen.getByLabelText('日程标题').closest('form')!;
  fireEvent.submit(form); fireEvent.submit(form);
  fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
  expect(api.create).toHaveBeenCalledTimes(1); expect(screen.getByRole('dialog')).toBeTruthy();
  expect(form.querySelector('fieldset')?.disabled).toBe(true);
  await act(async () => resolve({ ...record, title: '虚构安排' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('details and editing preserve original wall date, time, and time zone', async () => {
  const api = fixture(); const dialog = await openDetail(api);
  expect(within(dialog).getByText('2024年3月31日')).toBeTruthy();
  expect(within(dialog).getByText('00:15')).toBeTruthy();
  fireEvent.click(within(dialog).getByRole('button', { name: '编辑日程' }));
  expect((screen.getByLabelText('日期') as HTMLInputElement).value).toBe('2024-03-31');
  expect((screen.getByLabelText('时间') as HTMLInputElement).value).toBe('00:15');
  fireEvent.change(screen.getByLabelText('地点'), { target: { value: '新的模拟法庭' } });
  fireEvent.click(screen.getByRole('button', { name: '保存日程' }));
  await waitFor(() => expect(api.update).toHaveBeenCalledWith('one', expect.objectContaining({ date: '2024-03-31', time: '00:15', timeZone: 'Asia/Shanghai', location: '新的模拟法庭' }), 3));
});

test('conflicting updates keep the draft until explicit reload and then use the latest revision', async () => {
  const api = fixture(); await openDetail(api);
  api.update = vi.fn(async () => { throw new RuntimeApiError('冲突', { code: 'calendar_revision_conflict', retryable: false, status: 409 }); });
  fireEvent.click(screen.getByRole('button', { name: '编辑日程' }));
  fireEvent.change(screen.getByLabelText('日程标题'), { target: { value: '我的未保存修改' } });
  fireEvent.click(screen.getByRole('button', { name: '保存日程' }));
  await screen.findByText('这条日程已被修改或删除。请重新载入后再操作。');
  expect((screen.getByLabelText('日程标题') as HTMLInputElement).value).toBe('我的未保存修改');
  api.list = vi.fn(async () => [{ ...record, title: '已保存的新标题', revision: 4 }]);
  fireEvent.click(screen.getByRole('button', { name: '重新载入日程' }));
  expect((screen.getByLabelText('日程标题') as HTMLInputElement).value).toBe('我的未保存修改');
  fireEvent.click(screen.getByRole('button', { name: '放弃修改并重新载入' }));
  await waitFor(() => expect((screen.getByLabelText('日程标题') as HTMLInputElement).value).toBe('已保存的新标题'));
  api.update = vi.fn(async (id, input, revision) => ({ ...record, ...input, id, revision: revision + 1 }));
  fireEvent.click(screen.getByRole('button', { name: '保存日程' }));
  await waitFor(() => expect(api.update).toHaveBeenLastCalledWith('one', expect.anything(), 4));
});

test('capacity errors remain accurate and do not turn into a deleted-event conflict', async () => {
  const api = fixture([]); api.create = vi.fn(async () => { throw new RuntimeApiError('已达到5000条日程，请先删除不需要的日程。', { code: 'calendar_capacity', retryable: false, status: 409 }); });
  await openCreate(api); fireEvent.change(screen.getByLabelText('日程标题'), { target: { value: '虚构安排' } });
  fireEvent.click(screen.getByRole('button', { name: '保存日程' }));
  await screen.findByText('已达到5000条日程，请先删除不需要的日程。');
  expect(screen.queryByRole('button', { name: '重新载入日程' })).toBeNull();
});

test('delete is confirmed, keeps the entry on failure, and removes only after success', async () => {
  const api = fixture(); await openDetail(api);
  api.remove = vi.fn(async () => { throw new Error('offline'); });
  fireEvent.click(screen.getByRole('button', { name: '删除日程' })); expect(api.remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
  await screen.findByText('日程未能保存或读取，请检查应用连接后重试。');
  expect(screen.getByRole('dialog')).toBeTruthy();
  api.remove = vi.fn(async () => {}); fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(api.remove).toHaveBeenCalledWith('one', 3);
  expect(within(screen.getByLabelText('当天日程')).queryByRole('button', { name: /虚构开庭安排/ })).toBeNull();
});

test('reentering reloads saved data and reminder navigation changes the selected date', async () => {
  const api = fixture(); const view = render(<CaseCalendarView api={api} initialDate="2024-03-31" />);
  await waitFor(() => expect(api.list).toHaveBeenCalledTimes(1));
  view.rerender(<CaseCalendarView api={api} initialDate="2024-02-29" />);
  expect((screen.getByRole('button', { name: '2024年2月29日，0项日程' })).getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByRole('heading', { name: '2024年2月' })).toBeTruthy();
  view.unmount(); render(<CaseCalendarView api={api} initialDate={record.date} />);
  await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2));
  expect(await within(screen.getByLabelText('当天日程')).findByRole('button', { name: /虚构开庭安排/ })).toBeTruthy();
});

test('failed initial read offers refresh without pretending the calendar is empty', async () => {
  const api = fixture(); api.list = vi.fn(async () => { throw new Error('offline'); });
  render(<CaseCalendarView api={api} initialDate={record.date} />);
  await screen.findByText('日程暂时无法读取，请刷新重试。');
  expect(screen.queryByText('这一天还没有日程')).toBeNull();
  api.list = vi.fn(async () => [record]); fireEvent.click(screen.getByRole('button', { name: '刷新日程' }));
  expect(await within(screen.getByLabelText('当天日程')).findByRole('button', { name: /虚构开庭安排/ })).toBeTruthy();
});

test('week navigation shares its anchor, handles cross-year ranges and bounds', () => {
  expect(calendarWeekDays('2024-12-31')).toEqual(['2024-12-30', '2024-12-31', '2025-01-01', '2025-01-02', '2025-01-03', '2025-01-04', '2025-01-05']);
  expect(calendarTitle('2024-12-31', 'week')).toBe('2024年12月30日—2025年1月5日');
  expect(calendarTitle('2024-04-30', 'week')).toBe('2024年4月29日—5月5日');
  expect(navigateCalendar('2024-03-31', 'week', 1)).toBe('2024-04-07');
  expect(navigateCalendar('2024-03-31', 'list', -1)).toBe('2024-02-29');
  expect(navigateCalendar('1900-01-01', 'week', -1)).toBe('1900-01-01');
  expect(navigateCalendar('9999-12-31', 'week', 1)).toBe('9999-12-31');
  expect(calendarTitle('9999-12-31', 'week')).toBe('9999年12月27日—31日');
  expect(calendarMonthDays('9999-12-31').length).toBe(35);
});

test('switching views and jumping dates preserves the selected day and uses weekly steps', async () => {
  render(<CaseCalendarView api={fixture()} initialDate="2024-03-31" />);
  await screen.findByText('本月 1 项日程');
  fireEvent.click(screen.getByRole('button', { name: '周视图', exact: true }));
  expect(screen.getByRole('button', { name: '2024年3月31日，1项日程' }).getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: '下一周' }));
  expect((screen.getByLabelText('跳转日期') as HTMLInputElement).value).toBe('2024-04-07');
  expect(screen.getByRole('heading', { name: '2024年4月1日—7日' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '列表', exact: true }));
  expect(screen.getByRole('heading', { name: '2024年4月' })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('跳转日期'), { target: { value: '2024-03-31' } });
  expect(await within(screen.getByLabelText('本月日程列表')).findByRole('button', { name: /虚构开庭安排/ })).toBeTruthy();
});

test('list includes only the visible month and orders all-day before timed events', async () => {
  const api = fixture([
    { ...record, id: 'late', title: '下午会议', time: '14:00' },
    { ...record, id: 'next', title: '下月安排', date: '2024-04-01' },
    { ...record, id: 'all', title: '全天安排', time: null },
    { ...record, id: 'early', title: '上午开庭', time: '09:00' },
  ]);
  render(<CaseCalendarView api={api} initialDate={record.date} />);
  await screen.findByText('本月 3 项日程');
  fireEvent.click(screen.getByRole('button', { name: '列表', exact: true }));
  const list = within(screen.getByLabelText('本月日程列表'));
  expect(list.queryByText('下月安排')).toBeNull();
  expect(list.getAllByRole('listitem').map(row => row.querySelector('strong')?.textContent)).toEqual(['全天安排', '上午开庭', '下午会议']);
  fireEvent.click(list.getByRole('button', { name: /上午开庭/ }));
  expect(within(screen.getByRole('dialog')).getByText('09:00')).toBeTruthy();
});

test('search and kind filters agree in all views, and an empty result can be cleared', async () => {
  const api = fixture([record, { ...record, id: 'two', title: '材料截止', kind: 'deadline', caseName: '另一虚构案件', location: '另一地点' }]);
  render(<CaseCalendarView api={api} initialDate={record.date} />);
  await screen.findByText('本月 2 项日程');
  fireEvent.change(screen.getByLabelText('按类型筛选'), { target: { value: 'deadline' } });
  fireEvent.change(screen.getByLabelText('搜索日程'), { target: { value: ' 另一地点 ' } });
  expect(screen.getByText('本月匹配 1 / 2 项')).toBeTruthy();
  expect(within(screen.getByLabelText('当天日程')).queryByText('虚构开庭安排')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '周视图', exact: true }));
  expect(screen.getByText('本周匹配 1 / 2 项')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '列表', exact: true }));
  expect(within(screen.getByLabelText('本月日程列表')).getByText('材料截止')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('搜索日程'), { target: { value: '不存在的案件' } });
  expect(screen.getByText('本月没有符合筛选条件的日程')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '显示全部日程' }));
  expect(screen.getByText('本月 2 项日程')).toBeTruthy();
  expect(api.create).not.toHaveBeenCalled(); expect(api.update).not.toHaveBeenCalled();
});

test('weekly quick add uses that day and current kind; saving reveals the result despite search', async () => {
  const api = fixture([]);
  render(<CaseCalendarView api={api} initialDate="2024-03-31" />);
  await screen.findByText('本月 0 项日程');
  fireEvent.click(screen.getByRole('button', { name: '周视图', exact: true }));
  fireEvent.change(screen.getByLabelText('按类型筛选'), { target: { value: 'deadline' } });
  fireEvent.change(screen.getByLabelText('搜索日程'), { target: { value: '旧标题' } });
  fireEvent.click(screen.getByRole('button', { name: '添加2024年3月27日的日程' }));
  expect((screen.getByLabelText('日期') as HTMLInputElement).value).toBe('2024-03-27');
  expect((screen.getByLabelText('类型') as HTMLSelectElement).value).toBe('deadline');
  fireEvent.change(screen.getByLabelText('日程标题'), { target: { value: '虚构举证截止' } });
  fireEvent.click(screen.getByRole('button', { name: '保存日程' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ date: '2024-03-27', kind: 'deadline' }));
  expect((screen.getByLabelText('搜索日程') as HTMLInputElement).value).toBe('');
  expect(within(screen.getByLabelText('当天日程')).getByRole('button', { name: /虚构举证截止/ })).toBeTruthy();
});

test('month overflow opens the full day, and mini events open real details directly', async () => {
  render(<CaseCalendarView api={fixture(Array.from({ length: 5 }, (_, i) => ({ ...record, id: String(i), title: `虚构安排${i}` })))} initialDate={record.date} />);
  fireEvent.click(await screen.findByRole('button', { name: '还有 2 项' }));
  expect(within(screen.getByLabelText('当天日程')).getAllByRole('button', { name: /虚构安排/ })).toHaveLength(5);
  const month = within(screen.getByLabelText('2024年3月月历'));
  fireEvent.click(month.getByRole('button', { name: /虚构安排0/ }));
  expect(within(screen.getByRole('dialog')).getByText('携带副本')).toBeTruthy();
});

test('incoming reminder navigation clears filters so the referenced day is visible', async () => {
  const api = fixture(); const rendered = render(<CaseCalendarView api={api} initialDate="2024-02-29" />);
  await screen.findByText('本月 0 项日程');
  fireEvent.change(screen.getByLabelText('搜索日程'), { target: { value: '不匹配' } });
  fireEvent.change(screen.getByLabelText('按类型筛选'), { target: { value: 'deadline' } });
  rendered.rerender(<CaseCalendarView api={api} initialDate={record.date} />);
  expect(within(screen.getByLabelText('当天日程')).getByRole('button', { name: /虚构开庭安排/ })).toBeTruthy();
});

test('Beijing today crosses UTC midnight independent of the computer timezone', () => {
  expect(beijingToday(new Date('2026-10-12T15:59:00Z'))).toBe('2026-10-12');
  expect(beijingToday(new Date('2026-10-12T16:00:00Z'))).toBe('2026-10-13');
});

test('custom types and exact minutes survive saving, searching and editing', async () => {
  const api = fixture([]); await openCreate(api);
  fireEvent.change(screen.getByLabelText('日程标题'), { target: { value: '虚构材料准备' } });
  fireEvent.change(screen.getByLabelText('类型'), { target: { value: 'other' } });
  fireEvent.change(screen.getByLabelText('具体类型（选填）'), { target: { value: '  阅卷  ' } });
  fireEvent.change(screen.getByLabelText('时间'), { target: { value: '14:35' } });
  fireEvent.click(screen.getByRole('button', { name: '保存日程' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ kind: 'other', customKind: '阅卷', time: '14:35', timeZone: 'Asia/Shanghai' }));
  fireEvent.change(screen.getByLabelText('搜索日程'), { target: { value: '阅卷' } });
  const agenda = within(screen.getByLabelText('当天日程'));
  expect(agenda.getByText('阅卷')).toBeTruthy();
  fireEvent.click(agenda.getByRole('button', { name: /虚构材料准备/ }));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '编辑日程' }));
  expect((screen.getByLabelText('具体类型（选填）') as HTMLInputElement).value).toBe('阅卷');
  expect((screen.getByLabelText('时间') as HTMLInputElement).value).toBe('14:35');
  fireEvent.change(screen.getByLabelText('类型'), { target: { value: 'appeal-deadline' } });
  expect(screen.queryByLabelText('具体类型（选填）')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '保存日程' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(api.update).toHaveBeenCalledWith('created', expect.objectContaining({ kind: 'appeal-deadline', customKind: '' }), 1);
});

test('all-day remains available and disables the explicit time input', async () => {
  const api = fixture([]); await openCreate(api);
  fireEvent.change(screen.getByLabelText('日程标题'), { target: { value: '虚构全天安排' } });
  fireEvent.click(screen.getByLabelText('全天日程（不指定时间）'));
  expect((screen.getByLabelText('时间') as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '保存日程' }));
  await waitFor(() => expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ time: null, timeZone: 'Asia/Shanghai' })));
});
