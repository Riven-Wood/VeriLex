import {useEffect, useRef, useState} from "react";
import {getPreferences, putPreference} from "../runtime/api";
import {SettingsGroup} from "./SettingsControls";

const MAX_CHARS = 32000;

function readInstructionFile(file: File): Promise<string> {
  if (!file.name.toLowerCase().endsWith(".md")) {
    return Promise.reject(new Error("请选择 Markdown（.md）文件。"));
  }
  if (file.size > MAX_CHARS * 4) {
    return Promise.reject(new Error("文件过大，请将指令缩短至 32000 字以内。"));
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("未能读取文件，请重新选择。"));
    reader.onabort = () => reject(new Error("文件读取已取消。"));
    reader.onload = () => {
      try {
        const text = new TextDecoder("utf-8", {fatal: true}).decode(reader.result as ArrayBuffer);
        if (Array.from(text).length > MAX_CHARS) throw new Error("指令超过 32000 字，请缩短后导入。");
        if (text.includes("\0")) throw new Error("文件包含无效字符，请选择 UTF-8 编码的 Markdown 文本。");
        resolve(text);
      } catch (error) {
        reject(error instanceof TypeError ? new Error("无法识别文字编码，请将文件保存为 UTF-8 后导入。") : error);
      }
    };
    reader.readAsArrayBuffer(file);
  });
}

export function PersonalInstructions() {
  const [draft,setDraft]=useState("");
  const [saved,setSaved]=useState<string|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [retry,setRetry]=useState(0);
  const fileInput=useRef<HTMLInputElement>(null);
  const length=Array.from(draft).length;
  const invalid=length>MAX_CHARS||draft.includes("\0");
  useEffect(()=>{
    const controller=new AbortController();
    setError("");
    void getPreferences(controller.signal).then(p=>{
      if(controller.signal.aborted)return;
      setDraft(p.personal_instructions??"");setSaved(p.personal_instructions??"");
    }).catch(()=>{if(!controller.signal.aborted)setError("暂时无法读取全局指令，请重试。");});
    return ()=>controller.abort();
  },[retry]);
  const save=async()=>{
    if(invalid)return;
    setBusy(true);setError("");setNotice("");
    try {
      const result=await putPreference("personal_instructions",draft);
      setSaved(result.personal_instructions??"");setDraft(result.personal_instructions??"");
      setNotice("已保存，将从下一轮任务开始生效，已有会话也适用。");
    }catch{setError("未能保存，原指令仍然有效。请重试。");}
    finally{setBusy(false);}
  };
  const importFile=async(file:File)=>{
    setBusy(true);setError("");setNotice("");
    try {
      const text=await readInstructionFile(file);
      setDraft(text);
      setNotice(`已导入 ${file.name}，请查看后保存。原文件不会被修改，之后请在这里编辑或重新导入。`);
    }catch(error){setError(error instanceof Error?error.message:"未能读取文件，请重试。");}
    finally{setBusy(false);}
  };
  return <SettingsGroup title="全局指令（AGENTS.md）" id="personal-instructions">
    <div className="personal-instructions">
      <label htmlFor="personal-instructions-input">统一设置助手的工作方式</label>
      <p id="personal-instructions-description">适用于普通聊天、合同审查、诉讼工作等 AI 任务。保存后从下一轮生效，随对话发送给所选模型；当前任务的明确要求优先，不改变文件或工具权限。清空并保存可停用。</p>
      <textarea id="personal-instructions-input" aria-describedby="personal-instructions-description" rows={12} placeholder={"# 全局指令\n\n例如：先阅读材料再判断；引用须核实来源；回答使用简体中文。"} value={draft} disabled={saved===null||busy} onChange={e=>{setDraft(e.target.value);setNotice("");setError("");}}/>
      <input ref={fileInput} type="file" accept=".md,text/markdown" aria-label="选择 AGENTS.md 文件" hidden disabled={saved===null||busy} onChange={e=>{const file=e.currentTarget.files?.[0];e.currentTarget.value="";if(file)void importFile(file);}}/>
      <div className="settings-save-row">
        <button disabled={saved===null||busy} onClick={()=>fileInput.current?.click()}>导入 AGENTS.md</button>
        <span>{length} / {MAX_CHARS} 字{saved!==null&&draft!==saved?" · 尚未保存":""}</span>
        <button disabled={saved===null||draft===saved||busy||invalid} onClick={()=>void save()}>{busy?"正在处理…":"保存指令"}</button>
      </div>
      {invalid&&<p role="alert" className="preference-error">指令须为不超过 32000 字的文本，不能包含空字符。</p>}
      {notice&&<p role="status">{notice}</p>}
      {error&&<p role="alert" className="preference-error">{error} {saved===null&&<button onClick={()=>setRetry(v=>v+1)}>重试</button>}</p>}
    </div>
  </SettingsGroup>;
}
