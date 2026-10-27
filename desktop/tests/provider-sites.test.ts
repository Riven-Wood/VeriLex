import { afterEach, expect, test, vi } from 'vitest';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { openProviderSite } from '../src/platform/providerSites';
vi.mock('@tauri-apps/api/core', () => ({ invoke: vi.fn(), isTauri: vi.fn() }));
afterEach(() => vi.restoreAllMocks());
test('desktop sends only a fixed site identifier to the native opener', async () => {
  vi.mocked(isTauri).mockReturnValue(true);
  await openProviderSite('deepseek', 'console');
  expect(invoke).toHaveBeenCalledWith('open_case_database', { database: 'model-console-deepseek' });
  await expect(openProviderSite('https://untrusted.example', 'console')).rejects.toThrow('没有找到');
});
test('browser opens the official documentation with no opener access', async () => {
  vi.mocked(isTauri).mockReturnValue(false);
  const open = vi.spyOn(window, 'open').mockReturnValue(null);
  await openProviderSite('glm', 'docs');
  expect(open).toHaveBeenCalledWith('https://docs.bigmodel.cn/cn/guide/develop/openai/introduction', '_blank', 'noopener,noreferrer');
});
test('native launch failure is visible to the caller', async () => {
  vi.mocked(isTauri).mockReturnValue(true);
  vi.mocked(invoke).mockRejectedValueOnce(new Error('failed'));
  await expect(openProviderSite('kimi', 'console')).rejects.toThrow('系统浏览器没有打开');
});
