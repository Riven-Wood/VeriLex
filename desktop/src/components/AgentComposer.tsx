import { useUiPreferences, isSendKey } from "../settings/preferences";
import { KnowledgePicker } from "./KnowledgePicker";
import { LitigationSkillPicker } from "./LitigationSkillPicker";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ApprovalMode } from "../hooks/useAgentChat";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import {
  ChevronDown,
  Database,
  FolderOpen,
  ListTree,
  Paperclip,
  Plug,
  Plus,
  Send,
  ShieldCheck,
  Square,
  Wrench,
} from "lucide-react";
import type { ContextUsageRecord, ProviderKind, ReasoningEffort } from "../runtime/contracts";

import {
  COMPOSER_FILE_ACCEPT,
  MAX_COMPOSER_BYTES,
  MAX_COMPOSER_FILES,
  composerMediaTypeForName,
  type ComposerAttachment,
} from "../hooks/useComposerAttachments";
import { ComposerAttachmentChips } from "./ComposerAttachmentChips";
import { ContextUsageControl } from "./ContextUsageControl";
import { ReasoningEffortControl } from "./ReasoningEffortControl";

function nativeDroppedFileName(path: string): string {
  return path.split(/[\\/]/).pop() || "附件";
}

function nativeDropReadError(error: unknown): string {
  const code = error instanceof Error ? error.message : String(error);
  return code === "dropped_file_too_large"
    ? "附件总大小不能超过 30 MiB。"
    : "无法读取拖入的文件。";
}

interface AgentComposerProps {
  litigationSkillScope?: string;
  knowledgeSelection?: { selected: string[]; onChange: (ids: string[]) => void; disabled: boolean };
  hasModel: boolean;
  value: string;
  isRunning: boolean;
  modelLabel?: string;
  workspaceLabel?: string;
  /** Model popover rendered inside the actions row so it anchors right
   * above the model button instead of the composer's top-left corner. */
  modelMenuSlot?: ReactNode;
  contextUsage?: ContextUsageRecord | null;
  contextUsageLoading?: boolean;
  reasoningEffort?: ReasoningEffort;
  providerKind?: ProviderKind;
  supportedReasoningEfforts?: Exclude<ReasoningEffort, "auto">[] | null;
  attachments?: readonly ComposerAttachment[];
  hasReadyAttachments?: boolean;
  attachmentError?: string;
  sendBlockedReason?: string;
  submissionPending?: boolean;
  actionScopeKey?: string | null;
  focusRequestToken?: number;
  pluginFocusRequestToken?: number;
  filePickerRequestToken?: number;
  onAddFiles?: (files: File[]) => void;
  attachmentsEnabled?: boolean;
  onAttachmentsUnavailable?: () => void;
  onRetryAttachment?: (key: string) => void;
  onRemoveAttachment?: (key: string) => void;
  onSelectModel?: () => void;
  onSelectWorkspace?: () => void;
  onReasoningEffortChange?: (effort: ReasoningEffort) => void;
  onDismissContextMenus?: () => void;
  approvalMode?: ApprovalMode;
  onApprovalModeChange?: (mode: ApprovalMode) => void;
  webAccess?: {
    enabled: boolean;
    granted: boolean;
    loading: boolean;
    updating: boolean;
    error: string | null;
  };
  onWebAccessChange?: (enabled: boolean) => void;
  queuedLabel?: string;
  queueEditing?: boolean;
  onQueue?: () => void;
  onInterruptAndSend?: () => void;
  onEditQueue?: () => void;
  onCancelQueue?: () => void;
  onChange: (value: string) => void;
  onOpenPlugins?: () => void;
  onOpenSettings: () => void;
  onSend: () => void;
  onStop: () => void;
}

const APPROVAL_MODES: { value: ApprovalMode; label: string }[] = [
  { value: "ask", label: "请求审批" },
  { value: "help", label: "帮我审批" },
  { value: "full", label: "完全权限" },
];

