import { Menu, MenuItem, Submenu } from '@tauri-apps/api/menu';

let queue:Promise<unknown>=Promise.resolve();
let restoreCurrent:(()=>Promise<void>)|undefined;
const serial=<T>(work:()=>Promise<T>)=>{const next=queue.then(work,work);queue=next.catch(()=>{});return next;};

/** macOS consumes Cmd+Z in its application menu before WebKit keydown. */
export function installWordHistoryMenu(action:(command:'undo'|'redo')=>void){
  return serial(async()=>{
    await restoreCurrent?.();
    const menu=await Menu.default(),commands:MenuItem[]=[];
    try{
      for(const item of await menu.items()){
        if(item.kind!=='Submenu')continue;
        const submenu=item as Submenu,items=await submenu.items();
        for(let index=0;index<items.length;index++){
          const entry=items[index];if(entry.kind!=='Predefined')continue;
          const label=(await entry.text()).replaceAll('&','');
          const command=/^(Undo|撤销)$/i.test(label)?'undo':/^(Redo|重做)$/i.test(label)?'redo':null;
          if(!command)continue;
          await submenu.removeAt(index);
          // Creating a MenuItem registers its IPC action channel; insert(options) does not.
          const replacement=await MenuItem.new({id:`word-history-${command}`,text:command==='undo'?'撤销':'重做',accelerator:command==='undo'?'CmdOrCtrl+Z':'CmdOrCtrl+Shift+Z',action:()=>action(command)});
          commands.push(replacement);await submenu.insert(replacement,index);
        }
      }
      const previous=await menu.setAsAppMenu();
      const restore=async()=>{if(restoreCurrent!==restore)return;restoreCurrent=undefined;await previous?.setAsAppMenu();await menu.close();await Promise.all(commands.map(item=>item.close()));};
      restoreCurrent=restore;
      return ()=>serial(restore);
    }catch(error){await menu.close();await Promise.all(commands.map(item=>item.close()));throw error;}
  });
}
