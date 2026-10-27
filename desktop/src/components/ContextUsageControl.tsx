import { useEffect, useRef, useState } from "react";

import { formatContextWindowTokens } from "../providers/contextWindowInput";
import type { ContextUsageRecord } from "../runtime/contracts";

interface ContextUsageControlProps {
  usage?: ContextUsageRecord | null;
  loading?: boolean;
  disabled?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

const ROWS: { key: keyof ContextUsageRecord["breakdown"]; label: string }[] = [
  { key: "messages", label: "消息" },
  { key: "system_and_tools", label: "系统与工具" },
  { key: "skills", label: "技能" },
  { key: "attachments", label: "附件" },
  { key: "other", label: "其他" },
];

function number(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function contextWindowSourceCopy(source: ContextUsageRecord["context_window_source"]): string {
  switch (source) {
    case "provider":
      return "Provider 自动获取";
    case "curated":
      return "VeriLex 内置资料";
    case "user_declared":
      return "用户设置";
    case "unknown":
      return "";
  }
}

export function ContextUsageControl({
  usage = null,
  loading = false,
  disabled = false,
  open,
  onOpenChange,
}: ContextUsageControlProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [internalOpen, setInternalOpen] = useState(false);
  const visible = open ?? internalOpen;
  const setVisible = (next: boolean) => {
    if (open === undefined) setInternalOpen(next);
    onOpenChange?.(next);
  };
  const percent = usage?.used_percent;
  const ringPercent = percent === null || percent === undefined ? 0 : percent;
  const label = loading
    ? "上下文量，正在计算"
    : percent === null || percent === undefined
      ? `上下文量，约 ${number(usage?.used_tokens ?? 0)} 已用`
      : `上下文量，已用 ${percent}%`;

  useEffect(() => {
    if (!visible) return undefined;
    const pointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setVisible(false);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") setVisible(false);
    };
    document.addEventListener("pointerdown", pointer);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", pointer);
      document.removeEventListener("keydown", key);
    };
  }, [visible]);

  const warningText = usage?.warning === "critical"
    ? "上下文接近上限"
    : usage?.warning === "near_limit"
      ? "上下文用量较高"
      : "";

  return (
    <div className="context-usage-control" ref={rootRef}>
      <button
        type="button"
        className={`context-usage-button context-usage-${usage?.warning ?? "normal"}`}
        aria-label={label}
        aria-expanded={visible}
        disabled={disabled}
        onClick={() => setVisible(!visible)}
      >
        <svg className="context-usage-ring" viewBox="0 0 24 24" aria-hidden="true">
          <circle className="context-usage-ring-track" cx="12" cy="12" r="8.5" />
          {percent !== null && percent !== undefined && (
            <circle
              className="context-usage-ring-value"
              cx="12"
              cy="12"
              r="8.5"
              pathLength="100"
              strokeDasharray={`${Math.max(1, ringPercent)} 100`}
            />
          )}
        </svg>
        <span>{loading ? "…" : percent === null || percent === undefined ? "约" : `${percent}%`}</span>
      </button>
      {visible && (
        <div className="context-usage-popover" role="dialog" aria-label="上下文量详情">
          <div className="context-usage-heading">
            <strong>上下文量</strong>
            <span>
              {usage?.effective_window_tokens
                ? `${usage.estimated ? "约 " : ""}${number(usage.used_tokens)} / ${number(usage.effective_window_tokens)}`
                : `约 ${number(usage?.used_tokens ?? 0)} 已用`}
            </span>
          </div>
          {usage?.context_window_tokens && usage.context_window_source !== "unknown" ? (
            <p className="context-usage-note">
              模型上限 {formatContextWindowTokens(usage.context_window_tokens)} · {contextWindowSourceCopy(usage.context_window_source)}
            </p>
          ) : null}
          {usage?.effective_window_tokens ? (
            <div className="context-usage-progress" aria-hidden="true">
              <span style={{ width: `${Math.min(100, usage.used_percent ?? 0)}%` }} />
            </div>
          ) : (
            <p className="context-usage-note">当前模型未提供上下文上限</p>
          )}
          {warningText && <p className="context-usage-warning">{warningText}</p>}
          <div className="context-usage-breakdown">
            {ROWS.map((row) => (
              <div key={row.key}>
                <span>{row.label}</span>
                <span>{number(usage?.breakdown[row.key] ?? 0)}</span>
              </div>
            ))}
          </div>
          {usage?.estimated && <p className="context-usage-note">分类为本地估算，不会额外请求模型。</p>}
        </div>
      )}
    </div>
  );
}
