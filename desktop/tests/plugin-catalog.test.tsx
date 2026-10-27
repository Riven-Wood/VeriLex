import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { PluginCenterView } from '../src/views/PluginCenterView';
import { McpServerEditor } from '../src/components/McpServerEditor';
import { CREDENTIAL_SITES, PLUGIN_CATALOG } from '../src/mcp/catalog';
import { previewMcpImport } from '../src/mcp/import';
import * as api from '../src/runtime/api';
import * as platform from '../src/platform/pluginCatalog';
import { imaApi } from '../src/runtime/imaClient';
import type { McpServerRecord } from '../src/runtime/contracts';

vi.mock('@tauri-apps/api/core', () => ({ invoke: vi.fn(), isTauri: vi.fn(() => true) }));
import { invoke, isTauri } from '@tauri-apps/api/core';
Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, writable: true, value() {} });
beforeEach(() => { vi.spyOn(HTMLDialogElement.prototype, 'showModal').mockImplementation(function () { this.setAttribute('open', ''); }); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.mocked(invoke).mockReset(); vi.mocked(isTauri).mockReturnValue(true); });
function setup(records: McpServerRecord[] = []) {
  vi.spyOn(api, 'listMcpServers').mockResolvedValue(records);
  vi.spyOn(api, 'listMcpServerTools').mockResolvedValue([]);
  return { test: vi.spyOn(api, 'testMcpServerDraft'), save: vi.spyOn(api, 'createMcpServer'), select: vi.spyOn(api, 'activateMcpView') };
}
async function catalog() {
  render(<PluginCenterView />);
  await screen.findByText('已添加 0 个外部工具服务');
  fireEvent.click(screen.getByRole('button', { name: '分类推荐' }));
}
test('catalog classifies all requested services and search is local with no probing', async () => {
  const calls = setup(); await catalog();
  for (const name of ['威科先行', '企查查', '天眼查', 'GitHub', 'ima', '北大法宝', 'OfficeCLI']) expect(screen.getByRole('article', { name: `推荐 ${name}` })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '企业尽调（2）' }));
  expect(screen.queryByRole('article', { name: '推荐 北大法宝' })).toBeNull();
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '天眼' } });
  expect(screen.getAllByRole('article')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '了解 天眼查' }));
  expect(screen.getByLabelText('API Key')).toBeTruthy();
  expect(screen.getByRole('button', { name: '测试并查看工具' })).toHaveProperty('disabled', true);
  expect(screen.queryByRole('button', { name: '配置连接' })).toBeNull();
  expect(calls.test).not.toHaveBeenCalled(); expect(calls.save).not.toHaveBeenCalled(); expect(calls.select).not.toHaveBeenCalled();
});
test('official key link opens only the chosen site, preserves the draft and reports open failures', async () => {
  setup(); await catalog();
  const open = vi.spyOn(platform, 'openPluginSource').mockResolvedValue();
  fireEvent.click(screen.getByRole('button', { name: '了解 威科先行' }));
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'fake-test-key' } });
  fireEvent.click(screen.getByRole('button', { name: '前往官网获取 API Key' }));
  expect(open).toHaveBeenCalledWith('wkinfo', true);
  expect((screen.getByLabelText('API Key') as HTMLInputElement).value).toBe('fake-test-key');
  expect((screen.getByLabelText('API Key') as HTMLInputElement).type).toBe('password');
  expect(screen.queryByLabelText('秘密请求头')).toBeNull();
  open.mockRejectedValueOnce(new Error('do not echo raw error'));
  fireEvent.click(screen.getByRole('button', { name: '前往官网获取 API Key' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '系统浏览器没有打开，请稍后重试。');
});
test('preset requires a key, uses the authoritative test API, saves receipt and never selects a thread', async () => {
  const calls = setup();
  const option = PLUGIN_CATALOG.find(x => x.id === 'wkinfo')!.options![0];
  calls.test.mockResolvedValue({ testReceipt: 'receipt', tools: [], riskCodes: [] });
  calls.save.mockResolvedValue({ ...option.draft, id: 'saved' } as McpServerRecord);
  await catalog(); fireEvent.click(screen.getByRole('button', { name: '了解 威科先行' }));
  expect((screen.getByRole('button', { name: '测试并查看工具' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'synthetic-key' } });
  fireEvent.click(screen.getByRole('button', { name: '测试并查看工具' }));
  await waitFor(() => expect(calls.test).toHaveBeenCalled());
  const input = calls.test.mock.calls[0][0];
  expect(input.draft.url).toBe('https://mcp.wkinfo.com.cn/mcp-servers/integrated/');
  expect(input.draft.headers).toEqual({});
  expect(input.secretPatch?.headers).toEqual({ Authorization: 'Bearer synthetic-key' });
  fireEvent.click(screen.getByRole('button', { name: '保存' }));
  await waitFor(() => expect(calls.save).toHaveBeenCalled());
  expect(calls.save.mock.calls[0][0].testReceipt).toBe('receipt');
  expect(calls.select).not.toHaveBeenCalled();
});
test('pkulaw shortcut and catalog open one shared key form without selecting a service', async () => {
  const calls = setup(); render(<PluginCenterView />);
  await screen.findByText('已添加 0 个外部工具服务');
  const trigger = screen.getByRole('button', { name: '连接北大法宝' });
  trigger.focus(); fireEvent.click(trigger);
  expect(screen.getAllByLabelText('API Key')).toHaveLength(1);
  expect(screen.queryByLabelText('服务范围')).toBeNull();
  expect(within(screen.getByRole('list', { name: '法宝服务连接状态' })).getAllByRole('listitem')).toHaveLength(9);
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'synthetic-unused-key' } });
  fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
  expect(document.activeElement).toBe(trigger);
  fireEvent.click(screen.getByRole('button', { name: '分类推荐' }));
  fireEvent.click(screen.getByRole('button', { name: '了解 北大法宝' }));
  expect(screen.getByLabelText('API Key')).toHaveProperty('value', '');
  expect(screen.getByRole('button', { name: '测试全部服务' })).toHaveProperty('disabled', true);
  expect(calls.test).not.toHaveBeenCalled(); expect(calls.save).not.toHaveBeenCalled();
});

