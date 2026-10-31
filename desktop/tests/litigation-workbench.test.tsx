vi.mock('../src/views/LitigationVisualizations',()=>({LitigationVisualizations:()=>null}));
import {editorClient} from '../src/components/editor/client';
vi.mock('../src/components/editor/client',()=>({editorClient:{templates:vi.fn().mockResolvedValue({templates:[]}),list:vi.fn().mockResolvedValue({products:[],errors:[]}),create:vi.fn(),draft:vi.fn(),read:vi.fn(),openWord:vi.fn()}}));
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, expect, test, vi} from 'vitest';
import {LitigationWorkbench} from '../src/views/LitigationWorkbench';
import {litigationClient,litigationWorkClient} from '../src/views/LitigationClient';
import {runtimeRequest,getWorkspaceFilePreview, openWorkspacePath} from '../src/runtime/api';
vi.mock('../src/views/LitigationClient', () => ({litigationClient:{files:vi.fn(), snapshot:vi.fn(), materials:vi.fn(), documentTemplates:vi.fn()},litigationWorkClient:{read:vi.fn(),links:vi.fn(),versions:vi.fn()}}));
vi.mock('../src/runtime/api', () => ({runtimeRequest:vi.fn().mockResolvedValue({status:'text_available',gaps:[],notice:''}),getWorkspaceFilePreview:vi.fn(),openWorkspacePath:vi.fn()}));
vi.mock('../src/components/preview/PdfViewer',()=>({PdfViewer:({page}:{page?:number})=><div data-testid="pdf-source-page">{page}</div>}));
const workspace={id:'w1',display_name:'合成案件',root_path:'/synthetic',allow_read:true,allow_write:true,created_at:'2026-09-08'};
const empty={revision:0,data:null};
const props={onSelectWorkspace:vi.fn(),onChooseWorkspace:vi.fn(),workspace,workspaces:[workspace],thread:null,threads:[],busy:false,filesCollapsed:false,onBack:vi.fn(),onStartWorkflow:vi.fn(),onSelectThread:vi.fn(),onNewDiscussion:vi.fn(),renderAssistant:vi.fn(),draft:'',onDraftChange:vi.fn(),hasModel:true,onOpenSettings:vi.fn()};
beforeEach(() => {
 vi.mocked(editorClient.templates).mockResolvedValue({templates:[]});
 vi.mocked(runtimeRequest).mockResolvedValue({status:'text_available',gaps:[],notice:''});
 vi.mocked(editorClient.list).mockResolvedValue({products:[],errors:[]});
 vi.mocked(editorClient.create).mockResolvedValue({id:'d',title:'起诉状',kind:'documents',revision:1,file_hash:'hash',path:'文书起草/起诉状.docx',document:{type:'doc',content:[{type:'paragraph'}]},external_update:false,updated_at:'now'});
 vi.mocked(editorClient.draft).mockResolvedValue({});
 vi.mocked(editorClient.openWord).mockImplementation(async()=>({product:await vi.mocked(editorClient.create).mock.results.at(-1)!.value,content:'QQ=='}));
 localStorage.clear();
 for (const id of ['w1','']) localStorage.setItem(`verilex:litigation-desk:v1:${id}`,JSON.stringify({page:'overview',paths:[],file:null,aiOpen:true}));
 vi.mocked(litigationWorkClient.links).mockResolvedValue({links:{}});
 vi.mocked(litigationWorkClient.read).mockResolvedValue({revision:0,data:null});
 vi.mocked(litigationClient.documentTemplates).mockResolvedValue({templates:[]});
 vi.mocked(litigationClient.snapshot).mockResolvedValue({case:empty,facts:empty,evidence:empty,issues:empty,strategy:empty});
 vi.mocked(litigationClient.materials).mockResolvedValue({materials:[{name:'合同.txt',path:'合同.txt',supported:true,reason:''}],truncated:false});
 vi.mocked(litigationClient.files).mockImplementation(async (_id,path) => ({entries:path ? [{name:'起诉状.docx',path:'文书/起诉状.docx',directory:false}] : [{name:'合同.txt',path:'合同.txt',directory:false},{name:'文书',path:'文书',directory:true}],truncated:false}));
 vi.mocked(getWorkspaceFilePreview).mockResolvedValue({path:'合同.txt',name:'合同.txt',size_bytes:15,kind:'text',content:'原始合同内容',truncated:false});
});
afterEach(() => {cleanup();vi.resetAllMocks();localStorage.clear();});

