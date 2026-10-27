import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {createRef} from 'react';
import {WordDocumentEditor,type WordEditorHandle} from '../src/components/wordEditor/WordDocumentEditor';
import {saveContractReview} from '../src/platform/contractReviewExport';
import {wordDocumentApi} from '../src/runtime/wordDocumentClient';

vi.mock('../src/runtime/api',()=>({runtimeRequest:vi.fn()}));
vi.mock('../src/platform/contractReviewExport',()=>({saveContractReview:vi.fn(),wordCopyFilename:(s:string)=>s}));
beforeEach(()=>{vi.mocked(saveContractReview).mockReset().mockResolvedValue('/synthetic/copy.docx');});
afterEach(()=>{cleanup();vi.restoreAllMocks();});

async function openEditor(){
  const document={id:crypto.randomUUID(),filename:'恢复验收.docx',content:'QQ==',revision:0,updated_at:'now'};
  const save=vi.spyOn(wordDocumentApi,'save').mockResolvedValue({id:document.id,revision:1});
  const handle=createRef<WordEditorHandle>();
  render(<WordDocumentEditor ref={handle} document={document}/>);
  const iframe=screen.getByTitle('合同 Word 编辑器') as HTMLIFrameElement;
  let channel='';
  const receive=(data:object)=>window.dispatchEvent(new MessageEvent('message',{data:{channel,...data},origin:location.origin,source:iframe.contentWindow}));
  const post=vi.spyOn(iframe.contentWindow!,'postMessage').mockImplementation(message=>{
    if(message.type==='open'){channel=message.channel;queueMicrotask(()=>receive({type:'ready'}));}
  });
  await act(async()=>{receive({type:'word-editor-loaded'});});
  return {receive,post,save,handle,iframe};
}

