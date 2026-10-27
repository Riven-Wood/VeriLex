/** Human browsing only. Never forwards page content to the agent/runtime. */
export const BROWSER_PANEL_EVENT = 'verilex:open-browser';
export function openBrowserPanel(url?: string) {
  window.dispatchEvent(new CustomEvent(BROWSER_PANEL_EVENT, { detail: { url } }));
}
export function browserUrl(value: string): string {
  const input = value.trim();
  if (!input || /[\u0000-\u0020]/.test(input)) throw new Error('请输入完整的网页地址。');
  const url = new URL(input.includes('://') ? input : `https://${input}`);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('只支持不含账号密码的 http 或 https 网页地址。');
  return url.href;
}