test('drafting-only entry restores old page preference to documents and keeps case discussions', async()=>{
 localStorage.setItem('verilex:litigation-desk:v1:w1',JSON.stringify({page:'facts',paths:[],file:null,aiOpen:true}));
 const oldThread={id:'old',backendId:'old',title:'旧案件讨论',scene:'litigation-work',workspaceId:'w1',status:'idle'} as any;
 render(<LitigationWorkbench {...props} thread={oldThread} threads={[oldThread]} onStartWork={vi.fn()} renderAssistant={()=> <p>历史讨论内容</p>}/>);
 expect(screen.getByRole('region',{name:'文书起草'})).toBeTruthy();
 expect(screen.queryByRole('button',{name:'事实梳理'})).toBeNull();
 expect(screen.queryByRole('button',{name:'证据整理'})).toBeNull();
 expect(await screen.findByRole('region',{name:'文书起草工作区'})).toBeTruthy();
 expect(screen.getByText('历史讨论内容')).toBeTruthy();
 expect(screen.getByRole('combobox',{name:'历史会话'})).toHaveProperty('value','old');
});

test('selects the requested history after the editor is flushed', async () => {
 const selected=vi.fn();
 const historical={id:'project-history',backendId:'project-history',title:'项目历史',scene:'general',workspaceId:'w1',status:'idle'} as any;
 render(<LitigationWorkbench {...props} threads={[historical]} onSelectThread={selected}/>);
 fireEvent.change(screen.getByRole('combobox',{name:'历史会话'}),{target:{value:'project-history'}});
 await waitFor(()=>expect(selected).toHaveBeenCalledWith('project-history'));
});

test('lists generated folders, opens Word through system action, refreshes external edits', async () => {
 vi.mocked(getWorkspaceFilePreview).mockResolvedValue({path:'文书/起诉状.docx',name:'起诉状.docx',size_bytes:100,kind:'unsupported'});
 render(<LitigationWorkbench {...props}/>);
 fireEvent.click(await within(screen.getByRole('complementary',{name:'当前工作区文件'})).findByRole('button',{name:'文书'}));
 fireEvent.click(await screen.findByRole('button',{name:'起诉状.docx'}));
 const preview=await screen.findByRole('complementary',{name:'文件预览'});
 fireEvent.click(await within(preview).findByRole('button',{name:'用默认应用打开'}));
 await waitFor(() => expect(openWorkspacePath).toHaveBeenCalledWith('w1',{path:'文书/起诉状.docx',mode:'open'}));
 const count=vi.mocked(getWorkspaceFilePreview).mock.calls.length;
 fireEvent.focus(window);
 await waitFor(() => expect(vi.mocked(getWorkspaceFilePreview).mock.calls.length).toBeGreaterThan(count));
});

test('read failure offers retry and AI can collapse without replacing the case page', async () => {
 vi.mocked(litigationClient.files).mockRejectedValueOnce(new Error('unavailable'));
 render(<LitigationWorkbench {...props}/>);
 fireEvent.click(await screen.findByRole('button',{name:'重试'}));
 expect(await within(screen.getByRole('complementary',{name:'当前工作区文件'})).findByRole('button',{name:'合同.txt'})).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'收起讨论'}));
 expect(screen.queryByRole('complementary',{name:'案件 AI 助手'})).toBeNull();
 expect(screen.getByRole('region',{name:'文书起草'})).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'展开讨论'}));
 expect(screen.getByRole('complementary',{name:'案件 AI 助手'})).toBeTruthy();
});

test('unselected workspace keeps the desk and sidebar picker without reading case files', () => {
 render(<LitigationWorkbench {...props} workspace={null}/>);
 const sidebar=screen.getByRole('complementary',{name:'当前工作区文件'});
 expect(within(sidebar).getByRole('combobox',{name:'案件工作区'})).toHaveProperty('value','');
 expect(litigationClient.files).not.toHaveBeenCalled();
 expect(litigationClient.snapshot).not.toHaveBeenCalled();
 expect(props.renderAssistant).toHaveBeenCalled();
 expect(screen.queryByRole('button',{name:'开始讨论'})).toBeNull();
 expect(screen.queryByRole('combobox',{name:'历史会话'})).toBeNull();
 expect(screen.queryByRole('button',{name:'新建会话'})).toBeNull();
 fireEvent.change(within(sidebar).getByRole('combobox',{name:'案件工作区'}),{target:{value:'w1'}});
 expect(props.onSelectWorkspace).toHaveBeenCalledWith('w1');
 fireEvent.click(within(sidebar).getByRole('button',{name:'打开其他文件夹'}));
 expect(props.onChooseWorkspace).toHaveBeenCalledOnce();
});

