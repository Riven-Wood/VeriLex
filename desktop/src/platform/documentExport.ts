import { invoke } from '@tauri-apps/api/core';

export async function saveDocumentExport(result: { filename: string; content: string }, command: 'save_redaction' | 'save_contract_review'): Promise<string | null> {
  const raw = atob(result.content);
  const data = Uint8Array.from(raw, char => char.charCodeAt(0));
  if (Reflect.has(globalThis, '__TAURI_INTERNALS__')) {
    return invoke<string | null>(command, { defaultName: result.filename, content: Array.from(data) });
  }
  const url = URL.createObjectURL(new Blob([data], { type: result.filename.endsWith('.md') ? 'text/markdown;charset=utf-8' : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = result.filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return result.filename;
}
