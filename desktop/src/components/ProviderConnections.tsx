import {
  CheckCircle2,
  ChevronRight,
  KeyRound,
  ListTree,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { ProviderBrand, providerBrandId } from "./ProviderBrand";
import { ProviderGuide } from "./ProviderGuide";
import { PROVIDER_PRESETS, type ProviderPreset } from "../providers/presets";
import { ProviderModelContexts } from "./ProviderModelContexts";
import { RuntimeApiError } from "../runtime/api";
import {
  formatContextWindowTokens,
  parseContextWindowInput,
} from "../providers/contextWindowInput";
import type {
  ProviderContextWindowHint,
  ProviderAuthMode,
  ProviderDraft,
  ProviderKind,
  ProviderRecord,
} from "../runtime/contracts";
import type { ProviderApi } from "../runtime/providerClient";

function providerErrorCopy(error: unknown, fallback: string): string {
  if (!(error instanceof RuntimeApiError)) {
    return error instanceof Error ? error.message : fallback;
  }

  const guidance: Record<string, string> = {
    provider_auth_failed: "认证失败。请检查 API Key 与模型服务权限后重试。",
    provider_model_not_found: "未找到指定模型。请重新发现并选择可用模型。",
    provider_has_no_models: "服务可连接，但尚无模型。请先在平台开通模型或在本机安装模型后重试。",
    provider_unreachable: "无法连接模型服务。请检查 Base URL、网络或本地服务状态。",
    provider_timeout: "连接模型服务超时。请检查服务状态后重试。",
    provider_rate_limited: "模型服务当前限流或额度不足，请稍后重试。",
    provider_in_use: "该模型正在回复或等待操作确认。请结束当前任务后再删除，历史会话不会阻止删除。",
    provider_not_found: "该连接已不存在，可能已在其他窗口被删除。",
    invalid_provider_url: "Provider 地址无效，请检查 Base URL。",
    insecure_remote_url: "远程地址必须使用 HTTPS；HTTP 仅限本机回环地址。",
    secret_store_unavailable: "本地密钥配置暂时无法读取。请退出并重新打开应用。",
    secret_store_write_failed: "本地密钥配置无法保存。请检查磁盘空间后重试。",
  };

  return guidance[error.code]
    ?? `${error.message}${error.retryable ? " 可以重试。" : " 请检查配置后重试。"}`;
}

interface ProviderConnectionsProps {
  api: ProviderApi;
  runtimeStatusError?: string;
  onProvidersChanged: () => Promise<void>;
  onModelMetadataChanged?: () => void;
  autoDiscoverDelayMs?: number;
}

interface ProviderStatusCallbacks {
  success: () => void;
  failure: () => void;
  settled: () => void;
}

export async function runProviderStatusRefresh(
  refresh: () => Promise<void>,
  lifecycle: { current: boolean },
  callbacks: ProviderStatusCallbacks,
): Promise<void> {
  try {
    await refresh();
    if (lifecycle.current) callbacks.success();
  } catch {
    if (lifecycle.current) callbacks.failure();
  } finally {
    if (lifecycle.current) callbacks.settled();
  }
}

export function ProviderConnections({
  api,
  runtimeStatusError = "",
  onProvidersChanged,
  onModelMetadataChanged = () => undefined,
  autoDiscoverDelayMs = 500,
}: ProviderConnectionsProps) {
  const [providers, setProviders] = useState<ProviderRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const [refreshStatus, setRefreshStatus] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardStart, setWizardStart] = useState<ProviderPreset | "ollama" | "custom" | null>(null);
  const [reloadRevision, setReloadRevision] = useState(0);
  const addConnectionButtonRef = useRef<HTMLButtonElement>(null);
  const wizardTriggerRef = useRef<HTMLButtonElement | null>(null);
  const listControllerRef = useRef<AbortController | null>(null);
  const refreshInFlightRef = useRef(false);
  const wizardWasOpenRef = useRef(false);
  const mountedRef = useRef(true);

  const closeWizard = useCallback(() => setWizardOpen(false), []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (wizardWasOpenRef.current && !wizardOpen) {
      (wizardTriggerRef.current ?? addConnectionButtonRef.current)?.focus();
    }
    wizardWasOpenRef.current = wizardOpen;
  }, [wizardOpen]);

  useEffect(() => {
    if (!runtimeStatusError) setRefreshError("");
  }, [runtimeStatusError]);

  useEffect(() => {
    const controller = new AbortController();
    listControllerRef.current?.abort();
    listControllerRef.current = controller;
    setLoading(true);
    setLoadError("");
    void api.listProviders(controller.signal)
      .then((records) => {
        if (!controller.signal.aborted) setProviders(records);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setLoadError(providerErrorCopy(error, "无法读取模型连接"));
        }
      })
      .finally(() => {
        if (listControllerRef.current === controller) listControllerRef.current = null;
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      if (listControllerRef.current === controller) listControllerRef.current = null;
    };
  }, [api, reloadRevision]);

  const syncGlobalStatus = useCallback(async () => {
    if (refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;
    setRefreshing(true);
    setRefreshStatus("");
    try {
      await runProviderStatusRefresh(onProvidersChanged, mountedRef, {
        success: () => {
          setRefreshError("");
          setRefreshStatus("连接状态已同步");
        },
        failure: () => {
          setRefreshError("全局连接状态尚未同步。请检查 Runtime 后重试。");
        },
        settled: () => setRefreshing(false),
      });
    } finally {
      refreshInFlightRef.current = false;
    }
  }, [onProvidersChanged]);

  function openConnection(start: ProviderPreset | "ollama" | "custom" | null, trigger: HTMLButtonElement) {
    wizardTriggerRef.current = trigger;
    setWizardStart(start);
    setWizardOpen(true);
  }

  function savedFor(preset: ProviderPreset | "ollama") {
    return providers.find(provider => preset === "ollama"
      ? provider.kind === "ollama"
      : provider.base_url.replace(/\/+$/, "") === preset.baseUrl.replace(/\/+$/, ""));
  }

  function openCard(preset: ProviderPreset | "ollama", trigger: HTMLButtonElement) {
    const saved = savedFor(preset);
    if (saved) {
      const card = document.getElementById(`saved-provider-${saved.id}`);
      card?.scrollIntoView({ block: "nearest" });
      card?.focus({ preventScroll: true });
    } else openConnection(preset, trigger);
  }

  return (
    <>
      <section className="model-catalog" aria-labelledby="model-catalog-title">
        <h2 id="model-catalog-title">模型服务商</h2>
        <div className="model-catalog-grid">
          {PROVIDER_PRESETS.map(preset => {
            const saved = savedFor(preset);
            return <button type="button" className="model-catalog-card" key={preset.id}
              aria-label={saved ? `查看 ${preset.name} 的已连接模型` : `添加 ${preset.name} 连接`}
              onClick={event => openCard(preset, event.currentTarget)}>
              <span className="model-service-mark model-brand-tile" aria-hidden="true"><ProviderBrand id={preset.id} /></span>
              <span className="model-catalog-copy"><strong>{preset.name}</strong>
                <small className={saved?.enabled ? "model-catalog-connected" : ""}>
                  {loading ? "读取中…" : loadError ? "状态未知" : saved ? saved.enabled ? <><CheckCircle2 size={13} aria-hidden="true" />已连接</> : "已停用" : "未设置"}
                </small></span>
              <ChevronRight size={16} aria-hidden="true" />
            </button>;
          })}
          <button type="button" className="model-catalog-card" aria-label="配置本机 Ollama"
            onClick={event => openCard("ollama", event.currentTarget)}>
            <span className="model-service-mark model-brand-tile" aria-hidden="true"><ProviderBrand id="ollama" /></span>
            <span className="model-catalog-copy"><strong>Ollama</strong><small>{loading ? "读取中…" : loadError ? "状态未知" : savedFor("ollama") ? "已添加" : "本机模型"}</small></span>
            <ChevronRight size={16} aria-hidden="true" />
          </button>
          <button type="button" className="model-catalog-card" aria-label="添加自定义连接"
            onClick={event => openConnection("custom", event.currentTarget)}>
            <span className="model-service-mark" aria-hidden="true"><Plus size={22} /></span>
            <span className="model-catalog-copy"><strong>自定义连接</strong><small>兼容地址与其他服务</small></span>
            <ChevronRight size={16} aria-hidden="true" />
          </button>
        </div>
      </section>
      <section className="connected-models" aria-labelledby="connected-models-title">
        <div className="connected-models-heading"><h2 id="connected-models-title">已连接的模型</h2>
          <button ref={addConnectionButtonRef} type="button" className="model-add-button"
            onClick={event => openConnection(null, event.currentTarget)}>
            <Plus size={15} aria-hidden="true" />添加模型连接
          </button>
        </div>
      {loading ? <p className="provider-list-status" role="status">正在读取模型连接</p> : null}
      {!loading && !loadError && providers.length === 0 ? (
        <p className="provider-list-status">未连接任何模型提供商</p>
      ) : null}

      {loadError ? (
        <div className="provider-load-error" role="alert">
          <span>{loadError}。当前连接数量未知。</span>
          <button type="button" onClick={() => setReloadRevision((revision) => revision + 1)}>
            重新读取模型连接
          </button>
        </div>
      ) : null}
      {refreshError || runtimeStatusError ? (
        <div className="provider-load-error" role="alert">
          <span>{refreshError || "全局连接状态尚未同步。请检查 Runtime 后重试。"}</span>
          <button
            type="button"
            aria-label="重新同步连接状态"
            disabled={refreshing}
            onClick={() => void syncGlobalStatus()}
          >
            {refreshing ? "正在同步连接状态" : "重新同步连接状态"}
          </button>
        </div>
      ) : null}
      {refreshStatus ? <p className="provider-sync-status" role="status">{refreshStatus}</p> : null}
      {providers.length > 0 ? (
        <div className="provider-list" aria-label="模型连接">
          {providers.map((provider) => (
            <SavedProviderRow
              api={api}
              key={provider.id}
              provider={provider}
              onDeleted={(providerId) => {
                setProviders((current) => current.filter((item) => item.id !== providerId));
                onModelMetadataChanged();
                void syncGlobalStatus();
              }}
              onContextChanged={() => {
                onModelMetadataChanged();
                void syncGlobalStatus();
              }}
            />
          ))}
        </div>
      ) : null}

      </section>

      {wizardOpen ? (
        <ProviderWizard
          initialSelection={wizardStart}
          api={api}
          autoDiscoverDelayMs={autoDiscoverDelayMs}
          onClose={closeWizard}
          onCreated={(provider) => {
            listControllerRef.current?.abort();
            listControllerRef.current = null;
            setLoading(false);
            setLoadError("");
            setProviders((current) => {
              const existing = current.findIndex((item) => item.id === provider.id);
              if (existing < 0) return [...current, provider];
              return current.map((item) => item.id === provider.id ? provider : item);
            });
            setReloadRevision((revision) => revision + 1);
            setRefreshError("");
            setRefreshStatus("");
            onModelMetadataChanged();
            void syncGlobalStatus();
          }}
        />
      ) : null}
    </>
  );
}

