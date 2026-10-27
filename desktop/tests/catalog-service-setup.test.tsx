import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { CatalogServiceSetup } from '../src/components/CatalogServiceSetup';
import { GUIDED_MCP_ENTRIES } from '../src/mcp/catalog';
import * as api from '../src/runtime/api';
import type { McpServerRecord } from '../src/runtime/contracts';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const tool = { toolName: 'search', description: '虚构查询工具', descriptionHash: 'd', schemaHash: 's', inputSchema: {} };
function setup(id: string) {
  const entry = GUIDED_MCP_ENTRIES.find(item => item.id === id)!;
  vi.spyOn(api, 'listMcpServers').mockResolvedValue([]);
  const probe = vi.spyOn(api, 'testMcpServerDraft').mockImplementation(async input => ({ testReceipt: input.draft.url!, tools: [tool], riskCodes: [] }));
  const save = vi.spyOn(api, 'createMcpServer').mockImplementation(async input => ({ ...input.draft, id: input.draft.url! } as McpServerRecord));
  const complete = vi.fn();
  render(<CatalogServiceSetup entry={entry} servers={[]} onSaved={vi.fn()} onComplete={complete} onCancel={vi.fn()} onOpen={vi.fn()} onBusyChange={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'synthetic-key' } });
  return { entry, probe, save, complete };
}

test.each(['wkinfo', 'qcc', 'pkulaw', 'tianyancha'])('%s sends the documented authentication separately and saves only tested receipts', async id => {
  const { entry, probe, save, complete } = setup(id);
  const single = entry.options!.length === 1;
  fireEvent.click(screen.getByRole('button', { name: single ? '测试并查看工具' : '测试全部服务' }));
  const saveButton = single ? '保存' : '保存全部服务';
  await waitFor(() => expect(screen.getByRole('button', { name: saveButton })).toHaveProperty('disabled', false));
  for (const [request] of probe.mock.calls) {
    expect(request.draft.headers).toEqual({});
    expect(JSON.stringify(request.draft)).not.toContain('synthetic-key');
    expect(request.secretPatch?.headers).toEqual({ Authorization: `${id === 'tianyancha' ? '' : 'Bearer '}synthetic-key` });
  }
  if (id === 'tianyancha') expect(probe.mock.calls[0][0].draft.url).toBe('https://mcp.tianyancha.com/mcp');
  fireEvent.click(screen.getByRole('button', { name: saveButton }));
  await waitFor(() => expect(complete).toHaveBeenCalledTimes(1));
  expect(save).toHaveBeenCalledTimes(entry.options!.length);
  for (const [request] of save.mock.calls) {
    expect(request.testReceipt).toBe(request.draft.url);
    expect(request.toolDecisions).toEqual([{ toolName: 'search', expectedDescriptionHash: 'd', expectedSchemaHash: 's', enabled: true }]);
  }
});

test('QCC saves successful services, then retries only the failed endpoint', async () => {
  const { entry, probe, save, complete } = setup('qcc');
  probe.mockRejectedValueOnce(new api.RuntimeApiError('private-key-and-server-detail', { status: 401, code: 'mcp_auth_failed', retryable: false }));
  fireEvent.click(screen.getByRole('button', { name: '测试全部服务' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '保存已通过的服务（2）' })).toHaveProperty('disabled', false));
  expect(screen.queryByText(/private-key-and-server-detail/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '保存已通过的服务（2）' }));
  await screen.findByText('已保存 2 / 3 项服务。');
  expect(complete).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '重试失败服务' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '保存已通过的服务（1）' })).toHaveProperty('disabled', false));
  expect(probe).toHaveBeenCalledTimes(4);
  expect(probe.mock.calls[3][0].draft.url).toBe(entry.options![0].draft.url);
  fireEvent.click(screen.getByRole('button', { name: '保存已通过的服务（1）' }));
  await waitFor(() => expect(complete).toHaveBeenCalled());
  expect(save).toHaveBeenCalledTimes(3);
});
