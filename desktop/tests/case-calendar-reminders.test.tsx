import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { CaseCalendarReminders } from '../src/components/CaseCalendarReminders';
import type { CaseCalendarApi, CaseCalendarEvent } from '../src/runtime/caseCalendarClient';

Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.setAttribute('open', ''); } });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const item = {id:'one', revision:3, title:'虚构开庭安排',kind:'hearing',date:'2026-10-06',time:'09:30',timeZone:'Asia/Shanghai',caseName:'虚构案件',location:'',notes:'',reminderMinutes:0,reminderAt:'2026-10-06T01:30:00Z',reminderAcknowledgedAt:null,createdAt:'2026-10-05T00:00:00Z',updatedAt:'2026-10-05T00:00:00Z'} as CaseCalendarEvent;
function fixture() {return {listReminders:vi.fn(async () => [item]),ackReminder:vi.fn(async () => ({...item,reminderAcknowledgedAt:'2026-10-06T01:31:00Z'}))} as unknown as CaseCalendarApi;}

test('opens the matching calendar date and acknowledges only after persistence succeeds', async () => {
  const api = fixture(); const open = vi.fn();
  render(<CaseCalendarReminders enabled api={api} onOpenDate={open} />);
  fireEvent.click(await screen.findByRole('button',{name:'查看提醒'}));
  fireEvent.click(screen.getByRole('button',{name:'查看日程'}));
  expect(open).toHaveBeenCalledWith('2026-10-06');
  fireEvent.click(screen.getByRole('button',{name:'查看提醒'}));
  fireEvent.click(screen.getByRole('button',{name:'知道了'}));
  await screen.findByText('没有待确认的日程提醒。');
  expect(api.ackReminder).toHaveBeenCalledWith('one',3);
});

test('failed acknowledgement keeps the reminder and a retry path',async () => {
  const api=fixture(); api.ackReminder=vi.fn(async()=>{throw new Error('conflict');});
  render(<CaseCalendarReminders enabled api={api} onOpenDate={()=>{}} />);
  fireEvent.click(await screen.findByRole('button',{name:'查看提醒'}));
  fireEvent.click(screen.getByRole('button',{name:'知道了'}));
  await screen.findByRole('alert'); expect(screen.getByText(item.title)).toBeTruthy();
  expect(screen.getByRole('button',{name:'刷新'})).toBeTruthy();
});

test('a refresh after editing or deleting removes the stale reminder',async () => {
  const api=fixture(); const props={enabled:true,api,onOpenDate:()=>{}};
  const view=render(<CaseCalendarReminders {...props} refreshKey={0}/>);
  await screen.findByText('1 项日程待确认');
  api.listReminders=vi.fn(async()=>[]);
  view.rerender(<CaseCalendarReminders {...props} refreshKey={1}/>);
  await waitFor(()=>expect(screen.queryByLabelText('案件日程提醒')).toBeNull());
});

test('an older list response cannot undo a successful acknowledgement',async () => {
  const api=fixture();
  render(<CaseCalendarReminders enabled api={api} onOpenDate={()=>{}} />);
  fireEvent.click(await screen.findByRole('button',{name:'查看提醒'}));
  let complete!: (events:CaseCalendarEvent[]) => void;
  api.listReminders=vi.fn(()=>new Promise(resolve=>{complete=resolve;}));
  fireEvent(window,new Event('focus'));
  fireEvent.click(screen.getByRole('button',{name:'知道了'}));
  await screen.findByText('没有待确认的日程提醒。');
  await act(async()=>complete([item]));
  expect(screen.queryByText(item.title)).toBeNull();
});
