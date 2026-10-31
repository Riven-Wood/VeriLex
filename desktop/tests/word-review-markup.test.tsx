import {act,cleanup,render,screen,waitFor} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {createRef} from 'react';
import {WordDocumentEditor, type WordEditorHandle} from '../src/components/wordEditor/WordDocumentEditor';
import {wordDocumentApi} from '../src/runtime/wordDocumentClient';
vi.mock('../src/runtime/api',()=>({runtimeRequest:vi.fn()}));
vi.mock('../src/platform/contractReviewExport',()=>({saveContractReview:vi.fn(),wordCopyFilename:(s:string)=>s}));
afterEach(()=>{cleanup();vi.restoreAllMocks();});
describe('completed review to Word markup',()=>{
 it('AI 修改与文档保存以同一编号提交，旧版本不再写入',async()=>{
  const document={id:crypto.randomUUID(),filename:'合成.docx',content:'QQ==',revision:0,updated_at:'now'};
  const edit={id:'ai-edit',review_id:'r',document_id:document.id,revision:0,selection:{texts:['原文'],start:0,end:0,from:0,to:2},before_text:'原文',after_text:'改后',reason:'',status:'pending'};
  const save=vi.spyOn(wordDocumentApi,'save').mockResolvedValue({id:document.id,revision:1});
  const ref=createRef<WordEditorHandle>();render(<WordDocumentEditor document={document} ref={ref}/>);
  const iframe=screen.getByTitle('合同 Word 编辑器') as HTMLIFrameElement;let channel='';
  const receive=(data:object)=>window.dispatchEvent(new MessageEvent('message',{data:{channel,...data},origin:location.origin,source:iframe.contentWindow}));
  vi.spyOn(iframe.contentWindow!,'postMessage').mockImplementation(message=>{
    if(message.type==='open'){channel=message.channel;queueMicrotask(()=>receive({type:'ready'}));}
    if(message.type==='apply-ai-edit')queueMicrotask(()=>receive({type:'snapshot',bytes:new Uint8Array([66]),requestId:message.requestId,aiEditId:edit.id}));
  });
  await act(async()=>receive({type:'word-editor-loaded'}));
  await act(async()=>ref.current!.applyAiEdit(edit));
  expect(save).toHaveBeenCalledWith(document.id,0,new Uint8Array([66]),undefined,'ai-edit');
  await expect(ref.current!.applyAiEdit(edit)).rejects.toThrow('文档已改变');
  expect(save).toHaveBeenCalledTimes(1);
 });
 it.each([false,true])('applies and saves once with quiet=%s',async quiet=>{
  const document={id:crypto.randomUUID(),filename:'合成.docx',content:'QQ==',revision:0,updated_at:'now'};
  const plan={review_id:'review',document_id:document.id,revision:0,source:'QQ==',author:'AI',opinions:[{id:'op',anchor_text:'文字',occurrence:null,replacement_text:'改定文字',comment:'理由'}]};
  const getPlan=vi.spyOn(wordDocumentApi,'markupPlan').mockResolvedValue(plan);
  const save=vi.spyOn(wordDocumentApi,'save').mockResolvedValue({id:document.id,revision:1});
  const props={document,quiet,hideHeader:quiet,review:{id:'review',status:'running',opinion_count:1}};
  const view=render(<WordDocumentEditor {...props}/>);
  const iframe=screen.getByTitle('合同 Word 编辑器') as HTMLIFrameElement;
  let channel='';
  const receive=(data:object)=>window.dispatchEvent(new MessageEvent('message',{data:{channel,...data},origin:location.origin,source:iframe.contentWindow}));
  const post=vi.spyOn(iframe.contentWindow!,'postMessage').mockImplementation(message=>{
    if(message.type==='open') {channel=message.channel;queueMicrotask(()=>receive({type:'ready'}));}
    if(message.type==='snapshot')queueMicrotask(()=>receive({type:'snapshot',bytes:new Uint8Array([65]),requestId:message.requestId}));
    if(message.type==='apply-review')queueMicrotask(()=>receive({type:'snapshot',bytes:new Uint8Array([66]),requestId:message.requestId,markup:{review_id:'review',applied:1,skipped:0}}));
  });
  await act(async()=>{receive({type:'word-editor-loaded'});});
  expect(getPlan).not.toHaveBeenCalled();
  view.rerender(<WordDocumentEditor {...props} review={{...props.review,status:'completed'}}/>);
  await waitFor(()=>expect(save).toHaveBeenCalledWith(document.id,0,new Uint8Array([66]),{review_id:'review',applied:1,skipped:0}));
  if(quiet)expect(screen.queryByText(/已写入 1 条审查意见/)).toBeNull();else await screen.findByText(/已写入 1 条审查意见/);
  expect(post.mock.calls.find(([m])=>m.type==='open')?.[0].historyToolbar).toBe(true);
  expect(post.mock.calls.filter(([m])=>m.type==='apply-review')).toHaveLength(1);
  view.rerender(<WordDocumentEditor {...props} review={{...props.review,status:'completed',opinion_count:2}}/>);
  expect(getPlan).toHaveBeenCalledTimes(1);
 });
});
