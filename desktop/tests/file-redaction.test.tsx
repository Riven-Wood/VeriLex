import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import {FileRedactionView} from '../src/views/FileRedactionView';
import type {RedactionApi} from '../src/runtime/redactionClient';
import {saveRedaction,chooseRedactionDirectory,defaultRedactionDirectory,listRedactionExports,openRedaction,deleteRedactionExport} from '../src/platform/redactionExport';
vi.mock('../src/platform/redactionExport',()=>({saveRedaction:vi.fn(async()=>null),openRedaction:vi.fn(async()=>undefined),chooseRedactionDirectory:vi.fn(),defaultRedactionDirectory:vi.fn(async()=>'/Users/test/Desktop'),listRedactionExports:vi.fn(async()=>[]),deleteRedactionExport:vi.fn(async()=>undefined)}));
vi.mock('@tauri-apps/api/webview',()=>({getCurrentWebview:()=>({onDragDropEvent:async()=>()=>{}})}));
afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals();});
const api:RedactionApi={process:vi.fn(async()=>({filename:'脱敏修订版.docx',content:'dGVzdA=='}))};
const choose=()=>fireEvent.change(screen.getByLabelText('选择 Word 或 Markdown 文件'),{target:{files:[new File(['docx'],'测试合同.docx')]}});

test('single export applies custom pairs and selected format without a review or mapping',async()=>{
 render(<FileRedactionView api={api}/>);choose();
 fireEvent.change(screen.getByLabelText('要脱敏的文字'),{target:{value:'张三'}});
 fireEvent.change(screen.getByLabelText('脱敏后的替代文字'),{target:{value:'客户'}});
 fireEvent.click(screen.getByRole('button',{name:'添加'}));
 expect(screen.getByText('张三 → 客户')).toBeTruthy();
 fireEvent.change(screen.getByLabelText('导出文件名'),{target:{value:'输出'}});
 fireEvent.click(screen.getByRole('button',{name:'导出 Word 修订版'}));
 await waitFor(()=>expect(api.process).toHaveBeenCalledWith(expect.any(File),expect.any(Array),[{old:'张三',new:'客户'}],'word'));
 expect(vi.mocked(api.process).mock.calls.at(-1)?.[1]).toContain('organization');
 await waitFor(()=>expect(saveRedaction).toHaveBeenCalledWith({filename:'输出.docx',content:'dGVzdA=='},null));
 expect(screen.queryByText('改动清单')).toBeNull();expect(screen.queryByText('最近脱敏')).toBeNull();
 expect(screen.getByText(/被删除的原文仍可查看或恢复/)).toBeTruthy();
});

test('history deletion requires confirmation and retains the entry on failure',async()=>{
 vi.stubGlobal('__TAURI_INTERNALS__',{});
 vi.mocked(listRedactionExports).mockResolvedValue([{path:'/tmp/输出.docx',filename:'输出.docx',saved_at:1},{path:'/tmp/其他.md',filename:'其他.md',saved_at:2}]);
 vi.mocked(deleteRedactionExport).mockRejectedValueOnce(new Error('删除失败')).mockResolvedValueOnce(undefined);
 render(<FileRedactionView api={api}/>);
 fireEvent.click(await screen.findByRole('button',{name:'删除 输出.docx 的记录'}));
 fireEvent.click(screen.getByRole('button',{name:'取消'}));
 expect(deleteRedactionExport).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'删除 输出.docx 的记录'}));
 fireEvent.click(screen.getByRole('button',{name:'确认删除记录'}));
 expect((await screen.findByRole('alert')).textContent).toContain('删除失败');
 expect(screen.getByText('输出.docx')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'确认删除记录'}));
 await waitFor(()=>expect(screen.queryByText('输出.docx')).toBeNull());
 expect(screen.getByText('其他.md')).toBeTruthy();
 expect(deleteRedactionExport).toHaveBeenCalledWith('/tmp/输出.docx');
});
test('empty replacement deletes and unfinished input blocks export',async()=>{
 render(<FileRedactionView api={api}/>);choose();
 fireEvent.change(screen.getByLabelText('要脱敏的文字'),{target:{value:'秘密'}});
 fireEvent.click(screen.getByRole('button',{name:'导出 Word 修订版'}));
 expect((await screen.findByRole('alert')).textContent).toContain('请先添加');
 expect(api.process).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'添加'}));
 fireEvent.click(screen.getByRole('checkbox',{name:'导出为 Markdown'}));
 fireEvent.click(screen.getByRole('button',{name:'导出 Markdown 文件'}));
 await waitFor(()=>expect(api.process).toHaveBeenCalledWith(expect.any(File),expect.any(Array),[{old:'秘密',new:''}],'markdown'));
 await waitFor(()=>expect(saveRedaction).toHaveBeenCalledWith({filename:'脱敏版.md',content:'dGVzdA=='},null));
});
test('history lists saved paths and actions and output directory survives cancellation',async()=>{
 vi.stubGlobal('__TAURI_INTERNALS__',{});
 vi.mocked(listRedactionExports).mockResolvedValue([{path:'/tmp/输出.docx',filename:'输出.docx',saved_at:1}]);
 render(<FileRedactionView api={api}/>);
 expect(await screen.findByRole('region',{name:'脱敏历史'})).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'用默认应用打开'}));fireEvent.click(screen.getByRole('button',{name:'在访达显示'}));
 expect(openRedaction).toHaveBeenCalledWith('/tmp/输出.docx');expect(openRedaction).toHaveBeenCalledWith('/tmp/输出.docx',true);
 vi.mocked(chooseRedactionDirectory).mockResolvedValueOnce('/tmp/交付').mockResolvedValueOnce(null);
 await screen.findByText('/Users/test/Desktop');
 fireEvent.click(screen.getByRole('button',{name:'更换目录'}));await screen.findByText('/tmp/交付');
 await waitFor(()=>expect(screen.getByRole('button',{name:'更换目录'}).hasAttribute('disabled')).toBe(false));
 fireEvent.click(screen.getByRole('button',{name:'更换目录'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'更换目录'}).hasAttribute('disabled')).toBe(false));
 expect(screen.getByText('/tmp/交付')).toBeTruthy();
});

