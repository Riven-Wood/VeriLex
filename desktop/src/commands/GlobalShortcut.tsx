import {useEffect,useSyncExternalStore} from 'react';
import {invoke,isTauri} from '@tauri-apps/api/core';
import {APP_COMMANDS,GLOBAL_WAKE_SHORTCUT,isMacPlatform,shortcutLabel} from './registry';
import {SettingsGroup,SettingsRow,Toggle} from '../settings/SettingsControls';
import './commands.css';
const KEY='verilex:global-wake-shortcut:v1';
let state={enabled:false,busy:false,error:''};
const listeners=new Set<()=>void>();
function publish(next:typeof state){state=next;listeners.forEach(l=>l());}
const subscribe=(listener:()=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener);};};
export async function setGlobalWake(enabled:boolean){
  if(state.busy)return;
  publish({...state,busy:true,error:''});
  try{
    const registered=await invoke<boolean>('set_global_wake_shortcut',{enabled});
    try{localStorage.setItem(KEY,registered?'true':'false');}
    catch{
      // Never leave a newly enabled, non-persistable system shortcut hidden.
      if(registered)await invoke('set_global_wake_shortcut',{enabled:false});
      publish({enabled:false,busy:false,error:'无法保存设置，系统唤起快捷键未启用。'});return;
    }
    publish({enabled:registered,busy:false,error:''});
  }catch{if(enabled&&!state.enabled){try{localStorage.setItem(KEY,'false');}catch{/* report below */}}publish({...state,busy:false,error:'快捷键未能更改，可能已被其他应用占用。可关闭其他应用的相同键位后重试。'});}
}
let restored=false;
export function GlobalShortcutEffect(){
  useEffect(()=>{
    if(restored||!isTauri()||!isMacPlatform())return;
    restored=true;
    try{if(localStorage.getItem(KEY)==='true')void setGlobalWake(true);}catch{/* default off */}
  },[]);
  return null;
}
export function ShortcutReference(){
  return <div className="shortcut-reference">
    <p>仅在 VeriLex 窗口内生效。输入文字、编辑文档、使用终端或其他弹窗时，可点击顶部命令按钮。</p>
    <ul>{APP_COMMANDS.filter(c=>c.key).map(c=><li key={c.id}>
      <span className="shortcut-label">{c.title}{c.scope&&<small>{c.scope}</small>}</span>
      <kbd>{shortcutLabel(c)}</kbd>
    </li>)}</ul>
  </div>;
}
export function ShortcutSettings(){
  const value=useSyncExternalStore(subscribe,()=>state);
  return <SettingsGroup title="快捷键" id="shortcuts"><details className="shortcut-disclosure"><summary>查看窗口内快捷键</summary><ShortcutReference/></details>
    {isTauri()&&isMacPlatform()&&<SettingsRow title="从其他应用唤起 VeriLex" description={`${GLOBAL_WAKE_SHORTCUT} · 系统级快捷键，默认关闭。仅在 VeriLex 已运行时显示并切回应用；退出后失效。`}><Toggle label="从其他应用唤起 VeriLex" checked={value.enabled} disabled={value.busy} onChange={v=>void setGlobalWake(v)}/></SettingsRow>}
    {value.error&&<p role="alert" className="preference-error">{value.error}</p>}
  </SettingsGroup>;
}
