import { useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, Plus, RotateCw, Search, X } from "lucide-react";

import type { McpSelectionTarget } from "../hooks/useThreadMcp";
import { mcpErrorCopy } from "../mcp/errorCopy";
import {
  YUANDIAN_SERVICES,
  yuandianServiceFromServer,
} from "../mcp/yuandian";
import type { McpServerRecord, McpThreadSnapshot } from "../runtime/contracts";
import { McpServerEditor } from "./McpServerEditor";
import { YuandianSetup } from "./YuandianSetup";
import { PKULAW_ENTRY, configuredOption } from "../mcp/catalog";

interface McpThreadPickerProps {
  snapshot: McpThreadSnapshot | null;
  savedServers?: readonly McpServerRecord[];
  loading: boolean;
  error: string;
  pendingServerIds: ReadonlySet<string>;
  onSelect: (target: McpSelectionTarget) => Promise<void> | void;
  onDeselect: (serverId: string) => Promise<void> | void;
  onRetry: (serverId?: string) => Promise<void> | void;
  onAddServer: (target: Exclude<McpSelectionTarget, string>) => Promise<void> | void;
  onOpenSettings: () => void;
  onClose: () => void;
}

function statusCopy(status: string, selected: boolean, toolCount: number): string {
  if (!selected) return "未选择";
  switch (status) {
    case "connecting": return "连接中";
    case "ready": return `可用工具 ${toolCount} 个`;
    case "retrying": return "正在重试";
    case "needs_credentials": return "需要补充密钥";
    case "error": return "连接没有成功";
    case "stopping": return "正在停止";
    default: return "已选择";
  }
}