const CLOSED_WEB_ACCESS = {
  enabled: false,
  granted: false,
  loading: false,
  updating: false,
  error: null,
};

export function AgentComposer({
  litigationSkillScope,
  knowledgeSelection,
  hasModel,
  value,
  isRunning,
  modelLabel,
  workspaceLabel = "选择工作区",
  attachments = [],
  hasReadyAttachments = false,
  attachmentError = "",
  sendBlockedReason = "",
  submissionPending = false,
  actionScopeKey,
  focusRequestToken = 0,
  pluginFocusRequestToken = 0,
  filePickerRequestToken = 0,
  onAddFiles,
  attachmentsEnabled = Boolean(onAddFiles),
  onAttachmentsUnavailable,
  onRetryAttachment = () => undefined,
  onRemoveAttachment = () => undefined,
  onSelectModel,
  onSelectWorkspace,
  modelMenuSlot,
  contextUsage = null,
  contextUsageLoading = false,
  reasoningEffort = "auto",
  providerKind,
  supportedReasoningEfforts = null,
  onReasoningEffortChange,
  onDismissContextMenus = () => undefined,
  approvalMode = "ask",
  onApprovalModeChange = () => undefined,
  webAccess = CLOSED_WEB_ACCESS,
  onWebAccessChange = () => undefined,
  queuedLabel,
  queueEditing = false,
  onQueue,
  onInterruptAndSend,
  onEditQueue,
  onCancelQueue,
  onChange,
  onOpenPlugins = () => undefined,
  onOpenSettings,
  onSend,
  onStop
}: AgentComposerProps) {
  const uiPreferences = useUiPreferences();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const formRef = useRef<HTMLDivElement | null>(null);
  const addToggleRef = useRef<HTMLButtonElement | null>(null);
  const addMenuRef = useRef<HTMLDivElement | null>(null);
  const approvalToggleRef = useRef<HTMLButtonElement | null>(null);
  const approvalMenuRef = useRef<HTMLDivElement | null>(null);
  const sendButtonRef = useRef<HTMLButtonElement | null>(null);
  const runningActionsRef = useRef<HTMLDivElement | null>(null);
  const previousFocusRequestTokenRef = useRef(focusRequestToken);
  const previousPluginFocusRequestTokenRef = useRef(pluginFocusRequestToken);
  const previousFilePickerRequestTokenRef = useRef(filePickerRequestToken);
  const actionScopeKeyRef = useRef(actionScopeKey);
  const filePickerScopeRef = useRef<{ scope: string | null | undefined } | null>(null);
  actionScopeKeyRef.current = actionScopeKey;
  const [dragging, setDragging] = useState(false);
  const [nativeDropError, setNativeDropError] = useState("");
  const [showRunningActions, setShowRunningActions] = useState(false);
  const [showAddMenu, setShowAddMenu] = useState(false);
  const [showApprovalMenu, setShowApprovalMenu] = useState(false);
  const [showContextUsage, setShowContextUsage] = useState(false);
  const [showReasoningMenu, setShowReasoningMenu] = useState(false);
  const nativeDropOptionsRef = useRef({
    attachments,
    attachmentsEnabled,
    onAddFiles,
    onAttachmentsUnavailable,
  });
  nativeDropOptionsRef.current = {
    attachments,
    attachmentsEnabled,
    onAddFiles,
    onAttachmentsUnavailable,
  };
  const visibleModelLabel = modelLabel ?? (hasModel ? "已配置模型" : "连接模型后即可发送");
  const hasDraftContent = value.trim().length > 0 || hasReadyAttachments;
  const canSend =
    hasModel &&
    !submissionPending &&
    !sendBlockedReason &&
    hasDraftContent;
  const showStop = isRunning && !hasDraftContent;

  useEffect(() => {
    setShowRunningActions(false);
  }, [actionScopeKey]);

  // Close the "+" and approval popovers when the composer scope changes or on
  // outside/Escape interaction so a stale menu never lingers across tasks.
  useEffect(() => {
    setShowAddMenu(false);
    setShowApprovalMenu(false);
    setShowContextUsage(false);
    setShowReasoningMenu(false);
    onApprovalModeChange("ask");
  }, [actionScopeKey, onApprovalModeChange]);

  useEffect(() => {
    if (!showAddMenu && !showApprovalMenu && !showRunningActions) return undefined;
    function handlePointerDown(event: MouseEvent) {
      const target = event.target as Node;
      // Any click outside the open popover and its own toggle (blank space,
      // the textarea, the page behind) dismisses it. Toggles are excluded so
      // their click can still flip the menu open/closed.
      const insideAdd =
        addMenuRef.current?.contains(target)
        || addToggleRef.current?.contains(target);
      const insideApproval =
        approvalMenuRef.current?.contains(target)
        || approvalToggleRef.current?.contains(target);
      const insideRunning =
        runningActionsRef.current?.contains(target)
        || sendButtonRef.current?.contains(target);
      if (!insideAdd) setShowAddMenu(false);
      if (!insideApproval) setShowApprovalMenu(false);
      if (!insideRunning) setShowRunningActions(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (showAddMenu) addToggleRef.current?.focus();
        setShowAddMenu(false);
        setShowApprovalMenu(false);
        setShowRunningActions(false);
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [showAddMenu, showApprovalMenu, showRunningActions]);

  useEffect(() => {
    if (!isRunning || !canSend) setShowRunningActions(false);
  }, [canSend, isRunning]);

  useEffect(() => {
    const previous = previousFocusRequestTokenRef.current;
    previousFocusRequestTokenRef.current = focusRequestToken;
    if (focusRequestToken > previous) textareaRef.current?.focus();
  }, [focusRequestToken]);

  useEffect(() => {
    const previous = previousPluginFocusRequestTokenRef.current;
    previousPluginFocusRequestTokenRef.current = pluginFocusRequestToken;
    if (pluginFocusRequestToken > previous) addToggleRef.current?.focus();
  }, [pluginFocusRequestToken]);

  useEffect(() => {
    const previous = previousFilePickerRequestTokenRef.current;
    previousFilePickerRequestTokenRef.current = filePickerRequestToken;
    if (filePickerRequestToken > previous) {
      filePickerScopeRef.current = { scope: actionScopeKeyRef.current };
      fileInputRef.current?.click();
    }
  }, [filePickerRequestToken]);

  useEffect(() => {
    if (!Reflect.has(globalThis, "__TAURI_INTERNALS__")) return;
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    void getCurrentWebview().onDragDropEvent((event) => {
      // A template upload owns its drop; do not consume its one-shot native file grant.
      if (cancelled || formRef.current?.closest('[hidden]') || document.querySelector('dialog[aria-label="上传模板"]')) { setDragging(false); return; }
      const options = nativeDropOptionsRef.current;
      if (event.payload.type === "enter" || event.payload.type === "over") {
        if (options.onAddFiles) setDragging(true);
        return;
      }
      setDragging(false);
      if (event.payload.type !== "drop" || event.payload.paths.length === 0) return;
      if (!options.attachmentsEnabled) {
        options.onAttachmentsUnavailable?.();
        return;
      }
      const addFiles = options.onAddFiles;
      if (!addFiles) return;
      const droppedPaths = event.payload.paths;

      void (async () => {
        const availableCount = Math.max(
          0,
          MAX_COMPOSER_FILES - options.attachments.length,
        );
        let availableBytes = Math.max(
          0,
          MAX_COMPOSER_BYTES
            - options.attachments.reduce((total, item) => total + item.size, 0),
        );
        const files: File[] = [];
        const errors: string[] = [];
        for (const path of droppedPaths.slice(0, availableCount)) {
          try {
            const name = nativeDroppedFileName(path);
            const mediaType = composerMediaTypeForName(name);
            if (!mediaType) {
              errors.push(`${name} 的格式不支持。`);
              continue;
            }
            const response = await invoke<ArrayBuffer | number[]>(
              "read_dropped_file",
              { path },
            );
            const payload = response instanceof ArrayBuffer
              ? response
              : Uint8Array.from(response).buffer;
            const view = new DataView(payload);
            if (view.byteLength < 8) throw new Error("invalid dropped file payload");
            const lastModified = Number(view.getBigUint64(0, true));
            const bytes = new Uint8Array(payload, 8);
            if (bytes.byteLength > availableBytes) {
              errors.push("附件总大小不能超过 30 MiB。");
              continue;
            }
            files.push(new File([bytes], name, {
              type: mediaType,
              lastModified,
            }));
            availableBytes -= bytes.byteLength;
          } catch (error) {
            errors.push(nativeDropReadError(error));
          }
        }
        if (droppedPaths.length > availableCount) {
          errors.push(`每次最多添加 ${MAX_COMPOSER_FILES} 个附件。`);
        }
        if (cancelled) return;
        setNativeDropError(errors.join(" "));
        if (files.length > 0) addFiles(files);
      })();
    }).then((dispose) => {
      if (cancelled) dispose();
      else unlisten = dispose;
    }).catch(() => {
      if (!cancelled) setNativeDropError("无法启用文件拖放。");
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  function submitMessage() {
    if (!isRunning) { onSend(); return; }
    if (uiPreferences.followUp === "queue" && onQueue) onQueue();
    else if (uiPreferences.followUp === "interrupt" && onInterruptAndSend) onInterruptAndSend();
    else setShowRunningActions(current => !current);
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (isSendKey({ ...event, isComposing: event.nativeEvent.isComposing, keyCode: event.nativeEvent.keyCode }, uiPreferences.sendMode)) {
      event.preventDefault();
      if (!canSend) return;
      submitMessage();
    }
  }

  const visibleError = [
    sendBlockedReason,
    attachmentError,
    nativeDropError,
    webAccess.error,
  ]
    .filter(Boolean)
    .join(" ");
  const webAccessLabel = webAccess.loading
    ? "正在读取联网状态"
    : webAccess.updating
      ? "正在更新联网状态"
      : webAccess.granted
        ? "联网已授权"
        : webAccess.enabled
          ? "联网已开启，首次使用时确认"
          : "联网已关闭";

  return (
    <div
      ref={formRef}
      className={`agent-composer${dragging ? " agent-composer-dragging" : ""}`}
      data-testid="agent-composer"
      onDragOver={(event) => {
        event.preventDefault();
        if (onAddFiles) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        const files = Array.from(event.dataTransfer.files ?? []);
        if (files.length) {
          setNativeDropError("");
          onAddFiles?.(files);
        }
      }}
    >
      <ComposerAttachmentChips
        attachments={attachments}
        onRetry={onRetryAttachment}
        onRemove={onRemoveAttachment}
      />
      {queuedLabel && (
        <div className="composer-queue-banner">
          <span>{queuedLabel}</span>
          {!queueEditing && (
            <button type="button" aria-label="编辑排队消息" onClick={onEditQueue}>编辑</button>
          )}
          <button type="button" aria-label="取消排队消息" onClick={onCancelQueue}>取消</button>
        </div>
      )}
      <div className="composer-topbar">
        <button
          className="composer-workspace"
          type="button"
          aria-label={`选择工作区，当前 ${workspaceLabel}`}
          onClick={onSelectWorkspace}
          disabled={!onSelectWorkspace}
        >
          <FolderOpen aria-hidden="true" size={16} />
          {workspaceLabel}
        </button>
      </div>
      <textarea
        ref={textareaRef}
        aria-label="向智能体提问"
        maxLength={100000}
        placeholder="向智能体提问…"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={(event) => {
          const images = Array.from(event.clipboardData.files ?? []).filter((file) =>
            file.type.startsWith("image/"),
          );
          if (images.length > 0) {
            event.preventDefault();
            setNativeDropError("");
            onAddFiles?.(images);
          }
        }}
        rows={3}
      />
      {visibleError && (
        <div className="composer-inline-error" role="alert">
          {visibleError}
        </div>
      )}
      <div className="composer-actions">
        <input
          ref={fileInputRef}
          className="visually-hidden"
          type="file"
          multiple
          accept={COMPOSER_FILE_ACCEPT}
          aria-label="选择附件文件"
          onChange={(event) => {
            const files = Array.from(event.currentTarget.files ?? []);
            const pickerScope = filePickerScopeRef.current;
            filePickerScopeRef.current = null;
            if (
              pickerScope !== null &&
              pickerScope.scope !== actionScopeKeyRef.current
            ) {
              event.currentTarget.value = "";
              return;
            }
            if (files.length) {
              setNativeDropError("");
              onAddFiles?.(files);
            }
            event.currentTarget.value = "";
          }}
        />
        <button
          ref={addToggleRef}
          className="icon-button composer-add"
          type="button"
          aria-label="更多选项"
          aria-expanded={showAddMenu}
          onClick={() => {
            setShowAddMenu((value) => !value);
            setShowApprovalMenu(false);
            setShowContextUsage(false);
            setShowReasoningMenu(false);
            onDismissContextMenus();
          }}
        >
          <Plus aria-hidden="true" size={20} />
        </button>
        {!!knowledgeSelection?.selected.length && <span className="composer-knowledge-count">知识库 · {knowledgeSelection.selected.length}</span>}
        {showAddMenu && (
          <div
            ref={addMenuRef}
            className="composer-add-menu"
            role="menu"
            aria-label="更多选项"
          >
            <button
              type="button"
              role="menuitem"
              disabled={!onAddFiles}
              onClick={() => {
                setShowAddMenu(false);
                if (attachmentsEnabled) {
                  filePickerScopeRef.current = { scope: actionScopeKeyRef.current };
                  fileInputRef.current?.click();
                } else {
                  onAttachmentsUnavailable?.();
                }
              }}
            >
              <Paperclip aria-hidden="true" size={16} />
              添加附件
            </button>
            <button
              type="button"
              role="menuitem"
              disabled={!onSelectWorkspace}
              onClick={() => {
                setShowAddMenu(false);
                onSelectWorkspace?.();
              }}
            >
              <FolderOpen aria-hidden="true" size={16} />
              选择工作区
            </button>
            {knowledgeSelection ? <KnowledgePicker {...knowledgeSelection} /> : <button type="button" role="menuitem" disabled aria-disabled="true">
              <Database aria-hidden="true" size={16} />知识库
            </button>}
            <button type="button" role="menuitem" disabled aria-disabled="true">
              <ListTree aria-hidden="true" size={16} />
              问答 · 计划 · 目标
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setShowAddMenu(false);
                onOpenSettings();
              }}
            >
              <Wrench aria-hidden="true" size={16} />
              技能
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setShowAddMenu(false);
                onOpenPlugins();
              }}
            >
              <Plug aria-hidden="true" size={16} />
              插件
            </button>
          </div>
        )}
        <div className="composer-approval">
          <ShieldCheck aria-hidden="true" size={16} />
          <button
            ref={approvalToggleRef}
            type="button"
            className="composer-approval-select"
            aria-haspopup="menu"
            aria-expanded={showApprovalMenu}
            onClick={() => {
              setShowApprovalMenu((value) => !value);
              setShowAddMenu(false);
              setShowContextUsage(false);
              setShowReasoningMenu(false);
              onDismissContextMenus();
            }}
          >
            {APPROVAL_MODES.find((mode) => mode.value === approvalMode)?.label}
          </button>
          {showApprovalMenu && (
            <div
              ref={approvalMenuRef}
              className="composer-approval-menu"
              role="menu"
              aria-label="审批权限"
            >
              {APPROVAL_MODES.map((mode) => (
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={approvalMode === mode.value}
                  key={mode.value}
                  onClick={() => {
                    onApprovalModeChange(mode.value);
                    setShowApprovalMenu(false);
                  }}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          type="button"
          className={`composer-web-access${webAccess.enabled ? " is-enabled" : ""}`}
          aria-label={webAccessLabel}
          aria-pressed={webAccess.enabled}
          disabled={isRunning || webAccess.loading || webAccess.updating}
          onClick={() => onWebAccessChange(!webAccess.enabled)}
        >
          联网
        </button>
        {litigationSkillScope !== undefined && <LitigationSkillPicker key={litigationSkillScope}
          disabled={submissionPending}
          onOpen={() => {
            setShowAddMenu(false);
            setShowApprovalMenu(false);
            setShowContextUsage(false);
            setShowReasoningMenu(false);
            setShowRunningActions(false);
            onDismissContextMenus();
          }}
          onChoose={name => {
            const instruction = `请先读取并使用「${name}」技能。`;
            if (!value.split(/\r?\n/).includes(instruction)) {
              onChange(`${value}${value ? value.endsWith("\n") ? "\n" : "\n\n" : ""}${instruction}`);
            }
            requestAnimationFrame(() => textareaRef.current?.focus());
          }} />}
        <span className="composer-spacer" />
        <div className="composer-primary-controls" data-testid="composer-primary-controls">
          <ContextUsageControl
            usage={contextUsage}
            loading={contextUsageLoading}
            disabled={!contextUsage && !contextUsageLoading}
            open={showContextUsage}
            onOpenChange={(next) => {
              setShowContextUsage(next);
              if (next) {
                setShowAddMenu(false);
                setShowApprovalMenu(false);
                setShowReasoningMenu(false);
                setShowRunningActions(false);
                onDismissContextMenus();
              }
            }}
          />
          <button
            className="model-status"
            type="button"
            onClick={() => {
              setShowContextUsage(false);
              setShowReasoningMenu(false);
              (onSelectModel ?? onOpenSettings)();
            }}
            aria-label={hasModel ? `选择模型，当前 ${visibleModelLabel}` : "连接模型"}
          >
            <span>{visibleModelLabel}</span>
            <ChevronDown aria-hidden="true" size={13} />
          </button>
          <ReasoningEffortControl
            value={reasoningEffort}
            providerKind={providerKind}
            supportedEfforts={supportedReasoningEfforts}
            disabled={!hasModel || !onReasoningEffortChange || isRunning}
            open={showReasoningMenu}
            onOpenChange={(next) => {
              setShowReasoningMenu(next);
              if (next) {
                setShowAddMenu(false);
                setShowApprovalMenu(false);
                setShowContextUsage(false);
                setShowRunningActions(false);
                onDismissContextMenus();
              }
            }}
            onChange={(effort) => onReasoningEffortChange?.(effort)}
          />
          <button
            ref={sendButtonRef}
            className={`send-button${showStop ? " send-button-stop" : ""}`}
            type="button"
            aria-label={showStop ? "停止" : isRunning ? "提交下一条" : "发送"}
            disabled={showStop ? false : !canSend}
            onClick={() => {
              setShowContextUsage(false);
              setShowReasoningMenu(false);
              if (showStop) onStop();
              else submitMessage();
            }}
          >
            {showStop
              ? <Square aria-hidden="true" size={16} />
              : <Send aria-hidden="true" size={18} />}
          </button>
        </div>
        {showRunningActions && isRunning && canSend && (
          <div
            ref={runningActionsRef}
            className="composer-running-actions"
            role="menu"
            aria-label="运行中提交方式"
          >
            <button type="button" role="menuitem" onClick={() => { setShowRunningActions(false); onQueue?.(); }}>
              排队
            </button>
            <button type="button" role="menuitem" onClick={() => { setShowRunningActions(false); onInterruptAndSend?.(); }}>
              打断并发送
            </button>
          </div>
        )}
        {modelMenuSlot}
      </div>
    </div>
  );
}