test('panel widths resize in opposite directions, persist and reset', () => {
 const view=render(<LitigationWorkbench {...props} workspace={null}/>);
 const left=screen.getByRole('separator',{name:'调整案件工作区宽度'});
 const right=screen.getByRole('separator',{name:'调整讨论栏宽度'});
 fireEvent(left,new MouseEvent('pointerdown',{bubbles:true,button:0,clientX:200}));
 fireEvent(window,new MouseEvent('pointermove',{clientX:300}));
 fireEvent(window,new MouseEvent('pointerup'));
 expect(left.getAttribute('aria-valuenow')).toBe('300');
 fireEvent(right,new MouseEvent('pointerdown',{bubbles:true,button:0,clientX:700}));
 fireEvent(window,new MouseEvent('pointermove',{clientX:600}));
 fireEvent(window,new MouseEvent('pointercancel'));
 expect(right.getAttribute('aria-valuenow')).toBe('420');
 expect(document.body.style.cursor).toBe('');
 view.unmount();
 render(<LitigationWorkbench {...props} workspace={null}/>);
 expect(screen.getByRole('separator',{name:'调整案件工作区宽度'}).getAttribute('aria-valuenow')).toBe('300');
 const restored=screen.getByRole('separator',{name:'调整讨论栏宽度'});
 expect(restored.getAttribute('aria-valuenow')).toBe('420');
 fireEvent.doubleClick(restored);
 expect(restored.getAttribute('aria-valuenow')).toBe('320');
 fireEvent.keyDown(restored,{key:'ArrowLeft'});
 expect(restored.getAttribute('aria-valuenow')).toBe('336');
 fireEvent.click(screen.getByRole('button',{name:'收起讨论'}));
 expect(screen.queryByRole('separator',{name:'调整讨论栏宽度'})).toBeNull();
});

test('a pending approval remains discoverable when discussion is collapsed and a file is open', async()=>{
 localStorage.setItem('verilex:litigation-desk:v1:w1',JSON.stringify({page:'facts',paths:['合同.txt'],file:'合同.txt',aiOpen:false}));
 const thread={id:'approval-thread',backendId:'approval-thread',title:'事实讨论',status:'awaiting_approval',updatedAt:'now'} as any;
 const view=render(<LitigationWorkbench {...props} busy thread={thread} onStartWork={vi.fn()} renderAssistant={()=> <p>待确认的内容</p>}/>);
 expect(await screen.findByText('原始合同内容')).toBeTruthy();
 expect(screen.queryByRole('complementary',{name:'案件 AI 助手'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'查看待确认操作'}));
 expect(screen.getByRole('complementary',{name:'案件 AI 助手'}).textContent).toContain('待确认的内容');
 view.rerender(<LitigationWorkbench {...props} thread={{...thread,status:'idle'}} onStartWork={vi.fn()}/>);
 expect(screen.queryByText(/案件讨论有操作等待确认/)).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'收起讨论'}));
 expect(screen.getByRole('button',{name:'展开讨论'})).toBeTruthy();
});


test('expanded work area hides both panels and restores their content and draft', async()=>{
 render(<LitigationWorkbench {...props} draft="保留讨论草稿" renderAssistant={()=> <textarea aria-label="案件讨论内容" defaultValue="保留讨论草稿"/>}/>);
 await screen.findByRole('button',{name:'合同.txt'});
 fireEvent.click(screen.getByRole('button',{name:'展开工作区'}));
 expect(screen.queryByRole('complementary',{name:'当前工作区文件'})).toBeNull();
 expect(screen.queryByRole('complementary',{name:'案件 AI 助手'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'恢复两侧栏'}));
 expect(screen.getByRole('button',{name:'合同.txt'})).toBeTruthy();
 expect(screen.getByRole('textbox',{name:'案件讨论内容'})).toHaveProperty('value','保留讨论草稿');
});


