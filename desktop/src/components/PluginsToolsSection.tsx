import type { ReactNode, RefObject } from "react";
import { formatRelativeTime } from "../app/navigation";
import { mcpErrorCopy } from "../mcp/errorCopy";
import { GUIDED_MCP_ENTRIES, configuredOption, type CatalogEntry } from "../mcp/catalog";
import { McpPresetCards, matchingMcpPresets } from "./McpPresetCards";
import type { McpServerRecord, McpToolRecord } from "../runtime/contracts";

export const YUANDIAN_GROUP_ID = "preset:yuandian";

function statusCopy(servers: readonly McpServerRecord[]): string {
  if (!servers.length) return "尚未添加";
  if (servers.some(s => s.connectionStatus === "needs_credentials")) return "需要补充密钥";
  if (servers.some(s => s.configReviewState === "blocked")) return "配置已阻止";
  if (servers.some(s => s.configReviewState === "needs_review")) return "需要复核配置";
  const failed = servers.find(s => s.connectionStatus === "error" || s.lastTestStatus === "failed");
  if (failed) return `连接失败 · ${mcpErrorCopy(failed.errorCode ?? undefined)}`;
  if (servers.every(s => s.connectionStatus === "ready")) return "当前有对话正在使用";
  if (servers.every(s => s.lastTestStatus === "passed")) return "上次测试可用";
  return "未测试";
}

function lastTestCopy(servers: readonly McpServerRecord[]): string {
  const times = servers.map(s => s.lastTestedAt).filter((t): t is string => !!t);
  if (!times.length) return "尚无测试记录";
  // For a group, show the oldest member's test, never imply the whole group was just tested.
  if (times.length !== servers.length) return "部分连接尚未测试";
  return `最近测试：${formatRelativeTime(times.sort()[0])}`;
}

interface Props {
  addButtonRef: RefObject<HTMLButtonElement>;
  yuandianActionRef: RefObject<HTMLButtonElement>;
  counts: Record<string, number>;
  servers: McpServerRecord[];
  yuandianServers: McpServerRecord[];
  otherServers: McpServerRecord[];
  complete: boolean;
  query: string;
  expandedId: string | null;
  tools: Record<string, McpToolRecord[]>;
  toolsLoading: string | null;
  busy: boolean;
  testing: boolean;
  onBack: () => void;
  onOpen: (server: McpServerRecord) => void;
  onOpenYuandian: () => void;
  onAdd: () => void;
  onEdit: (server: McpServerRecord) => void;
  onYuandian: () => void;
  onPreset: (entry: CatalogEntry, trigger: HTMLButtonElement) => void;
  onTest: (servers: McpServerRecord[]) => void;
  onDelete: (servers: McpServerRecord[], label: string, builtin: boolean, trigger: HTMLButtonElement) => void;
  renderTool: (tool: McpToolRecord) => ReactNode;
}

