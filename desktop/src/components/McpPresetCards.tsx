import { GUIDED_MCP_ENTRIES, configuredOption, type CatalogEntry } from '../mcp/catalog';
import type { McpServerRecord, McpToolRecord } from '../runtime/contracts';

export function matchingMcpPresets(query: string, servers: McpServerRecord[] = []) {
  const search = query.trim().toLocaleLowerCase('zh-CN');
  return GUIDED_MCP_ENTRIES.filter(entry => `${entry.name} ${entry.id} ${entry.category} mcp`.toLocaleLowerCase('zh-CN').includes(search)
    || entry.options!.some(option => configuredOption(option, servers)?.displayName.toLocaleLowerCase('zh-CN').includes(search)));
}

export function McpPresetCards({ servers, tools = {}, query = '', disabled = false, onOpen }: {
  servers: McpServerRecord[]; query?: string; disabled?: boolean;
  tools?: Record<string, McpToolRecord[]>;
  onOpen: (entry: CatalogEntry, trigger: HTMLButtonElement) => void;
}) {
  return <>{matchingMcpPresets(query, servers).map(entry => {
    const records = entry.options!.flatMap(option => { const record = configuredOption(option, servers); return record ? [record] : []; });
    const count = records.length;
    const pending = records.reduce((sum, record) => sum + (tools[record.id] ?? []).filter(tool => tool.reviewState !== 'known').length, 0);
    const attention = records.some(record => record.connectionStatus === 'needs_credentials') ? '需要补充密钥'
      : records.some(record => record.configReviewState === 'blocked' || record.configReviewState === 'needs_review') ? '需要复核配置'
      : records.some(record => record.connectionStatus === 'error' || record.lastTestStatus === 'failed') ? '连接失败，请检查' : '';
    const total = entry.options!.length;
    return <article className="plugin-card" key={entry.id} aria-label={`${entry.name}快捷接入`}>
      <div className="plugin-card-title"><h3>{entry.name}</h3><span className="plugin-card-badge">{entry.category}</span></div>
      <p>{entry.summary}</p>
      <p>{count ? `已添加 ${count} / ${total} 项服务` : total > 1 ? `填写一次 API Key，接入 ${total} 项服务。` : '填写 API Key 即可测试接入。'}</p>
      {attention && <p role="status">{attention}</p>}
      {pending > 0 && <p role="status">{pending} 项工具更新待确认</p>}
      <button className="mcp-secondary-button plugin-card-action" type="button" disabled={disabled}
        aria-label={count ? `管理${entry.name}服务` : `连接${entry.name}`}
        onClick={event => onOpen(entry, event.currentTarget)}>{count ? '接入与管理' : '连接'}</button>
    </article>;
  })}</>;
}
