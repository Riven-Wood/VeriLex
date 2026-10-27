import { useEffect, useRef, useState } from "react";
import {
  BriefcaseBusiness,
  Check,
  ChevronDown,
  ChevronUp,
  KeyRound,
  Pencil,
  PlugZap,
  Plus,
  Trash2,
  Wrench,
} from "lucide-react";

import { mcpErrorCopy } from "../mcp/errorCopy";
import { YUANDIAN_SERVICES, yuandianServiceFromServer } from "../mcp/yuandian";
import type { McpServerRecord, McpToolRecord } from "../runtime/contracts";
import {
  createMcpServer,
  deleteMcpServer,
  listMcpServers,
  listMcpServerTools,
  reviewMcpServerTool,
  RuntimeApiError,
  refreshMcpServerTools,
} from "../runtime/api";
import { McpServerEditor } from "./McpServerEditor";
import { YuandianSetup } from "./YuandianSetup";

import { PluginCatalog } from "./PluginCatalog";
import { PluginCatalogDetail } from "./PluginCatalogDetail";
import { GUIDED_MCP_ENTRIES, type CatalogEntry } from "../mcp/catalog";
import { McpPresetCards } from "./McpPresetCards";
import { PluginsToolsSection, YUANDIAN_GROUP_ID } from "./PluginsToolsSection";

function errorCopy(error: unknown): string {
  return mcpErrorCopy(error instanceof RuntimeApiError ? error.code : undefined);
}

function stateCopy(server: McpServerRecord): string {
  if (server.configReviewState === "blocked") return "配置已阻止";
  if (server.configReviewState === "needs_review") return "需要复核";
  switch (server.connectionStatus) {
    case "ready": return "当前有对话正在使用";
    case "needs_credentials": return "需要补充密钥";
    case "error": return "连接失败";
    default: return "已保存";
  }
}

function yuandianStateCopy(servers: readonly McpServerRecord[], complete: boolean): string {
  if (!complete) return servers.length > 0 ? "尚未完整添加" : "输入一个 API Key 即可添加";
  if (servers.some((server) => server.connectionStatus === "needs_credentials")) return "需要补充 API Key";
  if (servers.some((server) => server.configReviewState !== "reviewed")) return "有工具需要复核";
  if (servers.some((server) => server.connectionStatus === "error")) return "连接需要检查";
  if (servers.some((server) => server.connectionStatus === "ready")) return "当前有对话正在使用";
  return "已添加，需要时在对话中选择";
}

const toolOrder = (tool: McpToolRecord) => tool.reviewState === "known" ? 1 : 0;

type EditingTarget = "generic" | "yuandian" | McpServerRecord | null;

interface DeleteTarget {
  kind: "server" | "yuandian";
  label: string;
  servers: McpServerRecord[];
}

interface McpConnectionsProps {
  onChanged?: () => Promise<void> | void;
  variant?: "settings" | "center";
  query?: string;
}

