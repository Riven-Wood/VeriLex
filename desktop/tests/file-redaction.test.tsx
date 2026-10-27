import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import {FileRedactionView} from '../src/views/FileRedactionView';
import type {RedactionApi,RedactionJob} from '../src/runtime/redactionClient';
import {saveRedaction,chooseRedactionDirectory} from '../src/platform/redactionExport';
vi.mock('../src/platform/redactionExport',()=>({saveRedaction:vi.fn(async()=>null),openRedaction:vi.fn(),chooseRedactionDirectory:vi.fn()}));
vi.mock('@tauri-apps/api/webview',()=>({getCurrentWebview:()=>({onDragDropEvent:async()=>()=>{}})}));
afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals();});
const item={id:'one',key:'word/document.xml:0:0',source_start:0,source_end:2,start:0,end:6,old:'张三',kind:'person',placeholder:'【人名1】',keep:false};
function fixture(){
 const job:RedactionJob={id:'job',filename:'测试合同.docx',created_at:'2026-09-15',revision:1,restorable:true,mapping:[item,{...item,id:'two',source_start:3}],kinds:['person'],custom:[]};
 const api:RedactionApi={list:vi.fn(async()=>[]),get:vi.fn(async()=>job),create:vi.fn(async()=>job),update:vi.fn(async()=>({...job,revision:2,mapping:[{...item,keep:true},{...item,id:'two',source_start:3}]})),rerun:vi.fn(async()=>job),export:vi.fn(async()=>({filename:'脱敏版.docx',content:'dGVzdA=='})),deleteMapping:vi.fn(async()=>({...job,revision:2,restorable:false,mapping:null,kinds:[],custom:[]}))};return {job,api};
}
async function create(api:RedactionApi){render(<FileRedactionView api={api}/>);fireEvent.change(screen.getByLabelText('选择 Word 文件'),{target:{files:[new File(['docx'],'测试合同.docx')]}});fireEvent.click(screen.getByRole('button',{name:'开始脱敏'}));await screen.findByRole('heading',{name:'改动清单'});await waitFor(()=>expect(screen.getByRole('button',{name:'保存脱敏版'}).hasAttribute('disabled')).toBe(false));}
test('empty state and rejects PDF without processing',async()=>{const {api}=fixture();render(<FileRedactionView api={api}/>);expect(screen.getByRole('button',{name:'选择文件'})).toBeTruthy();fireEvent.change(screen.getByLabelText('选择 Word 文件'),{target:{files:[new File(['pdf'],'扫描.pdf')]}});expect((await screen.findByRole('alert')).textContent).toContain('请选择 .doc 或 .docx');expect(api.create).not.toHaveBeenCalled();});
test('one occurrence restored without changing peer and cancel save is honest',async()=>{const {api}=fixture();await create(api);fireEvent.click(screen.getAllByRole('button',{name:/还原此处 张三/})[0]);await screen.findByText('保留原文');expect(screen.getAllByRole('button',{name:/还原此处 张三/})).toHaveLength(1);await waitFor(()=>expect(screen.getByRole('button',{name:'保存脱敏版'}).hasAttribute('disabled')).toBe(false));fireEvent.click(screen.getByRole('button',{name:'保存脱敏版'}));expect(await screen.findByText('已取消保存。')).toBeTruthy();expect(saveRedaction).toHaveBeenCalledTimes(1);});
test('markdown warns before conversion; changed options block stale exports',async()=>{const {api}=fixture();await create(api);fireEvent.click(screen.getByRole('checkbox',{name:'先转成 Markdown 再脱敏'}));expect(screen.getByText(/节省 token/).textContent).toContain('可能丢失结构性内容');fireEvent.change(screen.getByRole('textbox',{name:/自定义词/}),{target:{value:'李四'}});expect(screen.getByRole('button',{name:'保存 Markdown 版'}).hasAttribute('disabled')).toBe(true);expect(screen.getByRole('button',{name:'应用选项并重新处理'}).hasAttribute('disabled')).toBe(false);});
test('permanent export and mapping deletion require the specific action',async()=>{const {api}=fixture();await create(api);fireEvent.click(screen.getByRole('button',{name:'生成永久抹除版'}));expect(api.export).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'取消'}));fireEvent.click(screen.getByRole('button',{name:'删除对照表'}));fireEvent.click(screen.getByRole('button',{name:'确认删除'}));await screen.findByText(/仍可保存全占位符/);expect(screen.queryByRole('button',{name:'整份还原文字'})).toBeNull();});
test('changing options closes permanent confirmation and cannot export stale job',async()=>{const {api}=fixture();await create(api);fireEvent.click(screen.getByRole('button',{name:'生成永久抹除版'}));fireEvent.change(screen.getByRole('textbox',{name:/自定义词/}),{target:{value:'新敏感词'}});expect(screen.queryByRole('button',{name:'确认生成'})).toBeNull();expect(api.export).not.toHaveBeenCalled();});
test('unmount before export completes does not open a stale save dialog',async()=>{const {api}=fixture();let finish!:(v:{filename:string;content:string})=>void;vi.mocked(api.export).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));await create(api);fireEvent.click(screen.getByRole('button',{name:'保存脱敏版'}));await waitFor(()=>expect(api.export).toHaveBeenCalled());cleanup();finish({filename:'脱敏版.docx',content:'dGVzdA=='});await new Promise(resolve=>setTimeout(resolve,0));expect(saveRedaction).not.toHaveBeenCalled();});