test('desktop is the default export directory and can be changed',async()=>{
 vi.stubGlobal('__TAURI_INTERNALS__',{});
 render(<FileRedactionView api={api}/>);choose();
 await screen.findByText('/Users/test/Desktop');
 fireEvent.click(screen.getByRole('button',{name:'导出 Word 修订版'}));
 await waitFor(()=>expect(saveRedaction).toHaveBeenCalledWith(expect.anything(),'/Users/test/Desktop'));
});

test('desktop lookup failure requires a directory selection',async()=>{
 vi.stubGlobal('__TAURI_INTERNALS__',{});
 vi.mocked(defaultRedactionDirectory).mockRejectedValueOnce(new Error('unavailable'));
 render(<FileRedactionView api={api}/>);choose();
 expect((await screen.findByRole('alert')).textContent).toContain('无法找到桌面');
 expect(screen.getByRole('button',{name:'导出 Word 修订版'}).hasAttribute('disabled')).toBe(true);
 vi.mocked(chooseRedactionDirectory).mockResolvedValueOnce('/tmp/交付');
 fireEvent.click(screen.getByRole('button',{name:'选择目录'}));
 await screen.findByText('/tmp/交付');
 expect(screen.getByRole('button',{name:'导出 Word 修订版'}).hasAttribute('disabled')).toBe(false);
});

test('Markdown input exports directly as Markdown and switching back to Word works',async()=>{
 render(<FileRedactionView api={api}/>);
 const input=screen.getByLabelText('选择 Word 或 Markdown 文件');
 fireEvent.change(input,{target:{files:[new File(['# 张三'],'测试.MD')]}});
 expect(screen.getByText('测试.MD')).toBeTruthy();
 expect((screen.getByRole('checkbox',{name:'导出为 Markdown'}) as HTMLInputElement).disabled).toBe(true);
 fireEvent.change(screen.getByLabelText('要脱敏的文字'),{target:{value:'张三'}});
 fireEvent.change(screen.getByLabelText('脱敏后的替代文字'),{target:{value:'某人'}});
 fireEvent.click(screen.getByRole('button',{name:'添加'}));
 fireEvent.click(screen.getByRole('button',{name:'导出 Markdown 文件'}));
 await waitFor(()=>expect(api.process).toHaveBeenCalledWith(expect.any(File),expect.any(Array),[{old:'张三',new:'某人'}],'markdown'));
 await waitFor(()=>expect(saveRedaction).toHaveBeenCalledWith({filename:'脱敏版.md',content:'dGVzdA=='},null));
 await waitFor(()=>expect(screen.getByRole('button',{name:'导出 Markdown 文件'}).hasAttribute('disabled')).toBe(false));
 fireEvent.change(input,{target:{files:[new File(['doc'],'合同.docx')]}});
 expect(screen.getByRole('button',{name:'导出 Word 修订版'})).toBeTruthy();
});
