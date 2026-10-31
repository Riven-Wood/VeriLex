import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, expect, test, vi} from 'vitest';
import {ProjectFactsEmpty, ProjectFactsView} from '../src/views/ProjectFactsView';
import {runtimeRequest} from '../src/runtime/api';
import {litigationClient, litigationWorkClient} from '../src/views/LitigationClient';

vi.mock('../src/runtime/api',()=>({runtimeRequest:vi.fn()}));
vi.mock('../src/views/LitigationClient',()=>({litigationClient:{materials:vi.fn()},litigationWorkClient:{read:vi.fn()}}));
afterEach(()=>{cleanup();vi.resetAllMocks();});

test('没有项目时仍可查看和切换四类标签，并提示先选择项目',()=>{
  render(<ProjectFactsEmpty/>);
  expect(screen.getByText('请新建或选择项目')).toBeTruthy();
  expect(screen.getAllByRole('tab').map(tab=>tab.textContent)).toEqual(['时间轴','大事记','关系图','对抗图']);
  fireEvent.click(screen.getByRole('tab',{name:'关系图'}));
  expect(screen.getByRole('tab',{name:'关系图'}).getAttribute('aria-selected')).toBe('true');
  expect(runtimeRequest).not.toHaveBeenCalled();
});

test('等待生成时显示加载状态，完成后恢复生成按钮',async()=>{
  vi.mocked(runtimeRequest).mockResolvedValue({results:[],errors:[]});
  const props={workspaceId:'w',busy:true,canWrite:true,onStart:vi.fn()};
  const view=render(<ProjectFactsView {...props}/>);
  expect(screen.getByRole('status').textContent).toContain('正在生成');
  expect(screen.getByRole('status').querySelector('.loading-state-icon')).toBeTruthy();
  expect(screen.queryByRole('button',{name:'生成时间轴'})).toBeNull();
  view.rerender(<ProjectFactsView {...props} busy={false}/>);
  expect(await screen.findByRole('button',{name:'生成时间轴'})).toBeTruthy();
});

test('等待运行时可请求停止，停止后恢复操作',async()=>{
  vi.mocked(runtimeRequest).mockResolvedValue({results:[],errors:[]});
  const onStop=vi.fn(), props={workspaceId:'w',busy:true,canWrite:true,onStart:vi.fn(),onStop};
  const view=render(<ProjectFactsView {...props}/>);
  fireEvent.click(screen.getByRole('button',{name:'停止生成'}));
  expect(onStop).toHaveBeenCalledTimes(1);
  expect((screen.getByRole('button',{name:'正在停止…'}) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<ProjectFactsView {...props} busy={false}/>);
  expect(await screen.findByRole('button',{name:'生成时间轴'})).toBeTruthy();
});

test('准备材料时停止不会启动生成',async()=>{
  vi.mocked(runtimeRequest).mockResolvedValue({results:[],errors:[]});
  let release!:(value:any)=>void;
  vi.mocked(litigationClient.materials).mockReturnValue(new Promise(resolve=>{release=resolve;}));
  vi.mocked(litigationWorkClient.read).mockResolvedValue({revision:0,data:null});
  const start=vi.fn();
  render(<ProjectFactsView workspaceId="w" busy={false} canWrite onStart={start}/>);
  fireEvent.click(await screen.findByRole('button',{name:'生成时间轴'}));
  fireEvent.click(screen.getByRole('button',{name:'停止生成'}));
  release({materials:[{path:'合同.txt',name:'合同.txt',supported:true,reason:''}],truncated:false});
  await screen.findByRole('button',{name:'生成时间轴'});
  expect(start).not.toHaveBeenCalled();
});

test('each tab shows its own saved result and a new task binds the selected materials and kind',async()=>{
  const source={path:'合同.txt',page:null,quote:'双方签订合同。'};
  vi.mocked(runtimeRequest).mockResolvedValue({results:[{path:'a',kind:'timeline',title:'签约经过',created_ns:2,entries:[{title:'签约',date:'2024',detail:'签订合同',other:'',pending:[],sources:[source]}],gaps:[]}],errors:[]});
  vi.mocked(litigationClient.materials).mockResolvedValue({materials:[{path:'合同.txt',name:'合同.txt',supported:true,reason:''}],truncated:false});
  vi.mocked(litigationWorkClient.read).mockResolvedValue({revision:0,data:null});
  const start=vi.fn(), open=vi.fn();
  render(<ProjectFactsView workspaceId="w" busy={false} canWrite onStart={start} onOpenSource={open}/>);
  expect(await screen.findByText('签约经过')).toBeTruthy();
  fireEvent.click(screen.getByText('出处：合同.txt'));
  expect(open).toHaveBeenCalledWith(source);
  fireEvent.click(screen.getByRole('tab',{name:'关系图'}));
  expect(screen.queryByText('签约经过')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'生成关系图'}));
  await waitFor(()=>expect(start).toHaveBeenCalledTimes(1));
  expect(start.mock.calls[0][0]).toMatchObject({project_chart_kind:'relationship',materials:['合同.txt'],kind:'facts'});
  fireEvent.click(screen.getByRole('tab',{name:'时间轴'}));
  expect(screen.getByText('签约经过')).toBeTruthy();
});

