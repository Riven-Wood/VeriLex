import { useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { getPreferences, putPreference } from "../runtime/api";
import { SettingsGroup, SettingsRow, Toggle } from "./SettingsControls";
export const NATIVE_DEFAULTS = {notify_completed:1,notify_failed:1,notify_approval:1,notify_calendar:1,notification_sound:0,keep_awake:0};
export type NativePreferenceKey = keyof typeof NATIVE_DEFAULTS;
export function NativePreferences() {
  const [values,setValues] = useState<typeof NATIVE_DEFAULTS | null>(null);
  const [error,setError] = useState("");
  const [saving,setSaving] = useState(false);
  const [notice,setNotice] = useState("");
  const [status,setStatus] = useState<{awake:boolean;power_failed:boolean;notification_failed:boolean;power_supported:boolean}|null>(null);
  const load = (signal?:AbortSignal) => {setError("");void getPreferences(signal).then(p=>{if(!signal?.aborted)setValues({...NATIVE_DEFAULTS,...p});}).catch(()=>{if(!signal?.aborted)setError("设置暂时无法读取。");});};
  useEffect(()=>{const controller=new AbortController();load(controller.signal);return ()=>controller.abort();},[]);
  useEffect(()=>{
    if (!isTauri()) return;
    let active=true;
    const check=()=>void invoke<typeof status>("desktop_status").then(s=>{if(active)setStatus(s);}).catch(()=>{});
    check();const timer=setInterval(check,4000);return ()=>{active=false;clearInterval(timer);};
  },[]);
  const save = async (key:NativePreferenceKey,value:boolean) => {
    setSaving(true);setError("");
    try {const p=await putPreference(key,value?1:0);setValues({...NATIVE_DEFAULTS,...p});}
    catch {setError("未能保存，请重试。原设置仍然有效。");}
    finally {setSaving(false);}
  };
  const row=(key:NativePreferenceKey,title:string,description:string)=> <SettingsRow key={key} title={title} description={description}><Toggle label={title} checked={(values?.[key]??NATIVE_DEFAULTS[key])===1} disabled={!values||saving} onChange={v=>void save(key,v)}/></SettingsRow>;
  return <>
    <SettingsGroup title="通知" id="notifications">
      {row("notify_completed","任务完成","应用在后台时，提醒你查看结果。")}
      {row("notify_failed","任务失败","任务未能完成时提醒。")}
      {row("notify_approval","等待确认","任务需要你决定时提醒。")}
      {row("notify_calendar","案件日程","关闭后仍可在应用内查看到期提醒。")}
      {row("notification_sound","通知声音","随桌面通知播放提示音。")}
      <SettingsRow title="系统通知" description="横幅只显示任务状态，不包含案件内容。能否弹出由系统通知设置决定。"><button type="button" disabled={!isTauri()||saving} onClick={async()=>{setNotice("");try{await invoke("desktop_test_notification");setNotice("测试通知已提交；若未看到横幅，请在系统设置中允许 VeriLex 通知。");}catch{setNotice("未能发送测试通知，请检查系统通知权限。");}}}>发送测试通知</button></SettingsRow>
      {notice && <p role="status" className="preference-note">{notice}</p>}
      {status?.notification_failed && <p role="status" className="preference-note">最近一次通知未能发送，请检查系统通知权限。</p>}
    </SettingsGroup>
    <SettingsGroup title="运行" id="keep-awake">
      <SettingsRow title="任务运行时保持唤醒" description="仅在任务或后台命令执行期间防止空闲休眠；任务结束后恢复正常。仍可手动睡眠或合盖。"><Toggle label="任务运行时保持唤醒" checked={values?.keep_awake===1} disabled={!values||saving||status?.power_supported===false} onChange={v=>void save("keep_awake",v)}/></SettingsRow>
      {status?.awake && <p role="status" className="preference-note">有任务正在运行，已保持系统唤醒。</p>}
      {status?.power_failed && <p role="alert" className="preference-error">未能保持系统唤醒，请检查电脑的睡眠设置。</p>}
    </SettingsGroup>
    {error && <p role="alert" className="preference-error">{error} <button onClick={()=>load()}>重新读取</button></p>}
  </>;
}
