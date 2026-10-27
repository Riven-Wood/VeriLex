import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {AgentComposer} from '../src/components/AgentComposer';
import {TurnGroup} from '../src/components/timeline/TurnGroup';
import {WorkspacePermissions} from '../src/settings/WorkspacePermissions';
import {PersonalInstructions} from '../src/settings/PersonalInstructions';
import {NativePreferences} from '../src/settings/NativePreferences';
import {preferredWorkspaceId} from '../src/settings/StoragePreferences';
import {readUiPreferences,saveUiPreferences,UI_PREFERENCES_KEY} from '../src/settings/preferences';
import * as api from '../src/runtime/api';
const workspace={id:'w1',display_name:'演示',root_path:'/sample',allow_read:true,allow_write:true,created_at:'now',last_used_at:'now'};
beforeEach(()=>localStorage.clear());afterEach(()=>{cleanup();vi.restoreAllMocks();localStorage.clear();});
function composer(running=false){
  const send=vi.fn(),queue=vi.fn(),interrupt=vi.fn();
  render(<AgentComposer value="下一步" hasModel isRunning={running} modelLabel="Example" onChange={()=>{}} onSend={send} onStop={()=>{}} onOpenSettings={()=>{}} onQueue={queue} onInterruptAndSend={interrupt}/>);
  return {send,queue,interrupt,input:screen.getByRole('textbox',{name:'向智能体提问'})};
}
test('persists valid preferences and safely handles damaged storage',()=>{
  saveUiPreferences({theme:'dark',fontSize:'large',sendMode:'modifier'});
  expect(JSON.parse(localStorage.getItem(UI_PREFERENCES_KEY)!)).toMatchObject({theme:'dark',fontSize:'large'});
  localStorage.setItem(UI_PREFERENCES_KEY,'{broken');
  expect(readUiPreferences()).toMatchObject({theme:'light',sendMode:'enter'});
});
test('modifier sends only deliberately, including IME and Shift safeguards',()=>{
  saveUiPreferences({sendMode:'modifier'});const {input,send}=composer();
  fireEvent.keyDown(input,{key:'Enter'});
  fireEvent.keyDown(input,{key:'Enter',metaKey:true,isComposing:true});
  fireEvent.keyDown(input,{key:'Enter',metaKey:true,keyCode:229});
  fireEvent.keyDown(input,{key:'Enter',metaKey:true,shiftKey:true});
  expect(send).not.toHaveBeenCalled();
  fireEvent.keyDown(input,{key:'Enter',metaKey:true});expect(send).toHaveBeenCalledTimes(1);
});
test.each(['queue','interrupt'] as const)('running send applies %s preference for button and keyboard',mode=>{
  saveUiPreferences({followUp:mode});const actions=composer(true);
  fireEvent.keyDown(actions.input,{key:'Enter'});
  fireEvent.click(screen.getByRole('button',{name:'提交下一条'}));
  expect(actions[mode]).toHaveBeenCalledTimes(2);expect(actions.send).not.toHaveBeenCalled();
});
test('ask remains explicit while a task is running',()=>{
  const {input,queue,interrupt}=composer(true);fireEvent.keyDown(input,{key:'Enter'});
  expect(screen.getByRole('menu',{name:'运行中提交方式'})).toBeTruthy();expect(queue).not.toHaveBeenCalled();expect(interrupt).not.toHaveBeenCalled();
});
test('summary keeps critical cards and can reveal completed steps',()=>{
  saveUiPreferences({executionDetail:'summary'});
  const items:any[]=[{kind:'tool',id:'done',turnId:'t',name:'read_file',status:'succeeded',riskLevel:'low'},{kind:'tool',id:'failure',turnId:'t',name:'write_file',status:'failed',riskLevel:'high'},{kind:'tool',id:'approval',turnId:'t',name:'write_file',status:'awaiting_approval',riskLevel:'high'}];
  const renderItem=(item:any)=><span key={item.id}>{item.id}</span>;
  const view=render(<TurnGroup items={items} active renderItem={renderItem}/>);
  expect(document.querySelector('.tool-step-line')).toBeNull();expect(screen.queryByText('failure')).toBeNull();expect(screen.getByRole('button',{name:/执行记录.*1 次失败/})).toBeTruthy();expect(screen.getByText('approval')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:/查看执行过程/}));expect(document.querySelector('.tool-step-line')).toBeTruthy();
  view.rerender(<TurnGroup items={items} active={false} renderItem={renderItem}/>);
  expect(screen.queryByText('failure')).toBeNull();expect(screen.getByRole('button',{name:/执行记录.*1 次失败/})).toBeTruthy();expect(screen.getByText('approval')).toBeTruthy();
});
test('notification failed save retains the original toggle',async()=>{
  vi.spyOn(api,'getPreferences').mockResolvedValue({background_task_max_minutes:30,notify_completed:1});
  vi.spyOn(api,'putPreference').mockRejectedValue(new Error('private request details'));
  render(<NativePreferences/>);const toggle=screen.getByRole('switch',{name:'任务完成'});
  await waitFor(()=>expect(toggle.hasAttribute('disabled')).toBe(false));fireEvent.click(toggle);
  expect(await screen.findByText(/未能保存/)).toBeTruthy();expect((toggle as HTMLInputElement).checked).toBe(true);expect(document.body.textContent).not.toContain('private request details');
});
test('revoke requires confirmation and refuses a busy folder without removing it',async()=>{
  vi.spyOn(api,'listWorkspaces').mockResolvedValue([workspace]);const revoke=vi.spyOn(api,'revokeWorkspace').mockRejectedValue({code:'workspace_busy'});
  render(<WorkspacePermissions/>);fireEvent.click(await screen.findByRole('button',{name:'收回授权'}));expect(revoke).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'确认收回'}));expect(await screen.findByText(/仍有任务或终端在运行/)).toBeTruthy();expect(screen.getByText('/sample')).toBeTruthy();
  revoke.mockResolvedValue(undefined);fireEvent.click(screen.getByRole('button',{name:'确认收回'}));await waitFor(()=>expect(screen.queryByText('/sample')).toBeNull());
});
test('personal instructions save and clear through the runtime',async()=>{
  vi.spyOn(api,'getPreferences').mockResolvedValue({background_task_max_minutes:30,personal_instructions:'先写结论'});
  const save=vi.spyOn(api,'putPreference').mockImplementation(async(_,value)=>({background_task_max_minutes:30,personal_instructions:String(value)}));
  render(<PersonalInstructions/>);const input=screen.getByRole('textbox');await waitFor(()=>expect((input as HTMLTextAreaElement).value).toBe('先写结论'));
  fireEvent.change(input,{target:{value:''}});fireEvent.click(screen.getByRole('button',{name:'保存指令'}));
  expect(await screen.findByText(/已保存/)).toBeTruthy();expect(save).toHaveBeenCalledWith('personal_instructions','');
});
test('default workspace never revives a removed or unreadable authorization',async()=>{
  saveUiPreferences({defaultWorkspaceId:'w1'});const list=vi.spyOn(api,'listWorkspaces').mockResolvedValue([workspace]);
  expect(await preferredWorkspaceId()).toBe('w1');list.mockResolvedValue([]);expect(await preferredWorkspaceId()).toBeNull();
  list.mockResolvedValue([{...workspace,allow_read:false}]);expect(await preferredWorkspaceId()).toBeNull();
});