export function McpConnections({ onChanged, variant = "settings", query = "" }: McpConnectionsProps = {}) {
  const [servers, setServers] = useState<McpServerRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState<EditingTarget>(null);
  const [catalog, setCatalog] = useState(false);
  const [presetEntry, setPresetEntry] = useState<CatalogEntry | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [toolsByServer, setToolsByServer] = useState<Record<string, McpToolRecord[]>>({});
  const [toolCounts, setToolCounts] = useState<Record<string, number>>({});
  const [toolsLoading, setToolsLoading] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [search, setSearch] = useState("");
  const [reviewingTool, setReviewingTool] = useState(false);
  const actionControllerRef = useRef<AbortController | null>(null);
  const toolWriteControllerRef = useRef<AbortController | null>(null);
  const deleteTriggerRef = useRef<HTMLButtonElement | null>(null);
  const cancelDeleteRef = useRef<HTMLButtonElement | null>(null);
  const deleteDialogRef = useRef<HTMLDivElement | null>(null);
  const addButtonRef = useRef<HTMLButtonElement | null>(null);
  const yuandianActionRef = useRef<HTMLButtonElement | null>(null);
  const presetActionRef = useRef<HTMLButtonElement | null>(null);
  const closePreset = () => setPresetEntry(null);
  const openPreset = (entry: CatalogEntry, trigger: HTMLButtonElement) => {
    presetActionRef.current = trigger; setPresetEntry(entry);
  };
  useEffect(() => { if (!presetEntry) presetActionRef.current?.focus(); }, [presetEntry]);

  const yuandianServers = servers.filter((server) => yuandianServiceFromServer(server) !== null);
  const yuandianServiceIds = new Set(yuandianServers.flatMap((server) => {
    const serviceId = yuandianServiceFromServer(server);
    return serviceId ? [serviceId] : [];
  }));
  const yuandianComplete = yuandianServiceIds.size === YUANDIAN_SERVICES.length;
  const yuandianNeedsCredentials = yuandianServers.some((server) =>
    server.connectionStatus === "needs_credentials");
  const otherServers = servers.filter((server) => yuandianServiceFromServer(server) === null);
  const normalizedSearch = search.trim().toLocaleLowerCase("zh-CN");
  const filteredServers = normalizedSearch
    ? otherServers.filter((server) => server.displayName.toLocaleLowerCase("zh-CN").includes(normalizedSearch))
    : otherServers;

  useEffect(() => {
    const controller = new AbortController();
    listMcpServers(controller.signal)
      .then(records => { setServers(records); setHasLoaded(true); })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(errorCopy(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      actionControllerRef.current?.abort();
      toolWriteControllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (variant !== "center") return;
    const controller = new AbortController();
    void Promise.allSettled(servers.map(async server => {
      const tools = await listMcpServerTools(server.id, controller.signal);
      if (!controller.signal.aborted) setToolCounts(current => ({ ...current, [server.id]: tools.length }));
    }));
    return () => controller.abort();
  }, [servers, variant]);

  useEffect(() => {
    if (!deleteTarget) return;
    if (deleting) deleteDialogRef.current?.focus();
    else cancelDeleteRef.current?.focus();
  }, [deleteTarget, deleting]);

  const startAction = () => {
    actionControllerRef.current?.abort();
    const controller = new AbortController();
    actionControllerRef.current = controller;
    setError("");
    setStatus("");
    return controller;
  };

  const refreshServers = async () => {
    const controller = startAction();
    try {
      const records = await listMcpServers(controller.signal);
      if (controller.signal.aborted) return;
      setServers(records);
      setHasLoaded(true);
    } catch {
      if (!controller.signal.aborted) setError("已经保存，但列表暂时没有刷新。重新进入本页即可查看。");
    }
    try {
      await onChanged?.();
    } catch {
      // The app-level Runtime status owns this failure. Saving the MCP record
      // has already succeeded and must not be reported as a failed API key.
    }
  };

  const saved = (record: McpServerRecord) => {
    setServers((current) => current.some((item) => item.id === record.id)
      ? current.map((item) => item.id === record.id ? record : item)
      : [...current, record]);
    setStatus("已保存。需要使用时，请在当前对话中选择。");
  };

  const completeEditing = () => {
    const created = editing === "generic";
    setEditing(null);
    if (created) void onChanged?.();
  };

  const completeYuandian = () => {
    setEditing(null);
    setStatus("元典智库已添加。需要使用时，请在当前对话中选择。");
  };

  const closeDelete = () => {
    if (deleting) return;
    deleteTriggerRef.current?.focus();
    setDeleteTarget(null);
  };

  const toggleTools = async (server: McpServerRecord) => {
    if (expandedId === server.id) {
      actionControllerRef.current?.abort();
      setExpandedId(null);
      setToolsLoading(null);
      return;
    }
    const controller = startAction();
    setExpandedId(server.id);
    setToolsLoading(server.id);
    try {
      const tools = await listMcpServerTools(server.id, controller.signal);
      if (controller.signal.aborted) return;
      setToolsByServer((current) => ({
        ...current,
        [server.id]: [...tools].sort((left, right) => toolOrder(left) - toolOrder(right)),
      }));
    } catch (caught) {
      if (!controller.signal.aborted) setError(errorCopy(caught));
    } finally {
      if (!controller.signal.aborted) setToolsLoading(null);
    }
  };

  const toggleYuandianTools = async () => {
    if (expandedId === YUANDIAN_GROUP_ID) {
      actionControllerRef.current?.abort();
      setExpandedId(null);
      setToolsLoading(null);
      return;
    }
    const controller = startAction();
    setExpandedId(YUANDIAN_GROUP_ID);
    setToolsLoading(YUANDIAN_GROUP_ID);
    try {
      const entries = await Promise.all(yuandianServers.map(async (server) => [
        server.id,
        [...await listMcpServerTools(server.id, controller.signal)]
          .sort((left, right) => toolOrder(left) - toolOrder(right)),
      ] as const));
      if (controller.signal.aborted) return;
      setToolsByServer((current) => ({ ...current, ...Object.fromEntries(entries) }));
    } catch (caught) {
      if (!controller.signal.aborted) setError(errorCopy(caught));
    } finally {
      if (!controller.signal.aborted) setToolsLoading(null);
    }
  };

  const changeTool = async (tool: McpToolRecord, enabled: boolean) => {
    if (toolWriteControllerRef.current) return;
    const controller = new AbortController();
    toolWriteControllerRef.current = controller;
    setReviewingTool(true);
    setError("");
    setStatus("");
    try {
      const reviewed = await reviewMcpServerTool(tool.serverId, tool.toolName, {
        enabled,
        expectedDescriptionHash: tool.descriptionHash,
        expectedSchemaHash: tool.schemaHash,
      }, controller.signal);
      if (controller.signal.aborted) return;
      setToolsByServer((current) => ({
        ...current,
        [tool.serverId]: (current[tool.serverId] ?? [])
          .map((item) => item.toolName === reviewed.toolName ? reviewed : item)
          .sort((left, right) => toolOrder(left) - toolOrder(right)),
      }));
      setStatus(enabled ? "工具已启用。" : "工具已停用。");
    } catch (caught) {
      if (!controller.signal.aborted) setError(errorCopy(caught));
    } finally {
      if (toolWriteControllerRef.current === controller) {
        toolWriteControllerRef.current = null;
        if (!controller.signal.aborted) setReviewingTool(false);
      }
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget || deleting) return;
    const target = deleteTarget;
    const controller = startAction();
    setDeleting(true);
    try {
      let finalResult = { deleted: false, selectedThreadCount: 0 };
      for (const server of target.servers) {
        finalResult = await deleteMcpServer(server.id, controller.signal);
        if (controller.signal.aborted) return;
        setServers((current) => current.filter((item) => item.id !== server.id));
        setToolsByServer((current) => {
          const next = { ...current };
          delete next[server.id];
          return next;
        });
      }
      setExpandedId((current) => target.servers.some((server) => server.id === current)
        || (target.kind === "yuandian" && current === YUANDIAN_GROUP_ID) ? null : current);
      setStatus(target.kind === "yuandian"
        ? "已移除元典智库，并从相关对话中取消使用。"
        : finalResult.deleted
          ? `已删除服务器，并从 ${finalResult.selectedThreadCount} 个对话中移除。`
          : `服务器已不存在，已刷新列表；已从 ${finalResult.selectedThreadCount} 个对话中移除。`);
      void onChanged?.();
      setDeleteTarget(null);
      queueMicrotask(() => target.kind === "yuandian"
        ? (yuandianActionRef.current ?? addButtonRef.current)?.focus()
        : addButtonRef.current?.focus());
    } catch (caught) {
      if (!controller.signal.aborted) setError(errorCopy(caught));
    } finally {
      if (!controller.signal.aborted) setDeleting(false);
    }
  };

  const testConnections = async (records: McpServerRecord[]) => {
    if (testing || reviewingTool) return;
    const controller = startAction();
    setTesting(true);
    setToolsLoading(null);
    try {
      for (const server of records) {
        const tools = await refreshMcpServerTools(server.id, server.configRevision, controller.signal);
        if (controller.signal.aborted) return;
        setToolsByServer(current => ({ ...current, [server.id]: tools }));
        setToolCounts(current => ({ ...current, [server.id]: tools.length }));
      }
      setStatus("测试通过。工具清单已刷新。");
    } catch (caught) {
      if (!controller.signal.aborted) setError(errorCopy(caught));
    } finally {
      if (!controller.signal.aborted) {
        try {
          const current = await listMcpServers(controller.signal);
          if (!controller.signal.aborted) setServers(current);
        } catch {
          if (!controller.signal.aborted) setError("测试已结束，状态暂时无法刷新。请返回列表后重试。");
        }
        if (!controller.signal.aborted) setTesting(false);
      }
    }
  };

  const renderTool = (tool: McpToolRecord) => (
    <li key={`${tool.serverId}:${tool.toolName}`}>
      <div><strong>{tool.toolName}</strong><small>{tool.description || "无说明"}</small></div>
      {tool.reviewState !== "known" && (
        <span className="mcp-review-badge">{tool.reviewState === "new" ? "新增" : "已变化"}</span>
      )}
      <label>
        <input
          type="checkbox"
          disabled={reviewingTool || testing}
          aria-label={`启用 ${tool.toolName}`}
          checked={tool.enabled}
          onChange={(event) => void changeTool(tool, event.target.checked)}
        />
      </label>
    </li>
  );

  const deleteLabel = deleteTarget?.label ?? "";

  return (
    <div className="mcp-connections">
      {error && !deleteTarget && <p className="mcp-error" role="alert">{error}{variant === "center" && <button type="button" className="mcp-secondary-button" disabled={testing || reviewingTool || deleting} onClick={() => void refreshServers()}>刷新列表</button>}</p>}
      {status && <p className="mcp-save-status" role="status">{status}</p>}
      {presetEntry && <PluginCatalogDetail entry={presetEntry} servers={servers}
        onSaved={saved} onClose={closePreset}
        onComplete={() => { closePreset(); void onChanged?.(); }}
        onOpen={server => { setPresetEntry(null); void toggleTools(server); }}
        onYuandian={() => setEditing("yuandian")} />}

      {variant === "center" && !editing && !expandedId && <div className="plugin-browse-mode" role="group" aria-label="外部工具浏览方式">
        <button type="button" aria-pressed={!catalog} onClick={() => setCatalog(false)}>已添加</button>
        <button type="button" aria-pressed={catalog} onClick={() => setCatalog(true)}>分类推荐</button>
      </div>}
      {variant === "center" && catalog && !editing && !expandedId ? <PluginCatalog query={query} servers={servers}
        onSaved={saved} onComplete={() => { setCatalog(false); void onChanged?.(); }}
        onOpen={server => { setCatalog(false); void toggleTools(server); }}
        onYuandian={() => { setCatalog(false); if (yuandianComplete) void toggleYuandianTools(); else setEditing("yuandian"); }}
      /> : editing === "yuandian" ? (
        <div className="mcp-yuandian-detail">
          <YuandianSetup
            placement="settings"
            existingServers={yuandianServers}
            onAddServer={async (input) => saved(await createMcpServer(input))}
            onRefresh={refreshServers}
            onCancel={() => setEditing(null)}
            onComplete={completeYuandian}
          />
        </div>
      ) : editing ? (
        <McpServerEditor
          mode="settings"
          server={editing === "generic" ? undefined : editing}
          onSaved={saved}
          onComplete={completeEditing}
          onCancel={() => setEditing(null)}
        />
      ) : variant === "center" ? (
        loading ? <p role="status">正在读取插件…</p> : !hasLoaded ? null : <PluginsToolsSection
          servers={servers} yuandianServers={yuandianServers} otherServers={otherServers}
          addButtonRef={addButtonRef} yuandianActionRef={yuandianActionRef}
          counts={toolCounts} complete={yuandianComplete} query={query} expandedId={expandedId} tools={toolsByServer}
          toolsLoading={toolsLoading} busy={reviewingTool || testing} testing={testing}
          onBack={() => { actionControllerRef.current?.abort(); setExpandedId(null); setToolsLoading(null); }}
          onOpen={server => void toggleTools(server)} onOpenYuandian={() => void toggleYuandianTools()}
          onAdd={() => setEditing("generic")} onEdit={setEditing} onYuandian={() => setEditing("yuandian")}
          onPreset={openPreset}
          onTest={records => void testConnections(records)} renderTool={renderTool}
          onDelete={(records, label, builtin, trigger) => {
            deleteTriggerRef.current = trigger;
            setDeleteTarget({ kind: builtin ? "yuandian" : "server", label, servers: [...records] });
          }}
        />
      ) : (
        <>
          {!loading && (
            <p className="mcp-count">{`本机 ${servers.length} 个 MCP 连接`}</p>
          )}
          <div className="mcp-catalog-heading">
            <div>
              <h3>推荐</h3>
              <p>为法律工作整理的可靠连接，按服务添加即可。</p>
            </div>
            <span>共 {GUIDED_MCP_ENTRIES.length + 1} 个</span>
          </div>

          <section className="mcp-recommendation-group" aria-labelledby="mcp-legal-category">
            <header>
              <span className="mcp-category-icon"><BriefcaseBusiness aria-hidden="true" size={18} /></span>
              <div>
                <h4 id="mcp-legal-category">法律与检索</h4>
                <small>1 个服务</small>
              </div>
            </header>

            <article className="mcp-recommendation-card" aria-label="元典智库">
              <div className="mcp-recommendation-copy">
                <div className="mcp-recommendation-title">
                  <h5>元典智库</h5>
                  <span className="mcp-recommended-badge">推荐</span>
                  {yuandianComplete && <span className="mcp-installed-badge"><Check aria-hidden="true" size={12} />已添加</span>}
                </div>
                <p>检索法律法规、案例、企业和证券信息，适合法律调研与事实核查。</p>
                <small>{loading ? "正在读取状态…" : yuandianStateCopy(yuandianServers, yuandianComplete)}</small>
              </div>
              <div className="mcp-recommendation-actions">
                <button
                  ref={yuandianActionRef}
                  className={yuandianComplete ? "mcp-secondary-button mcp-key-button" : "mcp-add"}
                  type="button"
                  disabled={loading || reviewingTool}
                  aria-label={yuandianComplete
                    ? yuandianNeedsCredentials ? "补充元典智库密钥" : "更新元典智库密钥"
                    : "添加元典智库"}
                  onClick={() => setEditing("yuandian")}
                >
                  {yuandianComplete ? <KeyRound aria-hidden="true" size={15} /> : <Plus aria-hidden="true" size={16} />}
                  {yuandianComplete
                    ? yuandianNeedsCredentials ? "补充密钥" : "更新密钥"
                    : yuandianServers.length > 0 ? "继续添加" : "添加"}
                </button>
                {yuandianServers.length > 0 && (
                  <>
                    <button
                      className="mcp-icon-button"
                      type="button"
                      disabled={reviewingTool}
                      aria-label="查看元典智库的工具"
                      onClick={() => void toggleYuandianTools()}
                    >
                      <Wrench aria-hidden="true" size={15} />
                      {expandedId === YUANDIAN_GROUP_ID
                        ? <ChevronUp aria-hidden="true" size={13} />
                        : <ChevronDown aria-hidden="true" size={13} />}
                    </button>
                    <button
                      className="mcp-icon-button mcp-delete"
                      type="button"
                      disabled={reviewingTool}
                      aria-label="移除元典智库"
                      onClick={(event) => {
                        deleteTriggerRef.current = event.currentTarget;
                        setDeleteTarget({ kind: "yuandian", label: "元典智库", servers: [...yuandianServers] });
                      }}
                    >
                      <Trash2 aria-hidden="true" size={15} />
                    </button>
                  </>
                )}
              </div>
            </article>

            {expandedId === YUANDIAN_GROUP_ID && (
              <div className="mcp-server-tools mcp-preset-tools">
                {toolsLoading === YUANDIAN_GROUP_ID ? (
                  <p className="mcp-loading" role="status">正在读取工具…</p>
                ) : (
                  <ul aria-label="元典智库的工具">
                    {yuandianServers.flatMap((server) => toolsByServer[server.id] ?? []).map(renderTool)}
                  </ul>
                )}
              </div>
            )}
          </section>

          <div className="plugin-tools-grid"><McpPresetCards servers={servers} disabled={loading || reviewingTool} onOpen={openPreset} /></div>

          <section className="mcp-custom-section" aria-labelledby="mcp-custom-title">
            <div className="mcp-custom-heading">
              <div>
                <h3 id="mcp-custom-title">其他 MCP</h3>
                {otherServers.length > 0 && <p>{`已添加 ${otherServers.length} 个自定义连接`}</p>}
              </div>
              <button
                ref={addButtonRef}
                className="mcp-secondary-button mcp-custom-add"
                type="button"
                disabled={reviewingTool}
                onClick={() => setEditing("generic")}
              >
                <Plus aria-hidden="true" size={15} /> 添加其他 MCP
              </button>
            </div>

            {otherServers.length > 0 && (
              <>
                <label className="mcp-search">
                  <input
                    type="search"
                    aria-label="搜索 MCP 服务器"
                    value={search}
                    placeholder="搜索已添加的 MCP"
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </label>
                {filteredServers.length === 0 ? (
                  <p className="mcp-empty">没有找到匹配的 MCP 服务器。</p>
                ) : (
                  <ul className="mcp-list" aria-label="其他 MCP">
                    {filteredServers.map((server) => {
                      const expanded = expandedId === server.id;
                      const tools = toolsByServer[server.id] ?? [];
                      return (
                        <li key={server.id}>
                          <div className="mcp-server-row">
                            <span className="mcp-server-icon"><PlugZap aria-hidden="true" size={15} /></span>
                            <div className="mcp-server-copy">
                              <strong>{server.displayName}</strong>
                              <small>{stateCopy(server)}</small>
                            </div>
                            <button className="mcp-icon-button" type="button" aria-label={`查看 ${server.displayName} 的工具`} onClick={() => void toggleTools(server)}>
                              <Wrench aria-hidden="true" size={15} />
                              {expanded ? <ChevronUp aria-hidden="true" size={13} /> : <ChevronDown aria-hidden="true" size={13} />}
                            </button>
                            <button className="mcp-icon-button" type="button" disabled={reviewingTool} aria-label={`编辑 ${server.displayName}`} onClick={() => setEditing(server)}>
                              <Pencil aria-hidden="true" size={15} />
                            </button>
                            <button
                              className="mcp-icon-button mcp-delete"
                              type="button"
                              disabled={reviewingTool}
                              aria-label={`删除 ${server.displayName}`}
                              onClick={(event) => {
                                deleteTriggerRef.current = event.currentTarget;
                                setDeleteTarget({ kind: "server", label: server.displayName, servers: [server] });
                              }}
                            >
                              <Trash2 aria-hidden="true" size={15} />
                            </button>
                          </div>

                          {expanded && (
                            <div className="mcp-server-tools">
                              {toolsLoading === server.id ? (
                                <p className="mcp-loading" role="status">正在读取工具…</p>
                              ) : (
                                <ul aria-label={`${server.displayName} 的工具`}>
                                  {tools.map(renderTool)}
                                </ul>
                              )}
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </>
            )}
          </section>
        </>
      )}

      {deleteTarget && (
        <div className="mcp-dialog-backdrop">
          <div
            ref={deleteDialogRef}
            className="mcp-delete-dialog"
            role="dialog"
            tabIndex={-1}
            aria-modal="true"
            aria-label={`删除 ${deleteLabel}`}
            aria-describedby="mcp-delete-description"
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                if (!deleting) closeDelete();
                return;
              }
              if (event.key !== "Tab") return;
              const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"));
              if (buttons.length === 0) {
                event.preventDefault();
                event.currentTarget.focus();
                return;
              }
              const first = buttons[0];
              const last = buttons.at(-1);
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
              }
            }}
          >
            <h3>{`删除 ${deleteLabel}？`}</h3>
            <p id="mcp-delete-description">
              {deleteTarget.kind === "yuandian"
                ? `元典智库的连接配置会全部删除，并从相关对话中移除。最多影响 ${deleteTarget.servers.reduce((sum, server) => sum + server.selectedThreadCount, 0)} 个对话（同一对话可能使用多项服务）。`
                : `保存的配置会被删除，并从 ${deleteTarget.servers[0]?.selectedThreadCount ?? 0} 个对话中移除。`}
            </p>
            {error && <p className="mcp-error" role="alert">{error}</p>}
            {deleting && <p className="mcp-loading" role="status">正在删除，请稍候…</p>}
            <div className="mcp-editor-actions">
              <button ref={cancelDeleteRef} type="button" className="mcp-secondary-button" disabled={deleting} onClick={closeDelete}>取消</button>
              <button type="button" className="mcp-danger-button" disabled={deleting} onClick={() => void confirmDelete()}>{deleting ? "正在删除…" : "确认删除"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
