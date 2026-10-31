import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { YuandianSetup } from '../src/components/YuandianSetup';
import { YUANDIAN_SERVICES } from '../src/mcp/yuandian';
import type { McpServerRecord, McpToolRecord } from '../src/runtime/contracts';

afterEach(cleanup);
const server: McpServerRecord = {
  id: 'law', displayName: '元典法律法规', transport: 'http', command: null, args: [], cwd: null,
  url: YUANDIAN_SERVICES[0].url, env: {}, headers: {}, secretEnvKeys: [], secretHeaderKeys: ['Authorization'],
  connectTimeoutMs: 10000, requestTimeoutMs: 30000, configRevision: 2, secretRevision: 1,
  configReviewState: 'reviewed', riskFindings: [], connectionStatus: 'idle', errorCode: null,
  retryable: false, lastTestStatus: 'passed', lastTestedAt: null, selectedThreadCount: 0,
  createdAt: '', updatedAt: '',
};
const tool: McpToolRecord = {
  serverId: 'law', toolName: 'new_law_search', description: '检索法律法规', inputSchema: { type: 'object' },
  descriptionHash: 'd'.repeat(64), schemaHash: 'a'.repeat(64), enabled: false, reviewState: 'new',
  firstSeenAt: '', lastSeenAt: '',
};
function setup(refresh = vi.fn().mockResolvedValue([tool])) {
  const review = vi.fn().mockResolvedValue({ ...tool, enabled: true, reviewState: 'known' });
  const complete = vi.fn();
  const update = vi.fn().mockResolvedValue({ ...server, configRevision: 3 });
  const probe = vi.fn().mockResolvedValue({ testReceipt: 'receipt', tools: [], riskCodes: [] });
  const list = vi.fn().mockResolvedValue([tool]);
  render(<YuandianSetup placement="settings" inDialog existingServers={[server]}
    onAddServer={vi.fn()} onCancel={vi.fn()} onComplete={complete} onRefresh={vi.fn()}
    refreshSavedTools={refresh} reviewSavedTool={review} updateSaved={update} listSavedTools={list}
    probe={probe} probeSaved={vi.fn().mockResolvedValue({ testReceipt: 'receipt', tools: [tool], riskCodes: [] })} />);
  return { refresh, review, complete, update, list };
}
test('saved key synchronizes renamed tools, explicitly reviews exact snapshot, and verifies it again', async () => {
  const { refresh, review, complete, update } = setup();
  fireEvent.click(screen.getByRole('button', { name: '同步工具' }));
  await screen.findByText('元典有 1 项工具更新，请确认本次启用的能力。');
  expect(review).not.toHaveBeenCalled();
  expect(update).not.toHaveBeenCalled();
  expect(complete).not.toHaveBeenCalled();
  refresh.mockResolvedValue([{ ...tool, enabled: true, reviewState: 'known' }]);
  fireEvent.click(screen.getByRole('button', { name: '确认工具更新' }));
  await waitFor(() => expect(complete).toHaveBeenCalledTimes(1));
  expect(review).toHaveBeenCalledWith('law', tool.toolName, {
    enabled: true, expectedDescriptionHash: tool.descriptionHash, expectedSchemaHash: tool.schemaHash,
  }, expect.any(AbortSignal));
  expect(refresh).toHaveBeenCalledTimes(2);
});
test('user can decline a new tool while existing disabled tools remain untouched', async () => {
  const existing = { ...tool, toolName: 'disabled_search', reviewState: 'known', enabled: false };
  const { refresh, review, complete } = setup(vi.fn().mockResolvedValue([tool, existing]));
  fireEvent.click(screen.getByRole('button', { name: '同步工具' }));
  fireEvent.click(await screen.findByRole('checkbox'));
  refresh.mockResolvedValue([{ ...tool, reviewState: 'known' }, existing]);
  fireEvent.click(screen.getByRole('button', { name: '确认工具更新' }));
  await waitFor(() => expect(complete).toHaveBeenCalledTimes(1));
  expect(review).toHaveBeenCalledTimes(1);
  expect(review.mock.calls[0][2].enabled).toBe(false);
});
test('a second upstream change stays pending instead of reporting success', async () => {
  const { refresh, complete } = setup();
  fireEvent.click(screen.getByRole('button', { name: '同步工具' }));
  await screen.findByRole('checkbox');
  refresh.mockResolvedValue([{ ...tool, descriptionHash: 'e'.repeat(64), reviewState: 'changed' }]);
  fireEvent.click(screen.getByRole('button', { name: '确认工具更新' }));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2));
  expect(complete).not.toHaveBeenCalled();
  expect(await screen.findByText('元典有 1 项工具更新，请确认本次启用的能力。')).toBeTruthy();
});
test('failed refresh does not approve tools or report success and can be retried', async () => {
  const { refresh, review, complete } = setup(vi.fn().mockRejectedValue(new Error('offline')));
  fireEvent.click(screen.getByRole('button', { name: '同步工具' }));
  await screen.findByRole('alert');
  expect(complete).not.toHaveBeenCalled();
  expect(review).not.toHaveBeenCalled();
  refresh.mockResolvedValue([]);
  fireEvent.click(screen.getByRole('button', { name: '同步工具' }));
  await waitFor(() => expect(complete).toHaveBeenCalledTimes(1));
});
test('key replacement surfaces newly discovered tools and uses the saved revision for confirmation', async () => {
  const { refresh, review, complete, update } = setup();
  fireEvent.change(screen.getByLabelText('元典 API Key'), { target: { value: 'synthetic-test-key' } });
  fireEvent.click(screen.getByRole('button', { name: '确认更新' }));
  await screen.findByRole('checkbox');
  expect(update).toHaveBeenCalledTimes(1);
  expect(complete).not.toHaveBeenCalled();
  expect(review).not.toHaveBeenCalled();
  refresh.mockResolvedValue([{ ...tool, reviewState: 'known', enabled: true }]);
  fireEvent.click(screen.getByRole('button', { name: '确认工具更新' }));
  await waitFor(() => expect(complete).toHaveBeenCalledTimes(1));
  expect(refresh).toHaveBeenCalledWith('law', 3, expect.any(AbortSignal));
});
