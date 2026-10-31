import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { KnowledgeLibraryView } from '../src/views/KnowledgeLibraryView';
import type { KnowledgeApi, KnowledgeLibrary } from '../src/runtime/knowledgeClient';
afterEach(cleanup);
function fixture() {
  let libraries: KnowledgeLibrary[] = [{id:'one', name:'资料库', root:'/notes', count:1, status:'ready', error:null, excluded:[], directories:['sub'], synced_at:null}];
  const doc = {id:'doc',path:'sub/测试.md', version:'v',status:'ready',error:null,snippet:'预览片段'};
  const api: KnowledgeApi = {
    quote: vi.fn(), matches: vi.fn(async () => ({items:[],has_more:false,next_start:null})),
    list: vi.fn(async () => libraries), associate: vi.fn(async () => ({id:'one'})),
    update: vi.fn(async () => ({})), sync: vi.fn(async () => ({})),
    detach: vi.fn(async () => {libraries = []; return {};}),
    documents: vi.fn(async () => ({items:[doc], has_more:false})),
    read: vi.fn(async () => ({id:'doc',path:doc.path,version:'v',text:'第一条：“保留”；\r\n原文。',start:0,end:16,total_chars:16,has_more:false})),
  };
  return {api,doc};
}
test('browse exact source and detach without a file-delete action', async () => {
  const {api} = fixture(); render(<KnowledgeLibraryView api={api} />);
  fireEvent.click(await screen.findByRole('button', {name:/sub\/测试.md/}));
  await waitFor(() => expect(document.querySelector('pre')?.textContent).toBe('第一条：“保留”；\r\n原文。'));
  fireEvent.click(screen.getByText('管理知识库'));
  fireEvent.click(screen.getByRole('button', {name:'解除关联'}));
  await screen.findByText(/点击“建立知识库”/);
  expect(api.detach).toHaveBeenCalledWith('one');
});
test('search failure stays distinct from no matches', async () => {
  const {api} = fixture(); render(<KnowledgeLibraryView api={api} />);
  const search = await screen.findByRole('textbox', {name:'搜索库内资料'});
  vi.mocked(api.documents).mockRejectedValue(new Error('本次搜索未完成'));
  fireEvent.change(search, {target:{value:'原文关键词'}});
  fireEvent.submit(search.closest('form')!);
  await screen.findByText('本次搜索未完成');
  expect(screen.queryByText('没有找到包含该关键词的资料。')).toBeNull();
});
test('a failed refresh retains the last list and can be retried', async () => {
  const {api} = fixture(); render(<KnowledgeLibraryView api={api} />);
  await screen.findByRole('button', {name:'sub/测试.md'});
  vi.mocked(api.documents).mockRejectedValueOnce(new Error('资料列表加载超时，请重试。'));
  fireEvent.click(screen.getByRole('button', {name:'立即同步'}));
  await screen.findByText(/当前显示上次加载的资料/);
  expect(screen.getByRole('button', {name:'sub/测试.md'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name:'重试'}));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  expect(screen.getByRole('button', {name:'sub/测试.md'})).toBeTruthy();
});
test('changed source replaces preview with a truthful error', async () => {
  const {api} = fixture(); vi.mocked(api.read).mockRejectedValue(new Error('原文已变化，请刷新资料后重新打开。'));
  render(<KnowledgeLibraryView api={api} />);
  fireEvent.click(await screen.findByRole('button', {name:/sub\/测试.md/}));
  await screen.findByText('原文已变化，请刷新资料后重新打开。');
  expect(document.querySelector('pre')).toBeNull();
});
test('association uses the selected folder and exclusion stays relative', async () => {
  const {api} = fixture(); const chooseFolder = vi.fn(async () => '/chosen');
  render(<KnowledgeLibraryView api={api} chooseFolder={chooseFolder} />);
  await screen.findByText('管理知识库');
  fireEvent.click(screen.getByRole('button',{name:'建立知识库'}));
  fireEvent.click(screen.getByRole('button',{name:'关联文件夹'}));
  await waitFor(() => expect(api.associate).toHaveBeenCalledWith('/chosen'));
  await waitFor(() => expect(screen.getByRole('button',{name:'建立知识库'}).hasAttribute('disabled')).toBe(false));
  fireEvent.click(screen.getByText('管理知识库'));
  fireEvent.click(screen.getByRole('checkbox',{name:'sub'}));
  await waitFor(() => expect(api.update).toHaveBeenCalledWith('one', {excluded:['sub']}));
});

