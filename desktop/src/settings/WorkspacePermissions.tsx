import {useEffect,useState} from "react";
import {listWorkspaces,updateWorkspacePermissions,revokeWorkspace} from "../runtime/api";
import type {WorkspaceRecord} from "../runtime/contracts";
import {SettingsGroup} from "./SettingsControls";
export function WorkspacePermissions({onChanged}: {onChanged?:(id:string,revoked:boolean)=>void}) {
  const [rows,setRows]=useState<WorkspaceRecord[]|null>(null);
  const [error,setError]=useState("");const [busy,setBusy]=useState<string|null>(null);const [confirm,setConfirm]=useState<string|null>(null);
  const load=(signal?:AbortSignal)=>{setError("");void listWorkspaces(signal).then(rows=>{if(!signal?.aborted)setRows(rows);}).catch(()=>{if(!signal?.aborted)setError("无法读取授权文件夹，请重试。");});};
  useEffect(()=>{const controller=new AbortController();load(controller.signal);return ()=>controller.abort();},[]);
  const change=async(row:WorkspaceRecord,remove:boolean,write=false)=>{
    setBusy(row.id);setError("");
    try {
      if(remove){await revokeWorkspace(row.id);setRows(old=>old?.filter(r=>r.id!==row.id)??[]);setConfirm(null);}
      else {const updated=await updateWorkspacePermissions(row.id,true,write);setRows(old=>old?.map(r=>r.id===row.id?updated:r)??[]);}
      onChanged?.(row.id,remove);
    }catch(e){setError((e as {code?:string}).code==="workspace_busy"?"这个文件夹仍有任务或终端在运行，请先停止任务、关闭终端会话后再修改权限。":"未能修改权限，请刷新后重试。文件夹授权尚未改变。");}
    finally{setBusy(null);}
  };
  return <SettingsGroup title="已授权文件夹" id="workspace-permissions">
    <p className="preference-note">文件操作仍遵守当前任务的确认方式。收回授权不会删除磁盘文件或对话记录。</p>
    {rows===null&&!error&&<p className="preference-note" role="status">正在读取…</p>}
    {rows?.length===0&&<p className="preference-note">还没有授权文件夹。在任务中选择工作目录后，会显示在这里。</p>}
    {rows?.map(row=><div className="workspace-permission" key={row.id}><div className="workspace-permission-main"><div className="preference-copy"><strong>{row.display_name}</strong><p className="workspace-path" title={row.root_path}>{row.root_path}</p></div><div className="preference-control"><select aria-label={`${row.display_name}的访问权限`} disabled={!!busy} value={row.allow_read?(row.allow_write?"write":"read"):"none"} onChange={e=>void change(row,false,e.target.value==="write")}><option value="read">只读</option><option value="write">读取与修改</option>{!row.allow_read&&<option value="none" disabled>不可读取</option>}</select><button disabled={!!busy} onClick={()=>setConfirm(row.id)}>收回授权</button></div></div>{confirm===row.id&&<div role="alertdialog" aria-label={`收回${row.display_name}的授权`} className="workspace-revoke"><p>相关任务将解除与此文件夹的关联。以后需要访问时，可以重新选择文件夹。</p><div><button className="danger-action" disabled={!!busy} onClick={()=>void change(row,true)}>{busy?"正在收回…":"确认收回"}</button><button disabled={!!busy} onClick={()=>setConfirm(null)}>取消</button></div></div>}</div>)}
    {error&&<p className="preference-error" role="alert">{error} <button disabled={!!busy} onClick={()=>load()}>刷新</button></p>}
  </SettingsGroup>;
}
