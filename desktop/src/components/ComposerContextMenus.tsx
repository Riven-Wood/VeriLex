import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, FolderPlus, X } from "lucide-react";

import type { ComposerContext } from "../composer/state";
import { pickWorkspaceDirectory } from "../platform/directoryPicker";
import {
  createWorkspace,
  discoverProviderModels,
  listModelCapabilities,
  listWorkspaces,
  RuntimeApiError,
} from "../runtime/api";
import type {
  ModelCapabilityRecord,
  ProviderProbeResult,
  ProviderRecord,
  WorkspaceCreateRequest,
  WorkspaceRecord,
} from "../runtime/contracts";

interface ContextMenuApi {
  discoverProviderModels?: (providerId: string) => Promise<ProviderProbeResult>;
  listModelCapabilities?: (providerId: string) => Promise<ModelCapabilityRecord[]>;
  listWorkspaces?: () => Promise<WorkspaceRecord[]>;
  createWorkspace?: (request: WorkspaceCreateRequest) => Promise<WorkspaceRecord>;
}

interface ComposerContextMenusProps {
  kind: "model" | "workspace";
  providers: readonly ProviderRecord[];
  context: ComposerContext;
  onSelectModel: (
    providerId: string,
    model: string,
    supportsVision: boolean | undefined,
  ) => void;
  onSelectWorkspace: (workspace: WorkspaceRecord | null) => void;
  onClose: () => void;
  onError?: (message: string) => void;
  api?: ContextMenuApi;
  pickDirectory?: () => Promise<string | null>;
}

const DEFAULT_API: Required<ContextMenuApi> = {
  discoverProviderModels,
  listModelCapabilities,
  listWorkspaces,
  createWorkspace,
};

export function directoryName(path: string): string {
  const normalized = path.replace(/[\\/]+$/, "");
  return normalized.split(/[\\/]/).pop() || "工作区";
}

function contextErrorCopy(error: unknown, fallback: string): string {
  if (!(error instanceof RuntimeApiError)) {
    return error instanceof Error && error.message.startsWith("无法")
      ? error.message
      : fallback;
  }
  const guidance: Record<string, string> = {
    workspace_root_path_exists: "这个文件夹已经是工作区，已尝试刷新列表。",
    workspace_not_found: "这个工作区已不存在，请重新选择。",
    provider_not_found: "这个模型连接已不存在，请重新选择。",
    provider_model_invalid: "这个模型当前不可用，请重新选择。",
    thread_turn_active: "当前任务仍在运行，请结束后再切换。",
  };
  return guidance[error.code] ?? `${fallback}${error.retryable ? "，请稍后重试。" : "。"}`;
}

