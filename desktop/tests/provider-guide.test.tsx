import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { ProviderConnections } from '../src/components/ProviderConnections';
import { RuntimeApiError } from '../src/runtime/api';
import type { ProviderApi } from '../src/runtime/providerClient';
import { PROVIDER_PRESETS } from '../src/providers/presets';
import { readFileSync } from 'node:fs';

vi.mock('../src/platform/providerSites', () => ({ openProviderSite: vi.fn().mockResolvedValue(undefined) }));
afterEach(cleanup);
function makeApi(overrides: Partial<ProviderApi> = {}): ProviderApi {
  return {
    listProviders: vi.fn().mockResolvedValue([]),
    testProviderConnection: vi.fn().mockResolvedValue({ models: ['text-one', 'text-two'], selected_model: 'text-one' }),
    createProvider: vi.fn().mockImplementation(async draft => ({ ...draft, api_key: undefined, id: 'new', has_secret: true, enabled: true })),
    testSavedProvider: vi.fn(), updateProvider: vi.fn(), deleteProvider: vi.fn(),
    ...overrides,
  } as unknown as ProviderApi;
}
async function setup(api = makeApi()) {
  render(<ProviderConnections api={api} onProvidersChanged={async () => undefined} autoDiscoverDelayMs={0} />);
  await screen.findByText('未连接任何模型提供商');
  fireEvent.click(screen.getByRole('button', { name: '添加模型连接' }));
  return api;
}

test('offers only domestic guides and preserves advanced connections', async () => {
  await setup();
  for (const item of PROVIDER_PRESETS) expect(screen.getByRole('button', { name: `接入 ${item.name}` })).toBeTruthy();
  expect(screen.queryByText(/Anthropic|Gemini|OpenRouter/)).toBeNull();
  expect(screen.queryByLabelText('Base URL')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /其他连接/ }));
  expect(screen.getByLabelText('Base URL')).toBeTruthy();
  expect(screen.getByRole('option', { name: 'Ollama' })).toBeTruthy();
});

test('verifies on explicit continue, saves the selected model, and never persists a key in the browser', async () => {
  const api = await setup();
  fireEvent.click(screen.getByRole('button', { name: '接入 DeepSeek' }));
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'fake-domestic-test-key' } });
  await new Promise(resolve => setTimeout(resolve, 20));
  expect(api.testProviderConnection).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '验证并继续' }));
  await screen.findByText('已读取模型信息');
  expect(api.testProviderConnection).toHaveBeenCalledWith(expect.objectContaining({
    name: 'DeepSeek', base_url: 'https://api.deepseek.com', auth_mode: 'api_key', api_key: 'fake-domestic-test-key', default_model: '',
  }), expect.any(AbortSignal));
  fireEvent.change(screen.getByLabelText('默认模型'), { target: { value: 'text-two' } });
  fireEvent.click(screen.getByRole('button', { name: '保存连接' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(api.createProvider).toHaveBeenCalledWith(expect.objectContaining({ default_model: 'text-two' }));
  expect(JSON.stringify(localStorage)).not.toContain('fake-domestic-test-key');
});

test('changing vendor cancels an old request and clears credentials and stale model choices', async () => {
  let finish!: (value: unknown) => void;
  const api = await setup(makeApi({ testProviderConnection: vi.fn().mockReturnValue(new Promise(resolve => { finish = resolve; })) }));
  fireEvent.click(screen.getByRole('button', { name: '接入 DeepSeek' }));
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'fake-one' } });
  fireEvent.click(screen.getByRole('button', { name: '验证并继续' }));
  const signal = vi.mocked(api.testProviderConnection).mock.calls[0][1];
  fireEvent.click(screen.getByRole('button', { name: '更换服务商' }));
  expect(signal?.aborted).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '接入 Kimi' }));
  expect((screen.getByLabelText('API Key') as HTMLInputElement).value).toBe('');
  finish({ models: ['stale-model'], selected_model: 'stale-model' });
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(screen.queryByText('stale-model')).toBeNull();
  expect((screen.getByRole('button', { name: '验证并继续' }) as HTMLButtonElement).disabled).toBe(true);
});

