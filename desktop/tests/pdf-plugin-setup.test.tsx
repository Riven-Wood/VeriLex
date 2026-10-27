import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {describe,it,expect,vi,beforeEach,afterEach} from 'vitest';
import {PdfPluginSetup,pdfPluginDraft} from '../src/components/PdfPluginSetup';
const pick=vi.hoisted(()=>vi.fn());
vi.mock('../src/platform/directoryPicker',()=>({pickWorkspaceDirectory:pick}));
vi.mock('../src/components/McpServerEditor',()=>({McpServerEditor:({initialDraft}:{initialDraft:{cwd:string}})=><p>实际测试目录：{initialDraft.cwd}</p>}));
const props=()=>({servers:[],onSaved:vi.fn(),onComplete:vi.fn(),onOpen:vi.fn(),onBusyChange:vi.fn()});
describe('PDF folder-scoped setup',()=>{
 beforeEach(()=>vi.clearAllMocks());
 afterEach(cleanup);
 it('requires an explicit folder before testing, and handles cancelled choice',async()=>{
  pick.mockResolvedValueOnce(null).mockResolvedValueOnce('/合成 PDF');
  render(<PdfPluginSetup {...props()}/>);
  expect((screen.getByText('测试并添加') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByText('选择 PDF 文件夹'));
  await waitFor(()=>expect(pick).toHaveBeenCalledTimes(1));
  expect((screen.getByText('测试并添加') as HTMLButtonElement).disabled).toBe(true);
  await waitFor(()=>expect((screen.getByText('选择 PDF 文件夹') as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByText('选择 PDF 文件夹'));
  await screen.findByText('/合成 PDF'); fireEvent.click(screen.getByText('测试并添加'));
  expect(screen.getByText('实际测试目录：/合成 PDF')).toBeTruthy();
 });
 it('keeps folder failures visible and does not claim a connection',async()=>{
  pick.mockRejectedValue(new Error('unavailable'));render(<PdfPluginSetup {...props()}/>);
  fireEvent.click(screen.getByText('选择 PDF 文件夹'));
  expect((await screen.findByRole('alert')).textContent).toContain('请重试');
  expect((screen.getByText('测试并添加') as HTMLButtonElement).disabled).toBe(true);
 });
 it('uses the bundled stable launcher with no online installer',()=>{
  expect(pdfPluginDraft('/合成 PDF')).toMatchObject({command:'verilex-pdf-plugin',cwd:'/合成 PDF',args:[],env:{}});
 });
});