test('context search and status filter only change the visible list, never export scope',async()=>{
 const {job,api}=fixture();
 job.mapping=[{...item,context_before:'借款人：',context_after:'应归还【银行账号1】。'},{...item,id:'two',source_start:3,keep:true,context_before:'担保人：',context_after:'承担责任。'}];
 await create(api);
 expect(screen.getAllByLabelText('原文上下文')).toHaveLength(2);
 fireEvent.change(screen.getByRole('searchbox',{name:'搜索改动清单'}),{target:{value:'担保人'}});
 expect(screen.getAllByLabelText('原文上下文')).toHaveLength(1);
 expect(screen.queryByRole('button',{name:/还原此处/})).toBeNull();
 fireEvent.change(screen.getByRole('combobox',{name:'筛选改动状态'}),{target:{value:'redacted'}});
 expect(screen.getByText('没有符合筛选条件的改动。')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'保存脱敏版'}));
 await waitFor(()=>expect(api.export).toHaveBeenCalledWith(job,'placeholder'));
});

test('updating one occurrence preserves filters and changing the document resets them',async()=>{
 const {job,api}=fixture();await create(api);
 fireEvent.change(screen.getByRole('searchbox',{name:'搜索改动清单'}),{target:{value:'张三'}});
 fireEvent.change(screen.getByRole('combobox',{name:'筛选改动状态'}),{target:{value:'redacted'}});
 fireEvent.click(screen.getAllByRole('button',{name:/还原此处/})[0]);
 await waitFor(()=>expect(screen.getAllByRole('button',{name:/还原此处/})).toHaveLength(1));
 expect((screen.getByRole('searchbox') as HTMLInputElement).value).toBe('张三');
 expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('redacted');
 vi.mocked(api.create).mockResolvedValue({...job,id:'another'});
 fireEvent.change(screen.getByLabelText('选择 Word 文件'),{target:{files:[new File(['docx'],'另一份.docx')]}});
 fireEvent.click(screen.getByRole('button',{name:'开始脱敏'}));
 await screen.findByRole('searchbox');
 expect((screen.getByRole('searchbox') as HTMLInputElement).value).toBe('');
 expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('all');
});


test('chosen output directory survives cancellation and is used for export',async()=>{
 vi.stubGlobal('__TAURI_INTERNALS__',{});
 const {api}=fixture();await create(api);
 vi.mocked(chooseRedactionDirectory).mockResolvedValueOnce('/tmp/交付').mockResolvedValueOnce(null);
 fireEvent.click(screen.getByRole('button',{name:'选择目录'}));
 await screen.findByText('/tmp/交付');
 await waitFor(()=>expect(screen.getByRole('button',{name:'更换目录'}).hasAttribute('disabled')).toBe(false));
 fireEvent.click(screen.getByRole('button',{name:'更换目录'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'更换目录'}).hasAttribute('disabled')).toBe(false));
 expect(screen.getByText('/tmp/交付')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'保存脱敏版'}));
 await waitFor(()=>expect(saveRedaction).toHaveBeenCalledWith({filename:'脱敏版.docx',content:'dGVzdA=='},'/tmp/交付'));
 await waitFor(()=>expect(screen.getByRole('button',{name:'保存时选择'}).hasAttribute('disabled')).toBe(false));
 fireEvent.click(screen.getByRole('button',{name:'保存时选择'}));
 expect(screen.getByText('保存时选择位置')).toBeTruthy();
});
