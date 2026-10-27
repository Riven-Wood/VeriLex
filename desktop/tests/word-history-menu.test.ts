import {describe,it,expect,vi} from 'vitest';
const api=vi.hoisted(()=>({default:vi.fn(),createItem:vi.fn()}));
vi.mock('@tauri-apps/api/menu',()=>({Menu:{default:api.default},MenuItem:{new:api.createItem}}));
import {installWordHistoryMenu} from '../src/platform/wordHistoryMenu';
describe('Word Mac application history menu',()=>{
 it('routes native accelerators to the document and restores the previous menu',async()=>{
  const items=['Undo','Redo','Copy'].map(text=>({kind:'Predefined',text:async()=>text}));
  const submenu={kind:'Submenu',items:async()=>items,removeAt:vi.fn(),insert:vi.fn()};
  const previous={setAsAppMenu:vi.fn()},menu={items:async()=>[submenu],setAsAppMenu:vi.fn(async()=>previous),close:vi.fn()};
  api.createItem.mockImplementation(async options=>({rid:1,kind:'MenuItem',options,close:vi.fn()}));
  api.default.mockResolvedValue(menu);const action=vi.fn(),restore=await installWordHistoryMenu(action);
  expect(submenu.removeAt.mock.calls).toEqual([[0],[1]]);
  expect(submenu.insert.mock.calls.map(call=>call[0].kind)).toEqual(['MenuItem','MenuItem']);
  const undo=api.createItem.mock.calls[0][0],redo=api.createItem.mock.calls[1][0];
  expect(undo.accelerator).toBe('CmdOrCtrl+Z');expect(redo.accelerator).toBe('CmdOrCtrl+Shift+Z');undo.action();redo.action();
  expect(action.mock.calls).toEqual([['undo'],['redo']]);await restore();await restore();expect(previous.setAsAppMenu).toHaveBeenCalledOnce();expect(menu.close).toHaveBeenCalledOnce();
 });
});
