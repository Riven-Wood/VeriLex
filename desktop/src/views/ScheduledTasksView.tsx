import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Plus, Play, ArrowLeft, CalendarClock } from 'lucide-react';
import { scheduledApi, type ScheduledApi, type ScheduledTask, type ScheduledTaskInput, type ScheduledRun } from '../runtime/scheduledClient';
import { knowledgeApi, type KnowledgeLibrary } from '../runtime/knowledgeClient';
import { listWorkspaces } from '../runtime/api';
import type { WorkspaceRecord } from '../runtime/contracts';
import { DetailDialog } from '../components/DetailDialog';
import './ScheduledTasksView.css';
const weekdays = ['周一','周二','周三','周四','周五','周六','周日'];
const statusText: Record<string,string> = {running:'运行中', succeeded:'成功', failed:'失败', interrupted:'中断'};
const triggers = {manual:'手动',catchup:'补跑',schedule:'定时'};
const when = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN') : '—';
const timeLabel = (task: ScheduledTask) => task.schedule.kind === 'once' ? `单次 · ${when(task.schedule.fireAt ?? null)}` : `${task.schedule.kind === 'daily' ? '每天' : `每${weekdays[task.schedule.weekday ?? 0]}`} ${task.schedule.timeOfDay}`;
const message = (e: unknown) => e instanceof Error ? e.message : '操作失败，请重试。';
interface Props { api?: ScheduledApi; defaultProvider: string; defaultModel: string; onOpenRun: (id: string) => void | Promise<void>; runView?: ReactNode; onCloseRun?: () => void; }
export function ScheduledTasksView({api=scheduledApi, defaultProvider, defaultModel, onOpenRun, runView, onCloseRun}: Props) {
  const [tasks,setTasks] = useState<ScheduledTask[]>([]);
  const [selected,setSelected] = useState<string | null>(null);
  const [runs,setRuns] = useState<ScheduledRun[]>([]);
  const [form,setForm] = useState<ScheduledTaskInput | null>(null);
  const [editing,setEditing] = useState<string | null>(null);
  const [error,setError] = useState('');
  const [actionError,setActionError] = useState('');
  const [historyError,setHistoryError] = useState('');
  const [loading,setLoading] = useState(true);
  const [busy,setBusy] = useState(false);
  const [confirmDelete,setConfirmDelete] = useState(false);
  const [workspaces,setWorkspaces] = useState<WorkspaceRecord[]>([]);
  const [libraries,setLibraries] = useState<KnowledgeLibrary[]>([]);
  const [optionsError,setOptionsError] = useState('');
  const [revision,setRevision] = useState(0);
  const generation = useRef(0);
  const formTrigger = useRef<HTMLButtonElement | null>(null);
  const refresh = useCallback(() => setRevision(x=>x+1), []);
  useEffect(() => {
    let active=true; const current=++generation.current;
    api.list().then(items=>{if(active && current===generation.current){setTasks(items);setError('');}}).catch(e=>{if(active)setError(message(e));}).finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[api,revision]);
  useEffect(() => {
    let active=true; setRuns([]);setHistoryError('');setConfirmDelete(false);
    if(selected) api.runs(selected).then(items=>{if(active)setRuns(items);}).catch(e=>{if(active)setHistoryError(message(e));});
    return ()=>{active=false;};
  },[api,selected,revision]);
  useEffect(() => {
    // Runs can begin while this page is open, even when previously idle.
    const timer=window.setInterval(refresh,5000);
    window.addEventListener('focus',refresh);
    return ()=>{window.clearInterval(timer);window.removeEventListener('focus',refresh);};
  },[refresh]);
  async function act(action:()=>Promise<unknown>, onSuccess?:()=>void) {
    setBusy(true);setActionError('');
    try {await action();refresh();onSuccess?.();} catch(e){setActionError(message(e));} finally{setBusy(false);}
  }
  async function options() {
    try {const [w,l]=await Promise.all([listWorkspaces(),knowledgeApi.list()]);setWorkspaces(w);setLibraries(l.filter(x=>x.kind!=='ima'));setOptionsError('');} catch(e){setOptionsError(message(e));}
  }
  function openForm(trigger: HTMLButtonElement, task?: ScheduledTask) {
    formTrigger.current = trigger;
    setEditing(task?.id ?? null);setActionError('');
    setForm(task ? {name:task.name,instruction:task.instruction,schedule:task.schedule,providerId:task.providerId,model:task.model,workspaceId:task.workspaceId,knowledgeLibraryIds:task.knowledgeLibraryIds,maxMinutes:task.maxMinutes} : {name:'',instruction:'',schedule:{kind:'daily',timeOfDay:'09:00'},providerId:defaultProvider,model:defaultModel,workspaceId:null,knowledgeLibraryIds:[],maxMinutes:15});
  }
  const [query,setQuery] = useState('');
  const completed = (item: ScheduledTask) => item.schedule.kind==='once' && (item.scheduleRunCount ?? item.runCount)>0 && item.lastStatus!=='running';
  const groups = [
    {label:'当前', items:tasks.filter(item=>item.enabled && !completed(item))},
    {label:'已停用', items:tasks.filter(item=>!item.enabled && !completed(item))},
    {label:'已完成', items:tasks.filter(completed)},
  ];
  const task=tasks.find(x=>x.id===selected);
  if(runView) return <section className="scheduled-run-view"><button className="scheduled-back" onClick={onCloseRun}><ArrowLeft size={16}/> 返回定时任务</button>{runView}</section>;
  return <section className="scheduled-page" aria-label="定时任务" aria-busy={busy || loading}>
    <header><div><h1>定时任务</h1><p>应用打开时自动执行；错过的任务会在下次打开后补跑一次。</p></div><button className="scheduled-primary" onClick={event=>openForm(event.currentTarget)}><Plus size={16}/> 新建定时任务</button></header>
    {!form && actionError && <div role="alert" className="scheduled-error">{actionError}</div>}
    {error && <div role="alert" className="scheduled-error">{error} <button disabled={busy} onClick={refresh}>刷新</button></div>}
    {form && <DetailDialog returnFocus={formTrigger.current} title={editing ? "编辑任务" : "新建定时任务"} busy={busy} onClose={()=>setForm(null)} className="scheduled-dialog"><form className="scheduled-form" onSubmit={e=>{e.preventDefault();void act(async()=>{const saved=editing?await api.update(editing,form):await api.create(form);setSelected(saved.id);},()=>setForm(null));}}>
      {actionError && <div role="alert" className="scheduled-error">{actionError}</div>}
      <label>任务名称<input maxLength={40} value={form.name} placeholder="选填，默认使用指令前20字" onChange={e=>setForm({...form,name:e.target.value})}/></label>
      <label>任务指令<textarea required maxLength={2000} rows={4} autoFocus placeholder="例如：整理所选知识库中的本周工作要点" value={form.instruction} onChange={e=>setForm({...form,instruction:e.target.value})}/></label>
      <fieldset><legend>运行时间</legend><div className="scheduled-time-tabs">{(['daily','weekly','once'] as const).map((kind,i)=><button type="button" key={kind} aria-pressed={form.schedule.kind===kind} onClick={()=>setForm({...form,schedule:kind==='once'?{kind,fireAt:''}:{kind,timeOfDay:'09:00',...(kind==='weekly'?{weekday:0}:{})}})}>{['每天','每周','单次'][i]}</button>)}</div>
        <div className="scheduled-time-inputs">{form.schedule.kind==='weekly' && <label>星期<select value={form.schedule.weekday ?? 0} onChange={e=>setForm({...form,schedule:{...form.schedule,weekday:Number(e.target.value)}})}>{weekdays.map((d,i)=><option key={d} value={i}>{d}</option>)}</select></label>}
        {form.schedule.kind==='once'?<label>日期和时间<input type="datetime-local" required value={form.schedule.fireAt ? localDate(form.schedule.fireAt):''} onChange={e=>setForm({...form,schedule:{kind:'once',fireAt:e.target.value ? new Date(e.target.value).toISOString():''}})}/></label>:<label>时间<input type="time" required value={form.schedule.timeOfDay ?? ''} onChange={e=>setForm({...form,schedule:{...form.schedule,timeOfDay:e.target.value}})}/></label>}</div>
      </fieldset>
      <details onToggle={e=>{if(e.currentTarget.open)void options();}}><summary>高级选项</summary><div className="scheduled-options">
        <label>工作区<select value={form.workspaceId ?? ''} onChange={e=>setForm({...form,workspaceId:e.target.value || null})}><option value="">不绑定工作区</option>{form.workspaceId && !workspaces.some(w=>w.id===form.workspaceId) && <option value={form.workspaceId}>已绑定工作区</option>}{workspaces.map(w=><option key={w.id} value={w.id}>{w.display_name}</option>)}</select></label>
        <fieldset><legend>本地知识库</legend>{libraries.length===0 && <span>暂无本地知识库</span>}{libraries.map(l=><label className="scheduled-check" key={l.id}><input type="checkbox" checked={form.knowledgeLibraryIds.includes(l.id)} onChange={e=>setForm({...form,knowledgeLibraryIds:e.target.checked?[...form.knowledgeLibraryIds,l.id]:form.knowledgeLibraryIds.filter(id=>id!==l.id)})}/>{l.name}</label>)}</fieldset>
        <label>运行时长上限（分钟）<input type="number" min={5} max={60} required value={form.maxMinutes} onChange={e=>setForm({...form,maxMinutes:Number(e.target.value)})}/></label>
        <p>模型：{form.model || '尚未配置，请先到设置添加模型'}</p>{optionsError && <p role="alert">{optionsError}</p>}
      </div></details>
      <p className="scheduled-note">使用所选模型的额度。仅执行只读操作，不运行命令、写入文件或调用插件。</p>
      <footer><button type="button" disabled={busy} onClick={()=>setForm(null)}>取消</button><button className="scheduled-primary" disabled={busy || !form.providerId || !form.model || !form.instruction.trim()} type="submit">{busy?'正在保存…':'保存任务'}</button></footer>
    </form></DetailDialog>}
    {loading ? <p role="status">正在加载…</p> : tasks.length===0 && !error ? <div className="scheduled-empty"><CalendarClock size={32}/><h2>把重复的工作交给定时任务</h2><p>写下要整理的内容，选择每天、每周或单次运行。<br/>结果会保存在任务的运行记录中。</p><div className="scheduled-empty-steps"><span>写下任务指令</span><span>设定运行时间</span><span>查看运行结果</span></div></div> : <><div className="scheduled-toolbar"><span><CalendarClock size={16}/> 全部任务 · {tasks.length}</span><input aria-label="搜索定时任务" placeholder="搜索定时任务" value={query} onChange={e=>setQuery(e.target.value)}/></div><div className={`scheduled-layout ${task ? 'has-detail' : ''}`}><div className="scheduled-list">{groups.map(group=>{
        const items=group.items.filter(item=>item.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
        return items.length ? <section className="scheduled-group" key={group.label}><h2>{group.label}<span>{items.length}</span></h2>{items.map(item=><button key={item.id} className={`scheduled-item ${selected===item.id?'is-selected':''}`} onClick={()=>setSelected(item.id)} aria-pressed={selected===item.id}><div className="scheduled-item-name"><strong>{item.name}</strong><small>{statusText[item.lastStatus ?? ''] ?? '尚未运行'} · 共 {item.runCount} 次</small></div><span>{timeLabel(item)}</span><span className="scheduled-next">{completed(item)?'已完成，不再运行':!item.enabled?'已停用':`下次：${when(item.nextRun)}`}</span></button>)}</section> : null;
      })}{query.trim() && !tasks.some(item=>item.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) && <p className="scheduled-no-results">没有找到匹配的任务</p>}</div>
      <div className="scheduled-detail" hidden={!task}>{task ? <><header><h2>{task.name}</h2><button aria-label="收起任务详情" onClick={()=>setSelected(null)}>收起</button><label className="scheduled-check"><input type="checkbox" role="switch" aria-label="启用任务" checked={task.enabled} disabled={busy} onChange={e=>void act(()=>api.enable(task.id,e.target.checked))}/>启用</label></header><p className="scheduled-instruction">{task.instruction}</p><div className="scheduled-actions"><button disabled={busy || !task.enabled || tasks.some(t=>t.lastStatus==='running') || (task.schedule.kind==='once'&&(task.scheduleRunCount ?? task.runCount)>0)} onClick={()=>void act(()=>api.run(task.id))}><Play size={14}/>立即运行</button><button disabled={busy || task.lastStatus==='running'} onClick={event=>openForm(event.currentTarget, task)}>编辑</button><button disabled={busy || task.lastStatus==='running'} onClick={()=>setConfirmDelete(true)}>删除</button></div>
      {confirmDelete && <div className="scheduled-delete" role="alert">删除任务及全部运行记录？<button disabled={busy} onClick={()=>void act(async()=>{await api.remove(task.id);setSelected(null);setConfirmDelete(false);})}>确认删除</button><button onClick={()=>setConfirmDelete(false)}>取消</button></div>}
      <h3>运行记录</h3>{historyError?<p role="alert">{historyError}</p>:runs.length===0?<p>尚无运行记录</p>:runs.map(run=><article className="scheduled-run" key={run.id}><div><strong data-status={run.status}>{statusText[run.status] ?? run.status}</strong><span>{triggers[run.triggerKind]}</span><time>{when(run.startedAt)}</time></div>{run.error && <p>{run.error}</p>}{run.threadId && <button onClick={()=>void act(()=>Promise.resolve(onOpenRun(run.threadId!)))}>查看运行记录</button>}</article>)}</>:<p>选择任务，查看运行记录。</p>}</div></div></>}
  </section>;
}
function localDate(iso:string) { const d=new Date(iso);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16); }
