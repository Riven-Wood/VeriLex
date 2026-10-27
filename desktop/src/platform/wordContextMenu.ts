import { Menu } from '@tauri-apps/api/menu';
import { LogicalPosition } from '@tauri-apps/api/dpi';
export type WordMenuContext={table?:boolean};
/** The OS handles clipboard commands; document-specific items follow the clicked context. */
export async function createWordContextMenu(action:(command:string)=>void) {
  const menus=new Map<string,Promise<Menu>>();
  const item=(command:string,text:string)=>({id:`word-${command}`,text,action:()=>action(command)});
  return {
    async popup(x:number,y:number,context:WordMenuContext={}) {
      const key=String(Boolean(context.table));
      let pending=menus.get(key);
      if(!pending){pending=Menu.new({items:[
        {item:'Cut',text:'剪切'},{item:'Copy',text:'复制'},{item:'Paste',text:'粘贴'},
        {item:'Separator'},item('font','字体与字号…'),item('paragraph','段落…'),
        {text:'项目符号和编号',items:[item('bullet','项目符号'),item('decimal','编号')]},
        {item:'Separator'},item('comment','新建批注'),
        ...(context.table?[item('table','表格操作…')]:[]),
      ]});menus.set(key,pending);pending.catch(()=>menus.delete(key));}
      await (await pending).popup(new LogicalPosition(x,y));
    },
    async close(){await Promise.all([...menus.values()].map(async pending=>(await pending).close()));},
  };
}