test('switching libraries discards late results and clears the old preview', async () => {
  const {api,doc} = fixture();
  const base = (await api.list())[0];
  vi.mocked(api.list).mockResolvedValue([base, {...base,id:'two',name:'第二库'}]);
  let finishOldRead!: (page: Awaited<ReturnType<KnowledgeApi['read']>>) => void;
  vi.mocked(api.read).mockImplementation(() => new Promise(resolve => {finishOldRead = resolve;}));
  let finishNewList!: (page: Awaited<ReturnType<KnowledgeApi['documents']>>) => void;
  vi.mocked(api.documents).mockImplementation(async id => id === 'one' ? {items:[doc],has_more:false} : new Promise(resolve => {finishNewList = resolve;}));
  render(<KnowledgeLibraryView api={api} />);
  fireEvent.click(await screen.findByRole('button',{name:/sub\/测试.md/}));
  fireEvent.click(screen.getByRole('button',{name:/第二库/}));
  await waitFor(() => expect(screen.queryByRole('button',{name:/sub\/测试.md/})).toBeNull());
  finishOldRead({id:'doc',path:doc.path,version:'v',text:'旧库迟到的正文',start:0,end:8,total_chars:8,has_more:false});
  finishNewList({items:[],has_more:false});
  await screen.findByText('暂无可浏览的 Markdown 资料。');
  expect(screen.queryByText('旧库迟到的正文')).toBeNull();
  expect(screen.getByText('选择资料查看原文。')).toBeTruthy();
});

test('retrying an updated preview reads the new indexed version from its beginning', async () => {
  const {api,doc} = fixture();
  vi.mocked(api.read).mockRejectedValue(new Error('原文已变化，请刷新资料后重新打开。'));
  render(<KnowledgeLibraryView api={api} />);
  fireEvent.click(await screen.findByRole('button', {name:/sub\/测试.md/}));
  await screen.findByText('原文已变化，请刷新资料后重新打开。');
  const latest = {...doc, version:'new-version', snippet:'新版本片段'};
  vi.mocked(api.documents).mockResolvedValue({items:[latest], has_more:false});
  fireEvent.click(screen.getByRole('button', {name:'立即同步'}));
  await screen.findByText('新版本片段');
  vi.mocked(api.read).mockImplementation(async (_id, selectedDoc, start) => {
    if (selectedDoc.version !== 'new-version') throw new Error('旧版本不能读取');
    return {id:doc.id,path:doc.path,version:selectedDoc.version,text:'更新：“原样”；\r\n第二行。',start,end:16,total_chars:16,has_more:false};
  });
  fireEvent.click(screen.getByRole('button', {name:'重新读取原文'}));
  await waitFor(() => expect(document.querySelector('pre')?.textContent).toBe('更新：“原样”；\r\n第二行。'));
  expect(api.read).toHaveBeenLastCalledWith('one', latest, 0);
});

test('uses remote next offset instead of skipping an assumed hundred records', async () => {
  const {api,doc} = fixture();
  vi.mocked(api.documents).mockImplementation(async (_id,_q,offset) => ({items:[{...doc,path:offset ? '第二页.md':'第一页.md'}],has_more:offset===0,next_offset:offset===0 ? 1 : null}));
  render(<KnowledgeLibraryView api={api} />);
  await screen.findByRole('button',{name:'第一页.md'});
  fireEvent.click(screen.getByRole('button',{name:'下一页资料'}));
  await screen.findByRole('button',{name:'第二页.md'});
  expect(api.documents).toHaveBeenLastCalledWith('one','',1);
  fireEvent.click(screen.getByRole('button',{name:'上一页资料'}));
  await screen.findByRole('button',{name:'第一页.md'});
  expect(api.documents).toHaveBeenLastCalledWith('one','',0);
});