interface SavedProviderRowProps {
  api: ProviderApi;
  provider: ProviderRecord;
  onDeleted: (providerId: string) => void;
  onContextChanged: () => void;
}

function SavedProviderRow({ api, provider, onDeleted, onContextChanged }: SavedProviderRowProps) {
  const [phase, setPhase] = useState<"idle" | "testing">("idle");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [modelsOpen, setModelsOpen] = useState(false);
  const [deleteState, setDeleteState] = useState<"idle" | "deleting">("idle");
  const deletingRef = useRef(false);

  async function runTest() {
    if (phase !== "idle") return;
    setPhase("testing");
    setError("");
    setStatus("");
    try {
      const result = await api.testSavedProvider(provider.id);
      setStatus(`连接可用，当前模型 ${result.selected_model}`);
    } catch (testError: unknown) {
      setError(providerErrorCopy(testError, "连接测试失败"));
    } finally {
      setPhase("idle");
    }
  }

  async function deleteConnection() {
    if (phase !== "idle" || deletingRef.current) return;
    deletingRef.current = true;
    setDeleteState("deleting");
    setError("");
    setStatus("");
    try {
      await api.deleteProvider(provider.id);
      onDeleted(provider.id);
    } catch (deleteError: unknown) {
      setError(providerErrorCopy(deleteError, "删除连接失败"));
      deletingRef.current = false;
      setDeleteState("idle");
    }
  }

  return (
    <article className="provider-row" id={`saved-provider-${provider.id}`} tabIndex={-1}>
      <span className={`provider-kind-icon${providerBrandId(provider) ? " model-brand-tile" : ""}`}><ProviderBrand id={providerBrandId(provider)} /></span>
      <div className="provider-row-copy">
        <div>
          <strong>{provider.name}</strong>
          <span className={provider.enabled ? "provider-state-enabled" : "provider-state-disabled"}>
            {provider.enabled ? "已启用" : "已停用"}
          </span>
        </div>
        <small className="connected-model-name">{provider.default_model}</small>
        <small className="connected-model-url" title={provider.base_url}>{provider.base_url}</small>
        {status ? <p className="provider-row-status" role="status">{status}</p> : null}
        {error ? <p className="provider-row-error" role="alert">{error}</p> : null}
        {modelsOpen ? (
          <ProviderModelContexts
            api={api}
            provider={provider}
            onChanged={onContextChanged}
          />
        ) : null}
      </div>
      <div className="provider-row-actions">
        <button
          type="button"
          aria-label={`测试 ${provider.name}`}
          disabled={phase !== "idle" || deleteState !== "idle"}
          onClick={() => void runTest()}
        >
          <RefreshCw aria-hidden="true" size={15} />
          {phase === "testing" ? "测试中" : "测试"}
        </button>
        <button
          type="button"
          aria-label={`发现 ${provider.name} 的模型`}
          disabled={phase !== "idle" || deleteState !== "idle"}
          aria-expanded={modelsOpen}
          onClick={() => setModelsOpen((open) => !open)}
        >
          <ListTree aria-hidden="true" size={15} />
          {modelsOpen ? "收起" : "模型"}
        </button>
        <button
          type="button"
          aria-label={`删除 ${provider.name}`}
          disabled={phase !== "idle" || deleteState === "deleting"}
          onClick={() => void deleteConnection()}
        >
          <Trash2 aria-hidden="true" size={15} />
          {deleteState === "deleting" ? "正在删除" : "删除"}
        </button>
      </div>
    </article>
  );
}

