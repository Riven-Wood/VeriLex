import { useId, useState } from "react";

import type { ReasoningSegment } from "../../timeline/items";

interface ReasoningDisclosureProps {
  segment: ReasoningSegment;
}

function headline(segment: ReasoningSegment): string {
  if (segment.isStreaming && !segment.text) return "正在深度思考…";
  if (segment.isStreaming) return `正在深度思考…（已 ${segment.text.length} 字）`;
  return `已深度思考 · ${segment.text.length} 字`;
}

export function ReasoningDisclosure({ segment }: ReasoningDisclosureProps) {
  // While the model thinks, the process streams visibly; the moment the
  // answer takes over, collapse back to the headline row (the user can still
  // reopen it). Derived-state reset, so no effect is needed.
  const [open, setOpen] = useState(segment.isStreaming);
  const [wasStreaming, setWasStreaming] = useState(segment.isStreaming);
  if (wasStreaming !== segment.isStreaming) {
    setWasStreaming(segment.isStreaming);
    if (!segment.isStreaming) setOpen(false);
  }
  const buttonId = useId();
  if (!segment.text && !segment.isStreaming) return null;
  return (
    <div className="reasoning-block">
      <button
        type="button"
        id={buttonId}
        className="reasoning-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="reasoning-chevron" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        {headline(segment)}
      </button>
      {open && (
        <div className="reasoning-text">
          {segment.text || "…"}
          {segment.isStreaming && (
            <span className="streaming-cursor" aria-hidden="true">▋</span>
          )}
        </div>
      )}
    </div>
  );
}
