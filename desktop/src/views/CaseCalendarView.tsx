import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Bell, CalendarDays, ChevronLeft, ChevronRight, Clock3, MapPin, Plus, RefreshCw, Search, X } from 'lucide-react';
import { DetailDialog } from '../components/DetailDialog';
import { RuntimeApiError } from '../runtime/api';
import { caseCalendarApi, type CaseCalendarApi, type CaseCalendarEvent, type CaseCalendarInput, type CaseCalendarKind, type CaseCalendarReminder } from '../runtime/caseCalendarClient';
import './CaseCalendarView.css';
import { BEIJING_TIME_ZONE, CASE_CALENDAR_KINDS, beijingToday, calendarKindLabel, calendarMonthDays, calendarTitle, calendarWeekDays, dayLabel, navigateCalendar, parseDay, shortDayLabel, sortCalendarEvents, validDay, weekdays, type CalendarView } from './caseCalendarPresentation';
export { calendarMonthDays, moveCalendarMonth } from './caseCalendarPresentation';

export { CASE_CALENDAR_KINDS } from './caseCalendarPresentation';
const reminders: [CaseCalendarReminder, string][] = [[null, '不提醒'], [0, '准时'], [15, '提前 15 分钟'], [60, '提前 1 小时'], [1440, '提前 1 天'], [4320, '提前 3 天'], [10080, '提前 7 天']];
function inputFrom(item: CaseCalendarEvent): CaseCalendarInput {
  return { title: item.title, kind: item.kind, customKind: item.customKind ?? '', date: item.date, time: item.time, timeZone: BEIJING_TIME_ZONE, caseName: item.caseName, location: item.location, notes: item.notes, reminderMinutes: item.reminderMinutes };
}
function failure(error: unknown) {
  if (isConflict(error)) return '这条日程已被修改或删除。请重新载入后再操作。';
  if (error instanceof RuntimeApiError) return error.message;
  return '日程未能保存或读取，请检查应用连接后重试。';
}
function isConflict(error: unknown) {
  return error instanceof RuntimeApiError && ['calendar_revision_conflict', 'calendar_event_not_found'].includes(error.code);
}
type Editor = { record: CaseCalendarEvent | null; value: CaseCalendarInput; original: string };