test('opens a deep hit, navigates further hits and preserves highlighted original text', async () => {
  const {api,doc} = fixture();
  const raw = '😀前文\r\n目标：“全角”；\r\n后文。';
  const deep = {...doc,read_start:90000,match_start:90300,match_end:90302,snippet:'目标附近'};
  vi.mocked(api.documents).mockResolvedValue({items:[deep],has_more:false});
  vi.mocked(api.read).mockImplementation(async (_id,_doc,start) => ({id:'doc',path:doc.path,version:'v',text:raw,start,end:start+raw.length,total_chars:200000,has_more:true}));
  vi.mocked(api.matches).mockResolvedValue({items:[{match_start:90300,match_end:90302,read_start:90000,snippet:'目标'},{match_start:150300,match_end:150302,read_start:150000,snippet:'目标'}],has_more:false,next_start:null});
  render(<KnowledgeLibraryView api={api} />);
  const search = await screen.findByRole('textbox',{name:'搜索库内资料'});
  fireEvent.change(search,{target:{value:'目标'}}); fireEvent.submit(search.closest('form')!);
  fireEvent.click(await screen.findByRole('button',{name:doc.path}));
  await waitFor(() => expect(api.read).toHaveBeenLastCalledWith('one',deep,90000));
  await screen.findByText('第 1 处命中');
  expect(document.querySelector('pre')?.textContent).toBe(raw);
  expect(document.querySelector('pre mark')?.textContent).toBe('目标');
  fireEvent.click(screen.getByRole('button',{name:'下一处命中'}));
  await waitFor(() => expect(api.read).toHaveBeenLastCalledWith('one',deep,150000));
  await screen.findByText('第 2 处命中');
  fireEvent.click(screen.getByRole('button',{name:'上一处命中'}));
  await waitFor(() => expect(api.read).toHaveBeenLastCalledWith('one',deep,90000));
});

test('ima folders navigate without attempting document reads and return to root', async () => {
  const {api,doc}=fixture();
  vi.mocked(api.list).mockResolvedValue([{id:'ima:one',name:'合成远程库',kind:'ima',root:'ima',count:0,status:'ready',error:null,excluded:[],directories:[],synced_at:null}]);
  const folder={...doc,id:'folder',path:'分类目录',kind:'folder' as const,snippet:'文件夹 · 点击进入'};
  vi.mocked(api.documents).mockImplementation(async (_id,_q,_offset,folderId) => ({items:folderId ? [doc] : [folder],has_more:false}));
  render(<KnowledgeLibraryView api={api}/>);
  fireEvent.click(await screen.findByRole('button',{name:'分类目录'}));
  await screen.findByRole('button',{name:doc.path});
  expect(api.read).not.toHaveBeenCalled();
  expect(api.documents).toHaveBeenLastCalledWith('ima:one','',0,'folder');
  fireEvent.click(screen.getByRole('button',{name:doc.path}));
  await screen.findByLabelText('原文内容');
  fireEvent.click(screen.getByRole('button',{name:'知识库根目录'}));
  await screen.findByRole('button',{name:'分类目录'});
  expect(screen.queryByLabelText('原文内容')).toBeNull();
  expect(api.documents).toHaveBeenLastCalledWith('ima:one','',0);
});

test('browse uses full width and closing the source preserves the document list', async()=>{
 const {api}=fixture();render(<KnowledgeLibraryView api={api}/>);
 const file=await screen.findByRole('button',{name:'sub/测试.md'});
 expect(screen.queryByRole('complementary',{name:'原文预览'})).toBeNull();
 fireEvent.click(file);await screen.findByLabelText('原文内容');expect(screen.getByRole('complementary',{name:'原文预览'})).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'收起原文'}));expect(screen.queryByRole('complementary',{name:'原文预览'})).toBeNull();
 expect(screen.getByRole('button',{name:'sub/测试.md'})).toBe(file);
});


test('Obsidian uses the folder association and cancel creates nothing', async()=>{
 const {api}=fixture();const chooseFolder=vi.fn().mockResolvedValueOnce(null).mockResolvedValueOnce('/vault');
 render(<KnowledgeLibraryView api={api} chooseFolder={chooseFolder}/>);await screen.findByText('管理知识库');
 for (let i=0;i<2;i++) {
  fireEvent.click(screen.getByRole('button',{name:'建立知识库'}));fireEvent.click(screen.getByRole('button',{name:'接入外部工具'}));
  fireEvent.click(screen.getByRole('button',{name:'Obsidian'}));await waitFor(()=>expect(chooseFolder).toHaveBeenCalledTimes(i+1));
  await waitFor(()=>expect(screen.getByRole('button',{name:'建立知识库'}).hasAttribute('disabled')).toBe(false));
  if(i===0)expect(api.associate).not.toHaveBeenCalled();
 }
 expect(api.associate).toHaveBeenCalledTimes(1);expect(api.associate).toHaveBeenCalledWith('/vault');
});
