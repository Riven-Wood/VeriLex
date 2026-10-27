import { invoke, isTauri } from '@tauri-apps/api/core';
import { CREDENTIAL_SITES, PLUGIN_CATALOG } from '../mcp/catalog';

export async function openPluginSource(id: string, credentials = false): Promise<void> {
  const entry = PLUGIN_CATALOG.find(item => item.id === id);
  if (!entry) throw new Error('没有找到官方说明。');
  const url = credentials ? CREDENTIAL_SITES[id] : entry.source;
  if (!url) throw new Error("此工具不需要官网密钥。");
  try {
    if (isTauri()) await invoke('open_case_database', { database: `plugin-${credentials ? "key-" : ""}${id}` });
    else window.open(url, '_blank', 'noopener,noreferrer');
  } catch { throw new Error('系统浏览器没有打开，请稍后重试。'); }
}
