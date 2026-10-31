import { useEffect, useRef, useState } from "react";
import { Eye, EyeOff, LoaderCircle } from "lucide-react";
import { mcpErrorCopy } from "../mcp/errorCopy";
import { RuntimeApiError } from "../runtime/api";

import {
  YUANDIAN_SERVICES,
  yuandianCreateInput,
  yuandianProbe,
  yuandianServiceFromServer,
} from "../mcp/yuandian";
import { openYuandianConsole } from "../platform/yuandianConsole";
import {
  listMcpServerTools,
  refreshMcpServerTools,
  reviewMcpServerTool,
  testMcpServerDraft,
  testSavedMcpServer,
  updateMcpServer,
} from "../runtime/api";
import type {
  CreateMcpServerInput,
  McpProbeDraftRequest,
  McpSavedProbeRequest,
  McpServerRecord,
  McpTestResult,
  McpToolRecord,
  McpToolReviewRequest,
  UpdateMcpServerInput,
} from "../runtime/contracts";

interface YuandianSetupProps {
  placement?: "thread" | "settings";
  inDialog?: boolean;
  onBusyChange?: (busy: boolean) => void;
  existingServers?: readonly McpServerRecord[];
  onSelectServer?: (serverId: string) => Promise<void> | void;
  onRefresh?: () => Promise<void> | void;
  onAddServer: (input: CreateMcpServerInput) => Promise<void> | void;
  onCancel: () => void;
  onComplete: () => void;
  openConsole?: () => Promise<void>;
  probe?: (input: McpProbeDraftRequest, signal?: AbortSignal) => Promise<McpTestResult>;
  probeSaved?: (
    serverId: string,
    input: McpSavedProbeRequest,
    signal?: AbortSignal,
  ) => Promise<McpTestResult>;
  updateSaved?: (
    serverId: string,
    input: UpdateMcpServerInput,
    signal?: AbortSignal,
  ) => Promise<McpServerRecord>;
  listSavedTools?: (serverId: string, signal?: AbortSignal) => Promise<McpToolRecord[]>;
  refreshSavedTools?: typeof refreshMcpServerTools;
  reviewSavedTool?: (
    serverId: string,
    toolName: string,
    input: McpToolReviewRequest,
    signal?: AbortSignal,
  ) => Promise<McpToolRecord>;
}