test('imports Markdown into a draft, then saves the complete global instructions',async()=>{
  vi.spyOn(api,'getPreferences').mockResolvedValue({background_task_max_minutes:30,personal_instructions:'原有偏好'});
  const save=vi.spyOn(api,'putPreference').mockImplementation(async(_,value)=>({background_task_max_minutes:30,personal_instructions:String(value)}));
  render(<PersonalInstructions/>);
  const input=screen.getByRole('textbox') as HTMLTextAreaElement;
  await waitFor(()=>expect(input.value).toBe('原有偏好'));
  const text='# 全局指令\n'+'核实材料😀\n'.repeat(500);
  fireEvent.change(screen.getByLabelText('选择 AGENTS.md 文件'),{target:{files:[new File([text],'AGENTS.md')]}});
  await waitFor(()=>expect(input.value).toBe(text));
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'保存指令'}));
  expect(await screen.findByText(/已保存/)).toBeTruthy();
  expect(save).toHaveBeenCalledWith('personal_instructions',text);
});

test('invalid imports and failed saves preserve the saved instructions and current draft',async()=>{
  vi.spyOn(api,'getPreferences').mockResolvedValue({background_task_max_minutes:30,personal_instructions:'原规则'});
  vi.spyOn(api,'putPreference').mockRejectedValue(new Error('offline'));
  render(<PersonalInstructions/>);const input=screen.getByRole('textbox') as HTMLTextAreaElement;
  await waitFor(()=>expect(input.value).toBe('原规则'));
  for(const file of [new File(['wrong'],'rules.docx'),new File([new Uint8Array([0xff])],'AGENTS.md'),new File(['\0'],'AGENTS.md'),new File(['x'.repeat(32001)],'AGENTS.md')]){
    fireEvent.change(screen.getByLabelText('选择 AGENTS.md 文件'),{target:{files:[file]}});
    await screen.findByRole('alert');
    await waitFor(()=>expect((screen.getByRole('button',{name:'导入 AGENTS.md'}) as HTMLButtonElement).disabled).toBe(false));
    expect(input.value).toBe('原规则');
  }
  fireEvent.change(input,{target:{value:'新草稿'}});
  fireEvent.click(screen.getByRole('button',{name:'保存指令'}));
  expect(await screen.findByText(/未能保存，原指令仍然有效/)).toBeTruthy();
  expect(input.value).toBe('新草稿');
});
