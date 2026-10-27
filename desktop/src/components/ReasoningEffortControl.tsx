import { useEffect, useRef, useState } from "react";
import { Brain, Check, ChevronDown } from "lucide-react";

import type { ProviderKind, ReasoningEffort } from "../runtime/contracts";

interface ReasoningEffortControlProps {
  value: ReasoningEffort;
  providerKind?: ProviderKind;
  supportedEfforts?: Exclude<ReasoningEffort, "auto">[] | null;
  disabled?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onChange: (value: ReasoningEffort) => void;
}

const LABELS: Record<ReasoningEffort, string> = {
  auto: "自动",
  low: "低",
  medium: "中",
  high: "高",
};

export function ReasoningEffortControl({
  value,
  providerKind,
  supportedEfforts = null,
  disabled = false,
  open,
  onOpenChange,
  onChange,
}: ReasoningEffortControlProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [internalOpen, setInternalOpen] = useState(false);
  const visible = open ?? internalOpen;
  const setVisible = (next: boolean) => {
    if (open === undefined) setInternalOpen(next);
    onOpenChange?.(next);
  };
  const confirmedUnsupported = providerKind === "ollama" || supportedEfforts?.length === 0;
  const values: ReasoningEffort[] = confirmedUnsupported
    ? ["auto"]
    : ["auto", ...(supportedEfforts ?? ["low", "medium", "high"])];

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

  return (
    <div className="reasoning-effort-control" ref={rootRef}>
      <button
        type="button"
        className="reasoning-effort-button"
        aria-label={`推理强度，当前${LABELS[value]}`}
        aria-expanded={visible}
        disabled={disabled}
        onClick={() => setVisible(!visible)}
      >
        <Brain aria-hidden="true" size={17} />
        <span>{LABELS[value]}</span>
        <ChevronDown aria-hidden="true" size={13} />
      </button>
      {visible && (
        <div className="reasoning-effort-menu" role="menu" aria-label="推理强度">
          <div className="reasoning-effort-heading">推理强度</div>
          {values.map((effort) => (
            <button
              key={effort}
              type="button"
              role="menuitemradio"
              aria-checked={value === effort}
              onClick={() => {
                onChange(effort);
                setVisible(false);
              }}
            >
              <span>{LABELS[effort]}</span>
              {value === effort && <Check aria-hidden="true" size={15} />}
            </button>
          ))}
          {confirmedUnsupported ? (
            <p>由当前模型决定</p>
          ) : supportedEfforts === null ? (
            <p>兼容性由模型服务确认；不支持时会安全回到自动。</p>
          ) : null}
        </div>
      )}
    </div>
  );
}