test('勾选文件时事实梳理只绑定勾选的材料',async()=>{
  vi.mocked(runtimeRequest).mockResolvedValue({results:[],errors:[]});
  vi.mocked(litigationClient.materials).mockResolvedValue({materials:[{path:'甲.txt',name:'甲.txt',supported:true,reason:''},{path:'乙.txt',name:'乙.txt',supported:true,reason:''}],truncated:false});
  vi.mocked(litigationWorkClient.read).mockResolvedValue({revision:0,data:null});
  const start=vi.fn();
  render(<ProjectFactsView workspaceId="w" selectedFiles={['乙.txt']} busy={false} canWrite onStart={start}/>);
  fireEvent.click(await screen.findByRole('button',{name:'生成时间轴'}));
  await waitFor(()=>expect(start).toHaveBeenCalledTimes(1));
  expect(start.mock.calls[0][0].materials).toEqual(['乙.txt']);
});

test('勾选文件不可读取时明确提示，不启动事实梳理',async()=>{
  vi.mocked(runtimeRequest).mockResolvedValue({results:[],errors:[]});
  vi.mocked(litigationClient.materials).mockResolvedValue({materials:[{path:'扫描件.pdf',name:'扫描件.pdf',supported:false,reason:'不可读取'}],truncated:false});
  vi.mocked(litigationWorkClient.read).mockResolvedValue({revision:0,data:null});
  const start=vi.fn();
  render(<ProjectFactsView workspaceId="w" selectedFiles={['扫描件.pdf']} busy={false} canWrite onStart={start}/>);
  fireEvent.click(await screen.findByRole('button',{name:'生成时间轴'}));
  expect((await screen.findByRole('alert')).textContent).toContain('部分选中文件无法用于事实梳理');
  expect(start).not.toHaveBeenCalled();
});

test('a completed task without a saved result reports failure',async()=>{
  vi.mocked(runtimeRequest).mockResolvedValue({results:[],errors:[]});
  vi.mocked(litigationClient.materials).mockResolvedValue({materials:[{path:'合同.txt',name:'合同.txt',supported:true,reason:''}],truncated:false});
  vi.mocked(litigationWorkClient.read).mockResolvedValue({revision:0,data:null});
  const props={workspaceId:'w',busy:false,canWrite:true,onStart:vi.fn()};
  const view=render(<ProjectFactsView {...props}/>);
  fireEvent.click(await screen.findByRole('button',{name:'生成时间轴'}));
  await waitFor(()=>expect(props.onStart).toHaveBeenCalledTimes(1));
  view.rerender(<ProjectFactsView {...props} busy/>);
  view.rerender(<ProjectFactsView {...props} busy={false}/>);
  expect((await screen.findByRole('alert')).textContent).toContain('没有保存新的成果');
});

test('saved narrative context and material gaps remain available without hiding the result',async()=>{
  vi.mocked(runtimeRequest).mockResolvedValue({results:[{path:'a',kind:'timeline',title:'签约经过',created_ns:2,entries:[{title:'签约',date:'2024',detail:'签订合同',other:'合同条件待核对',pending:[],sources:[]}],gaps:['合同附件未提供']}],errors:[]});
  render(<ProjectFactsView workspaceId="w" busy={false} canWrite onStart={vi.fn()}/>);
  expect(await screen.findByText('合同条件待核对')).toBeTruthy();
  const disclosure=screen.getByText('材料范围与待核实事项 · 1 项').closest('details')!;
  expect(disclosure.open).toBe(false);
  fireEvent.click(screen.getByText('材料范围与待核实事项 · 1 项'));
  expect(disclosure.open).toBe(true);
  expect(screen.getByText('合同附件未提供')).toBeTruthy();
});

test('an unfinished source review is visible without opening the gaps section',async()=>{
  const warning='来源自动复核未完成，请结合原文核对材料归属。';
  vi.mocked(runtimeRequest).mockResolvedValue({results:[{path:'a',kind:'timeline',title:'签约经过',created_ns:2,entries:[{title:'签约',date:'2024',detail:'签订合同',other:'',pending:[],sources:[]}],gaps:[warning]}],errors:[]});
  render(<ProjectFactsView workspaceId="w" busy={false} canWrite onStart={vi.fn()}/>);
  expect((await screen.findByRole('alert')).textContent).toBe(warning);
});

test('generation excludes previous saved results, including results created since the page loaded',async()=>{
  const prior={path:'文书起草/项目梳理-old.json',kind:'timeline',title:'旧成果',created_ns:1,entries:[],gaps:[]};
  vi.mocked(runtimeRequest).mockResolvedValueOnce({results:[],errors:[]}).mockResolvedValue({results:[prior],errors:[]});
  vi.mocked(litigationClient.materials).mockResolvedValue({materials:[
    {path:'合同.txt',name:'合同.txt',supported:true,reason:''},
    {path:prior.path,name:'旧成果.json',supported:true,reason:''},
  ],truncated:false});
  vi.mocked(litigationWorkClient.read).mockResolvedValue({revision:0,data:null});
  const start=vi.fn();
  render(<ProjectFactsView workspaceId="w" busy={false} canWrite onStart={start}/>);
  fireEvent.click(await screen.findByRole('button',{name:'生成时间轴'}));
  await waitFor(()=>expect(start).toHaveBeenCalledTimes(1));
  expect(start.mock.calls[0][0].materials).toEqual(['合同.txt']);
});