test('requires a model ID for platforms without reliable discovery and supports actionable retry', async () => {
  const api = await setup(makeApi({ testProviderConnection: vi.fn()
    .mockRejectedValueOnce(new RuntimeApiError('not found', { code: 'provider_model_not_found', retryable: false, status: 400 }))
    .mockResolvedValue({ models: ['ep-sample'], selected_model: 'ep-sample' }) }));
  fireEvent.click(screen.getByRole('button', { name: '接入 豆包' }));
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'fake-ark-key' } });
  expect((screen.getByRole('button', { name: '验证并继续' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('模型标识'), { target: { value: 'wrong-model' } });
  fireEvent.click(screen.getByRole('button', { name: '验证并继续' }));
  expect((await screen.findByRole('alert')).textContent).toContain('从官网复制已开通的模型标识');
  expect(screen.queryByText('已读取模型信息')).toBeNull();
  fireEvent.change(screen.getByLabelText('模型标识'), { target: { value: 'ep-sample' } });
  fireEvent.click(screen.getByRole('button', { name: '验证并继续' }));
  await screen.findByText('已读取模型信息');
  expect(api.createProvider).not.toHaveBeenCalled();
  expect(api.testProviderConnection).toHaveBeenLastCalledWith(expect.objectContaining({
    base_url: 'https://ark.cn-beijing.volces.com/api/v3', default_model: 'ep-sample',
  }), expect.any(AbortSignal));
});

test('does not claim success for an empty catalog or swallow a save failure', async () => {
  const api = await setup(makeApi({
    testProviderConnection: vi.fn().mockResolvedValueOnce({ models: [], selected_model: '' })
      .mockResolvedValue({ models: ['text-one'], selected_model: 'text-one' }),
    createProvider: vi.fn().mockRejectedValue(new Error('fake-key-must-not-appear')),
  }));
  fireEvent.click(screen.getByRole('button', { name: '接入 DeepSeek' }));
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'fake-key-must-not-appear' } });
  fireEvent.click(screen.getByRole('button', { name: '验证并继续' }));
  expect((await screen.findByRole('alert')).textContent).toContain('未读取到模型');
  fireEvent.click(screen.getByRole('button', { name: '验证并继续' }));
  await screen.findByText('已读取模型信息');
  fireEvent.click(screen.getByRole('button', { name: '保存连接' }));
  expect((await screen.findByRole('alert')).textContent).toContain('连接未保存');
  expect(screen.queryByText(/fake-key-must-not-appear/)).toBeNull();
  expect(screen.getByRole('dialog')).toBeTruthy();
});

test('native official-site allowlist matches every displayed link', () => {
  const source = readFileSync('src-tauri/src/lib.rs', 'utf8');
  const destinations = new Map([...source.matchAll(/"([^"]+)"\s*=>\s*(?:\{\s*)?Ok\(\s*"([^"]+)"\s*,?\s*\)/g)]
    .map(([, id, url]) => [id, url]));
  for (const preset of PROVIDER_PRESETS) {
    expect(destinations.get(`model-console-${preset.id}`)).toBe(preset.consoleUrl);
    expect(destinations.get(`model-docs-${preset.id}`)).toBe(preset.docsUrl);
  }
});


test('optional manual model recovery stays open while the user corrects input', async () => {
  await setup(makeApi({ testProviderConnection: vi.fn().mockRejectedValue(new RuntimeApiError('not found', {
    code: 'provider_model_not_found', retryable: false, status: 400,
  })) }));
  fireEvent.click(screen.getByRole('button', { name: '接入 DeepSeek' }));
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'fake-key' } });
  fireEvent.click(screen.getByRole('button', { name: '验证并继续' }));
  await screen.findByRole('alert');
  const input = screen.getByLabelText('模型标识');
  expect(input.closest('details')?.open).toBe(true);
  fireEvent.change(input, { target: { value: 'm' } });
  expect(input.closest('details')?.open).toBe(true);
  fireEvent.change(input, { target: { value: 'model-complete' } });
  expect((input as HTMLInputElement).value).toBe('model-complete');
});

test('catalog opens the selected preset directly and restores focus on close', async () => {
  const api = makeApi();
  render(<ProviderConnections api={api} onProvidersChanged={async () => undefined} />);
  await screen.findByText('未连接任何模型提供商');
  const trigger = screen.getByRole('button', {name: '添加 DeepSeek 连接'});
  fireEvent.click(trigger);
  expect(screen.getByLabelText('API Key')).toBeTruthy();
  expect(screen.queryByRole('button', {name: '接入 DeepSeek'})).toBeNull();
  expect(api.testProviderConnection).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: '关闭连接向导'}));
  expect(document.activeElement).toBe(trigger);
});

test('catalog locates saved connections without opening a duplicate wizard', async () => {
  const scroll = vi.fn();
  const original = HTMLElement.prototype.scrollIntoView;
  HTMLElement.prototype.scrollIntoView = scroll;
  try {
    const api = makeApi({listProviders: vi.fn().mockResolvedValue([{id:'existing',name:'我的连接',kind:'openai_compatible',base_url:'https://api.deepseek.com/',default_model:'model-one',enabled:true,has_secret:true,auth_mode:'api_key'}])});
    render(<ProviderConnections api={api} onProvidersChanged={async () => undefined} />);
    fireEvent.click(await screen.findByRole('button', {name:'查看 DeepSeek 的已连接模型'}));
    expect(document.activeElement?.id).toBe('saved-provider-existing');
    expect(scroll).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.testSavedProvider).not.toHaveBeenCalled();
  } finally { HTMLElement.prototype.scrollIntoView = original; }
});

test('local and custom catalog entries retain the existing connection types', async () => {
  render(<ProviderConnections api={makeApi()} onProvidersChanged={async () => undefined} />);
  await screen.findByText('未连接任何模型提供商');
  fireEvent.click(screen.getByRole('button', {name:'配置本机 Ollama'}));
  expect((screen.getByLabelText('提供商类型') as HTMLSelectElement).value).toBe('ollama');
  expect((screen.getByLabelText('Base URL') as HTMLInputElement).value).toBe('http://127.0.0.1:11434');
  fireEvent.click(screen.getByRole('button', {name:'关闭连接向导'}));
  fireEvent.click(screen.getByRole('button', {name:'添加自定义连接'}));
  expect((screen.getByLabelText('提供商类型') as HTMLSelectElement).value).toBe('openai_compatible');
});