test('pkulaw keeps management for renamed existing endpoints', async () => {
  const option = PLUGIN_CATALOG.find(entry => entry.id === 'pkulaw')!.options![0];
  setup([{ ...option.draft, id: 'pkulaw-existing', displayName: '我的法宝检索',
    configReviewState: 'reviewed', riskFindings: [], selectedThreadCount: 0 } as McpServerRecord]);
  render(<PluginCenterView />); await screen.findByText('已添加 1 个外部工具服务');
  fireEvent.click(screen.getByRole('button', { name: '管理北大法宝服务' }));
  fireEvent.click(screen.getByRole('button', { name: '管理 法规智能检索' }));
  expect(await screen.findByRole('heading', { name: '我的法宝检索' })).toBeTruthy();
  expect(screen.queryByRole('dialog')).toBeNull();
});
test('preset risk scanning cannot be bypassed and changing key invalidates test receipt', async () => {
  const option = PLUGIN_CATALOG.find(x => x.id === 'github')!.options![0];
  const parse = vi.spyOn(api, 'parseMcpImport').mockResolvedValue({ drafts: [{ ...option.draft, risks: [{ code: 'mcp_config_invalid', severity: 'block' }] }] });
  const probe = vi.spyOn(api, 'testMcpServerDraft').mockResolvedValue({ testReceipt: 'r', tools: [], riskCodes: [] });
  render(<McpServerEditor mode="settings" initialDraft={option.draft} credential={option.credential} />);
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'synthetic-pat' } });
  fireEvent.click(screen.getByRole('button', { name: '测试并查看工具' }));
  await screen.findByText('这份配置存在不安全内容，不能添加。');
  expect(probe).not.toHaveBeenCalled();
  parse.mockResolvedValue({ drafts: [{ ...option.draft, risks: [] }] });
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'new-synthetic-pat' } });
  fireEvent.click(screen.getByRole('button', { name: '测试并查看工具' }));
  await waitFor(() => expect((screen.getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(false));
  expect(probe.mock.calls[0][0].draft.headers['X-MCP-Readonly']).toBe('true');
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'changed-again' } });
  expect((screen.getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(true);
});
test('existing renamed endpoint is managed instead of duplicated', async () => {
  const option = PLUGIN_CATALOG.find(x => x.id === 'wkinfo')!.options![0];
  setup([{ ...option.draft, id: 'wk', displayName: '我的检索', configReviewState: 'reviewed', riskFindings: [], secretHeaderKeys: ['Authorization'], selectedThreadCount: 0 } as unknown as McpServerRecord]);
  render(<PluginCenterView />); await screen.findByRole('button', { name: '管理威科先行服务' });
  fireEvent.click(screen.getByRole('button', { name: '分类推荐' })); fireEvent.click(screen.getByRole('button', { name: '了解 威科先行' }));
  expect(screen.queryByRole('button', { name: '配置连接' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '管理 综合服务' }));
  expect(await screen.findByRole('heading', { name: '我的检索' })).toBeTruthy();
});
test('ima uses the existing connection dialog without connecting on browse', async () => {
  setup();
  const status = vi.spyOn(imaApi, 'status').mockResolvedValue({ configured: false });
  const connect = vi.spyOn(imaApi, 'connect');
  await catalog(); fireEvent.click(screen.getByRole('button', { name: '了解 ima' }));
  expect(status).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '管理 ima 连接' }));
  await screen.findByLabelText('Client ID');
  expect(status).toHaveBeenCalledTimes(1); expect(connect).not.toHaveBeenCalled();
});
test('source and key destinations match the native allowlist; arbitrary URLs are rejected', async () => {
  const rust = readFileSync('src-tauri/src/lib.rs', 'utf8');
  const destinations = new Map([...rust.matchAll(/"([^"]+)"\s*=>\s*(?:\{\s*)?Ok\(\s*"([^"]+)"\s*,?\s*\)/g)]
    .map(([, id, url]) => [id, url]));
  for (const entry of PLUGIN_CATALOG) expect(destinations.get(`plugin-${entry.id}`)).toBe(entry.source);
  for (const [id, url] of Object.entries(CREDENTIAL_SITES)) expect(destinations.get(`plugin-key-${id}`)).toBe(url);
  await platform.openPluginSource('qcc', true);
  expect(invoke).toHaveBeenCalledWith('open_case_database', { database: 'plugin-key-qcc' });
  await expect(platform.openPluginSource('https://evil.invalid', true)).rejects.toThrow();
  await expect(platform.openPluginSource('officecli', true)).rejects.toThrow();
  expect(invoke).toHaveBeenCalledTimes(1);
});

