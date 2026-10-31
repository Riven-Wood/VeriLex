import {beforeEach,describe,expect,it,vi} from 'vitest';
import {WordSession,wordDocumentApi,retainedWordSession} from '../src/runtime/wordDocumentClient';
vi.mock('../src/runtime/api',()=>({runtimeRequest:vi.fn()}));
const doc={id:'w',filename:'合同.docx',content:'QQ==',revision:0,updated_at:'t'};
beforeEach(()=>vi.restoreAllMocks());
describe('Word copy saves',()=>{
 it('serializes pending input and preserves the newest bytes while a save is running',async()=>{
  let finish!:(v:{id:string;revision:number})=>void;
  const save=vi.spyOn(wordDocumentApi,'save').mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;})).mockResolvedValueOnce({id:'w',revision:2});
  const session=new WordSession(doc);session.update(new Uint8Array([66]));session.update(new Uint8Array([67]));
  expect(save).toHaveBeenCalledTimes(1);finish({id:'w',revision:1});await session.flush();
  expect(save).toHaveBeenNthCalledWith(2,'w',1,new Uint8Array([67]));expect(session.document.revision).toBe(2);expect(session.dirty).toBe(false);
 });
 it('keeps unsaved bytes after conflict and requires explicit retry',async()=>{
  const save=vi.spyOn(wordDocumentApi,'save').mockRejectedValue(new Error('conflict'));
  const session=new WordSession(doc);session.update(new Uint8Array([66]));await expect(session.flush()).rejects.toThrow('conflict');
  expect(session.bytes).toEqual(new Uint8Array([66]));expect(session.dirty).toBe(true);expect(session.document.revision).toBe(0);
  session.update(new Uint8Array([67]));expect(save).toHaveBeenCalledTimes(1);
 });
 it('retains a failed legacy workspace save when the view is reopened',async()=>{
  const save=vi.fn().mockRejectedValue(new Error('版本冲突'));
  const session=retainedWordSession('legacy:test:failed',doc,save);
  session.update(new Uint8Array([66]));
  await expect(session.flush()).rejects.toThrow('版本冲突');
  const reopened=retainedWordSession('legacy:test:failed',{...doc,revision:2},vi.fn());
  expect(reopened).toBe(session);
  expect(reopened.dirty).toBe(true);
  expect(reopened.bytes).toEqual(new Uint8Array([66]));
 });
 it('persists review markers with their bytes and retains them across a failed save',async()=>{
  const markup={review_id:'review',applied:2,skipped:1};
  const save=vi.spyOn(wordDocumentApi,'save').mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({id:'w',revision:1}).mockResolvedValueOnce({id:'w',revision:2});
  const session=new WordSession(doc);session.update(new Uint8Array([66]),markup);
  await expect(session.flush()).rejects.toThrow('offline');
  expect(session.document.review_markups).toBeUndefined();
  await session.flush();
  expect(save).toHaveBeenNthCalledWith(2,'w',0,new Uint8Array([66]),markup);
  expect(session.document.review_markups).toEqual({review:{applied:2,skipped:1}});
  session.update(new Uint8Array([67]));await session.flush();
  expect(save).toHaveBeenNthCalledWith(3,'w',1,new Uint8Array([67]));
  expect(session.document.review_markups?.review.applied).toBe(2);
 });
});