test('left file selection is passed to drafting and survives refresh without opening discussion', async()=>{
 localStorage.setItem('verilex:litigation-desk:v1:w1',JSON.stringify({page:'facts',paths:[],file:null,aiOpen:false}));
 const start=vi.fn();render(<LitigationWorkbench {...props} onStartWork={start}/>);
 const selection=await screen.findByRole('checkbox',{name:'选取材料 合同.txt'});
 expect(selection).toHaveProperty('checked',false);
 await waitFor(()=>expect(screen.getByRole('checkbox',{name:'选取材料 合同.txt'})).toHaveProperty('checked',false));
 fireEvent.click(screen.getByRole('checkbox',{name:'选取材料 合同.txt'}));
 fireEvent.change(await screen.findByRole('textbox',{name:'起草或修改要求'}),{target:{value:'起草答辩状'}});
 const run=await screen.findByRole('button',{name:'生成正文'});
 await waitFor(()=>expect(run).toHaveProperty('disabled',false));fireEvent.click(run);
 await waitFor(()=>expect(start).toHaveBeenCalledWith(expect.objectContaining({materials:['合同.txt'],editorContext:expect.objectContaining({operation:'generate'})})));
 expect(screen.queryByRole('complementary',{name:'案件 AI 助手'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'刷新案件文件'}));
 await waitFor(()=>expect(litigationClient.materials).toHaveBeenCalledTimes(3));
 expect(screen.getByRole('checkbox',{name:'选取材料 合同.txt'})).toHaveProperty('checked',true);
 fireEvent.click(screen.getByRole('button',{name:'全选',exact:true}));
 expect(screen.getByRole('checkbox',{name:'选取材料 合同.txt'})).toHaveProperty('checked',true);
});


test.each(['issues','strategy'])('retired %s page preserves open files and historical discussion without a dead locate action',async(page)=>{
 localStorage.setItem('verilex:litigation-desk:v1:w1',JSON.stringify({page,paths:['合同.txt'],file:'合同.txt',aiOpen:true}));
 const thread={id:'legacy',backendId:'legacy',title:'原来的分析讨论',updatedAt:'now'} as any;
 vi.mocked(litigationWorkClient.links).mockResolvedValue({links:{legacy:{kind:page as any,section_id:null,materials:[],expected_revision:1}}});
 render(<LitigationWorkbench {...props} thread={thread} threads={[thread]} onStartWork={vi.fn()} renderAssistant={()=> <p>保留历史消息</p>}/>);
 expect(await screen.findByText('原始合同内容')).toBeTruthy();
 expect(await screen.findByRole('combobox',{name:'历史会话'})).toHaveProperty('value','legacy');
 expect(screen.queryByRole('button',{name:'定位讨论对象'})).toBeNull();
 expect(screen.queryByRole('button',{name:'争议焦点',exact:true})).toBeNull();
 expect(screen.queryByRole('button',{name:'策略备忘',exact:true})).toBeNull();
 expect(screen.getByText('保留历史消息')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'关闭 合同.txt'}));
 expect(screen.getByRole('region',{name:'文书起草工作区'})).toBeTruthy();
});


test('header keeps work navigation without duplicating the workspace picker or back button',()=>{
 render(<LitigationWorkbench {...props}/>);
 expect(screen.queryByRole('button',{name:'返回主页面'})).toBeNull();
 expect(screen.getByRole('combobox',{name:'案件工作区'})).toBeTruthy();
 expect(document.querySelector('.litigation-desk-header h1')).toBeNull();
 expect(screen.queryByRole('navigation',{name:'案件工作'})).toBeNull();
 expect(screen.getByText('文书起草',{selector:'strong'})).toBeTruthy();
});


test('discussion heading renames the current conversation and retains input on failure',async()=>{
 const thread={id:'talk',backendId:'talk',title:'原会话名称',updatedAt:'now'} as any;
 const rename=vi.fn().mockRejectedValueOnce(Error('暂时无法保存名称')).mockResolvedValueOnce(undefined);
 const view=render(<LitigationWorkbench {...props} thread={thread} threads={[thread]} onRenameThread={rename}/>);
 fireEvent.click(screen.getByRole('button',{name:'重命名当前会话'}));
 fireEvent.change(screen.getByRole('textbox',{name:'会话名称'}),{target:{value:'  庭前准备讨论  '}});
 fireEvent.click(screen.getByRole('button',{name:'保存会话名称'}));
 await screen.findByText('暂时无法保存名称');
 expect(screen.getByRole('textbox',{name:'会话名称'})).toHaveProperty('value','  庭前准备讨论  ');
 fireEvent.click(screen.getByRole('button',{name:'保存会话名称'}));
 await screen.findByRole('combobox',{name:'历史会话'});
 expect(rename).toHaveBeenLastCalledWith('talk','庭前准备讨论');
 view.rerender(<LitigationWorkbench {...props} thread={{...thread,title:'庭前准备讨论'}} threads={[{...thread,title:'庭前准备讨论'}]} onRenameThread={rename}/>);
 expect(screen.getByRole('option',{name:'庭前准备讨论'})).toHaveProperty('selected',true);
 expect(screen.queryByText(/正在讨论：/)).toBeNull();
});