export function McpThreadPicker({
  snapshot,
  savedServers = [],
  loading,
  error,
  pendingServerIds,
  onSelect,
  onDeselect,
  onRetry,
  onAddServer,
  onOpenSettings,
  onClose,
}: McpThreadPickerProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const rowRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [query, setQuery] = useState("");
  const [pkulawBusy, setPkulawBusy] = useState(false);
  const pkulawIds = new Set(savedServers.filter(server => PKULAW_ENTRY.options!.some(option =>
    configuredOption(option, [server]))).map(server => server.id));
  const pkulawServers = (snapshot?.servers ?? []).filter(server => pkulawIds.has(server.serverId));
  const pkulawSelected = pkulawServers.length > 0 && pkulawServers.every(server => server.selected);
  const pkulawPending = pkulawBusy || pkulawServers.some(server => pendingServerIds.has(server.serverId));
  const [adding, setAdding] = useState<"" | "generic" | "yuandian">("");
  const yuandianServers = useMemo(() =>
    (snapshot?.servers ?? []).filter((server) => server.presetId === "yuandian"), [snapshot]);
  const existingYuandianServices = useMemo(() => new Set(
    yuandianServers.flatMap((server) => server.presetServiceId ? [server.presetServiceId] : []),
  ), [yuandianServers]);
  const yuandianComplete = existingYuandianServices.size === YUANDIAN_SERVICES.length;
  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("zh-CN");
    return (snapshot?.servers ?? []).filter((server) => server.presetId !== "yuandian" && !pkulawIds.has(server.serverId) && (
      !normalized || server.displayName.toLocaleLowerCase("zh-CN").includes(normalized)
    ));
  }, [query, snapshot, savedServers]);
  const showYuandian = !query.trim()
    || "元典智库".toLocaleLowerCase("zh-CN").includes(query.trim().toLocaleLowerCase("zh-CN"));

  useEffect(() => {
    if (!adding) searchRef.current?.focus();
  }, [adding]);

  function handleDialogKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
      "button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary",
    ) ?? []);
    if (focusable.length === 0) return;
    const current = focusable.indexOf(document.activeElement as HTMLElement);
    const next = event.shiftKey
      ? (current <= 0 ? focusable.length - 1 : current - 1)
      : (current === focusable.length - 1 ? 0 : current + 1);
    event.preventDefault();
    focusable[next]?.focus();
  }

  function focusRow(index: number) {
    if (filtered.length === 0) return;
    const normalized = (index + filtered.length) % filtered.length;
    rowRefs.current[normalized]?.focus();
  }

  function runSafely(operation: Promise<void> | void) {
    void Promise.resolve(operation).catch(() => undefined);
  }

  return (
    <div
      className="mcp-dialog-backdrop mcp-picker-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="mcp-thread-picker"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-thread-picker-title"
        onKeyDown={handleDialogKeyDown}
      >
        <header className="mcp-picker-header">
          <div>
            <h2 id="mcp-thread-picker-title">当前对话的插件</h2>
            <p>选择后，这个对话可以直接使用已审核的工具。</p>
          </div>
          <button type="button" className="mcp-icon-button" aria-label="关闭插件" onClick={onClose}>
            <X aria-hidden="true" size={17} />
          </button>
        </header>

        {adding === "generic" ? (
          <McpServerEditor
            mode="thread"
            onAtomicSelected={async (input) => onAddServer(input)}
            onCancel={() => setAdding("")}
            onComplete={() => setAdding("")}
          />
        ) : adding === "yuandian" ? (
          <YuandianSetup
            existingServers={savedServers.filter((server) =>
              yuandianServiceFromServer(server) !== null)}
            onSelectServer={onSelect}
            onRefresh={() => onRetry()}
            onAddServer={onAddServer}
            onCancel={() => setAdding("")}
            onComplete={() => setAdding("")}
          />
        ) : (
          <>
            <label className="mcp-picker-search">
              <Search aria-hidden="true" size={15} />
              <input
                ref={searchRef}
                type="search"
                aria-label="搜索插件"
                placeholder="搜索已保存的插件"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                    event.preventDefault();
                    focusRow(event.key === "ArrowDown" ? 0 : filtered.length - 1);
                  }
                }}
              />
            </label>

            {error && (
              <div className="mcp-picker-error" role="alert">
                <span>{error}</span>
                <button type="button" onClick={() => runSafely(onRetry())}>重试</button>
              </div>
            )}

            <div className="mcp-picker-list" aria-live="polite">
              {loading && !snapshot ? (
                <p className="mcp-loading"><LoaderCircle className="attachment-spinner" aria-hidden="true" size={15} />正在读取插件</p>
              ) : filtered.length === 0 && !showYuandian && !(pkulawServers.length && (!query.trim() || "北大法宝 pkulaw".includes(query.trim().toLowerCase()))) ? (
                <p className="mcp-empty">{query ? "没有匹配的插件。" : "还没有保存的插件。"}</p>
              ) : (
                <ul>
                  {showYuandian && (() => {
                    const selected = yuandianComplete
                      && yuandianServers.every((server) => server.selected);
                    const partlySelected = yuandianServers.some((server) => server.selected);
                    const pending = yuandianServers.some((server) =>
                      pendingServerIds.has(server.serverId));
                    const failed = yuandianServers.some((server) => Boolean(server.errorCode));
                    const needsCredentials = yuandianServers.some((server) =>
                      server.connectionStatus === "needs_credentials");
                    const readyTools = yuandianServers.reduce((sum, server) =>
                      sum + (server.connectionStatus === "ready" ? server.toolCount : 0), 0);
                    const label = !yuandianComplete
                      ? "输入 API Key 即可连接"
                      : pending
                        ? "正在更新"
                        : selected && !failed
                          ? `可用工具 ${readyTools} 个`
                          : partlySelected
                            ? "部分能力已选择"
                            : "未选择";
                    const toggle = async () => {
                      if (!yuandianComplete) {
                        setAdding("yuandian");
                        return;
                      }
                      if (selected) {
                        for (const server of yuandianServers) {
                          await onDeselect(server.serverId);
                        }
                        return;
                      }
                      for (const server of yuandianServers.filter((item) => !item.selected)) {
                        await onSelect(server.serverId);
                      }
                    };
                    return (
                      <li key="preset:yuandian">
                        <button
                          type="button"
                          className="mcp-picker-row yuandian-picker-row"
                          aria-pressed={selected}
                          aria-label={`${yuandianComplete ? (selected ? "取消选择" : "选择") : "连接"} 元典智库`}
                          disabled={pending}
                          onClick={() => runSafely(toggle())}
                        >
                          <span className="mcp-picker-check" aria-hidden="true">{selected ? "✓" : "元"}</span>
                          <span className="mcp-picker-copy">
                            <strong>元典智库</strong>
                            <small>{label}</small>
                            {needsCredentials
                              ? <small className="mcp-picker-row-error">需要补充密钥</small>
                              : failed && <small className="mcp-picker-row-error">连接没有成功</small>}
                          </span>
                          <span className="yuandian-built-in">内置</span>
                        </button>
                        {needsCredentials ? (
                          <button
                            type="button"
                            className="mcp-picker-retry"
                            onClick={() => setAdding("yuandian")}
                          >
                            补充 API Key
                          </button>
                        ) : failed && (
                          <button
                            type="button"
                            className="mcp-picker-retry"
                            onClick={() => runSafely((async () => {
                              for (const server of yuandianServers.filter((item) => item.errorCode)) {
                                await onRetry(server.serverId);
                              }
                            })())}
                          >
                            <RotateCw aria-hidden="true" size={14} />
                            重试
                          </button>
                        )}
                      </li>
                    );
                  })()}
                  {pkulawServers.length > 0 && (!query.trim() || "北大法宝 pkulaw".includes(query.trim().toLowerCase())) && (() => {
                    const partial = pkulawServers.some(server => server.selected);
                    const failed = pkulawServers.some(server => server.selected && Boolean(server.errorCode));
                    const readyTools = pkulawServers.reduce((sum, server) => sum + (server.connectionStatus === "ready" ? server.toolCount : 0), 0);
                    return <li key="preset:pkulaw">
                      <button type="button" className="mcp-picker-row" disabled={pkulawPending}
                        aria-pressed={pkulawSelected} aria-label={`${pkulawSelected ? "取消选择" : "选择"} 北大法宝`}
                        onClick={() => runSafely((async () => {
                          if (pkulawBusy) return;
                          setPkulawBusy(true);
                          try {
                            for (const server of pkulawServers) {
                              if (pkulawSelected) await onDeselect(server.serverId);
                              else if (!server.selected) await onSelect(server.serverId);
                            }
                          } finally { setPkulawBusy(false); }
                        })())}>
                        <span className="mcp-picker-check" aria-hidden="true">{pkulawSelected ? "✓" : ""}</span>
                        <span className="mcp-picker-copy"><strong>北大法宝</strong>
                          <small>{pkulawPending ? "正在更新" : pkulawSelected && !failed ? `可用工具 ${readyTools} 个` : partial ? "部分能力已选择" : "未选择"}</small>
                          {failed && <small className="mcp-picker-row-error">部分服务连接没有成功</small>}
                        </span>
                      </button>
                      {failed && pkulawServers.some(server => server.selected && server.errorCode && server.retryable) &&
                        <button type="button" className="mcp-picker-retry" onClick={() => runSafely((async () => {
                          for (const server of pkulawServers.filter(item => item.selected && item.errorCode && item.retryable)) await onRetry(server.serverId);
                        })())}><RotateCw aria-hidden="true" size={14} />重试</button>}
                    </li>;
                  })()}
                  {filtered.map((server, index) => {
                    const pending = pendingServerIds.has(server.serverId) || (pkulawBusy && pkulawIds.has(server.serverId));
                    const failed = server.selected && Boolean(server.errorCode);
                    return (
                      <li key={server.serverId}>
                        <button
                          ref={(node) => { rowRefs.current[index] = node; }}
                          type="button"
                          className="mcp-picker-row"
                          aria-pressed={server.selected}
                          aria-label={`${server.selected ? "取消选择" : "选择"} ${server.displayName}`}
                          disabled={pending}
                          onClick={() => runSafely(server.selected
                            ? onDeselect(server.serverId)
                            : onSelect(server.serverId))}
                          onKeyDown={(event) => {
                            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                              event.preventDefault();
                              focusRow(index + (event.key === "ArrowDown" ? 1 : -1));
                            } else if (event.key === " " || event.key === "Enter") {
                              event.preventDefault();
                              event.currentTarget.click();
                            }
                          }}
                        >
                          <span className="mcp-picker-check" aria-hidden="true">{server.selected ? "✓" : ""}</span>
                          <span className="mcp-picker-copy">
                            <strong>{server.displayName}</strong>
                            <small>{pending ? "正在更新" : statusCopy(server.connectionStatus, server.selected, server.toolCount)}</small>
                            {server.selected && server.errorCode && <small className="mcp-picker-row-error">{mcpErrorCopy(server.errorCode)}</small>}
                          </span>
                          {pending && <LoaderCircle className="attachment-spinner" aria-hidden="true" size={15} />}
                        </button>
                        {failed && server.retryable && (
                          <button
                            type="button"
                            className="mcp-picker-retry"
                            aria-label={`重新连接 ${server.displayName}`}
                            onClick={() => runSafely(onRetry(server.serverId))}
                          >
                            <RotateCw aria-hidden="true" size={14} />
                            重试
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <footer className="mcp-picker-actions">
              <button type="button" className="mcp-secondary-button" onClick={onOpenSettings}>管理</button>
              <button type="button" className="mcp-add" onClick={() => setAdding("generic")}>
                <Plus aria-hidden="true" size={15} />
                添加其他 MCP
              </button>
            </footer>
          </>
        )}
      </div>
    </div>
  );
}