export function YuandianSetup({
  placement = "thread",
  inDialog = false,
  onBusyChange,
  existingServers = [],
  onSelectServer = async () => undefined,
  onRefresh = async () => undefined,
  onAddServer,
  onCancel,
  onComplete,
  openConsole = openYuandianConsole,
  probe = testMcpServerDraft,
  probeSaved = testSavedMcpServer,
  updateSaved = updateMcpServer,
  listSavedTools = listMcpServerTools,
  refreshSavedTools = refreshMcpServerTools,
  reviewSavedTool = reviewMcpServerTool,
}: YuandianSetupProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const savedRevisions = useRef(new Map<string, number>());
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [consoleError, setConsoleError] = useState("");
  const [pendingTools, setPendingTools] = useState<McpToolRecord[]>([]);
  const [disabledUpdates, setDisabledUpdates] = useState<Set<string>>(new Set());
  const toolKey = (tool: McpToolRecord) => `${tool.serverId}:${tool.toolName}`;
  const settingsPlacement = placement === "settings";

  useEffect(() => {
    inputRef.current?.focus();
    return () => controllerRef.current?.abort();
  }, []);

  const showUpdates = (tools: McpToolRecord[]) => {
    const pending = tools.filter(tool => tool.reviewState !== "known");
    setPendingTools(pending);
    setDisabledUpdates(new Set());
    return pending.length > 0;
  };

  const syncTools = async (confirm = false) => {
    if (controllerRef.current) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    onBusyChange?.(true);
    setError("");
    try {
      if (confirm) {
        for (const tool of pendingTools) {
          if (controller.signal.aborted) return;
          await reviewSavedTool(tool.serverId, tool.toolName, {
            enabled: !disabledUpdates.has(toolKey(tool)),
            expectedDescriptionHash: tool.descriptionHash,
            expectedSchemaHash: tool.schemaHash,
          }, controller.signal);
        }
      }
      // Re-fetch after confirmation too: a provider can change the catalog
      // while this dialog is open. Never report success for a stale snapshot.
      const tools = (await Promise.all(existingServers.map(server =>
        refreshSavedTools(server.id, Math.max(savedRevisions.current.get(server.id) ?? 0, server.configRevision), controller.signal),
      ))).flat();
      if (controller.signal.aborted) return;
      await onRefresh();
      if (!showUpdates(tools)) onComplete();
    } catch (error) {
      if (!controller.signal.aborted) {
        setPendingTools([]);
        setError(error instanceof RuntimeApiError ? mcpErrorCopy(error.code) : "同步没有完成，请重新同步工具。已保存的密钥不会被删除。");
      }
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (!controller.signal.aborted) { setBusy(false); onBusyChange?.(false); }
    }
  };

  const connect = async () => {
    if (controllerRef.current) return;
    const key = apiKey.trim();
    if (!key) {
      setError("请输入 API Key。");
      inputRef.current?.focus();
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    onBusyChange?.(true);
    setError("");
    try {
      // Test every endpoint before saving any of them. This avoids leaving a
      // half-configured preset when the key itself is invalid.
      const prepared = await Promise.all(YUANDIAN_SERVICES.map(async (service) => {
        const existing = existingServers.find((server) =>
          yuandianServiceFromServer(server) === service.id);
        const request = yuandianProbe(service, key);
        if (!existing) {
          const result = await probe(request, controller.signal);
          return { existing: null, input: yuandianCreateInput(request, result) };
        }
        const savedProbe: McpSavedProbeRequest = {
          expectedRevision: Math.max(savedRevisions.current.get(existing.id) ?? 0, existing.configRevision),
          patch: {},
          secretPatch: request.secretPatch,
          acceptedRiskCodes: [...existing.riskFindings],
        };
        const [result, previousTools] = await Promise.all([
          probeSaved(existing.id, savedProbe, controller.signal),
          listSavedTools(existing.id, controller.signal),
        ]);
        return {
          existing,
          input: yuandianCreateInput(request, result),
          savedProbe,
          previousTools,
        };
      }));
      for (const item of prepared) {
        if (controller.signal.aborted) return;
        if (item.existing && item.savedProbe) {
          const saved = await updateSaved(item.existing.id, {
            ...item.savedProbe,
            testReceipt: item.input.testReceipt,
            toolDecisions: item.input.toolDecisions,
          }, controller.signal);
          savedRevisions.current.set(saved.id, saved.configRevision);
          const priorByName = new Map(
            (item.previousTools ?? []).map((tool) => [tool.toolName, tool]),
          );
          for (const tool of item.input.toolDecisions) {
            const prior = priorByName.get(tool.toolName);
            if (!prior
              || prior.reviewState !== "known"
              || prior.descriptionHash !== tool.expectedDescriptionHash
              || prior.schemaHash !== tool.expectedSchemaHash) continue;
            await reviewSavedTool(item.existing.id, tool.toolName, {
              enabled: prior.enabled,
              expectedDescriptionHash: tool.expectedDescriptionHash,
              expectedSchemaHash: tool.expectedSchemaHash,
            }, controller.signal);
          }
          await onSelectServer(item.existing.id);
        } else {
          await onAddServer(item.input);
        }
      }
      setApiKey("");
      await onRefresh();
      const tools = (await Promise.all(existingServers.map(server =>
        listSavedTools(server.id, controller.signal),
      ))).flat();
      if (!controller.signal.aborted && !showUpdates(tools)) onComplete();
    } catch (error) {
      if (!controller.signal.aborted) {
        setError(error instanceof RuntimeApiError ? mcpErrorCopy(error.code) : "连接或保存没有成功，请稍后重试。");
      }
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (!controller.signal.aborted) { setBusy(false); onBusyChange?.(false); }
    }
  };

  return (
    <section className={`yuandian-setup${settingsPlacement ? " yuandian-setup-settings" : ""}${pendingTools.length ? " yuandian-setup-review" : ""}`} aria-labelledby={inDialog ? undefined : "yuandian-setup-title"} aria-label={inDialog ? "元典密钥设置" : undefined}>
      {!inDialog && <div className="yuandian-setup-heading">
        <div>
          <h3 id="yuandian-setup-title">元典智库</h3>
          <p>{settingsPlacement
            ? "输入一次 API Key，即可添加元典的法律检索能力。"
            : "登录元典取得 API Key，粘贴到这里即可。"}</p>
        </div>
        <span className="yuandian-built-in">{settingsPlacement ? "推荐" : "内置"}</span>
      </div>}

      {pendingTools.length > 0 ? <>
        <p role="status">元典有 {pendingTools.length} 项工具更新，请确认本次启用的能力。</p>
        <div className="yuandian-tool-updates">
          {pendingTools.map(tool => <label key={toolKey(tool)}>
            <input type="checkbox" checked={!disabledUpdates.has(toolKey(tool))} disabled={busy}
              onChange={event => setDisabledUpdates(prior => {
                const next = new Set(prior);
                if (event.target.checked) next.delete(toolKey(tool)); else next.add(toolKey(tool));
                return next;
              })} />
            <span><strong>{tool.toolName}</strong><small>{tool.description}</small></span>
          </label>)}
        </div>
        {error && <p className="yuandian-error" role="alert">{error}</p>}
        <div className="mcp-editor-actions">
          <button type="button" className="mcp-secondary-button" disabled={busy} onClick={onCancel}>稍后处理</button>
          <button type="button" className="mcp-add" disabled={busy} onClick={() => void syncTools(true)}>
            {busy ? "正在同步…" : "确认工具更新"}
          </button>
        </div>
      </> : <>

      <label className="yuandian-key-field">
        <span>API Key</span>
        <span className="yuandian-key-input">
          <input
            ref={inputRef}
            type={showKey ? "text" : "password"}
            aria-label="元典 API Key"
            autoComplete="off"
            spellCheck={false}
            maxLength={4096}
            placeholder={settingsPlacement ? "在元典开放平台获取 API Key" : undefined}
            value={apiKey}
            disabled={busy}
            onChange={(event) => {
              setApiKey(event.target.value);
              setError("");
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void connect();
              }
            }}
          />
          <button
            type="button"
            aria-label={showKey ? "隐藏 API Key" : "显示 API Key"}
            disabled={busy}
            onClick={() => setShowKey((value) => !value)}
          >
            {showKey ? <EyeOff aria-hidden="true" size={15} /> : <Eye aria-hidden="true" size={15} />}
          </button>
        </span>
      </label>

      <p className="yuandian-disclosure">{settingsPlacement
        ? "API Key 只保存在 VeriLex 的本地私有配置中；使用时，检索内容会发送给元典。"
        : "连接后，当前对话可以把检索内容发送给元典。"}</p>

      <button
        type="button"
        className="mcp-secondary-button yuandian-console-button"
        disabled={busy}
        onClick={() => {
          setConsoleError("");
          void openConsole().catch(() => setConsoleError("元典窗口没有打开，请稍后重试。"));
        }}
      >
        登录元典并获取 API Key
      </button>
      {consoleError && <p className="yuandian-error" role="alert">{consoleError}</p>}
      {error && <p className="yuandian-error" role="alert">{error}</p>}

      <div className="mcp-editor-actions">
        {existingServers.length > 0 && <button type="button" className="mcp-secondary-button" disabled={busy}
          onClick={() => void syncTools()}>{busy ? "正在处理…" : "同步工具"}</button>}
        <button type="button" className="mcp-secondary-button" disabled={busy} onClick={onCancel}>
          {settingsPlacement ? "取消" : "返回"}
        </button>
        <button type="button" className="mcp-add" disabled={busy} onClick={() => void connect()}>
          {busy && <LoaderCircle className="attachment-spinner" aria-hidden="true" size={15} />}
          {busy
            ? settingsPlacement ? "正在验证" : "正在连接"
            : settingsPlacement
              ? existingServers.length > 0 ? "确认更新" : "确认添加"
              : "连接到当前对话"}
        </button>
      </div>
      </>}
    </section>
  );
}