describe('Word editor recovery export',()=>{
  it('shows pending input as unsaved before the delayed snapshot and clears it even for unchanged bytes',async()=>{
    const {receive,save}=await openEditor();expect(screen.getByText('已保存到本机')).toBeTruthy();
    await act(async()=>{receive({type:'dirty'});});expect(screen.getByText('正在保存…')).toBeTruthy();
    const leave=new Event('beforeunload',{cancelable:true});window.dispatchEvent(leave);expect(leave.defaultPrevented).toBe(true);
    await act(async()=>{receive({type:'snapshot',bytes:new Uint8Array([65])});});expect(screen.getByText('已保存到本机')).toBeTruthy();expect(save).not.toHaveBeenCalled();
  });
  it('closes iframe controls on parent-page clicks without requesting a save or stealing focus',async()=>{
    const {post,iframe}=await openEditor();post.mockClear();
    const outside=document.createElement('input');document.body.append(outside);outside.focus();
    fireEvent.pointerDown(outside);
    expect(post).toHaveBeenCalledWith(expect.objectContaining({type:'close-controls',channel:expect.any(String)}),location.origin);
    expect(document.activeElement).toBe(outside);expect(post).toHaveBeenCalledTimes(1);
    fireEvent.pointerDown(iframe);expect(post).toHaveBeenCalledTimes(1);outside.remove();
  });
  it('only remounts the retained copy after explicit discard confirmation, then permits a fresh save',async()=>{
    const {receive,post,handle,iframe}=await openEditor();
    await act(async()=>{receive({type:'snapshot',bytes:new Uint8Array([66])});});
    await act(async()=>{receive({type:'dirty'});receive({type:'error',message:'本次编辑未能保存。'});});
    const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button',{name:'重新打开可用副本'}));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('将丢弃本次未能保存的输入'));
    expect(screen.getByTitle('合同 Word 编辑器')).toBe(iframe);
    await expect(handle.current!.flush()).rejects.toThrow('本次编辑未能保存。');
    const leave=new Event('beforeunload',{cancelable:true});window.dispatchEvent(leave);expect(leave.defaultPrevented).toBe(true);

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button',{name:'重新打开可用副本'}));
    await waitFor(()=>expect(screen.getByTitle('合同 Word 编辑器')).not.toBe(iframe));
    expect(screen.queryByText('编辑已暂停')).toBeNull();expect(screen.getByText('正在打开…')).toBeTruthy();
    const fresh=screen.getByTitle('合同 Word 编辑器') as HTMLIFrameElement;
    const channel=post.mock.calls.find(([message])=>message.type==='open')![0].channel;
    const receiveFresh=(data:object)=>window.dispatchEvent(new MessageEvent('message',{data:{channel,...data},origin:location.origin,source:fresh.contentWindow}));
    const reopened=vi.spyOn(fresh.contentWindow!,'postMessage').mockImplementation(message=>{
      if(message.type==='open')queueMicrotask(()=>receiveFresh({type:'ready'}));
      if(message.type==='snapshot')queueMicrotask(()=>receiveFresh({type:'snapshot',bytes:new Uint8Array([66]),requestId:message.requestId}));
    });
    await act(async()=>{receiveFresh({type:'word-editor-loaded'});});
    expect(reopened).toHaveBeenCalledWith(expect.objectContaining({type:'open',bytes:new Uint8Array([66])}),location.origin);
    await act(async()=>{await expect(handle.current!.flush()).resolves.toMatchObject({bytes:new Uint8Array([66])});});
    expect(screen.queryByRole('button',{name:'重新打开可用副本'})).toBeNull();
  });

  it('keeps the iframe inert through a delayed disk save and shares concurrent leave requests',async()=>{
    const {receive,post,save,handle,iframe}=await openEditor();
    let finish!:(value:{id:string;revision:number})=>void;
    save.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    const first=handle.current!.flush(),second=handle.current!.flush();
    expect(second).toBe(first);expect(iframe.inert).toBe(true);
    const requests=post.mock.calls.filter(([message])=>message.type==='snapshot');
    expect(requests).toHaveLength(1);
    await act(async()=>{receive({type:'snapshot',requestId:requests[0][0].requestId,bytes:new Uint8Array([66])});});
    expect(save).toHaveBeenCalledTimes(1);expect(iframe.inert).toBe(true);
    await act(async()=>{finish({id:'saved',revision:1});await first;});
    expect(iframe.inert).toBe(false);expect((await first).bytes).toEqual(new Uint8Array([66]));
  });

  it('restores the previous inert state when a leave snapshot fails',async()=>{
    const {receive,post,handle,iframe}=await openEditor();iframe.inert=true;
    const leaving=handle.current!.flush();const rejected=expect(leaving).rejects.toThrow('未能取得编辑内容');
    const request=post.mock.calls.find(([message])=>message.type==='snapshot')![0];
    await act(async()=>{receive({type:'snapshot-error',requestId:request.requestId,message:'未能取得编辑内容'});await rejected;});
    expect(iframe.inert).toBe(true);
  });

  it('exports the last successful snapshot after a fatal error without requesting the paused editor or clearing failed-input protection',async()=>{
    const {receive,post}=await openEditor();
    await act(async()=>{receive({type:'snapshot',bytes:new Uint8Array([66])});});
    await act(async()=>{receive({type:'dirty'});receive({type:'error',message:'本次编辑未能保存。'});receive({type:'snapshot',bytes:new Uint8Array([68])});});
    const snapshots=post.mock.calls.filter(([message])=>message.type==='snapshot').length;
    fireEvent.click(screen.getByRole('button',{name:'另存可用副本'}));
    await screen.findByText('可用副本已另存，不包含本次失败的输入。');
    expect(saveContractReview).toHaveBeenCalledWith({filename:'恢复验收.docx',content:'Qg=='});
    expect(post.mock.calls.filter(([message])=>message.type==='snapshot')).toHaveLength(snapshots);
    expect(screen.getByText('编辑已暂停')).toBeTruthy();
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    const leave=new Event('beforeunload',{cancelable:true});window.dispatchEvent(leave);
    expect(leave.defaultPrevented).toBe(true);
  });

  it('requires a fresh snapshot for normal export and never silently exports older bytes when that request fails',async()=>{
    const {receive,post}=await openEditor();
    fireEvent.click(screen.getByRole('button',{name:'另存 Word'}));
    const request=post.mock.calls.find(([message])=>message.type==='snapshot')![0];
    expect(saveContractReview).not.toHaveBeenCalled();
    await act(async()=>{receive({type:'snapshot-error',requestId:request.requestId,message:'请完成当前输入后再保存。'});});
    expect(await screen.findByText('请完成当前输入后再保存。')).toBeTruthy();
    expect(saveContractReview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'另存 Word'}));
    const retry=post.mock.calls.filter(([message])=>message.type==='snapshot').at(-1)![0];
    await act(async()=>{receive({type:'snapshot',requestId:retry.requestId,bytes:new Uint8Array([67])});});
    await waitFor(()=>expect(saveContractReview).toHaveBeenCalledWith({filename:'恢复验收.docx',content:'Qw=='}));
  });

  it('settles an outstanding export on fatal error without duplicate notices or automatically falling back',async()=>{
    const {receive}=await openEditor();
    fireEvent.click(screen.getByRole('button',{name:'另存 Word'}));
    await act(async()=>{receive({type:'error',message:'本次编辑未能保存。'});});
    expect(saveContractReview).not.toHaveBeenCalled();
    expect(screen.getAllByText(/本次编辑未能保存/)).toHaveLength(1);
    expect((screen.getByRole('button',{name:'另存可用副本'}) as HTMLButtonElement).disabled).toBe(false);
  });

  it('ignores stale save and ready messages after a fatal error and does not claim failed input was saved',async()=>{
    const {receive,save}=await openEditor();
    await act(async()=>{receive({type:'dirty'});receive({type:'error',message:'本次编辑未能保存。'});receive({type:'save'});receive({type:'ready'});});
    expect(save).not.toHaveBeenCalled();
    expect(screen.queryByText('已保存到本机。')).toBeNull();
    expect(screen.getByText('编辑已暂停')).toBeTruthy();
  });
});