export function CaseCalendarView({ api = caseCalendarApi, initialDate, onEventsChanged }: {
  api?: CaseCalendarApi; initialDate?: string; onEventsChanged?: () => void;
}) {
  const [selectedDate, setSelectedDate] = useState(() => validDay(initialDate) ? initialDate : beijingToday());
  const [view, setView] = useState<CalendarView>('month');
  const [kind, setKind] = useState<CaseCalendarKind | 'all'>('all');
  const [query, setQuery] = useState('');
  const [today, setToday] = useState(() => beijingToday());
  const [items, setItems] = useState<CaseCalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [status, setStatus] = useState('');
  const [detailId, setDetailId] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [discardAction, setDiscardAction] = useState<'close' | 'reload' | null>(null);
  const [conflict, setConflict] = useState(false);
  const actionLock = useRef(false);
  const generation = useRef(0);
  const mounted = useRef(false);
  const trigger = useRef<HTMLElement | null>(null);

  const refresh = useCallback(async () => {
    const request = ++generation.current;
    setLoading(true);
    try {
      const next = await api.list();
      if (mounted.current && request === generation.current) { setItems(next); setLoadError(''); }
    } catch { if (mounted.current && request === generation.current) setLoadError('日程暂时无法读取，请刷新重试。'); }
    finally { if (mounted.current && request === generation.current) setLoading(false); }
  }, [api]);
  useEffect(() => { mounted.current = true; void refresh(); return () => { mounted.current = false; generation.current += 1; }; }, [refresh]);
  useEffect(() => { if (validDay(initialDate)) { setSelectedDate(initialDate); setQuery(''); setKind('all'); } }, [initialDate]);
  useEffect(() => {
    const updateToday = () => setToday(beijingToday());
    const timer = window.setInterval(updateToday, 60_000);
    window.addEventListener('focus', updateToday);
    return () => { window.clearInterval(timer); window.removeEventListener('focus', updateToday); };
  }, []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return sortCalendarEvents(items.filter(item => (kind === 'all' || item.kind === kind)
      && (!needle || [item.title, calendarKindLabel(item), item.caseName, item.location, item.notes].some(text => text.toLocaleLowerCase().includes(needle)))));
  }, [items, kind, query]);
  const isFiltered = kind !== 'all' || Boolean(query.trim());
  const byDate = useMemo(() => {
    const grouped = new Map<string, CaseCalendarEvent[]>();
    filtered.forEach(item => grouped.set(item.date, [...(grouped.get(item.date) ?? []), item]));
    return grouped;
  }, [filtered]);
  const days = useMemo(() => view === 'week' ? calendarWeekDays(selectedDate) : calendarMonthDays(selectedDate), [selectedDate, view]);
  const inPeriod = (item: CaseCalendarEvent) => view === 'week' ? days.includes(item.date) : item.date.slice(0, 7) === selectedDate.slice(0, 7);
  const periodItems = filtered.filter(inPeriod);
  const periodCount = items.filter(inPeriod).length;
  const listDays = [...new Set(periodItems.map(item => item.date))];
  const dayItems = byDate.get(selectedDate) ?? [];
  const detail = items.find(item => item.id === detailId);
  const period = view === 'week' ? '本周' : '本月';
  const title = calendarTitle(selectedDate, view);
  const canCreate = !loading && !loadError;
  function clearFilters() { setKind('all'); setQuery(''); }
  function showDetail(item: CaseCalendarEvent, source: HTMLElement) {
    trigger.current = source; setSelectedDate(item.date); setDetailId(item.id);
    setActionError(''); setConflict(false); setConfirmDelete(false);
  }
  function createOnDate(day: string, source: HTMLElement) {
    trigger.current = source; openEditor(undefined, day);
  }

  function openEditor(record?: CaseCalendarEvent, day = selectedDate) {
    const value = record ? inputFrom(record) : { title: '', kind: kind === 'all' ? 'hearing' as const : kind, customKind: '', date: day, time: '09:00', timeZone: BEIJING_TIME_ZONE, caseName: '', location: '', notes: '', reminderMinutes: null };
    setEditor({ record: record ?? null, value, original: JSON.stringify(value) });
    setActionError(''); setDiscardAction(null); setConflict(false); setConfirmDelete(false);
  }
  function change<K extends keyof CaseCalendarInput>(key: K, value: CaseCalendarInput[K]) {
    setEditor(current => current ? { ...current, value: { ...current.value, [key]: value } } : null);
    setDiscardAction(null);
  }
  function closeDialog() {
    if (actionLock.current) return;
    if (editor && JSON.stringify(editor.value) !== editor.original) { setDiscardAction('close'); return; }
    setEditor(null); setDetailId(null); setActionError(''); setConfirmDelete(false); setDiscardAction(null);
  }
  function abandon() { setEditor(null); setDetailId(null); setActionError(''); setDiscardAction(null); }
  function changed() { onEventsChanged?.(); }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!editor || actionLock.current || conflict) return;
    if (!editor.value.title.trim() || !validDay(editor.value.date)) { setActionError('请填写日程标题和有效日期。'); return; }
    actionLock.current = true; setBusy(true); setActionError('');
    generation.current += 1;
    const value = { ...editor.value, title: editor.value.title.trim(), customKind: editor.value.kind === 'other' ? editor.value.customKind?.trim() ?? '' : '', timeZone: BEIJING_TIME_ZONE };
    try {
      const saved = editor.record ? await api.update(editor.record.id, value, editor.record.revision) : await api.create(value);
      if (!mounted.current) return;
      setItems(current => [...current.filter(item => item.id !== saved.id), saved]);
      setSelectedDate(saved.date); clearFilters(); setEditor(null); setDetailId(null); setStatus('日程已保存'); setLoading(false); changed();
    } catch (error) {
      if (mounted.current) { setActionError(failure(error)); setConflict(isConflict(error)); }
    } finally { actionLock.current = false; if (mounted.current) { setBusy(false); setLoading(false); } }
  }
  async function remove(item: CaseCalendarEvent) {
    if (actionLock.current) return;
    actionLock.current = true; setBusy(true); setActionError(''); generation.current += 1;
    try {
      await api.remove(item.id, item.revision);
      if (!mounted.current) return;
      setItems(current => current.filter(row => row.id !== item.id)); setDetailId(null); setConfirmDelete(false); setStatus('日程已删除'); changed();
    } catch (error) { if (mounted.current) { setActionError(failure(error)); setConflict(isConflict(error)); } }
    finally { actionLock.current = false; if (mounted.current) { setBusy(false); setLoading(false); } }
  }
  async function reloadEditor() {
    if (actionLock.current) return;
    actionLock.current = true; setBusy(true); setActionError('');
    try {
      const next = await api.list();
      if (!mounted.current) return;
      generation.current += 1; setItems(next); setLoadError(''); setLoading(false);
      const record = next.find(item => item.id === (editor?.record?.id ?? detailId));
      if (!record) { abandon(); setStatus('这条日程已删除，列表已更新'); }
      else if (editor) openEditor(record);
      else { setConflict(false); setConfirmDelete(false); }
    } catch { if (mounted.current) setActionError('重新载入失败，原有填写内容仍保留，请重试。'); }
    finally { actionLock.current = false; if (mounted.current) setBusy(false); }
  }

  return <section className="case-calendar-page" aria-label="案件日程">
    <header className="case-calendar-heading"><div><h1>案件日程</h1><p>北京时间 · 安排开庭、期限和办案事项</p></div><button className="case-calendar-primary" disabled={!canCreate} onClick={event => createOnDate(selectedDate, event.currentTarget)}><Plus size={16} aria-hidden="true" />新建日程</button></header>
    {loadError && <div className="case-calendar-error" role="alert">{loadError}</div>}
    <div className="case-calendar-toolbar">
      <div className="case-calendar-navigation">
        <button aria-label={view === 'week' ? '上一周' : '上个月'} disabled={view === 'week' ? days[0] <= '1900-01-01' : selectedDate.slice(0, 7) === '1900-01'} onClick={() => setSelectedDate(navigateCalendar(selectedDate, view, -1))}><ChevronLeft size={17} /></button>
        <h2>{title}</h2>
        <button aria-label={view === 'week' ? '下一周' : '下个月'} disabled={view === 'week' ? days.some(day => !validDay(day) && parseDay(day) > parseDay(selectedDate)) || days[6] === '9999-12-31' : selectedDate.slice(0, 7) === '9999-12'} onClick={() => setSelectedDate(navigateCalendar(selectedDate, view, 1))}><ChevronRight size={17} /></button>
        <button onClick={() => setSelectedDate(beijingToday())}>今天</button>
      </div>
      <div className="case-calendar-view-switch" role="group" aria-label="日历视图">
        {([['month', '月视图'], ['week', '周视图'], ['list', '列表']] as const).map(([value, label]) => <button key={value} aria-pressed={view === value} onClick={() => setView(value)}>{label}</button>)}
      </div>
    </div>
    <div className="case-calendar-filters">
      <label className="case-calendar-type-filter">筛选<select aria-label="按类型筛选" value={kind} onChange={event => setKind(event.target.value as CaseCalendarKind | 'all')}><option value="all">全部类型</option>{Object.entries(CASE_CALENDAR_KINDS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <div className="case-calendar-search"><Search size={14} aria-hidden="true" /><input type="search" aria-label="搜索日程" placeholder={`搜索${period}标题、案件、地点`} value={query} onChange={event => setQuery(event.target.value)} />{query && <button aria-label="清空搜索" onClick={() => setQuery('')}><X size={13} /></button>}</div>
    </div>
    <div className="case-calendar-range-info">
      <span aria-live="polite">{loading ? '正在读取…' : loadError ? '日程读取失败' : isFiltered ? `${period}匹配 ${periodItems.length} / ${periodCount} 项` : `${period} ${periodCount} 项日程`}{isFiltered && <button onClick={clearFilters}>清除筛选</button>}</span>
      <div><label className="case-calendar-jump">跳转日期<input aria-label="跳转日期" type="date" min="1900-01-01" max="9999-12-31" value={selectedDate} onChange={event => { if (validDay(event.target.value)) setSelectedDate(event.target.value); }} /></label><button disabled={loading} onClick={() => void refresh()} aria-label="刷新日程" title="刷新日程"><RefreshCw size={14} /></button></div>
    </div>
    <div className={`case-calendar-layout view-${view}`} aria-busy={loading}>
      {view === 'list' ? <div className="case-calendar-list" aria-label="本月日程列表">
        {listDays.map(day => <section key={day} className="case-calendar-list-day">
          <button className="case-calendar-list-date" aria-pressed={selectedDate === day} onClick={() => setSelectedDate(day)}><strong>{shortDayLabel(day)}</strong><span>星期{weekdays[(parseDay(day).getDay() + 6) % 7]}{day === today ? ' · 今天' : ''}</span><span>{byDate.get(day)!.length} 项</span></button>
          <ul>{byDate.get(day)!.map(item => <li key={item.id}><button className="case-calendar-list-item" onClick={event => showDetail(item, event.currentTarget)}><time>{item.time ?? '全天'}</time><span className={`case-calendar-kind kind-${item.kind}`}>{calendarKindLabel(item)}</span><span className="case-calendar-list-content"><strong>{item.title}</strong>{(item.caseName || item.location) && <span>{[item.caseName, item.location].filter(Boolean).join(' · ')}</span>}</span>{item.reminderMinutes !== null && <Bell size={14} aria-label={reminders.find(([minutes]) => minutes === item.reminderMinutes)?.[1]} />}<ChevronRight size={14} aria-hidden="true" /></button></li>)}</ul>
        </section>)}
        {!listDays.length && <div className="case-calendar-empty"><CalendarDays size={28} aria-hidden="true" /><p>{loading ? '正在读取日程…' : loadError ? '刷新后查看日程' : isFiltered ? '本月没有符合筛选条件的日程' : '本月还没有日程'}</p>{!loading && !loadError && (isFiltered ? <button onClick={clearFilters}>清除筛选</button> : <button onClick={event => createOnDate(selectedDate, event.currentTarget)}>添加日程</button>)}</div>}
      </div> : <div className={`case-calendar-month ${view === 'week' ? 'case-calendar-week' : ''}`} aria-label={`${title}${view === 'week' ? '周历' : '月历'}`}>
        <div className="case-calendar-weekdays" aria-hidden="true">{weekdays.map(day => <span key={day}>周{day}</span>)}</div>
        <div className="case-calendar-days">{days.map(day => {
          const events = byDate.get(day) ?? [];
          return <div key={day} className={`case-calendar-day ${selectedDate === day ? 'is-selected' : ''} ${day.slice(0, 7) !== selectedDate.slice(0, 7) && view === 'month' ? 'is-outside' : ''}`}>
            <button className="case-calendar-day-select" disabled={!validDay(day)} aria-label={`${dayLabel(day)}，${events.length}项日程`} aria-pressed={selectedDate === day} aria-current={day === today ? 'date' : undefined} onClick={() => setSelectedDate(day)}><span className="case-calendar-day-number">{Number(day.slice(-2))}</span>{day === today && <span className="case-calendar-today-label">今天</span>}{view === 'week' && day.slice(-2) === '01' && <span className="case-calendar-today-label">{Number(day.slice(5, 7))}月</span>}</button>
            <div className="case-calendar-day-events">{(view === 'week' ? events : events.slice(0, 3)).map(item => <button key={item.id} className={`case-calendar-mini kind-${item.kind}`} title={`${calendarKindLabel(item)} · ${item.time ?? '全天'} · ${item.title}`} onClick={event => showDetail(item, event.currentTarget)}><span className="case-calendar-mini-time">{item.time ?? '全天'}</span><span>{item.title}</span>{view === 'week' && <span className="case-calendar-mini-meta">{calendarKindLabel(item)}{item.caseName ? ` · ${item.caseName}` : ''}</span>}</button>)}{view === 'month' && events.length > 3 && <button className="case-calendar-more" onClick={() => setSelectedDate(day)}>还有 {events.length - 3} 项</button>}</div>
            {view === 'week' && validDay(day) && <button className="case-calendar-week-add" aria-label={`添加${dayLabel(day)}的日程`} disabled={!canCreate} onClick={event => createOnDate(day, event.currentTarget)}><Plus size={13} /><span>添加</span></button>}
          </div>;
        })}</div>
      </div>}
      <aside className="case-calendar-agenda" aria-label="当天日程"><header><div><h2>{shortDayLabel(selectedDate)}</h2><span>星期{weekdays[(parseDay(selectedDate).getDay() + 6) % 7]}{selectedDate === today ? ' · 今天' : ''} · {dayItems.length} 项{isFiltered ? '符合筛选' : ''}</span></div><button aria-label="添加当日日程" title="添加当日日程" disabled={!canCreate} onClick={event => createOnDate(selectedDate, event.currentTarget)}><Plus size={16} /></button></header>
        {dayItems.length ? <ul>{dayItems.map(item => <li key={item.id}><button className="case-calendar-agenda-item" onClick={event => showDetail(item, event.currentTarget)}><span className="case-calendar-agenda-meta"><span className={`case-calendar-kind kind-${item.kind}`}>{calendarKindLabel(item)}</span><span><Clock3 size={12} />{item.time ?? '全天'}</span></span><strong>{item.title}</strong>{item.caseName && <span className="case-calendar-case-name">{item.caseName}</span>}{item.location && <span><MapPin size={13} />{item.location}</span>}<span className="case-calendar-reminder-label"><Bell size={12} />{reminders.find(([minutes]) => minutes === item.reminderMinutes)?.[1]}{item.reminderAcknowledgedAt ? ' · 已确认' : ''}</span>{item.notes && <span className="case-calendar-note-preview">{item.notes}</span>}</button></li>)}</ul> : !loading && !loadError ? <div className="case-calendar-empty"><CalendarDays size={26} aria-hidden="true" /><p>{isFiltered ? '当天没有符合筛选条件的日程' : '这一天还没有日程'}</p>{isFiltered ? <button onClick={clearFilters}>显示全部日程</button> : <button onClick={event => createOnDate(selectedDate, event.currentTarget)}>添加当天安排</button>}</div> : <p className="case-calendar-empty-note">{loading ? '正在读取日程…' : '刷新后查看当天安排'}</p>}
      </aside>
    </div>
    <footer className="case-calendar-footnote"><p>应用运行时提醒；退出或休眠期间到期，重新打开或唤醒后补提醒。</p><span role="status">{status}</span></footer>
    {(editor || detail) && <DetailDialog title={editor ? editor.record ? '编辑日程' : '新建日程' : '日程详情'} busy={busy} returnFocus={trigger.current} onClose={closeDialog} className="case-calendar-dialog">
      {editor ? <form className="case-calendar-form" onSubmit={event => void save(event)}>
        <fieldset disabled={busy}>
          <label>日程标题<input autoFocus required maxLength={120} value={editor.value.title} onChange={event => change('title', event.target.value)} placeholder="例如：证据提交截止" /></label>
          <div className="case-calendar-form-row"><label>类型<select value={editor.value.kind} onChange={event => change('kind', event.target.value as CaseCalendarKind)}>{Object.entries(CASE_CALENDAR_KINDS).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select></label>{editor.value.kind === 'other' && <label>具体类型（选填）<input maxLength={40} value={editor.value.customKind ?? ''} onChange={event => change('customKind', event.target.value)} placeholder="例如：阅卷、法律培训" /></label>}</div>
          <div className="case-calendar-form-row"><label>日期<input type="date" min="1900-01-01" max="9999-12-31" required value={editor.value.date} onChange={event => change('date', event.target.value)} /></label><label>时间（北京时间）<input aria-label="时间" type="time" step={60} disabled={editor.value.time === null} required={editor.value.time !== null} value={editor.value.time ?? ''} onChange={event => change('time', event.target.value)} /></label></div>
          <label className="case-calendar-check"><input type="checkbox" checked={editor.value.time === null} onChange={event => change('time', event.target.checked ? null : '09:00')} />全天日程（不指定时间）</label>
          <label>提醒<select value={editor.value.reminderMinutes ?? 'none'} onChange={event => change('reminderMinutes', event.target.value === 'none' ? null : Number(event.target.value) as CaseCalendarReminder)}>{reminders.map(([minutes, label]) => <option key={minutes ?? 'none'} value={minutes ?? 'none'}>{label}</option>)}</select></label>
          <p className="case-calendar-hint">统一使用北京时间（UTC+8），精确到分钟。{editor.value.time === null && editor.value.reminderMinutes !== null ? '全天日程按当天 09:00 计算提醒时间。' : ''}</p>
          <details className="case-calendar-extra" open={Boolean(editor.value.caseName || editor.value.location || editor.value.notes) || undefined}><summary>案件名称、地点和备注（选填）</summary><div><label>案件名称<input maxLength={200} value={editor.value.caseName} onChange={event => change('caseName', event.target.value)} /></label><label>地点<input maxLength={300} value={editor.value.location} onChange={event => change('location', event.target.value)} /></label><label>备注<textarea rows={3} maxLength={5000} value={editor.value.notes} onChange={event => change('notes', event.target.value)} /></label></div></details>
        </fieldset>
        {actionError && <div className="case-calendar-error" role="alert">{actionError}{conflict && <button type="button" disabled={busy} onClick={() => setDiscardAction('reload')}>重新载入日程</button>}</div>}
        {discardAction && <div className="case-calendar-confirm" role="alert"><p>{discardAction === 'reload' ? '重新载入会放弃本次尚未保存的修改。' : '本次填写尚未保存，要放弃吗？'}</p><button type="button" disabled={busy} onClick={() => discardAction === 'reload' ? void reloadEditor() : abandon()}>{discardAction === 'reload' ? '放弃修改并重新载入' : '放弃修改'}</button><button type="button" disabled={busy} onClick={() => setDiscardAction(null)}>继续编辑</button></div>}
        <footer><button type="button" disabled={busy} onClick={closeDialog}>取消</button><button type="submit" className="case-calendar-primary" disabled={busy || conflict || !editor.value.title.trim()}>{busy ? '正在保存…' : '保存日程'}</button></footer>
      </form> : detail && <div className="case-calendar-detail"><span className={`case-calendar-kind kind-${detail.kind}`}>{calendarKindLabel(detail)}</span><h3>{detail.title}</h3><dl><div><dt>日期</dt><dd>{dayLabel(detail.date)}</dd></div><div><dt>时间</dt><dd>{detail.time ?? '全天'}</dd></div><div><dt>时区</dt><dd>北京时间（UTC+8）</dd></div><div><dt>提醒</dt><dd>{reminders.find(([minutes]) => minutes === detail.reminderMinutes)?.[1]}{detail.time === null && detail.reminderMinutes !== null ? '（按当天 09:00 计算）' : ''}</dd></div>{detail.caseName && <div><dt>案件名称</dt><dd>{detail.caseName}</dd></div>}{detail.location && <div><dt>地点</dt><dd>{detail.location}</dd></div>}{detail.notes && <div><dt>备注</dt><dd className="case-calendar-notes">{detail.notes}</dd></div>}</dl>
        {actionError && <div className="case-calendar-error" role="alert">{actionError}{conflict && <button type="button" disabled={busy} onClick={() => void reloadEditor()}>重新载入日程</button>}</div>}
        {confirmDelete && <div className="case-calendar-confirm" role="alert"><p>删除「{detail.title}」？此操作无法撤销，相关提醒也会取消。</p><button className="case-calendar-danger" disabled={busy || conflict} onClick={() => void remove(detail)}>{busy ? '正在删除…' : '确认删除'}</button><button disabled={busy} onClick={() => setConfirmDelete(false)}>取消删除</button></div>}
        <footer><button className="case-calendar-danger" disabled={busy || conflict} onClick={() => setConfirmDelete(true)}>删除日程</button><button className="case-calendar-primary" disabled={busy || conflict} onClick={() => openEditor(detail)}>编辑日程</button></footer>
      </div>}
    </DetailDialog>}
  </section>;
}
