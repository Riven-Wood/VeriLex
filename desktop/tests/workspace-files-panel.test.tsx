import {afterEach, expect, test, vi} from 'vitest';
import {cleanup, fireEvent, render, waitFor} from '@testing-library/react';
import {useState} from 'react';
import {WorkspaceFilesPanel, type WorkspaceBrowseLocation} from '../src/components/WorkspaceFilesPanel';
const mocks = vi.hoisted(() => ({workspaces: vi.fn(), files: vi.fn()}));
vi.mock('../src/runtime/api', () => ({listWorkspaces: mocks.workspaces}));
vi.mock('../src/runtime/workspaceFilesClient', () => ({listWorkspaceFiles: mocks.files}));
afterEach(() => {cleanup(); vi.resetAllMocks();});
const records = [{id:'a', display_name:'演示目录', allow_read:true}, {id:'b', display_name:'其他目录', allow_read:true}, {id:'blocked', display_name:'不可读', allow_read:false}];
const page = (entries: {name:string;path:string;directory:boolean}[], next_offset: number | null = null) => ({entries, next_offset, truncated:false, snapshot:'stamp', path:''});
function setup() {
  const onOpen = vi.fn();
  function Host() {
    const [location, setLocation] = useState<WorkspaceBrowseLocation>({workspaceId:'a', path:''});
    return <WorkspaceFilesPanel location={location} onLocation={setLocation} onOpen={onOpen}/>;
  }
  mocks.workspaces.mockResolvedValue(records);
  return {...render(<Host/>), onOpen};
}
test('navigates folders, returns to parent, previews files and switches only readable roots', async () => {
  mocks.files.mockImplementation(async (_id, path) => path ? page([{name:'说明.md',path:'资料/说明.md',directory:false}]) : page([{name:'资料',path:'资料',directory:true}]));
  const view = setup();
  fireEvent.click(await view.findByRole('button', {name:'资料 文件夹'}));
  fireEvent.click(await view.findByRole('button', {name:'说明.md'}));
  expect(view.onOpen).toHaveBeenCalledWith('a','资料/说明.md');
  fireEvent.click(view.getByRole('button', {name:'返回上一级文件夹'}));
  await view.findByRole('button', {name:'资料 文件夹'});
  expect(view.queryByRole('option', {name:'不可读'})).toBeNull();
  fireEvent.change(view.getByRole('combobox'), {target:{value:'b'}});
  await waitFor(() => expect(mocks.files).toHaveBeenLastCalledWith('b','',0,'',expect.any(AbortSignal)));
});
test('load more uses snapshot and preserves entries', async () => {
  mocks.files.mockResolvedValueOnce(page([{name:'first.txt', path:'first.txt',directory:false}],200)).mockResolvedValueOnce(page([{name:'last.txt',path:'last.txt',directory:false}]));
  const view = setup();
  fireEvent.click(await view.findByRole('button',{name:'显示更多'}));
  await view.findByText('last.txt');
  expect(view.getByText('first.txt')).toBeTruthy();
  expect(mocks.files).toHaveBeenLastCalledWith('a','',200,'stamp');
});
test('stale response never replaces a newly selected workspace', async () => {
  let resolve!: (value: unknown) => void;
  mocks.files.mockImplementation(id => id === 'a' ? new Promise(r => {resolve = r;}) : Promise.resolve(page([{name:'new.txt',path:'new.txt',directory:false}])));
  const view = setup();
  await waitFor(() => expect(mocks.files).toHaveBeenCalled());
  fireEvent.change(view.getByRole('combobox'), {target:{value:'b'}});
  await view.findByText('new.txt');
  resolve(page([{name:'old.txt',path:'old.txt',directory:false}]));
  await waitFor(() => expect(view.queryByText('old.txt')).toBeNull());
});
test('refresh removes revoked workspace results and errors can retry', async () => {
  mocks.files.mockResolvedValue(page([{name:'file.txt',path:'file.txt',directory:false}]));
  const view = setup(); await view.findByText('file.txt');
  mocks.workspaces.mockResolvedValue(records.filter(item => item.id !== 'a'));
  fireEvent.click(view.getByRole('button',{name:'刷新文件列表'}));
  await view.findByRole('alert');
  expect(view.queryByText('file.txt')).toBeNull();
  mocks.workspaces.mockResolvedValue(records);
  fireEvent.click(view.getByRole('button',{name:'重试'}));
  await view.findByText('file.txt');
});
