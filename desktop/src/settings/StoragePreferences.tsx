import {useEffect,useState} from "react";
import {invoke,isTauri} from "@tauri-apps/api/core";
import {listWorkspaces} from "../runtime/api";
import type {WorkspaceRecord} from "../runtime/contracts";
import {readUiPreferences,saveUiPreferences,useUiPreferences} from "./preferences";
import {SettingsGroup,SettingsRow} from "./SettingsControls";

/** Resolve only an existing authorization; defaults must never grant access. */
export async function preferredWorkspaceId():Promise<string|null> {
  const id=readUiPreferences().defaultWorkspaceId;
  if(!id)return null;
  const workspaces=await listWorkspaces();
  return workspaces.some(w=>w.id===id&&w.allow_read)?id:null;
}
export function StoragePreferences() {
  const prefs=useUiPreferences();
  const [rows,setRows]=useState<WorkspaceRecord[]|null>(null);
  const [error,setError]=useState("");
  const [retry,setRetry]=useState(0);
  const [opening,setOpening]=useState(false);
  useEffect(()=>{
    const controller=new AbortController();setError("");
    void listWorkspaces(controller.signal).then(rows=>{if(!controller.signal.aborted)setRows(rows);}).catch(()=>{if(!controller.signal.aborted)setError("无法读取工作目录，请重试。");});
    return ()=>controller.abort();
  },[retry]);
  const available=rows?.filter(w=>w.allow_read)??[];
  return <SettingsGroup title="存储位置" id="storage">
    <SettingsRow title="新会话默认工作目录" description="只可选择已授权的文件夹。用于之后新建的普通会话，已有会话不变。">
      <select aria-label="新会话默认工作目录" disabled={!rows} value={available.some(w=>w.id===prefs.defaultWorkspaceId)?prefs.defaultWorkspaceId:""} onChange={e=>{try{saveUiPreferences({defaultWorkspaceId:e.target.value});setError("");}catch{setError("未能保存默认目录，请重试。");}}}>
        <option value="">每次选择</option>{available.map(w=><option key={w.id} value={w.id}>{w.display_name}</option>)}
      </select>
    </SettingsRow>
    {available.find(w=>w.id===prefs.defaultWorkspaceId)&&<p className="preference-note workspace-path">{available.find(w=>w.id===prefs.defaultWorkspaceId)?.root_path}</p>}
    <SettingsRow title="应用数据文件夹" description="查看本机存放对话、配置与附件的位置。请勿在应用运行时移动或删除其中的文件。"><button disabled={!isTauri()||opening} onClick={async()=>{setOpening(true);try{await invoke("open_app_data_directory");}catch{setError("未能打开应用数据文件夹，请重试。");}finally{setOpening(false);}}}>{opening?"正在打开…":"打开文件夹"}</button></SettingsRow>
    {error&&<p role="alert" className="preference-error">{error} {!rows&&<button onClick={()=>setRetry(v=>v+1)}>重试</button>}</p>}
  </SettingsGroup>;
}
