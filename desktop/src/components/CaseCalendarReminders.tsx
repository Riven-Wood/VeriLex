import { useCallback, useEffect, useRef, useState } from 'react';
import { Bell } from 'lucide-react';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { caseCalendarApi, type CaseCalendarApi, type CaseCalendarEvent } from '../runtime/caseCalendarClient';
import { DetailDialog } from './DetailDialog';
import './CaseCalendarReminders.css';

export function CaseCalendarReminders({ enabled, refreshKey = 0, onOpenDate, api = caseCalendarApi }: {
  enabled: boolean; refreshKey?: number; onOpenDate: (date: string) => void; api?: CaseCalendarApi;
}) {
  const [items, setItems] = useState<CaseCalendarEvent[]>([]);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notificationFailed, setNotificationFailed] = useState(false);
  const generation = useRef(0);
  const mounted = useRef(false);
  const request = useRef<Promise<void> | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  const refresh = useCallback(async () => {
    if (request.current) return request.current;
    const current = generation.current;
    const pending = (async () => {
      try {
        const next = await api.listReminders();
        if (mounted.current && current === generation.current) { setItems(next); setError(''); }
      } catch {
        if (mounted.current && current === generation.current) setError('日程提醒暂时无法更新。');
      }
      if (isTauri()) {
        try {
          const failed = await invoke<boolean>('case_calendar_notification_failed');
          if (mounted.current && current === generation.current) setNotificationFailed(failed);
        } catch { /* An older native host still supports the in-app reminder list. */ }
      }
    })();
    request.current = pending;
    try { await pending; } finally { if (request.current === pending) request.current = null; }
  }, [api]);

  useEffect(() => {
    if (!enabled) return;
    mounted.current = true;
    void refresh();
    const interval = window.setInterval(() => void refresh(), 10000);
    const focus = () => void refresh();
    window.addEventListener('focus', focus);
    return () => {
      mounted.current = false; generation.current += 1; request.current = null;
      window.clearInterval(interval); window.removeEventListener('focus', focus);
    };
  }, [enabled, refresh, refreshKey]);

  async function acknowledge(item: CaseCalendarEvent) {
    if (busy) return;
    setBusy(item.id);
    generation.current += 1;
    try {
      await api.ackReminder(item.id, item.revision);
      if (mounted.current) { setItems(current => current.filter(row => row.id !== item.id)); setError(''); }
    } catch {
      if (mounted.current) setError('未能确认这条提醒，日程可能已修改。请刷新后重试。');
    } finally {
      generation.current += 1;
      if (mounted.current) setBusy(null);
    }
  }

  if (!enabled || (!items.length && !error && !open)) return null;
  return <>
    <aside className="case-reminder-toast" aria-label="案件日程提醒">
      <Bell size={16} aria-hidden="true" />
      <span role="status">{error || `${items.length} 项日程待确认`}</span>
      {error && <button type="button" onClick={() => void refresh()}>重试</button>}
      <button ref={trigger} type="button" onClick={() => setOpen(true)}>查看提醒</button>
    </aside>
    {open && <DetailDialog title="日程提醒" onClose={() => setOpen(false)} busy={Boolean(busy)} returnFocus={trigger.current} className="case-reminder-dialog">
      <div className="case-reminder-body">
      {error && <p role="alert">{error} <button type="button" onClick={() => void refresh()}>刷新</button></p>}
      {notificationFailed && <p className="case-reminder-note">系统通知未能发出，你仍可在这里查看全部待确认提醒。</p>}
      {!items.length && !error && <p className="case-reminder-empty">没有待确认的日程提醒。</p>}
      {items.length > 0 && <ul aria-label="待确认日程">{items.map(item => <li key={item.id}>
        <div><strong>{item.title}</strong><span>{item.date} · {item.time ?? '全天'} · 北京时间</span>{item.caseName && <span>{item.caseName}</span>}</div>
        <div className="case-reminder-actions">
          <button type="button" className="case-reminder-open" disabled={Boolean(busy)} onClick={() => { setOpen(false); onOpenDate(item.date); }}>查看日程</button>
          <button type="button" disabled={Boolean(busy)} onClick={() => void acknowledge(item)}>{busy === item.id ? '正在确认…' : '知道了'}</button>
        </div>
      </li>)}</ul>}
      </div>
      {items.length > 0 && <footer className="case-reminder-footer">确认后不再提醒，日程仍会保留。</footer>}
    </DetailDialog>}
  </>;
}
