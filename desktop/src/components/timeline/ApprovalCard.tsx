import { ChevronDown, CornerDownLeft, SquareTerminal, Wrench } from "lucide-react";
import { useId, useState } from "react";
import type { ApprovalSubmissionState } from "../../hooks/useAgentChat";
import type { ToolItem } from "../../timeline/items";
import { approvalErrorMessage, isWebTool, toolArgumentBasename, toolPresentation, webApprovalImpact, webToolArguments } from "../../timeline/toolPresentation";
import { LitigationSaveApproval } from "./LitigationSaveApproval";
import "./ApprovalCard.css";

interface ApprovalCardProps {
  item: ToolItem;
  submission?: ApprovalSubmissionState;
  connectionReady?: boolean;
  onResolve?: (decision: "allowed" | "denied", reason: string) => void;
  pendingCount?: number;
}

function argumentPreview(item: ToolItem): string {
  const web = webToolArguments(item);
  if (web) return web;
  return Object.entries(item.argumentsSummary ?? {}).map(([key, value]) => {
    const visible = key === "path" ? toolArgumentBasename(value) : value;
    const text = typeof visible === "string" ? visible : JSON.stringify(visible);
    return key === "command" ? text : `${key}: ${text}`;
  }).join("\n");
}

/** Presents an existing pending request; permission scope stays with Runtime. */
export function ApprovalCard({ item, submission, connectionReady = true, onResolve, pendingCount = 1 }: ApprovalCardProps) {
  const titleId = useId();
  const reasonId = useId();
  const [showReason, setShowReason] = useState(false);
  const [reason, setReason] = useState("");
  const presentation = toolPresentation(item);
  const terminal = item.name === "run_command" || item.name === "run_background_command";
  const firstWebApproval = isWebTool(item) && item.approvalBehavior === "thread_first_use";
  const allowLabel = firstWebApproval ? "允许此任务联网" : item.name === "litigation_work_save" ? "保存这次修改" : "允许一次";
  const canResolve = item.status === "awaiting_approval" && Boolean(item.turnId && item.id && item.approvalId && onResolve);
  const locked = !canResolve || !connectionReady || submission?.status === "submitting"
    || (submission?.status === "failed" && submission.retryable === false);
  const preview = argumentPreview(item);
  if (item.status !== "awaiting_approval") return null;

  return <section className="approval-card" aria-labelledby={titleId}
    onKeyDown={event => {
      // Shortcuts belong to this card only; never approve from a document or draft.
      if (event.nativeEvent.isComposing || event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if ((event.target as HTMLElement).closest("textarea, input, [contenteditable=true]")) return;
      if (event.key === "Escape" && !locked) {
        event.preventDefault(); event.stopPropagation(); onResolve?.("denied", reason);
      }
    }}>
    <div className="approval-card-body">
      <div className="approval-card-label">
        {terminal ? <SquareTerminal size={17} aria-hidden="true" /> : <Wrench size={17} aria-hidden="true" />}
        <span>{terminal ? "终端" : "操作确认"}</span>
        {pendingCount > 1 && <span className="approval-card-count">还有 {pendingCount - 1} 项</span>}
      </div>
      <h3 id={titleId}>{presentation.title}</h3>
      <p className="approval-card-impact">{webApprovalImpact(item) ?? presentation.impact}</p>
      {item.name === "litigation_work_save" ? <LitigationSaveApproval item={item} />
        : preview && <pre className="approval-card-preview">{preview}</pre>}
      {showReason && <div className="tool-approval-reason" id={reasonId}>
        <label><span>审批说明（可选）</span>
          <textarea autoFocus value={reason} maxLength={500} disabled={locked}
            onChange={event => setReason(event.target.value.slice(0, 500))} />
        </label>
        <span className="tool-approval-count">还可输入 {500 - reason.length} 字</span>
      </div>}
    </div>
    <div className="approval-card-footer">
      {submission?.status === "failed" && <div className="tool-approval-error" role="alert">
        {submission.code ? approvalErrorMessage(submission.code, submission.retryable)
          : submission.error ?? approvalErrorMessage(undefined)}
      </div>}
      {!connectionReady ? <div className="tool-approval-submitting" role="status">正在重新连接，连接后可审批</div>
        : submission?.status === "submitting" ? <div className="tool-approval-submitting" role="status">正在提交决定…</div> : null}
      {canResolve && <div className="approval-card-actions">
        <button type="button" className="approval-card-deny" aria-label="拒绝" disabled={locked}
          onClick={() => onResolve?.("denied", reason)}>拒绝 <kbd aria-hidden="true">Esc</kbd></button>
        <div className="approval-card-allow-group">
          <button type="button" className="approval-card-allow" aria-label={allowLabel} disabled={locked}
            onClick={() => onResolve?.("allowed", reason)}>{allowLabel} <kbd aria-hidden="true"><CornerDownLeft size={14} /></kbd></button>
          <button type="button" className="approval-card-more" aria-label="添加说明（可选）" title="添加说明（可选）"
            aria-expanded={showReason} aria-controls={reasonId} disabled={locked}
            onClick={() => setShowReason(value => !value)}><ChevronDown size={16} aria-hidden="true" /></button>
        </div>
      </div>}
    </div>
  </section>;
}
