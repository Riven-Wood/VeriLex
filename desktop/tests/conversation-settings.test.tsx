import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, expect, test, vi} from 'vitest';
import {ConversationSettings, type ConversationSettingsProps} from '../src/settings/ConversationSettings';
import type {ThreadSummary} from '../src/app/navigation';

const archived:ThreadSummary={id:'a',backendId:'a',title:'项目旧对话',workspaceId:'w',createdAt:'2026-01-01',updatedAt:'2026-01-01',status:'idle'};
const trashed:ThreadSummary={...archived,id:'t',backendId:'t',title:'测试合同'};
function props(overrides:Partial<ConversationSettingsProps>={}):ConversationSettingsProps {
  return {status:'ready',retry:vi.fn().mockResolvedValue(undefined),archived:[archived],trash:[trashed],unarchive:vi.fn().mockResolvedValue(undefined),trashArchived:vi.fn().mockResolvedValue(undefined),restore:vi.fn().mockResolvedValue(undefined),remove:vi.fn().mockResolvedValue(undefined),...overrides};
}
afterEach(()=>{cleanup();vi.restoreAllMocks();});

test('设置中分别恢复归档、移入回收站和恢复回收站',async()=>{
  const p=props();render(<ConversationSettings {...p}/>);
  expect(screen.getByRole('heading',{name:'已归档的对话'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'恢复归档：项目旧对话'}));
  await waitFor(()=>expect(p.unarchive).toHaveBeenCalledWith('a'));
  await waitFor(()=>expect(screen.getByRole('button',{name:'移入回收站：项目旧对话'}).hasAttribute('disabled')).toBe(false));
  fireEvent.click(screen.getByRole('button',{name:'移入回收站：项目旧对话'}));
  await waitFor(()=>expect(p.trashArchived).toHaveBeenCalledWith('a'));
  await waitFor(()=>expect(screen.getByRole('button',{name:'恢复回收站：测试合同'}).hasAttribute('disabled')).toBe(false));
  fireEvent.click(screen.getByRole('button',{name:'恢复回收站：测试合同'}));
  await waitFor(()=>expect(p.restore).toHaveBeenCalledWith('t'));
});

test('彻底删除单条对话不需要确认',async()=>{
  const p=props();render(<ConversationSettings {...p}/>);
  fireEvent.click(screen.getByRole('button',{name:'彻底删除：测试合同'}));
  await waitFor(()=>expect(p.remove).toHaveBeenCalledWith('t'));
});

test('回收站支持选择、全选和直接批量删除，失败项可重试',async()=>{
  const second={...trashed,id:'t2',title:'另一对话'};
  const remove=vi.fn().mockImplementation((id:string)=>id==='t2'?Promise.reject(new Error()):Promise.resolve());
  render(<ConversationSettings {...props({trash:[trashed,second],remove})}/>);
  const all=screen.getByRole('checkbox',{name:'全选回收站对话'}) as HTMLInputElement;
  fireEvent.click(screen.getByRole('checkbox',{name:'选择：测试合同'}));
  expect(all.checked).toBe(false);
  fireEvent.click(all);
  expect(all.checked).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'彻底删除所选'}));
  await waitFor(()=>expect(screen.getByRole('alert').textContent).toContain('部分对话删除失败'));
  expect(remove.mock.calls.map(call=>call[0])).toEqual(['t','t2']);
  expect((screen.getByRole('checkbox',{name:'选择：测试合同'}) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole('checkbox',{name:'选择：另一对话'}) as HTMLInputElement).checked).toBe(true);
});

test('读取或操作失败明确提示，保留对话并可重试',async()=>{
  const retry=vi.fn().mockResolvedValue(undefined);
  const p=props({status:'error',retry,unarchive:vi.fn().mockRejectedValue(new Error('secret token'))});render(<ConversationSettings {...p}/>);
  expect(screen.getByRole('alert').textContent).toContain('读取失败');
  fireEvent.click(screen.getByRole('button',{name:'重试'}));expect(retry).toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'恢复归档：项目旧对话'}));
  await waitFor(()=>expect(screen.getAllByRole('alert').some(row=>row.textContent?.includes('操作未完成'))).toBe(true));
  expect(screen.getByText('项目旧对话')).toBeTruthy();
  expect(document.body.textContent).not.toContain('secret token');
});

test('等待或读取失败时不把空列表说成没有对话',()=>{
  const p=props({status:'loading',archived:[],trash:[]});const {rerender}=render(<ConversationSettings {...p}/>);
  expect(screen.getByRole('status').textContent).toContain('正在读取');
  expect(screen.queryByText('没有已归档的对话。')).toBeNull();
  rerender(<ConversationSettings {...p} status="error"/>);
  expect(screen.queryByText('回收站为空。')).toBeNull();
  rerender(<ConversationSettings {...p} status="ready"/>);
  expect(screen.getByText('回收站为空。')).toBeTruthy();
  expect(within(screen.getByRole('heading',{name:'已归档的对话'}).parentElement!).getByText('没有已归档的对话。')).toBeTruthy();
});

test('设置搜索能定位归档及回收站', async()=>{
  const {searchSettings}=await import('../src/settings/categories');
  expect(searchSettings('已归档的对话').map(item=>item.id)).toContain('archived-conversations');
  expect(searchSettings('回收站').map(item=>item.id)).toContain('trashed-conversations');
});
