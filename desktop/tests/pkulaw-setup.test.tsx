import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { PkulawSetup } from '../src/components/PkulawSetup';
import { PKULAW_ENTRY } from '../src/mcp/catalog';
import * as api from '../src/runtime/api';
import type { McpServerRecord, McpTestResult } from '../src/runtime/contracts';

const options = PKULAW_ENTRY.options!;
const tool = { toolName: 'search', description: '示例检索工具', descriptionHash: 'a'.repeat(64), schemaHash: 'b'.repeat(64), inputSchema: {} };
const result: McpTestResult = { testReceipt: 'test-receipt', tools: [tool], riskCodes: [] };
const record = (index: number, overrides: Partial<McpServerRecord> = {}) => ({ ...options[index].draft,
  id: `saved-${index}`, configRevision: 2, secretRevision: 1, riskFindings: [], configReviewState: 'reviewed',
  connectionStatus: 'idle', ...overrides } as McpServerRecord);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function setup(records: McpServerRecord[] = []) {
  const list = vi.spyOn(api, 'listMcpServers').mockResolvedValue(records);
  const probe = vi.spyOn(api, 'testMcpServerDraft').mockImplementation(async input => ({ ...result, testReceipt: input.draft.url! }));
  const savedProbe = vi.spyOn(api, 'testSavedMcpServer').mockResolvedValue(result);
  vi.spyOn(api, 'listMcpServerTools').mockResolvedValue([{ ...tool, serverId: 'saved-0', enabled: false, reviewState: 'known', firstSeenAt: '', lastSeenAt: '' }]);
  const save = vi.spyOn(api, 'createMcpServer').mockImplementation(async input => ({ ...record(0), ...input.draft, id: input.draft.url! }));
  const update = vi.spyOn(api, 'updateMcpServer').mockResolvedValue(record(0, { configRevision: 3 }));
  const review = vi.spyOn(api, 'reviewMcpServerTool').mockResolvedValue({ ...tool, serverId: 'saved-0', enabled: false, reviewState: 'known', firstSeenAt: '', lastSeenAt: '' });
  const onSaved = vi.fn(); const onComplete = vi.fn();
  render(<PkulawSetup servers={records} onSaved={onSaved} onComplete={onComplete} onCancel={vi.fn()} onOpen={vi.fn()} onBusyChange={vi.fn()} />);
  return { list, probe, savedProbe, save, update, review, onSaved, onComplete };
}
const enterKey = (key = 'synthetic-key') => fireEvent.change(screen.getByLabelText('API Key'), { target: { value: key } });
async function testAll() {
  fireEvent.click(screen.getByRole('button', { name: '测试全部服务' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '保存全部服务' })).toHaveProperty('disabled', false));
}

test('one key tests every official endpoint, then saves separately reviewed receipts without selecting any conversation', async () => {
  const calls = setup();
  const select = vi.spyOn(api, 'selectThreadMcpServer'); const activate = vi.spyOn(api, 'activateMcpView');
  enterKey('Bearer synthetic-key'); await testAll();
  expect(calls.probe.mock.calls.map(([input]) => input.draft.url)).toEqual(options.map(option => option.draft.url));
  for (const [input] of calls.probe.mock.calls) {
    expect(JSON.stringify(input.draft)).not.toContain('synthetic-key');
    expect(input.secretPatch.headers).toEqual({ Authorization: 'Bearer synthetic-key' });
  }
  expect(calls.save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox', { name: '法律引用核验：search', hidden: true }));
  fireEvent.click(screen.getByRole('button', { name: '保存全部服务' }));
  await waitFor(() => expect(calls.onComplete).toHaveBeenCalledTimes(1));
  expect(calls.save).toHaveBeenCalledTimes(9);
  for (const [input] of calls.save.mock.calls) {
    expect(input.testReceipt).toBe(input.draft.url);
    expect(input.secretPatch.headers).toEqual({ Authorization: 'Bearer synthetic-key' });
    expect(input.toolDecisions[0].enabled).toBe(input.draft.url !== options[2].draft.url);
  }
  expect(select).not.toHaveBeenCalled(); expect(activate).not.toHaveBeenCalled();
});

test('one failed service can be retried without retesting successful services or losing the key', async () => {
  const calls = setup(); calls.probe.mockRejectedValueOnce(new Error('private server detail'));
  enterKey(); fireEvent.click(screen.getByRole('button', { name: '测试全部服务' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '保存已通过的服务（8）' })).toHaveProperty('disabled', false));
  expect(calls.save).not.toHaveBeenCalled();
  expect(screen.queryByText('private server detail')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '重试失败服务' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '保存全部服务' })).toHaveProperty('disabled', false));
  expect(calls.probe).toHaveBeenCalledTimes(10);
  expect(calls.probe.mock.calls[9][0].draft.url).toBe(options[0].draft.url);
  expect(screen.getByLabelText('API Key')).toHaveProperty('value', 'synthetic-key');
});

