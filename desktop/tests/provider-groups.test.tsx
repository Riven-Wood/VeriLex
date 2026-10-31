import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { LegalChannelPicker } from '../src/components/LegalChannelPicker';
import { CaseChannelPicker } from '../src/components/CaseChannelPicker';
import { PKULAW_ENTRY, PLUGIN_CATALOG } from '../src/mcp/catalog';
import { searchServerIds, legalWorkbenchServers } from '../src/mcp/providerGroups';
import type { McpServerRecord } from '../src/runtime/contracts';

afterEach(cleanup);
const yuan = ['law', 'case', 'company', 'securities'].map((name) => ({ id: `yuan-${name}`, displayName: `元典-${name}`, transport: 'http', url: `https://open.chineselaw.com/mcp/${name}/stream` } as McpServerRecord));
const pku = PKULAW_ENTRY.options!.map((option, index) => ({ ...option.draft, id: `pku-${index}` } as McpServerRecord));
const other = { id: 'other', displayName: '其他插件', transport: 'http', url: 'https://example.com/mcp' } as McpServerRecord;
const servers = [...yuan, ...pku, other];

test.each(['legal', 'case'])('%s picker shows one item per provider and selects its group', (kind) => {
  const onChange = vi.fn();
  const props = { mcpServers: servers, value: { kind: 'plugin' as const, serverId: null }, onChange, onOpenMcpSettings: vi.fn(), menu: 'plugin' as const, onMenuChange: vi.fn() };
  render(kind === 'legal' ? <LegalChannelPicker {...props} /> : <CaseChannelPicker {...props} />);
  expect(screen.getAllByRole('menuitemradio')).toHaveLength(3);
  fireEvent.click(screen.getByRole('menuitemradio', { name: '北大法宝' }));
  const chosen = onChange.mock.calls[0][0].serverId;
  expect(searchServerIds(servers, chosen)).toEqual(pku.map(item => item.id));
  expect(searchServerIds(servers, yuan[2].id)).toEqual(yuan.map(item => item.id));
  expect(searchServerIds(servers, other.id)).toEqual(['other']);
});


test('retrieval workbenches omit recognized nonlegal plugins while retaining custom services', () => {
  const office = PLUGIN_CATALOG.find(entry => entry.id === 'officecli')!.options![0];
  const record = {...office.draft, id: 'office'} as McpServerRecord;
  expect(legalWorkbenchServers([...servers, record])).toEqual(servers);
});
