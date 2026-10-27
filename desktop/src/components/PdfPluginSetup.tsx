import { useState } from 'react';
import { pickWorkspaceDirectory } from '../platform/directoryPicker';
import type { McpServerDraft, McpServerRecord } from '../runtime/contracts';
import { McpServerEditor } from './McpServerEditor';

export const PDF_PLUGIN_COMMAND = 'verilex-pdf-plugin';
export function pdfPluginDraft(folder: string): McpServerDraft {
  return {displayName:`PDF 页面处理 · ${folder.split('/').filter(Boolean).at(-1) ?? '文件夹'}`,
    transport:'stdio',command:PDF_PLUGIN_COMMAND,args:[],cwd:folder,url:null,
    env:{},headers:{},secretEnvKeys:[],secretHeaderKeys:[],connectTimeoutMs:10000,requestTimeoutMs:60000};
}
export function PdfPluginSetup(props: {
  servers:McpServerRecord[]; onSaved:(record:McpServerRecord)=>void; onComplete:()=>void;
  onOpen:(record:McpServerRecord)=>void; onBusyChange:(busy:boolean)=>void;
}) {
  const [folder,setFolder] = useState<string|null>(null);
  const [error,setError] = useState('');
  const [picking,setPicking] = useState(false);
  const [configuring,setConfiguring] = useState(false);
  const existing = props.servers.find(s => s.command === PDF_PLUGIN_COMMAND && s.cwd === folder);
  async function choose() {
    setPicking(true); props.onBusyChange(true); setError('');
    try { const selected = await pickWorkspaceDirectory(); if (selected) setFolder(selected); }
    catch { setError('文件夹没有选定，请重试。'); }
    finally { setPicking(false); props.onBusyChange(false); }
  }
  if (folder && configuring) return <McpServerEditor key={folder} mode="settings" initialDraft={pdfPluginDraft(folder)}
    onSaved={props.onSaved} onComplete={props.onComplete} onBusyChange={props.onBusyChange}
    onCancel={()=>setConfiguring(false)} />;
  return <section aria-label="PDF 页面处理接入">
    <p>选择存放 PDF 的文件夹。插件只读取其中的 PDF，结果保存到新建的「PDF结果」文件夹。</p>
    <p className="plugin-catalog-note">之后在同一工作区的对话里选择此插件，即可要求合并、拆分或提取页。原文件保持不变。</p>
    <button type="button" className="mcp-secondary-button" disabled={picking} onClick={()=>void choose()}>{folder?'更换文件夹':'选择 PDF 文件夹'}</button>
    {folder && <p style={{overflowWrap:'anywhere'}}>{folder}</p>}
    {error && <p role="alert" className="mcp-error">{error}</p>}
    <button type="button" className="mcp-add" disabled={!folder || picking} onClick={()=>existing?props.onOpen(existing):setConfiguring(true)}>
      {existing?'管理已添加连接':'测试并添加'}</button>
  </section>;
}