test('changing the key invalidates all tested receipts and busy testing cannot be closed or edited', async () => {
  const calls = setup(); enterKey(); await testAll();
  enterKey('replacement-key');
  expect(screen.queryByRole('button', { name: '保存全部服务' })).toBeNull();
  expect(screen.getByRole('button', { name: '保存已通过的服务' })).toHaveProperty('disabled', true);
  let finish!: (value: McpTestResult) => void;
  calls.probe.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: '测试全部服务' }));
  await waitFor(() => expect(calls.probe).toHaveBeenCalledTimes(10));
  expect(screen.getByLabelText('API Key')).toHaveProperty('disabled', true);
  expect(screen.getByRole('button', { name: '取消' })).toHaveProperty('disabled', true);
  finish(result);
  await waitFor(() => expect(screen.getByRole('button', { name: '保存全部服务' })).toHaveProperty('disabled', false));
  expect(calls.probe.mock.calls[17][0].secretPatch.headers).toEqual({ Authorization: 'Bearer replacement-key' });
});

test('existing renamed service updates its key without duplicates, preserves disabled tools and retries review after a successful update', async () => {
  const calls = setup([record(0, { displayName: '我的法宝' })]);
  calls.review.mockRejectedValueOnce(new Error('review failed'));
  enterKey(); await testAll();
  expect(calls.probe).toHaveBeenCalledTimes(8); expect(calls.savedProbe).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('checkbox', { name: '法规智能检索：search', hidden: true })).toHaveProperty('checked', false);
  fireEvent.click(screen.getByRole('button', { name: '保存全部服务' }));
  await waitFor(() => expect(screen.getByText('已保存 8 / 9 项服务。')).toBeTruthy());
  expect(calls.update).toHaveBeenCalledTimes(1); expect(calls.save).toHaveBeenCalledTimes(8);
  expect(calls.update.mock.calls[0][1].patch.displayName).toBeUndefined();
  expect(calls.onComplete).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '保存已通过的服务（1）' }));
  await waitFor(() => expect(calls.onComplete).toHaveBeenCalledTimes(1));
  expect(calls.update).toHaveBeenCalledTimes(1); expect(calls.save).toHaveBeenCalledTimes(8);
  expect(calls.review.mock.calls[1][2]).toMatchObject({ enabled: false, expectedSchemaHash: tool.schemaHash });
});

test('partial save preserves successes and refreshes records before retry to avoid duplicating an uncertain write', async () => {
  const calls = setup(); calls.save.mockRejectedValueOnce(new Error('response lost'));
  enterKey(); await testAll(); fireEvent.click(screen.getByRole('button', { name: '保存全部服务' }));
  await waitFor(() => expect(screen.getByText('已保存 8 / 9 项服务。')).toBeTruthy());
  calls.list.mockResolvedValue([record(0)]);
  fireEvent.click(screen.getByRole('button', { name: '重试失败服务' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '保存已通过的服务（1）' })).toHaveProperty('disabled', false));
  expect(calls.savedProbe).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '保存已通过的服务（1）' }));
  await waitFor(() => expect(calls.onComplete).toHaveBeenCalledTimes(1));
  expect(calls.save).toHaveBeenCalledTimes(9); expect(calls.update).toHaveBeenCalledTimes(1);
});
