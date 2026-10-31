import type { McpServerRecord } from '../runtime/contracts';
import { PKULAW_ENTRY, PLUGIN_CATALOG, configuredOption } from './catalog';
import { yuandianServiceFromUrl } from './yuandian';

/** Keep legal providers and user-defined services; omit recognized unrelated presets. */
export function legalWorkbenchServers(servers: readonly McpServerRecord[]): McpServerRecord[] {
  return servers.filter(server => {
    if (providerGroup(server)) return true;
    const preset = PLUGIN_CATALOG.find(entry => entry.options?.some(option => configuredOption(option, [server])));
    return !preset || preset.category === '法律检索';
  });
}

export function providerGroup(server: McpServerRecord): 'yuandian' | 'pkulaw' | null {
  if (server.transport === 'http' && yuandianServiceFromUrl(server.url)) return 'yuandian';
  if (PKULAW_ENTRY.options!.some(option => configuredOption(option, [server]))) return 'pkulaw';
  return null;
}

export function groupedSearchServers(servers: readonly McpServerRecord[]) {
  const groups: { label: string; representative: McpServerRecord; ids: string[] }[] = [];
  for (const server of servers) {
    const provider = providerGroup(server);
    const label = provider === 'yuandian' ? '元典智库' : provider === 'pkulaw' ? '北大法宝' : server.displayName;
    const existing = provider && groups.find(group => providerGroup(group.representative) === provider);
    if (existing) existing.ids.push(server.id);
    else groups.push({ label, representative: server, ids: [server.id] });
  }
  return groups;
}

export function searchServerIds(servers: readonly McpServerRecord[], selectedId: string): string[] {
  return groupedSearchServers(servers).find(group => group.ids.includes(selectedId))?.ids ?? [selectedId];
}