export function ComposerContextMenus({
  kind,
  providers,
  context,
  onSelectModel,
  onSelectWorkspace,
  onClose,
  onError,
  api: apiOverrides,
  pickDirectory = pickWorkspaceDirectory,
}: ComposerContextMenusProps) {
  const api = useMemo(
    () => ({ ...DEFAULT_API, ...apiOverrides }),
    [apiOverrides],
  );
  const rootRef = useRef<HTMLDivElement>(null);

  // Click anywhere outside the popover (blank space included) or Escape
  // dismisses it, matching the composer's other popovers.
  useEffect(() => {
    function handlePointerDown(event: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        onClose();
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);
  const [providerId, setProviderId] = useState(() =>
    providers.some((provider) => provider.id === context.providerId)
      ? context.providerId : providers[0]?.id ?? "",
  );
  const [selectedModel, setSelectedModel] = useState(context.model);
  const [models, setModels] = useState<string[]>([]);
  const [capabilities, setCapabilities] = useState<ModelCapabilityRecord[]>([]);
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const modelRequestGenerationRef = useRef(0);

  const loadModels = useCallback(async (nextProviderId: string) => {
    const provider = providers.find((item) => item.id === nextProviderId);
    if (!provider) return;
    const generation = modelRequestGenerationRef.current + 1;
    modelRequestGenerationRef.current = generation;
    setLoading(true);
    setError("");
    setModels([]);
    setCapabilities([]);
    try {
      const [probe, records] = await Promise.all([
        api.discoverProviderModels(nextProviderId),
        api.listModelCapabilities(nextProviderId),
      ]);
      if (modelRequestGenerationRef.current !== generation) return;
      const preferredModel = nextProviderId === context.providerId
        ? context.model
        : provider.default_model;
      const discovered = Array.isArray(probe?.models) ? probe.models : [];
      setModels([...new Set([preferredModel, ...discovered])].filter(Boolean));
      setCapabilities(Array.isArray(records) ? records : []);
    } catch (loadError) {
      if (modelRequestGenerationRef.current !== generation) return;
      setModels([provider.default_model]);
      setCapabilities([]);
      setError("无法刷新模型列表，已显示默认模型。");
      onError?.(contextErrorCopy(loadError, "无法刷新模型列表"));
    } finally {
      if (modelRequestGenerationRef.current === generation) setLoading(false);
    }
  }, [api, context.model, context.providerId, onError, providers]);

  useEffect(() => {
    let cancelled = false;
    if (kind === "model") {
      void loadModels(providerId);
      return () => undefined;
    }
    setLoading(true);
    api.listWorkspaces()
      .then((records) => {
        if (!cancelled) setWorkspaces(Array.isArray(records) ? records : []);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          const message = loadError instanceof Error
            ? loadError.message
            : "无法加载工作区";
          setError(message);
          onError?.(message);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api, kind, loadModels, onError, providerId]);

  async function chooseNewDirectory() {
    try {
      const path = await pickDirectory();
      if (!path) return;
      const existing = workspaces.find((workspace) => workspace.root_path === path);
      let workspace = existing;
      if (!workspace) {
        try {
          workspace = await api.createWorkspace({
            root_path: path,
            display_name: directoryName(path),
            allow_read: true,
            allow_write: true,
          });
        } catch (createError) {
          if (
            createError instanceof RuntimeApiError
            && createError.code === "workspace_root_path_exists"
          ) {
            const refreshed = await api.listWorkspaces();
            const safeRecords = Array.isArray(refreshed) ? refreshed : [];
            setWorkspaces(safeRecords);
            workspace = safeRecords.find((item) => item.root_path === path);
            if (workspace) {
              onSelectWorkspace(workspace);
              onClose();
              return;
            }
          }
          throw createError;
        }
      }
      if (!workspace) return;
      if (!existing) setWorkspaces((current) => [workspace, ...current]);
      onSelectWorkspace(workspace);
      onClose();
    } catch (workspaceError) {
      const message = contextErrorCopy(workspaceError, "无法添加工作区");
      setError(message);
      onError?.(message);
    }
  }

  return (
    <div
      className="composer-context-menu"
      ref={rootRef}
      role="dialog"
      aria-label={kind === "model" ? "选择模型" : "选择工作区"}
    >
      <div className="composer-context-heading">
        <strong>{kind === "model" ? "模型" : "工作区"}</strong>
        <button type="button" aria-label="关闭" onClick={onClose}>
          <X aria-hidden="true" size={15} />
        </button>
      </div>
      {kind === "model" ? (
        <>
          <label className="composer-provider-select">
            <span>提供方</span>
            <select
              aria-label="模型提供方"
              value={providerId}
              onChange={(event) => {
                const next = event.target.value;
                const provider = providers.find((item) => item.id === next);
                setProviderId(next);
                setSelectedModel(provider?.default_model ?? "");
              }}
            >
              {providers.map((provider) => (
                <option key={provider.id} value={provider.id}>{provider.name}</option>
              ))}
            </select>
          </label>
          <div className="composer-context-options">
            {models.map((model) => (
              <button
                type="button"
                key={model}
                aria-label={model}
                onClick={() => {
                  setSelectedModel(model);
                  const found = capabilities.find((item) => item.model === model);
                  onSelectModel(providerId, model, found?.supports_vision);
                }}
              >
                <span>{model}</span>
                {model === selectedModel && <Check aria-hidden="true" size={14} />}
              </button>
            ))}
          </div>
        </>
      ) : (
        <div className="composer-context-options">
          <button type="button" onClick={() => { onSelectWorkspace(null); onClose(); }}>
            <span>不使用工作区</span>
            {context.workspaceId === null && <Check aria-hidden="true" size={14} />}
          </button>
          {workspaces.map((workspace) => (
            <button
              type="button"
              key={workspace.id}
              onClick={() => { onSelectWorkspace(workspace); onClose(); }}
            >
              <span>{workspace.display_name}</span>
              {workspace.id === context.workspaceId && <Check aria-hidden="true" size={14} />}
            </button>
          ))}
          <button type="button" aria-label="选择新文件夹" onClick={() => void chooseNewDirectory()}>
            <FolderPlus aria-hidden="true" size={14} />
            <span>选择新文件夹</span>
          </button>
        </div>
      )}
      {loading && <p className="composer-context-note">正在加载…</p>}
      {error && <p className="composer-context-error" role="alert">{error}</p>}
    </div>
  );
}
