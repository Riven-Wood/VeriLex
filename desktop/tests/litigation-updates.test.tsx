import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, expect, test, vi} from 'vitest';
import {LitigationWorkspaceView} from '../src/views/LitigationWorkspaceView';
import {LitigationElementsReview} from '../src/views/LitigationElementsReview';
import {LitigationFactHistory} from '../src/views/LitigationFactHistory';
import {litigationClient, type LitigationSnapshot} from '../src/views/LitigationClient';

vi.mock('../src/views/LitigationClient', () => ({litigationClient:{
  snapshot:vi.fn(),materials:vi.fn(),saveCase:vi.fn(),regenerateFacts:vi.fn(),
  factVersions:vi.fn(),factVersion:vi.fn(),restoreFacts:vi.fn(),confirmElements:vi.fn(),arrangeEvidence:vi.fn(),
}}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
const fact = {id:'f1',date:'2026-01-02',text:'双方签订合同。',sources:[{path:'合同.txt',page:null,quote:'双方签订合同。'}],importance:'low' as const,pending:[],status:'supported' as const,user_note:''};
const facts={revision:3,data:{facts:[fact],gaps:['付款记录缺失'],relationships:'合同关系',conflicts:[]}};
const outline={revision:1,data:{issues:[{title:'合同是否成立',elements:['合意']}],summary:'拟用要件',confirmed:false,case_revision:0}};
const empty={revision:0,data:null};
const snapshot:LitigationSnapshot={case:empty,facts,evidence:empty,elements:outline,issues:empty,strategy:empty};
const workspace={id:'workspace',display_name:'合成案件',root_path:'/synthetic',allow_read:true,allow_write:true,created_at:'2026-09-08'};
const props={workspaces:[workspace],selectedWorkspaceId:'workspace',onSelectWorkspace:vi.fn(),onChooseWorkspace:vi.fn(),onStartWorkflow:vi.fn(),busy:false};
beforeEach(()=>{
  vi.mocked(litigationClient.snapshot).mockResolvedValue(snapshot);
  vi.mocked(litigationClient.materials).mockResolvedValue({materials:[{path:'合同.txt',name:'合同.txt',supported:true,reason:''}],truncated:false});
});

test('regeneration starts only after its revision is saved and failure preserves the current result',async()=>{
  vi.mocked(litigationClient.regenerateFacts).mockRejectedValueOnce(new Error('案件已有更新')).mockResolvedValueOnce({...facts,revision:4});
  render(<LitigationWorkspaceView {...props}/>);
  const regenerate=await screen.findByRole('button',{name:'整体重新梳理'});
  fireEvent.click(regenerate);
  expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('案件已有更新'));
  expect(props.onStartWorkflow).not.toHaveBeenCalled();
  expect(screen.getByText('双方签订合同。',{selector:'p'})).toBeTruthy();
  fireEvent.click(regenerate);
  await waitFor(()=>expect(props.onStartWorkflow).toHaveBeenCalledOnce());
  expect(litigationClient.regenerateFacts).toHaveBeenLastCalledWith('workspace',3);
  expect(props.onStartWorkflow).toHaveBeenCalledWith(expect.stringContaining('mode=replace'));
});

test('element edits survive refresh and submission uses the originally read revision',async()=>{
  const confirm=vi.fn().mockResolvedValue(null);
  const view=render(<LitigationElementsReview record={outline} disabled={false} onConfirm={confirm}/>);
  fireEvent.change(screen.getByRole('textbox',{name:'构成要件（每行一项）'}),{target:{value:'合意\n履行'}});
  view.rerender(<LitigationElementsReview record={{...outline,revision:2}} disabled={false} onConfirm={confirm}/>);
  expect((screen.getByRole('textbox',{name:'构成要件（每行一项）'}) as HTMLTextAreaElement).value).toBe('合意\n履行');
  fireEvent.click(screen.getByRole('button',{name:'确认清单并继续分析'}));
  expect(confirm).toHaveBeenCalledWith({issues:[{title:'合同是否成立',elements:['合意','履行']}],summary:'用户核对要件清单'},1);
  fireEvent.click(screen.getByRole('button',{name:'重新载入清单'}));
  expect((screen.getByRole('textbox',{name:'构成要件（每行一项）'}) as HTMLTextAreaElement).value).toBe('合意');
});

test('confirmation is saved before continuing the same workflow',async()=>{
  vi.mocked(litigationClient.confirmElements).mockResolvedValue({...outline,revision:2,data:{...outline.data,confirmed:true}});
  render(<LitigationWorkspaceView {...props}/>);
  await screen.findByRole('button',{name:'整体重新梳理'});
  fireEvent.click(screen.getByRole('tab',{name:'争议焦点'}));
  fireEvent.click(await screen.findByRole('button',{name:'确认清单并继续分析'}));
  await waitFor(()=>expect(litigationClient.confirmElements).toHaveBeenCalledOnce());
  await waitFor(()=>expect(props.onStartWorkflow).toHaveBeenCalledWith(expect.stringContaining('先读elements')));
});

test('history previews the full old fact and a stale restore keeps the preview',async()=>{
  vi.mocked(litigationClient.factVersions).mockResolvedValue({versions:[{revision:1,summary:'初稿'}]});
  vi.mocked(litigationClient.factVersion).mockResolvedValue({...facts,revision:1});
  const restore=vi.fn().mockRejectedValue(new Error('案件已有更新'));
  const view=render(<LitigationFactHistory workspaceId="workspace" revision={3} disabled={false} onRestore={restore}/>);
  fireEvent.click(screen.getByRole('button',{name:'查看历史版本'}));
  fireEvent.change(await screen.findByRole('combobox',{name:'事实版本'}),{target:{value:'1'}});
  expect(await screen.findByText('双方签订合同。',{selector:'p'})).toBeTruthy();
  view.rerender(<LitigationFactHistory workspaceId="workspace" revision={4} disabled={false} onRestore={restore}/>);
  fireEvent.click(screen.getByRole('button',{name:'恢复此版并保留现版'}));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent','案件已有更新');
  expect(restore).toHaveBeenCalledWith(1,3);
  expect(screen.getByLabelText('历史事实内容')).toBeTruthy();
});

test('evidence drag persists a complete order, and keyboard down remains available',async()=>{
  const item={id:'e1',group:'合同',name:'合同原件',kind:'书证',purpose:'证明合同',sources:fact.sources,strength:'weak',assessment:'内部评估'};
  vi.mocked(litigationClient.snapshot).mockResolvedValue({...snapshot,evidence:{revision:2,data:{evidence:[item,{...item,id:'e2',name:'付款凭证'}],gaps:[],contradictions:[],suggestions:[]}}});
  vi.mocked(litigationClient.arrangeEvidence).mockResolvedValue({});
  render(<LitigationWorkspaceView {...props}/>);
  await screen.findByRole('button',{name:'整体重新梳理'});
  fireEvent.click(screen.getByRole('tab',{name:'证据整理 · 已生成'}));
  const handle=screen.getByRole('button',{name:'拖动合同原件调整顺序'});
  fireEvent.dragStart(handle,{dataTransfer:{setData:vi.fn(),effectAllowed:''}});
  fireEvent.drop(screen.getByRole('button',{name:'拖动付款凭证调整顺序'}).closest('article')!);
  await waitFor(()=>expect(litigationClient.arrangeEvidence).toHaveBeenCalledWith('workspace',[{id:'e2',group:'合同'},{id:'e1',group:'合同'}],2));
  await waitFor(()=>expect((within(handle.closest('article')!).getByRole('button',{name:'下移'}) as HTMLButtonElement).disabled).toBe(false));
});

test('user testimony without a material source is labelled as user supplied',async()=>{
  vi.mocked(litigationClient.snapshot).mockResolvedValue({...snapshot,facts:{...facts,data:{...facts.data,facts:[{...fact,sources:[],user_note:'用户口述：双方签订合同。'}]}}});
  render(<LitigationWorkspaceView {...props}/>);
  expect(await screen.findByText('用户已补充')).toBeTruthy();
  expect(screen.queryByText('有材料支持')).toBeNull();
});

test('editing elements survives switching to facts and back',async()=>{
  render(<LitigationWorkspaceView {...props}/>);
  await screen.findByRole('button',{name:'整体重新梳理'});
  fireEvent.click(screen.getByRole('tab',{name:'争议焦点'}));
  fireEvent.change(screen.getByRole('textbox',{name:'构成要件（每行一项）'}),{target:{value:'合意\n尚未保存的要件'}});
  fireEvent.click(screen.getByRole('tab',{name:'事实梳理 · 已生成'}));
  fireEvent.click(screen.getByRole('tab',{name:'争议焦点'}));
  expect((screen.getByRole('textbox',{name:'构成要件（每行一项）'}) as HTMLTextAreaElement).value).toBe('合意\n尚未保存的要件');
});

test('saved elements remain visible when the subsequent refresh fails',async()=>{
  const saved={...outline,revision:2,data:{...outline.data,confirmed:true,issues:[{title:'合同是否成立',elements:['合意','已保存的新要件']} ]}};
  vi.mocked(litigationClient.confirmElements).mockResolvedValue(saved);
  vi.mocked(litigationClient.snapshot).mockResolvedValueOnce(snapshot).mockRejectedValue(new Error('刷新失败'));
  render(<LitigationWorkspaceView {...props}/>);
  await screen.findByRole('button',{name:'整体重新梳理'});
  fireEvent.click(screen.getByRole('tab',{name:'争议焦点'}));
  fireEvent.change(screen.getByRole('textbox',{name:'构成要件（每行一项）'}),{target:{value:'合意\n已保存的新要件'}});
  fireEvent.click(screen.getByRole('button',{name:'确认清单并继续分析'}));
  await screen.findByRole('alert');
  expect((screen.getByRole('textbox',{name:'构成要件（每行一项）'}) as HTMLTextAreaElement).value).toBe('合意\n已保存的新要件');
  expect(screen.getByText('构成要件清单 · 已核对')).toBeTruthy();
});

test('restoring facts updates the workspace even after the history panel is unmounted',async()=>{
  const restored={...facts,revision:4,data:{...facts.data,facts:[{...fact,text:'恢复的旧版事实'}]}};
  vi.mocked(litigationClient.factVersions).mockResolvedValue({versions:[{revision:1,summary:'旧版'}]});
  vi.mocked(litigationClient.factVersion).mockResolvedValue({...restored,revision:1});
  let finish!: (value: typeof restored) => void;
  vi.mocked(litigationClient.restoreFacts).mockReturnValue(new Promise(resolve=>{finish=resolve;}));
  vi.mocked(litigationClient.snapshot).mockResolvedValueOnce(snapshot).mockRejectedValue(new Error('刷新失败'));
  render(<LitigationWorkspaceView {...props}/>);
  fireEvent.click(await screen.findByRole('button',{name:'查看历史版本'}));
  fireEvent.change(await screen.findByRole('combobox',{name:'事实版本'}),{target:{value:'1'}});
  fireEvent.click(await screen.findByRole('button',{name:'恢复此版并保留现版'}));
  fireEvent.click(screen.getByRole('tab',{name:'争议焦点'}));
  finish(restored);
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('tab',{name:'事实梳理 · 已生成'}));
  expect(screen.getByText('恢复的旧版事实',{selector:'p'})).toBeTruthy();
});
