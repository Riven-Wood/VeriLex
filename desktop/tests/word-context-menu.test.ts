import {describe,it,expect,vi} from 'vitest';
const api=vi.hoisted(()=>({new:vi.fn(),popup:vi.fn().mockResolvedValue(undefined),close:vi.fn().mockResolvedValue(undefined)}));
vi.mock('@tauri-apps/api/menu',()=>({Menu:{new:api.new}}));
import {createWordContextMenu} from '../src/platform/wordContextMenu';
describe('Word native editing menu',()=>{
 it('routes clipboard operations to native editing and custom operations back to the document',async()=>{
   api.new.mockResolvedValue({popup:api.popup,close:api.close});const action=vi.fn();const menu=await createWordContextMenu(action);
   await menu.popup(10,20);
   const items=api.new.mock.calls.at(-1)![0].items;
   expect(items.filter((x:{item?:string})=>['Copy','Cut','Paste'].includes(x.item||'')).map((x:{item:string})=>x.item)).toEqual(['Cut','Copy','Paste']);
   expect(items.some((x:{id?:string})=>['word-select-all','word-undo','word-table','word-accept-insert'].includes(x.id||''))).toBe(false);items.find((x:{id?:string})=>x.id==='word-paragraph').action();
   expect(action.mock.calls).toEqual([['paragraph']]);
   expect(api.popup).toHaveBeenCalledWith(expect.objectContaining({x:10,y:20}));await menu.popup(10,20,{table:true});const contextual=api.new.mock.calls.at(-1)![0].items;expect(contextual.some((x:{id?:string})=>x.id==='word-table')).toBe(true);expect(contextual.some((x:{id?:string})=>['word-accept-insert','word-reject-insert'].includes(x.id||''))).toBe(false);await menu.close();expect(api.close).toHaveBeenCalledTimes(2);
 });
});
