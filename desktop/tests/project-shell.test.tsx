import {act, cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, expect, test, vi} from 'vitest';
import {ProjectShell} from '../src/app/WorkspaceRouter';

vi.mock('../src/views/ProjectFactsView',()=>({ProjectFactsView:()=> <div>四类图表页面</div>, ProjectFactsEmpty:()=> <div>项目空状态</div>}));
vi.mock('../src/components/PreviewRail',()=>({PreviewRail:({target,onClose}:any)=> <div><span>文件预览：{target.path}</span><button onClick={onClose}>关闭文件预览</button></div>}));
afterEach(cleanup);

test('without a selected project the facts page shows its empty state',()=>{
  render(<ProjectShell workspace={null} busy={false} error={null} threadId={null} onStart={vi.fn()} renderAssistant={()=> <div/>}/>);
  fireEvent.click(screen.getByRole('button',{name:'事实梳理'}));
  expect(screen.getByText('项目空状态')).toBeTruthy();
});

test('project home keeps the ordinary conversation while the facts page is selected',()=>{
  const workspace={id:'w1',display_name:'示例',root_path:'/tmp/example',allow_read:true,allow_write:true} as Parameters<typeof ProjectShell>[0]['workspace'];
  render(<ProjectShell workspace={workspace} busy={false} error={null} threadId={null} onStart={vi.fn()} renderAssistant={()=> <textarea aria-label="普通对话"/>}/>);
  const chat=screen.getByRole('textbox',{name:'普通对话'}) as HTMLTextAreaElement;
  fireEvent.change(chat,{target:{value:'保留的输入'}});
  fireEvent.click(screen.getByRole('button',{name:'事实梳理'}));
  expect(screen.getByText('四类图表页面')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'首页'}));
  expect((screen.getByRole('textbox',{name:'普通对话'}) as HTMLTextAreaElement).value).toBe('保留的输入');
});

test('项目文件在右侧打开，事实梳理保持可见，切换文件和关闭不丢失页面',()=>{
  const workspace={id:'w1',display_name:'示例',root_path:'/tmp/example',allow_read:true,allow_write:true} as Parameters<typeof ProjectShell>[0]['workspace'];
  render(<ProjectShell workspace={workspace} busy={false} error={null} threadId={null} onStart={vi.fn()} renderAssistant={()=> <textarea aria-label="普通对话"/>}/>);
  fireEvent.click(screen.getByRole('button',{name:'事实梳理'}));
  act(()=>window.dispatchEvent(new CustomEvent('verilex:project-open-file',{detail:{workspaceId:'w1',path:'材料/甲.txt'}})));
  expect(screen.getByText('四类图表页面').closest('[hidden]')).toBeNull();
  expect(screen.getByRole('complementary',{name:'项目文件预览'}).textContent).toContain('材料/甲.txt');
  act(()=>window.dispatchEvent(new CustomEvent('verilex:project-open-file',{detail:{workspaceId:'w1',path:'乙.txt'}})));
  expect(screen.getByRole('complementary',{name:'项目文件预览'}).textContent).toContain('乙.txt');
  fireEvent.click(screen.getByRole('button',{name:'首页'}));
  expect(screen.getByRole('textbox',{name:'普通对话'})).toBeTruthy();
  expect(screen.getByRole('complementary',{name:'项目文件预览'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'关闭文件预览'}));
  expect(screen.queryByRole('complementary',{name:'项目文件预览'})).toBeNull();
});
