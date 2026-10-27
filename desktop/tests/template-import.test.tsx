import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {TemplateImport} from '../src/components/editor/TemplateImport';
import {editorClient} from '../src/components/editor/client';
const native=vi.hoisted(()=>({listen:vi.fn(),read:vi.fn(),off:vi.fn()}));
vi.mock('@tauri-apps/api/webview',()=>({getCurrentWebview:()=>({onDragDropEvent:native.listen})}));
vi.mock('@tauri-apps/api/core',()=>({invoke:native.read}));
vi.mock('../src/components/editor/client',()=>({editorClient:{importTemplate:vi.fn()}}));
const saved=vi.fn(),close=vi.fn();
beforeEach(()=>{HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};native.listen.mockResolvedValue(native.off);});
afterEach(()=>{cleanup();vi.clearAllMocks();Reflect.deleteProperty(globalThis,'__TAURI_INTERNALS__');});
const mount=()=>render(<TemplateImport workspaceId="w" onSaved={saved} onClose={close}/>);
test('DOM drop and editable title retry the same file after failed save',async()=>{
 vi.mocked(editorClient.importTemplate).mockRejectedValueOnce(new Error('磁盘空间不足')).mockResolvedValue({name:'通用格式',path:'我的文书模板/通用格式.docx'});
 const {container}=mount();const file=new File(['bytes'],'外部.docx');
 fireEvent.drop(container.querySelector('.drafting-upload-drop')!,{dataTransfer:{files:[file]}});
 fireEvent.change(screen.getByLabelText('模板名称'),{target:{value:'通用格式'}});
 fireEvent.click(screen.getByText('保存为模板'));await screen.findByText('磁盘空间不足');
 expect(saved).not.toHaveBeenCalled();fireEvent.click(screen.getByText('保存为模板'));
 await waitFor(()=>expect(saved).toHaveBeenCalledOnce());expect(editorClient.importTemplate).toHaveBeenLastCalledWith('w',file,'通用格式');
});
test('invalid, empty and multiple files never upload',()=>{
 const {container}=mount(),zone=container.querySelector('.drafting-upload-drop')!;
 for(const files of [[new File(['x'],'old.doc')],[new File([],'empty.docx')],[new File(['a'],'a.docx'),new File(['b'],'b.docx')]]){
  fireEvent.drop(zone,{dataTransfer:{files}});expect(screen.getByRole('alert')).toBeTruthy();expect(screen.getByText('保存为模板')).toHaveProperty('disabled',true);
 }
 expect(editorClient.importTemplate).not.toHaveBeenCalled();
});
test('native drop is bounded to dialog and strips the native timestamp header',async()=>{
 Reflect.set(globalThis,'__TAURI_INTERNALS__',{});native.read.mockResolvedValue([0,0,0,0,0,0,0,0,80,75,3,4]);
 const {container,unmount}=mount();const zone=container.querySelector('[data-template-drop]')!;
 vi.spyOn(zone,'getBoundingClientRect').mockReturnValue({left:100,top:100,right:400,bottom:400} as DOMRect);
 await waitFor(()=>expect(native.listen).toHaveBeenCalled());const handler=native.listen.mock.calls[0][0];
 act(()=>handler({payload:{type:'drop',paths:['/tmp/test.docx'],position:{x:0,y:0}}}));expect(native.read).not.toHaveBeenCalled();
 await act(async()=>handler({payload:{type:'drop',paths:['/tmp/test.docx'],position:{x:200,y:200}}}));
 expect(screen.getByLabelText('模板名称')).toHaveProperty('value','test');
 fireEvent.click(screen.getByText('保存为模板'));await waitFor(()=>expect(editorClient.importTemplate).toHaveBeenCalled());
 const file=vi.mocked(editorClient.importTemplate).mock.calls[0][1];expect(file.size).toBe(4);expect(file.name).toBe('test.docx');
 unmount();expect(native.off).toHaveBeenCalled();
});
