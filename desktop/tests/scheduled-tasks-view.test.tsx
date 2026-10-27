Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, writable: true, value() { this.setAttribute('open', ''); } });
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { ScheduledTasksView } from '../src/views/ScheduledTasksView';
import type { ScheduledApi, ScheduledTask, ScheduledRun } from '../src/runtime/scheduledClient';
afterEach(cleanup);
const task:ScheduledTask={id:'one',name:'日报',instruction:'整理摘要',providerId:'p',model:'m',workspaceId:null,knowledgeLibraryIds:[],maxMinutes:15,schedule:{kind:'daily',timeOfDay:'09:00'},enabled:true,nextRun:'2026-09-18T01:00:00Z',lastStatus:'failed',runCount:1,createdAt:'2026-09-17T01:00:00Z'};
const run:ScheduledRun={id:'run',taskId:'one',threadId:'thread',turnId:'turn',triggerKind:'catchup',status:'failed',startedAt:'2026-09-17T01:00:00Z',finishedAt:null,error:'运行超过时长上限。'};
function fixture(items:ScheduledTask[]=[task]) { const api:ScheduledApi={list:vi.fn(async()=>items),create:vi.fn(async()=>task),update:vi.fn(async()=>task),enable:vi.fn(async()=>task),remove:vi.fn(async()=>{}),run:vi.fn(async()=>run),runs:vi.fn(async()=>[run])};return api; }
test('rescheduled once task remains runnable while retaining past run count', async()=>{
 const api=fixture([{...task,schedule:{kind:'once',fireAt:'2026-10-01T01:00:00Z'},nextRun:'2026-10-01T01:00:00Z',runCount:3,scheduleRunCount:0,lastStatus:'succeeded'}]);
 render(<ScheduledTasksView api={api} defaultProvider="p" defaultModel="m" onOpenRun={()=>{}}/>);
 fireEvent.click(await screen.findByRole('button',{name:/日报/}));
 expect(screen.queryByText('已完成，不再运行')).toBeNull();
 expect(screen.getByText(/共 3 次/)).toBeDefined();
 const button=screen.getByRole('button',{name:'立即运行'}) as HTMLButtonElement;
 expect(button.disabled).toBe(false);fireEvent.click(button);
 await waitFor(()=>expect(api.run).toHaveBeenCalledWith('one'));
});
test('empty has a real create flow and captures configured model',async()=>{
 const api=fixture([]);render(<ScheduledTasksView api={api} defaultProvider="p" defaultModel="m" onOpenRun={()=>{}}/>);
 fireEvent.click(await screen.findByText('新建定时任务'));fireEvent.change(screen.getByLabelText('任务指令'),{target:{value:'整理资料'}});fireEvent.click(screen.getByText('保存任务'));
 await waitFor(()=>expect(api.create).toHaveBeenCalledWith(expect.objectContaining({instruction:'整理资料',providerId:'p',model:'m',schedule:{kind:'daily',timeOfDay:'09:00'}})));
});
test('history opens result, labels catchup and failure; disable and delete use real API',async()=>{
 const api=fixture();const open=vi.fn();render(<ScheduledTasksView api={api} defaultProvider="p" defaultModel="m" onOpenRun={open}/>);
 fireEvent.click(await screen.findByRole('button',{name:/日报/}));await screen.findByText('补跑');
 fireEvent.click(screen.getByText('查看运行记录'));await waitFor(()=>expect(open).toHaveBeenCalledWith('thread'));
 fireEvent.click(screen.getByRole('switch'));await waitFor(()=>expect(api.enable).toHaveBeenCalledWith('one',false));
 await waitFor(()=>expect((screen.getByText('删除') as HTMLButtonElement).disabled).toBe(false));fireEvent.click(screen.getByText('删除'));expect(api.remove).not.toHaveBeenCalled();fireEvent.click(screen.getByText('确认删除'));await waitFor(()=>expect(api.remove).toHaveBeenCalledWith('one'));
});
test('list failure is not shown as empty',async()=>{
 const api=fixture();api.list=vi.fn(async()=>{throw new Error('读取失败');});render(<ScheduledTasksView api={api} defaultProvider="p" defaultModel="m" onOpenRun={()=>{}}/>);
 expect((await screen.findByRole('alert')).textContent).toContain('读取失败');expect(screen.queryByText('把重复的工作交给定时任务')).toBeNull();
});
test('late history from a previous selection is discarded',async()=>{
 const api=fixture([task,{...task,id:'two',name:'周报'}]);let finish!:(r:ScheduledRun[])=>void;api.runs=vi.fn(id=>id==='one'?new Promise(resolve=>{finish=resolve;}):Promise.resolve([]));
 render(<ScheduledTasksView api={api} defaultProvider="p" defaultModel="m" onOpenRun={()=>{}}/>);
 fireEvent.click(await screen.findByRole('button',{name:/日报/}));fireEvent.click(screen.getByRole('button',{name:/周报/}));finish([run]);await waitFor(()=>expect(screen.queryByText('补跑')).toBeNull());
});

test('failed save stays inside the dialog and Escape cannot dismiss a pending save', async()=>{
 const api=fixture([]);let reject!:(e:Error)=>void;api.create=vi.fn(()=>new Promise((_,no)=>{reject=no;}));
 render(<ScheduledTasksView api={api} defaultProvider="p" defaultModel="m" onOpenRun={()=>{}}/>);
 fireEvent.click(await screen.findByRole('button',{name:'新建定时任务'}));
 fireEvent.change(screen.getByLabelText('任务指令'),{target:{value:'整理资料'}});fireEvent.click(screen.getByText('保存任务'));
 const dialog=screen.getByRole('dialog');fireEvent(dialog,new Event('cancel',{cancelable:true}));expect(screen.getByRole('dialog')).toBe(dialog);
 reject(new Error('保存失败，请重试'));await screen.findByText('保存失败，请重试');expect(dialog.contains(screen.getByRole('alert'))).toBe(true);
 fireEvent(dialog,new Event('cancel',{cancelable:true}));expect(screen.queryByRole('dialog')).toBeNull();
});


test.each(['create', 'edit'])('closing %s returns focus to the mouse trigger', async mode=>{
 const api=fixture();render(<ScheduledTasksView api={api} defaultProvider="p" defaultModel="m" onOpenRun={()=>{}}/>);
 await screen.findByRole('button',{name:/日报/});
 if(mode==='edit') fireEvent.click(screen.getByRole('button',{name:/日报/}));
 const trigger=screen.getByRole('button',{name:mode==='edit'?'编辑':'新建定时任务'});
 expect(document.activeElement).not.toBe(trigger);fireEvent.click(trigger);
 fireEvent(screen.getByRole('dialog'),new Event('cancel',{cancelable:true}));
 expect(screen.queryByRole('dialog')).toBeNull();expect(document.activeElement).toBe(trigger);
});


test.each(['create', 'edit'])('saving %s returns focus to its trigger', async mode=>{
 const api=fixture();render(<ScheduledTasksView api={api} defaultProvider="p" defaultModel="m" onOpenRun={()=>{}}/>);
 await screen.findByRole('button',{name:/日报/});
 if(mode==='edit') fireEvent.click(screen.getByRole('button',{name:/日报/}));
 const trigger=screen.getByRole('button',{name:mode==='edit'?'编辑':'新建定时任务'});
 fireEvent.click(trigger);fireEvent.change(screen.getByLabelText('任务指令'),{target:{value:'整理资料'}});
 fireEvent.click(screen.getByText('保存任务'));
 await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
 expect(document.activeElement).toBe(trigger);
});
