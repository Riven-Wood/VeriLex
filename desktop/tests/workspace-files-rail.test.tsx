import {afterEach, expect, test, vi} from 'vitest';
import {cleanup, fireEvent, render} from '@testing-library/react';
import {useState} from 'react';
import {PreviewRail, type PreviewTarget} from '../src/components/PreviewRail';
const mocks = vi.hoisted(() => ({workspaces:vi.fn(), files:vi.fn(), artifacts:vi.fn(), preview:vi.fn()}));
vi.mock('../src/runtime/api', async importOriginal => ({...await importOriginal<typeof import('../src/runtime/api')>(), listWorkspaces:mocks.workspaces, listThreadArtifacts:mocks.artifacts, getWorkspaceFilePreview:mocks.preview}));
vi.mock('../src/runtime/workspaceFilesClient', () => ({listWorkspaceFiles:mocks.files}));
afterEach(() => {cleanup(); vi.resetAllMocks();});
test('ordinary rail returns from preview to the same folder and retains session artifacts', async () => {
  mocks.workspaces.mockResolvedValue([{id:'a',display_name:'演示目录',allow_read:true}]);
  mocks.artifacts.mockResolvedValue([{kind:'legacy_workspace',path:'result.md',name:'成果.md',last_operation:'write_file'}]);
  mocks.files.mockImplementation(async (_id,path) => ({entries:path ? [{name:'note.md',path:'folder/note.md',directory:false}]:[{name:'folder',path:'folder',directory:true}],next_offset:null,truncated:false,snapshot:'x'}));
  mocks.preview.mockResolvedValue({kind:'text',name:'note.md',path:'folder/note.md',size_bytes:5,content:'sample preview',truncated:false});
  function Host(){const [target,setTarget]=useState<PreviewTarget>({source:'artifacts-list'});return <PreviewRail open terminalEnabled target={target} threadId="thread" workspaceId="a" onClose={()=>{}} onOpenList={()=>setTarget({source:'artifacts-list'})} onOpenWorkspaceFile={(workspaceId,path)=>setTarget({source:'workspace',workspaceId,path})}/>;}
  const view=render(<Host/>);
  fireEvent.click(await view.findByRole('button',{name:'folder 文件夹'}));
  fireEvent.click(await view.findByRole('button',{name:'note.md'}));
  await view.findByText('sample preview');
  fireEvent.click(view.getByRole('button',{name:'返回文件列表'}));
  await view.findByRole('button',{name:'note.md'});
  fireEvent.click(view.getByRole('button',{name:'会话成果'}));
  await view.findByText('成果.md');
  expect(view.getByRole('button',{name:'终端',exact:true})).toBeTruthy();
});
test('embedded litigation rail keeps its existing artifact list', async () => {
  mocks.artifacts.mockResolvedValue([]);
  const view=render(<PreviewRail embedded open target={{source:'artifacts-list'}} threadId="thread" workspaceId="a" onClose={()=>{}} onOpenList={()=>{}} onOpenWorkspaceFile={()=>{}}/>);
  await view.findByText('本次对话还没有生成或修改过文件。');
  expect(view.queryByRole('button',{name:'工作区文件'})).toBeNull();
  expect(mocks.workspaces).not.toHaveBeenCalled();
});
test('new conversation cannot display the previous conversation artifacts', async () => {
  mocks.workspaces.mockResolvedValue([]);
  mocks.artifacts.mockResolvedValue([{kind:'legacy_workspace',path:'old.md',name:'上一会话成果.md',last_operation:'write_file'}]);
  const common={open:true,terminalEnabled:true,target:{source:'artifacts-list'} as PreviewTarget,workspaceId:null,onClose:()=>{},onOpenList:()=>{},onOpenWorkspaceFile:()=>{}};
  const view=render(<PreviewRail {...common} threadId="previous"/>);
  fireEvent.click(view.getByRole('button',{name:'会话成果'}));
  await view.findByText('上一会话成果.md');
  view.rerender(<PreviewRail {...common} threadId={null}/>);
  fireEvent.click(view.getByRole('button',{name:'会话成果'}));
  await view.findByText('开始对话后，这里会显示生成或修改过的文件。');
  expect(view.queryByText('上一会话成果.md')).toBeNull();
});