test('closed previews reopen only from the file tree, which remains accessible in focused and collapsed modes', async()=>{
 const view=render(<LitigationWorkbench {...props}/>);
 fireEvent.click(await within(screen.getByRole('complementary',{name:'当前工作区文件'})).findByRole('button',{name:'合同.txt'}));
 await screen.findByText('原始合同内容');
 fireEvent.click(screen.getByRole('button',{name:'展开工作区'}));
 fireEvent.click(screen.getByRole('button',{name:'关闭 合同.txt'}));
 expect(screen.queryByRole('complementary',{name:'当前工作区文件'})).toBeNull();
 expect(screen.queryByRole('combobox',{name:'重新打开文件'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'显示案件文件'}));
 fireEvent.click(await within(screen.getByRole('complementary',{name:'当前工作区文件'})).findByRole('button',{name:'合同.txt'}));
 expect(await screen.findByText('原始合同内容')).toBeTruthy();
 view.unmount();
 render(<LitigationWorkbench {...props} filesCollapsed/>);
 fireEvent.click(screen.getByRole('button',{name:'显示案件文件'}));
 expect(screen.getByRole('complementary',{name:'当前工作区文件'})).toBeTruthy();
});

test('registered draft from the file tree goes to editing; an ordinary docx still opens a preview',async()=>{
 const product={id:'doc',title:'起诉状',kind:'documents' as const,path:'文书/起诉状.docx',revision:1,file_hash:'h',document:{type:'doc',content:[{type:'paragraph'}]},external_update:false,updated_at:'now'};
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});
 vi.mocked(editorClient.read).mockResolvedValue(product);
 vi.mocked(editorClient.openWord).mockResolvedValue({product,content:'QQ=='});
 localStorage.setItem('verilex:drafting:w1',JSON.stringify({closedIds:['doc']}));
 render(<LitigationWorkbench {...props} onStartWork={vi.fn()}/>);
 fireEvent.click(await screen.findByRole('button',{name:'文书'}));
 fireEvent.click(await screen.findByRole('button',{name:'起诉状.docx'}));
 await waitFor(()=>expect(editorClient.openWord).toHaveBeenCalledWith('w1','doc','h'));
 expect(screen.getByRole('region',{name:'文书起草工作区'})).toBeTruthy();
 expect(getWorkspaceFilePreview).not.toHaveBeenCalled();
 expect(screen.queryByRole('combobox',{name:'打开已关闭文书'})).toBeNull();
 vi.mocked(editorClient.list).mockResolvedValue({products:[],errors:[]});
 fireEvent.click(screen.getByRole('button',{name:'起诉状.docx'}));
 await waitFor(()=>expect(getWorkspaceFilePreview).toHaveBeenCalledWith('w1','文书/起诉状.docx'));
});

test('a slow document lookup cannot replace a later material preview',async()=>{
 let finish!:(value:any)=>void;
 render(<LitigationWorkbench {...props} onStartWork={vi.fn()}/>);
 fireEvent.click(await screen.findByRole('button',{name:'文书'}));
 vi.mocked(editorClient.list).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
 fireEvent.click(await screen.findByRole('button',{name:'起诉状.docx'}));
 fireEvent.click(within(screen.getByRole('complementary',{name:'当前工作区文件'})).getByRole('button',{name:'合同.txt'}));
 await screen.findByText('原始合同内容');
 finish({products:[{id:'late',path:'文书/起诉状.docx'}],errors:[]});
 await waitFor(()=>expect(getWorkspaceFilePreview).toHaveBeenLastCalledWith('w1','合同.txt'));
 expect(editorClient.openWord).not.toHaveBeenCalled();
});