export function PluginsToolsSection(props: Props) {
  const { servers, yuandianServers, otherServers, complete, query, expandedId, tools, toolsLoading, busy, testing } = props;
  const builtin = expandedId === YUANDIAN_GROUP_ID;
  const selected = builtin ? yuandianServers : servers.filter(s => s.id === expandedId);
  const name = builtin ? "元典智库" : selected[0]?.displayName;
  const count = (records: McpServerRecord[]) => records.length === 0 ? "" : records.every(s => props.counts[s.id] !== undefined)
    ? `${records.reduce((sum, s) => sum + props.counts[s.id], 0)} 个工具` : "工具数量待读取";
  if (expandedId && name) return <div className="plugin-detail">
    <button type="button" className="mcp-secondary-button" disabled={busy} onClick={props.onBack}>返回列表</button>
    <div className="plugin-detail-heading"><h2>{name}</h2><span className="plugin-card-badge">{builtin ? "内置" : "自定义"}</span></div>
    <p>{statusCopy(selected)} · {lastTestCopy(selected)}</p>
    <div className="plugin-detail-actions">
      <button type="button" className="mcp-secondary-button" disabled={busy || !selected.length}
        onClick={() => props.onTest(selected)}>{testing ? "正在测试…" : "测试连接"}</button>
      {builtin ? <button type="button" className="mcp-secondary-button" disabled={busy} onClick={props.onYuandian}
        aria-label={!complete ? "添加元典智库" : selected.some(s => s.connectionStatus === "needs_credentials") ? "补充元典智库密钥" : "更新元典智库密钥"}>
        {!complete ? "继续添加" : selected.some(s => s.connectionStatus === "needs_credentials") ? "补充 API Key" : "更新密钥"}
      </button> : <button type="button" className="mcp-secondary-button" disabled={busy} onClick={() => props.onEdit(selected[0])}>编辑配置</button>}
      <button type="button" className="mcp-danger-button" disabled={busy || !selected.length} aria-label={`删除 ${name}`}
        onClick={e => props.onDelete(selected, name, builtin, e.currentTarget)}>删除</button>
    </div>
    <div className="mcp-server-tools">
      <h3>工具清单</h3>
      {toolsLoading === expandedId ? <p role="status">正在读取工具…</p> :
        <ul aria-label={`${name} 的工具`}>{selected.flatMap(s => tools[s.id] ?? []).map(props.renderTool)}</ul>}
      {!toolsLoading && selected.every(s => tools[s.id]?.length === 0) && <p>此服务暂未提供工具。</p>}
      {selected.some(s => (tools[s.id] ?? []).some(t => t.reviewState !== "known")) && <p>服务器增加或改变了工具，请查看说明后再启用。</p>}
    </div>
  </div>;
  const search = query.trim().toLocaleLowerCase("zh-CN");
  const showYuandian = "元典智库".includes(search);
  const showPresets = matchingMcpPresets(search, servers).length > 0;
  const guidedIds = new Set(GUIDED_MCP_ENTRIES.flatMap(entry => entry.options!.map(option => configuredOption(option, servers)?.id)));
  const filtered = otherServers.filter(s => !guidedIds.has(s.id) && s.displayName.toLocaleLowerCase("zh-CN").includes(search))
    .sort((a, b) => a.displayName.localeCompare(b.displayName, "zh-CN"));
  return <>
    <div className="plugin-tools-heading"><p>{`已添加 ${otherServers.length + (yuandianServers.length ? 1 : 0)} 个外部工具服务`}</p>
      <button ref={props.addButtonRef} type="button" className="mcp-add" onClick={props.onAdd}>添加外部工具</button>
    </div>
    {!showYuandian && !showPresets && !filtered.length && <p className="mcp-empty">没有找到匹配的名称</p>}
    <div className="plugin-tools-grid">
      {showYuandian && <article className="plugin-card" aria-label="元典智库">
        <div className="plugin-card-title"><h3>元典智库</h3><span className="plugin-card-badge">内置</span></div>
        <p>{yuandianServers.length && !complete ? "尚未完整添加" : statusCopy(yuandianServers)}</p>
        <p>网络服务{count(yuandianServers) ? ` · ${count(yuandianServers)}` : ""}</p>
        <p>{lastTestCopy(yuandianServers)}</p>
        <button ref={props.yuandianActionRef} className="mcp-secondary-button plugin-card-action" type="button"
          aria-label={yuandianServers.length ? "查看 元典智库" : "添加元典智库"}
          onClick={yuandianServers.length ? props.onOpenYuandian : props.onYuandian}>{yuandianServers.length ? "查看详情" : "连接元典"}</button>
      </article>}
      <McpPresetCards servers={servers} query={query} disabled={busy} onOpen={props.onPreset} />
      {filtered.map(server => <article className="plugin-card" key={server.id} aria-label={server.displayName}>
        <div className="plugin-card-title"><h3>{server.displayName}</h3><span className="plugin-card-badge">自定义</span></div>
        <p>{statusCopy([server])}</p><p>{server.transport === "stdio" ? "本机工具" : "网络服务"} · {count([server])}</p>
        <p>{lastTestCopy([server])}</p>
        <button className="mcp-secondary-button plugin-card-action" type="button" aria-label={`查看 ${server.displayName}`} onClick={() => props.onOpen(server)}>查看详情</button>
      </article>)}
    </div>
  </>;
}
