import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { McpThreadPicker } from '../src/components/McpThreadPicker';
import { PKULAW_ENTRY } from '../src/mcp/catalog';
import type { McpServerRecord, McpThreadSnapshot } from '../src/runtime/contracts';
afterEach(cleanup);
const savedServers = PKULAW_ENTRY.options!.map((option, index) => ({ ...option.draft, id: `pku-${index}` } as McpServerRecord));
function props(selected: boolean) {
  const snapshot = { servers: [...savedServers.map(server => ({ serverId: server.id, displayName: server.displayName,
    selected, connectionStatus: 'idle', toolCount: 1 })),
    { serverId: 'unrelated', displayName: '其他服务', selected: false, connectionStatus: 'idle', toolCount: 0 }],
  } as McpThreadSnapshot;
  return { snapshot, savedServers, loading: false, error: '', pendingServerIds: new Set<string>(),
    onSelect: vi.fn().mockResolvedValue(undefined), onDeselect: vi.fn().mockResolvedValue(undefined),
    onRetry: vi.fn(), onAddServer: vi.fn(), onOpenSettings: vi.fn(), onClose: vi.fn() };
}
test('one click selects or deselects the exact saved Pkulaw endpoints only', async () => {
  const input = props(false); const view = render(<McpThreadPicker {...input} />);
  expect(input.onSelect).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '全选法宝服务（9）' }));
  await waitFor(() => expect(input.onSelect).toHaveBeenCalledTimes(9));
  expect(input.onSelect.mock.calls.map(([id]) => id)).toEqual(savedServers.map(server => server.id));
  const selected = props(true); view.rerender(<McpThreadPicker {...selected} />);
  fireEvent.click(screen.getByRole('button', { name: '取消全选法宝服务（9）' }));
  await waitFor(() => expect(selected.onDeselect).toHaveBeenCalledTimes(9));
  expect(selected.onDeselect.mock.calls.map(([id]) => id)).toEqual(savedServers.map(server => server.id));
});
test('partial failure keeps successful selections visible and allows retrying the remaining endpoints', async () => {
  const input = props(false); input.onSelect.mockRejectedValueOnce(new Error('offline'));
  const view = render(<McpThreadPicker {...input} />);
  fireEvent.click(screen.getByRole('button', { name: '全选法宝服务（9）' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '全选法宝服务（9）' })).toHaveProperty('disabled', false));
  const partial = props(false); partial.snapshot.servers[0].selected = true;
  view.rerender(<McpThreadPicker {...partial} />);
  fireEvent.click(screen.getByRole('button', { name: '全选法宝服务（9）' }));
  await waitFor(() => expect(partial.onSelect).toHaveBeenCalledTimes(8));
  expect(partial.onSelect).not.toHaveBeenCalledWith('pku-0');
});