interface ProviderWizardProps {
  initialSelection?: ProviderPreset | "ollama" | "custom" | null;
  api: ProviderApi;
  autoDiscoverDelayMs: number;
  onClose: () => void;
  onCreated: (provider: ProviderRecord) => void;
}

function ProviderWizard({ api, autoDiscoverDelayMs, onClose, onCreated, initialSelection = null }: ProviderWizardProps) {
  const initialPreset = typeof initialSelection === "object" ? initialSelection : null;
  const [mode, setMode] = useState<"guided" | "custom">(typeof initialSelection === "string" ? "custom" : "guided");
  const [preset, setPreset] = useState<ProviderPreset | null>(initialPreset);
  const [guideStep, setGuideStep] = useState<"choose" | "credentials" | "model">(initialPreset ? "credentials" : "choose");
  const [name, setName] = useState(initialPreset?.name ?? (initialSelection === "ollama" ? "Ollama" : ""));
  const [kind, setKind] = useState<ProviderKind>(initialSelection === "ollama" ? "ollama" : "openai_compatible");
  const [baseUrl, setBaseUrl] = useState(initialPreset?.baseUrl ?? (initialSelection === "ollama" ? "http://127.0.0.1:11434" : ""));
  const [defaultModel, setDefaultModel] = useState("");
  const [authMode, setAuthMode] = useState<ProviderAuthMode>(initialSelection === "ollama" ? "none" : "api_key");
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [contextWindows, setContextWindows] = useState<Record<string, ProviderContextWindowHint>>({});
  const [manualContextInput, setManualContextInput] = useState("");
  const [contextWasEdited, setContextWasEdited] = useState(false);
  const [connectionRevision, setConnectionRevision] = useState(0);
  const [testedRevision, setTestedRevision] = useState<number | null>(null);
  const [phase, setPhase] = useState<"idle" | "testing" | "saving">("idle");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const connectionRevisionRef = useRef(0);
  const dialogRef = useRef<HTMLElement>(null);
  const phaseRef = useRef(phase);
  const testControllerRef = useRef<AbortController | null>(null);

  function updatePhase(nextPhase: "idle" | "testing" | "saving") {
    phaseRef.current = nextPhase;
    setPhase(nextPhase);
  }

  function close() {
    if (phaseRef.current === "saving") return;
    testControllerRef.current?.abort();
    testControllerRef.current = null;
    setApiKey("");
    onClose();
  }

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close();
        return;
      }
      if (event.key !== "Tab") return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [href], [tabindex]:not([tabindex="-1"])',
      )).filter(element => !element.closest('details:not([open])') || element.tagName === 'SUMMARY');
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (!dialog.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      }
    };

    const backdrop = dialogRef.current?.parentElement ?? null;
    const background = Array.from(document.body.children)
      .filter((element) => element !== backdrop)
      .map((element) => ({
        element,
        ariaHidden: element.getAttribute("aria-hidden"),
        inert: element.hasAttribute("inert"),
      }));
    for (const item of background) {
      item.element.setAttribute("aria-hidden", "true");
      item.element.setAttribute("inert", "");
    }
    const bodyAlreadyLocked = document.body.classList.contains("provider-dialog-open");
    document.body.classList.add("provider-dialog-open");
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      testControllerRef.current?.abort();
      document.removeEventListener("keydown", handleKeyDown);
      if (!bodyAlreadyLocked) document.body.classList.remove("provider-dialog-open");
      for (const item of background) {
        if (item.ariaHidden === null) item.element.removeAttribute("aria-hidden");
        else item.element.setAttribute("aria-hidden", item.ariaHidden);
        if (!item.inert) item.element.removeAttribute("inert");
      }
    };
  }, [onClose]);

  function currentDraft(includeContextOverride = false): ProviderDraft {
    const draft: ProviderDraft = {
      name: name.trim(),
      kind,
      base_url: baseUrl.trim(),
      default_model: defaultModel.trim(),
      auth_mode: kind === "ollama" ? "none" : authMode,
    };
    if (draft.auth_mode === "api_key") draft.api_key = apiKey;
    if (includeContextOverride && contextWasEdited) {
      const parsed = parseContextWindowInput(manualContextInput);
      if ("tokens" in parsed) {
        draft.default_model_context_window_tokens = parsed.tokens;
      }
    }
    return draft;
  }

  function selectModelContext(
    model: string,
    contexts: Record<string, ProviderContextWindowHint> = contextWindows,
  ) {
    const hint = contexts[model];
    setManualContextInput(
      hint?.tokens == null ? "" : formatContextWindowTokens(hint.tokens),
    );
    setContextWasEdited(false);
  }

  function invalidateTest() {
    const nextRevision = connectionRevisionRef.current + 1;
    connectionRevisionRef.current = nextRevision;
    setConnectionRevision(nextRevision);
    testControllerRef.current?.abort();
    testControllerRef.current = null;
    if (phaseRef.current === "testing") updatePhase("idle");
    setTestedRevision(null);
    setModels([]);
    setContextWindows({});
    setManualContextInput("");
    setContextWasEdited(false);
    setStatus("");
    setError("");
  }

  function changeKind(nextKind: ProviderKind) {
    setKind(nextKind);
    setApiKey("");
    setAuthMode(nextKind === "ollama" ? "none" : "api_key");
    if (nextKind === "ollama" && !baseUrl.trim()) {
      setBaseUrl("http://127.0.0.1:11434");
    }
    invalidateTest();
  }

  function selectPreset(next: ProviderPreset | null) {
    invalidateTest();
    setApiKey("");
    setDefaultModel("");
    setPreset(next);
    setMode(next ? "guided" : "custom");
    setGuideStep(next ? "credentials" : "choose");
    setName(next?.name ?? "");
    setBaseUrl(next?.baseUrl ?? "");
    setKind("openai_compatible");
    setAuthMode("api_key");
  }

  function backToProviders() {
    selectPreset(null);
    setMode("guided");
  }

  const draft = currentDraft();
  const hasConnectionFields = Boolean(
    draft.name
    && draft.base_url
    && (draft.auth_mode === "none" || draft.api_key?.trim()),
  );
  const canTest = hasConnectionFields && (!preset?.modelRequired || Boolean(draft.default_model));
  const selectedContextWindow = contextWindows[defaultModel];
  const parsedContextInput = contextWasEdited
    ? parseContextWindowInput(manualContextInput)
    : null;
  const contextInputError = parsedContextInput && "error" in parsedContextInput
    ? parsedContextInput.error
    : "";
  const contextAboveAutomatic = Boolean(
    contextWasEdited
    && parsedContextInput
    && "tokens" in parsedContextInput
    && selectedContextWindow?.tokens != null
    && parsedContextInput.tokens > selectedContextWindow.tokens,
  );
  const canSave = hasConnectionFields
    && Boolean(draft.default_model)
    && testedRevision === connectionRevision
    && !contextInputError;
  const isDiscovering = phase === "testing" && models.length === 0;

  async function handleTest() {
    if (!canTest || phase !== "idle") return;
    const request = currentDraft();
    const requestRevision = connectionRevisionRef.current;
    const controller = new AbortController();
    testControllerRef.current?.abort();
    testControllerRef.current = controller;
    updatePhase("testing");
    setError("");
    setStatus("");
    try {
      const result = await api.testProviderConnection(request, controller.signal);
      if (controller.signal.aborted || connectionRevisionRef.current !== requestRevision) return;
      if (!result.models.length || !result.selected_model) {
        setTestedRevision(null);
        setModels([]);
        setError("未读取到模型。请在服务商平台开通文本模型，再填写模型标识重试。");
        return;
      }
      setDefaultModel(result.selected_model);
      setModels(result.models);
      const discoveredContexts = Object.fromEntries(
        (result.context_windows ?? result.models.map((model) => ({
          model,
          tokens: null,
          source: "unknown" as const,
        }))).map((hint) => [hint.model, hint]),
      );
      setContextWindows(discoveredContexts);
      selectModelContext(result.selected_model, discoveredContexts);
      setTestedRevision(requestRevision);
      setStatus(`连接测试通过，发现 ${result.models.length} 个模型`);
      if (preset) setGuideStep("model");
    } catch (testError: unknown) {
      if (controller.signal.aborted) return;
      setTestedRevision(null);
      setModels([]);
      setError(preset
        ? testError instanceof RuntimeApiError && testError.code === "provider_model_not_found"
          ? "平台未提供模型列表或未找到该模型。请从官网复制已开通的模型标识，填写后重新验证。"
          : testError instanceof RuntimeApiError ? providerErrorCopy(testError, "连接测试失败") : "连接失败，请检查密钥、模型权限和网络后重试。"
        : providerErrorCopy(testError, "连接测试失败"));
    } finally {
      if (testControllerRef.current === controller) {
        testControllerRef.current = null;
        if (!controller.signal.aborted) updatePhase("idle");
      }
    }
  }

  const handleTestRef = useRef(handleTest);
  handleTestRef.current = handleTest;

  useEffect(() => {
    if (mode !== "custom" || !hasConnectionFields || testedRevision !== null) return;
    const timer = setTimeout(() => {
      void handleTestRef.current();
    }, autoDiscoverDelayMs);
    return () => clearTimeout(timer);
  }, [autoDiscoverDelayMs, hasConnectionFields, testedRevision, connectionRevision, mode]);

  async function handleSave() {
    if (!canSave || phase !== "idle") return;
    const request = currentDraft(true);
    let saved = false;
    updatePhase("saving");
    setError("");
    try {
      const provider = await api.createProvider(request);
      setApiKey("");
      onCreated(provider);
      saved = true;
      onClose();
    } catch (saveError: unknown) {
      setError(preset && !(saveError instanceof RuntimeApiError)
        ? "连接未保存，请稍后重试。"
        : providerErrorCopy(saveError, "模型连接保存失败"));
    } finally {
      if (!saved) updatePhase("idle");
    }
  }

  return createPortal(
    <div className="provider-dialog-backdrop" onMouseDown={(event) => {
      if (event.currentTarget === event.target) close();
    }}>
      <section
        className={`provider-dialog${mode === "guided" ? " provider-dialog-guided" : ""}`}
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="provider-dialog-title"
      >
        <header>
          <div>
            <span><KeyRound aria-hidden="true" size={19} /></span>
            <div>
              <h2 id="provider-dialog-title">添加模型连接</h2>
              <p>{mode === "custom" ? "填写连接信息，测试后保存到本机。" : "三步连接，让 VeriLex 使用你的模型。"}</p>
            </div>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="关闭连接向导"
            disabled={phase === "saving"}
            onClick={close}
          >
            <X aria-hidden="true" size={18} />
          </button>
        </header>

        {mode === "guided" ? <ProviderGuide
          key={`${preset?.id ?? "gallery"}-${guideStep}`}
          preset={preset} step={guideStep} phase={phase} apiKey={apiKey} name={name}
          model={defaultModel} models={models} status={status} error={error}
          canTest={canTest} canSave={canSave}
          onSelect={selectPreset} onClose={close}
          onBack={() => {
            if (guideStep === "model") {
              invalidateTest();
              setGuideStep("credentials");
            } else backToProviders();
          }}
          onKeyChange={value => { setApiKey(value); invalidateTest(); }}
          onNameChange={setName}
          onModelChange={value => {
            setDefaultModel(value);
            if (guideStep === "model") selectModelContext(value); else invalidateTest();
          }}
          onTest={() => void handleTest()} onSave={() => void handleSave()}
        /> : <form
          className="provider-dialog-body"
          onSubmit={(event) => {
            event.preventDefault();
            void handleSave();
          }}
        >
          <button type="button" className="secondary-button" disabled={phase === "saving"} onClick={backToProviders}>返回服务商选择</button>
          <div className="provider-security-boundary">
            <ShieldCheck aria-hidden="true" size={20} />
            <div>
              <strong>仅保存在本机</strong>
              <p>API Key 只写入 VeriLex 的本地私有配置；界面不会在保存后读回或展示密钥。</p>
            </div>
            <CheckCircle2 aria-hidden="true" size={17} />
          </div>
          <fieldset className="provider-form-grid" disabled={phase === "saving"}>
            <label className="provider-field provider-field-wide">
              <span>连接名称</span>
              <input
                aria-label="连接名称"
                autoFocus
                name="provider_name"
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：律所模型网关"
              />
            </label>
            <label className="provider-field">
              <span>提供商类型</span>
              <select
                aria-label="提供商类型"
                name="provider_kind"
                value={kind}
                onChange={(event) => changeKind(event.target.value as ProviderKind)}
              >
                <option value="openai_compatible">OpenAI 兼容</option>
                <option value="ollama">Ollama</option>
              </select>
            </label>
            <label className="provider-field">
              <span>认证方式</span>
              <select
                aria-label="认证方式"
                disabled={kind === "ollama"}
                name="provider_auth_mode"
                value={kind === "ollama" ? "none" : authMode}
                onChange={(event) => {
                  const nextMode = event.target.value as ProviderAuthMode;
                  setAuthMode(nextMode);
                  if (nextMode === "none") setApiKey("");
                  invalidateTest();
                }}
              >
                <option value="api_key">API Key</option>
                <option value="none">无需密钥</option>
              </select>
            </label>
            <label className="provider-field provider-field-wide">
              <span>Base URL</span>
              <input
                aria-label="Base URL"
                aria-describedby="provider-base-url-help"
                autoComplete="url"
                inputMode="url"
                name="provider_base_url"
                required
                type="url"
                value={baseUrl}
                onChange={(event) => {
                  setBaseUrl(event.target.value);
                  invalidateTest();
                }}
                placeholder={kind === "ollama" ? "http://127.0.0.1:11434" : "https://…/v1"}
              />
              <small id="provider-base-url-help">远程地址必须使用 HTTPS；仅回环地址可使用 HTTP。</small>
            </label>
            <label className="provider-field provider-field-wide">
              <span>默认模型</span>
              {models.length > 0 ? (
                <select
                  aria-label="默认模型"
                  aria-describedby="provider-default-model-help"
                  name="provider_default_model"
                  required
                  value={defaultModel}
                  onChange={(event) => {
                    const nextModel = event.target.value;
                    setDefaultModel(nextModel);
                    selectModelContext(nextModel);
                  }}
                >
                  {models.map((model) => <option key={model}>{model}</option>)}
                </select>
              ) : (
                <input
                  aria-label="默认模型"
                  aria-describedby="provider-default-model-help"
                  name="provider_default_model"
                  required
                  value={defaultModel}
                  onChange={(event) => {
                    setDefaultModel(event.target.value);
                    invalidateTest();
                  }}
                  placeholder={
                    isDiscovering
                      ? "正在发现可用模型…"
                      : hasConnectionFields
                        ? "将自动发现可用模型，也可手动输入"
                        : "填写 Base URL 后自动发现模型"
                  }
                  disabled={isDiscovering}
                />
              )}
              <small id="provider-default-model-help">填写 Base URL 后将自动发现可用模型；也可手动输入模型标识后测试。</small>
            </label>
            <label className="provider-field provider-field-wide">
              <span>上下文上限</span>
              <input
                aria-label="上下文上限"
                aria-describedby="provider-context-window-help provider-context-window-validation"
                inputMode="text"
                name="provider_context_window"
                value={manualContextInput}
                disabled={testedRevision !== connectionRevision || !defaultModel}
                onChange={(event) => {
                  setManualContextInput(event.target.value);
                  setContextWasEdited(true);
                }}
                placeholder="例如 128K、1M 或 32768"
              />
              <small id="provider-context-window-help">
                {selectedContextWindow?.tokens != null
                  ? `${formatContextWindowTokens(selectedContextWindow.tokens)} · ${
                    selectedContextWindow.source === "provider"
                      ? "Provider 自动获取"
                      : selectedContextWindow.source === "curated"
                        ? "VeriLex 内置资料"
                        : "自动资料"
                  }`
                  : testedRevision === connectionRevision
                    ? "未知，可手动设置"
                    : "连接测试后自动获取，也可逐模型手动设置"}
              </small>
              <small
                id="provider-context-window-validation"
                className={contextInputError ? "provider-field-error" : contextAboveAutomatic ? "provider-field-warning" : ""}
              >
                {contextInputError
                  || (contextAboveAutomatic
                    ? "手动上限高于 Provider 自动值，请确认模型确实支持。"
                    : contextWasEdited ? "将只覆盖当前选择的模型。" : "")}
              </small>
            </label>
            {kind !== "ollama" && authMode === "api_key" ? (
              <label className="provider-field provider-field-wide">
                <span>API Key</span>
                <input
                  aria-label="API Key"
                  autoComplete="new-password"
                  name="provider_api_key"
                  required
                  spellCheck={false}
                  type="password"
                  value={apiKey}
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    invalidateTest();
                  }}
                  placeholder="仅发送到本机 Runtime"
                />
              </label>
            ) : null}
          </fieldset>

          {status ? <p className="provider-form-status" role="status">{status}</p> : null}
          {error ? <p className="provider-form-error" role="alert">{error}</p> : null}

          <footer className="provider-dialog-actions">
            <button
              className="secondary-button"
              type="button"
              disabled={phase === "saving"}
              onClick={close}
            >
              取消
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={!canTest || phase !== "idle"}
              onClick={() => void handleTest()}
            >
              {phase === "testing"
                ? (kind === "ollama" ? "正在发现…" : "正在测试…")
                : (kind === "ollama" ? "发现并测试 Ollama" : "测试并发现模型")}
            </button>
            <button
              className="primary-button"
              type="submit"
              disabled={!canSave || phase !== "idle"}
            >
              {phase === "saving" ? "正在保存…" : "保存连接"}
            </button>
          </footer>
        </form>}
      </section>
    </div>,
    document.body,
  );
}
