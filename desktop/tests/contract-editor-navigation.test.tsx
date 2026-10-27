import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import * as React from 'react';
import {ContractReviewView} from '../src/views/ContractReviewView';
import {contractReviewApi} from '../src/runtime/contractReviewClient';
import {wordDocumentApi} from '../src/runtime/wordDocumentClient';

const editor=vi.hoisted(()=>({flush:vi.fn()}));
vi.mock('../src/components/wordEditor/WordDocumentEditor',()=>({WordDocumentEditor:React.forwardRef(({document,onReady}:any,ref:any)=>{
  React.useEffect(()=>onReady?.(true),[]);React.useImperativeHandle(ref,()=>({flush:editor.flush}));
  return <div data-testid="word-editor">{document.filename}</div>;
})}));
vi.mock('../src/components/ContractAnnotationPreview',()=>({ContractAnnotationPreview:()=> <div>原文预览</div>}));
vi.mock('../src/runtime/wordDocumentClient',()=>({wordDocumentApi:{list:vi.fn(),get:vi.fn(),open:vi.fn(),forReview:vi.fn()}}));
vi.mock('../src/runtime/contractReviewClient',()=>({DEFAULT_REVIEW_SKILL:'通用合同审查',contractReviewApi:{list:vi.fn(),get:vi.fn(),listSkills:vi.fn()}}));

const document={id:'doc-current',filename:'编辑中的合同.docx',content:'QQ==',revision:1,updated_at:'t'};
const opinion={id:'o',clause_label:'验收条款',clause_excerpt:'合成条款',body:'合成意见',citations:[],adopted:true};
const review={id:'r',thread_id:'t',filename:'历史审查合同.docx',skill:'通用合同审查',status:'completed',party:'neutral',strictness:'normal',signature:'V',retrieval_enabled:false,opinion_count:1,opinions:[opinion]};
const words=vi.mocked(wordDocumentApi), reviews=vi.mocked(contractReviewApi);
beforeEach(()=>{
  localStorage.clear();localStorage.setItem("verilex:contract-assistant-open:v1", "true");vi.clearAllMocks();
  editor.flush.mockReset().mockResolvedValue({document,bytes:new Uint8Array([65])});
  words.list.mockResolvedValue([]);words.get.mockResolvedValue(document);words.open.mockResolvedValue(document);words.forReview.mockResolvedValue(document);
  reviews.list.mockResolvedValue([review] as never);reviews.get.mockResolvedValue(review as never);reviews.listSkills.mockResolvedValue([]);
});
afterEach(()=>{cleanup();localStorage.clear();});
async function open(withReview=false){
  const select=vi.fn();
  render(<ContractReviewView thread={withReview?{id:'t',status:'idle'} as never:null} hasModel renderAssistant={()=>null} onStartReview={vi.fn()} onSelectReview={select}/>);
  if(!withReview)fireEvent.change(screen.getByLabelText('选择合同文件'),{target:{files:[new File(['a'],'编辑中的合同.docx')]}});
  await screen.findByTestId('word-editor');
  return select;
}

describe('contract editor navigation preserves unsaved input',()=>{
  it('does not import a dropped replacement until the current snapshot has saved, and keeps the editor on failure',async()=>{
    await open();words.open.mockClear();
    let reject!:(error:Error)=>void;
    editor.flush.mockImplementationOnce(()=>new Promise((_,no)=>{reject=no;}));
    fireEvent.drop(screen.getByLabelText('合同审查'),{dataTransfer:{files:[new File(['b'],'另一份.docx')]}});
    expect(editor.flush).toHaveBeenCalledTimes(1);expect(words.open).not.toHaveBeenCalled();
    expect(screen.getByTestId('word-editor').textContent).toBe(document.filename);
    await act(async()=>reject(new Error('编辑尚未保存')));
    expect(await screen.findByText('编辑尚未保存')).toBeTruthy();expect(words.open).not.toHaveBeenCalled();
    fireEvent.drop(screen.getByLabelText('合同审查'),{dataTransfer:{files:[new File(['b'],'另一份.docx')]}});
    await waitFor(()=>expect(words.open).toHaveBeenCalledTimes(1));
    expect(editor.flush).toHaveBeenCalledTimes(2);
  });

  it('waits for saving before selecting review history and stays on the existing editor when saving fails',async()=>{
    const select=await open();editor.flush.mockRejectedValueOnce(new Error('编辑尚未保存'));
    fireEvent.click(screen.getByLabelText('审查历史'));
    fireEvent.click(screen.getByText(review.filename));
    expect(await screen.findByText('编辑尚未保存')).toBeTruthy();expect(select).not.toHaveBeenCalled();
    expect(screen.getByTestId('word-editor')).toBeTruthy();
    let finish!:(value:unknown)=>void;editor.flush.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    fireEvent.click(screen.getByText(review.filename));expect(select).not.toHaveBeenCalled();
    await act(async()=>finish({document,bytes:new Uint8Array([65])}));
    expect(select).toHaveBeenCalledWith('t');expect(editor.flush).toHaveBeenCalledTimes(2);
  });

  it('keeps the editor mounted while reading review clauses',async()=>{
    await open(true);
    const originalEditor = screen.getByTestId('word-editor');
    expect(screen.queryByRole('button',{name:'本次审查原文与批注'})).toBeNull();
    expect(screen.queryByRole('button',{name:'展开意见栏'})).toBeNull();
    expect(screen.getByTestId('word-editor')).toBe(originalEditor);
    expect(screen.queryByText('原文预览')).toBeNull();
    expect(editor.flush).not.toHaveBeenCalled();
  });
});
