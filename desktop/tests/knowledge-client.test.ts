import { afterEach, expect, test, vi } from 'vitest';
import { knowledgeApi } from '../src/runtime/knowledgeClient';
afterEach(() => vi.unstubAllGlobals());
test('management sends JSON that the real HTTP API can parse', async () => {
  const fetch = vi.fn(async () => new Response('{"id":"one"}', {status:200, headers:{'Content-Type':'application/json'}}));
  vi.stubGlobal('fetch', fetch);
  await knowledgeApi.associate('/notes');
  await knowledgeApi.update('one', {excluded:['案例']});
  for (const call of fetch.mock.calls as unknown as [string, RequestInit][]) {
    expect(new Headers(call[1].headers).get('Content-Type')).toBe('application/json');
    expect(() => JSON.parse(String(call[1].body))).not.toThrow();
  }
});
