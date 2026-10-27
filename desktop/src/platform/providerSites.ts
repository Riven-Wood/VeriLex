import { invoke, isTauri } from '@tauri-apps/api/core';
import { PROVIDER_PRESETS } from '../providers/presets';

export async function openProviderSite(id: string, site: 'console' | 'docs'): Promise<void> {
  const preset = PROVIDER_PRESETS.find(item => item.id === id);
  if (!preset) throw new Error('没有找到此服务商的官方页面。');
  try {
    if (isTauri()) await invoke('open_case_database', { database: `model-${site}-${id}` });
    else window.open(site === 'console' ? preset.consoleUrl : preset.docsUrl, '_blank', 'noopener,noreferrer');
  } catch { throw new Error('系统浏览器没有打开，请稍后重试。'); }
}
