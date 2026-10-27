import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import * as React from 'react';
import {ContractReviewTasks} from '../src/views/ContractReviewTasks';
import {contractReviewApi} from '../src/runtime/contractReviewClient';
import {wordDocumentApi} from '../src/runtime/wordDocumentClient';
const controls=vi.hoisted(()=>({flush:vi.fn(),editor:vi.fn()}));
vi.mock('@tauri-apps/api/webview',()=>({getCurrentWebview:()=>({onDragDropEvent:vi.fn().mockResolvedValue(()=>{})})}));
vi.mock('@tauri-apps/api/core',()=>({invoke:vi.fn()}));
vi.mock('../src/runtime/contractReviewClient',async()=>({...await vi.importActual('../src/runtime/contractReviewClient'),contractReviewApi:{list:vi.fn(),get:vi.fn(),listSkills:vi.fn().mockResolvedValue([])}}));
vi.mock('../src/runtime/wordDocumentClient',()=>({wordDocumentApi:{list:vi.fn().mockResolvedValue([]),open:vi.fn(),get:vi.fn(),forReview:vi.fn(),remove:vi.fn()}}));
vi.mock('../src/components/wordEditor/WordDocumentEditor',()=>({WordDocumentEditor:React.forwardRef((props:any,ref)=>{
  controls.editor(props);React.useEffect(()=>{props.onReady?.(true);return()=>props.onReady?.(false);},[]);
  React.useImperativeHandle(ref,()=>({flush:async()=>({document:await controls.flush(props.document),bytes:new Uint8Array([65])}),exportCopy:vi.fn()}));
  return <div data-testid="editor">{props.document.filename}</div>;
})}));
const api=vi.mocked(contractReviewApi), word=vi.mocked(wordDocumentApi);
const doc=(name='甲合同.docx')=>({id:name,filename:name,content:'QQ==',revision:0,updated_at:'t'});
const review=(id:string)=>({id,thread_id:id,filename:id+'.docx',skill:'通用合同审查',status:'completed',created_at:'t',updated_at:'t',opinion_count:0,party:'neutral',strictness:'normal',signature:'V',retrieval_enabled:false,requirements:id+'要求',opinions:[]});
const props={hasModel:true,renderAssistant:()=> <span data-testid="controller"/>,onStartReview:vi.fn(),onSelectReview:vi.fn()};
beforeEach(()=>{localStorage.clear();vi.clearAllMocks();api.list.mockResolvedValue([]);controls.flush.mockImplementation(async d=>({...d,revision:d.revision+1}));word.open.mockImplementation(async file=>doc(file.name));word.get.mockImplementation(async id=>doc(id));word.forReview.mockImplementation(async id=>doc(id+'.docx'));api.get.mockImplementation(async id=>review(id) as any);});
afterEach(cleanup);
const requirements=()=>screen.getByLabelText('审查要求（选填）') as HTMLTextAreaElement;
const upload=async(name='甲合同.docx')=>{fireEvent.change(screen.getByLabelText('选择合同文件'),{target:{files:[new File(['fixture'],name)]}});await screen.findByTestId('editor');};
const newTab=async()=>{fireEvent.click(screen.getByRole('button',{name:'新建任务',exact:true}));await screen.findByText('把合同拖到这里打开编辑');};
it('新建、切换、关闭保留独立要求和最新保存内容，最后一个关闭后为空任务',async()=>{
 render(<ContractReviewTasks {...props} thread={null}/>);await screen.findByText('把合同拖到这里打开编辑');
 await upload();fireEvent.change(requirements(),{target:{value:'甲要求'}});await newTab();
 expect(controls.flush).toHaveBeenCalledTimes(1);expect(requirements().value).toBe('');await upload('乙合同.docx');fireEvent.change(requirements(),{target:{value:'乙要求'}});
 fireEvent.click(screen.getByRole('tab',{name:'甲合同.docx'}));await waitFor(()=>expect(requirements().value).toBe('甲要求'));
 expect(controls.editor.mock.lastCall?.[0].document.revision).toBe(1);
 fireEvent.click(screen.getByRole('button',{name:'关闭任务：甲合同.docx'}));await waitFor(()=>expect(requirements().value).toBe('乙要求'));
 expect(screen.getAllByRole('tab')).toHaveLength(1);expect(word.remove).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'关闭任务：乙合同.docx'}));await screen.findByText('把合同拖到这里打开编辑');expect(requirements().value).toBe('');expect(screen.getAllByRole('tab')).toHaveLength(1);
});
it.each(['新建','切换','关闭'])('保存失败阻止%s，重试可继续',async action=>{
 render(<ContractReviewTasks {...props} thread={null}/>);await screen.findByText('把合同拖到这里打开编辑');await upload();await newTab();await upload('乙合同.docx');
 controls.flush.mockRejectedValueOnce(new Error('输入没有保存'));
 const button=()=>action==='新建'?screen.getByRole('button',{name:'新建任务',exact:true}):action==='关闭'?screen.getByRole('button',{name:'关闭任务：乙合同.docx'}):screen.getByRole('tab',{name:'甲合同.docx'});
 fireEvent.click(button());await screen.findByText('输入没有保存');expect(screen.getByTestId('editor').textContent).toBe('乙合同.docx');expect(screen.getAllByRole('tab')).toHaveLength(2);
 fireEvent.click(button());await waitFor(()=>expect(screen.queryByText('输入没有保存')).toBeNull());
});
it('重开工作台恢复文档和未提交的要求，关闭非当前页不扰动当前编辑器',async()=>{
 const view=render(<ContractReviewTasks {...props} thread={null}/>);await screen.findByText('把合同拖到这里打开编辑');await upload();await newTab();await upload('乙合同.docx');fireEvent.change(requirements(),{target:{value:'乙未提交要求'}});
 const editor=screen.getByTestId('editor');fireEvent.click(screen.getByRole('button',{name:'关闭任务：甲合同.docx'}));expect(screen.getByTestId('editor')).toBe(editor);
 await waitFor(()=>expect(localStorage.getItem('verilex:contract-tasks:v1')).toContain('乙未提交要求'));
 view.unmount();render(<ContractReviewTasks {...props} thread={null}/>);await screen.findByTestId('editor');expect(requirements().value).toBe('乙未提交要求');expect(screen.getAllByRole('tab')).toHaveLength(1);expect(word.get).toHaveBeenCalledWith('乙合同.docx');
});
it('历史任务切换不改写另一个标签，关闭当前历史任务后可返回其他历史',async()=>{
 api.list.mockResolvedValue([review('甲'),review('乙')] as any);
 function Host(){const [thread,setThread]=React.useState<any>(null);return <ContractReviewTasks {...props} thread={thread} onSelectReview={id=>setThread({id,status:'idle'})}/>;}
 render(<Host/>);fireEvent.click(await screen.findByText('甲.docx'));await screen.findByTestId('editor');await waitFor(()=>expect(requirements().value).toBe('甲要求'));
 fireEvent.click(screen.getByText('乙.docx'));await waitFor(()=>expect(requirements().value).toBe('乙要求'));
 fireEvent.click(screen.getByRole('tab',{name:'甲.docx'}));await waitFor(()=>expect(requirements().value).toBe('甲要求'));
 fireEvent.click(screen.getByRole('button',{name:'关闭任务：甲.docx'}));await waitFor(()=>expect(requirements().value).toBe('乙要求'));
 expect(screen.queryByRole('tab',{name:'甲.docx'})).toBeNull();
});
it('开始审查绑定当前标签并挂载控制器，审查进行中禁用标签操作',async()=>{
 function Host(){const [thread,setThread]=React.useState<any>(null);return <ContractReviewTasks {...props} thread={thread} onStartReview={async()=>{api.list.mockResolvedValue([review('新审查')] as any);setThread({id:'新审查',status:'running'});}}/>;}
 render(<Host/>);await screen.findByText('把合同拖到这里打开编辑');await upload();fireEvent.click(screen.getByRole('button',{name:'开始审查'}));
 await screen.findByTestId('controller');expect(screen.getAllByRole('tab')).toHaveLength(1);await waitFor(()=>expect(requirements().value).toBe('新审查要求'));
 expect((screen.getByRole('button',{name:'新建任务',exact:true}) as HTMLButtonElement).disabled).toBe(true);
});
