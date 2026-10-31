import {beforeEach, expect, test, vi} from 'vitest';
import {clearPreviouslyRemovedProjects, readProjectSidebarState, writeProjectSidebarState} from '../src/app/projectSidebarState';

beforeEach(()=>localStorage.clear());

test('清理过去移除的项目授权，保留仍在使用的项目和失败项',async()=>{
  writeProjectSidebarState({hidden:['removed','busy','gone'],pinned:[],groups:{},order:[],groupOrder:[]});
  const revoke=vi.fn(async (id:string)=>{if(id==='busy')throw new Error('busy');});
  const success=await clearPreviouslyRemovedProjects(async()=>[{id:'removed'},{id:'busy'},{id:'active'}],revoke);
  expect(success).toBe(false);
  expect(revoke.mock.calls.map(call=>call[0])).toEqual(['removed','busy']);
  expect(readProjectSidebarState().hidden).toEqual(['busy']);
});
