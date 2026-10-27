import { invoke, isTauri } from '@tauri-apps/api/core';
export const FEEDBACK_URL = 'https://github.com/Riven-Wood/VeriLex/issues';
export interface HelpDiagnostics { appVersion?: string; runtimeVersion?: string; runtimeConnected: boolean; }
// Allow only bounded version syntax, never raw errors, URLs, paths or machine identifiers.
export function safeVersion(value?: string): string {
  return value && /^\d{1,4}\.\d{1,4}\.\d{1,4}(?:[-+][A-Za-z0-9.-]{1,32})?$/.test(value) ? value : '未知';
}
export function feedbackTemplate(diagnostics: HelpDiagnostics, includeDiagnostics: boolean): string {
  const lines = ['VeriLex 问题反馈', '', '问题简述：', '', '复现步骤（请用虚构示例）：', '1. ', '2. ', '', '期望看到：', '', '实际看到：', '', '出现频率：', '', '请勿粘贴密钥、案件正文、当事人信息、完整文件路径或未经处理的截图。'];
  if (includeDiagnostics) lines.push('', '基础诊断（仅以下字段）：', `应用版本：${safeVersion(diagnostics.appVersion)}`, `后台版本：${safeVersion(diagnostics.runtimeVersion)}`, `本地后台：${diagnostics.runtimeConnected ? '已连接' : '未连接或状态不可用'}`);
  return lines.join('\n');
}
export async function openFeedback(): Promise<void> {
  if (isTauri()) await invoke('open_case_database', { database: 'verilex-feedback' });
  else window.open(FEEDBACK_URL, '_blank', 'noopener,noreferrer');
}
