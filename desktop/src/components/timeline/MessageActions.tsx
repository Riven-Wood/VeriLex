// Bubble action placement and success-only feedback adapted from OpenWorker
// surfaces/gui/src/components/Transcript.tsx, 5bc10d9 (MIT).
// Copyright (c) 2024 Andrew Ng. See THIRD_PARTY_NOTICES.md.
import { useEffect, useRef, useState } from "react";
import { Copy, RefreshCw } from "lucide-react";

export function CopyButton({ text, label = "复制" }: { text: string; label?: string }) {
  const [state, setState] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("已复制");
    } catch {
      setState("复制失败");
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState(""), 1200);
  }
  return <button type="button" className="message-action" title={state || label} aria-label={state || label} onClick={() => void copy()}>
    <Copy size={13} />{state || (label === "复制代码" ? label : "")}
  </button>;
}

export function MessageActions({ text, createdAt, onRegenerate, onConvertOpinion }: { text: string; createdAt?: string; onRegenerate?: () => void; onConvertOpinion?: (text: string) => boolean }) {
  const conversionLock = useRef(false);
  const [converting, setConverting] = useState(false);
  useEffect(() => { if (!onConvertOpinion) { conversionLock.current = false; setConverting(false); } }, [onConvertOpinion]);
  return <div className="message-actions">
    <CopyButton text={text} />
    {onConvertOpinion && <button type="button" className="message-action" disabled={converting} onClick={() => {
      if (conversionLock.current) return;
      conversionLock.current = true;
      const accepted = onConvertOpinion(text);
      conversionLock.current = accepted;
      setConverting(accepted);
    }}>{converting ? "正在整理…" : "转为审查意见"}</button>}
    {onRegenerate && <button type="button" className="message-action" aria-label="重新生成" title="重新生成：保留旧回答，只重发文字，不重新附加附件或知识库选择" onClick={onRegenerate}><RefreshCw size={13} /></button>}
    {createdAt && <time dateTime={createdAt}>{new Date(createdAt).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}</time>}
  </div>;
}
