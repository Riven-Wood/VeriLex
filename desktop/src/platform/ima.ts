import { invoke, isTauri } from '@tauri-apps/api/core';
export async function openIma(setup = false) {
  if (isTauri()) await invoke('open_case_database', {database: setup ? 'ima-setup' : 'ima'});
  else window.open(setup ? 'https://ima.qq.com/agent-interface' : 'https://ima.qq.com/', '_blank', 'noopener,noreferrer');
}