test('details open as a modal and return to the same filtered card on close or Escape', async () => {
  setup(); await catalog();
  fireEvent.click(screen.getByRole('button', { name: '企业尽调（2）' }));
  const trigger = screen.getByRole('button', { name: '了解 天眼查' });
  trigger.focus(); fireEvent.click(trigger);
  expect(screen.getByRole('dialog', { name: '天眼查' })).toBeTruthy();
  expect(screen.getAllByRole('article')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: '关闭插件详情' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(trigger);
  fireEvent.click(trigger);
  fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

test.each(['威科先行', '北大法宝', '企查查', '天眼查', 'Tavily', 'Firecrawl', 'GitHub'])('%s exposes its API Key input immediately', async name => {
  const calls = setup(); await catalog();
  fireEvent.click(screen.getByRole('button', { name: `了解 ${name}` }));
  expect(screen.getByLabelText('API Key')).toHaveProperty('type', 'password');
  expect(screen.getByLabelText('API Key')).toHaveProperty('value', '');
  expect(screen.queryByRole('button', { name: '配置连接' })).toBeNull();
  expect(screen.getByRole('button', { name: ['北大法宝', '企查查'].includes(name) ? '测试全部服务' : '测试并查看工具' })).toHaveProperty('disabled', true);
  expect(calls.test).not.toHaveBeenCalled();
});

 test.each(['威科先行', '北大法宝', '企查查', '天眼查'])('%s shortcut opens a guided form and restores focus on cancel', async name => {
  const calls = setup(); render(<PluginCenterView />);
  await screen.findByText('已添加 0 个外部工具服务');
  const trigger = screen.getByRole('button', { name: `连接${name}` });
  fireEvent.click(trigger);
  expect(screen.getByRole('dialog', { name })).toBeTruthy();
  expect(screen.getByRole('list', { name: '接入步骤' })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'synthetic-unused' } });
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(document.activeElement).toBe(trigger);
  fireEvent.click(trigger);
  expect(screen.getByLabelText('API Key')).toHaveProperty('value', '');
  expect(calls.test).not.toHaveBeenCalled();
 });

test('guided service groups stay compact and renamed connections remain searchable', async () => {
  const entry = PLUGIN_CATALOG.find(item => item.id === 'qcc')!;
  setup(entry.options!.map((option, index) => ({ ...option.draft, id: `qcc-${index}`, displayName: index ? option.draft.displayName : '自定义核验名称' } as McpServerRecord)));
  render(<PluginCenterView />);
  await screen.findByText('已添加 3 个外部工具服务');
  expect(screen.queryByRole('article', { name: '企查查-企业风险' })).toBeNull();
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '自定义核验名称' } });
  expect(screen.getAllByRole('article')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '管理企查查服务' }));
  expect(screen.getByRole('button', { name: '管理 企业信息' })).toBeTruthy();
});

test('grouped preset keeps connection failures visible in the main list', async () => {
  const option = PLUGIN_CATALOG.find(item => item.id === 'qcc')!.options![0];
  setup([{ ...option.draft, id: 'failed-qcc', connectionStatus: 'error', lastTestStatus: 'failed' } as McpServerRecord]);
  render(<PluginCenterView />);
  const card = await screen.findByRole('article', { name: '企查查快捷接入' });
  expect(within(card).getByText('连接失败，请检查')).toBeTruthy();
  expect(within(card).getByRole('button', { name: '管理企查查服务' })).toBeTruthy();
});
