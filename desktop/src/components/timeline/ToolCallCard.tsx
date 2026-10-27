import { mcpErrorCopy } from "../../mcp/errorCopy";
import { KnowledgeQuoteCard } from "./KnowledgeQuoteCard";
import { ApprovalCard } from "./ApprovalCard";
import { SquareTerminal, Wrench } from "lucide-react";
import { caseCardId } from "../../timeline/caseCitations";
import { caseResultSummary } from "../../timeline/caseResults";
import { CaseCard } from "./CaseCard";
import { lawResultSummary } from "../../timeline/lawResults";
import { memo, useState } from "react";

import type { ApprovalSubmissionState } from "../../hooks/useAgentChat";
import type { ToolItem } from "../../timeline/items";
import {
  isWebTool,
  toolPresentation,
  webSearchCandidates,
  webToolArguments,
  webToolSummary,
} from "../../timeline/toolPresentation";
import {
  GENERIC_WEB_ERROR,
  webErrorMessageFromPreview,
} from "../../timeline/webErrors";

const STATUS_LABEL: Record<ToolItem["status"], string> = {
  running: "执行中",
  awaiting_approval: "等待审批",
  succeeded: "成功",
  failed: "失败",
  denied: "已拒绝",
  cancelled: "已取消",
};

interface ToolCallCardProps {
  presentation?: "chat" | "legal";
  compactApproval?: boolean;
  item: ToolItem;
  submission?: ApprovalSubmissionState;
  connectionReady?: boolean;
  onResolve?: (decision: "allowed" | "denied", reason: string) => void;
  /** Workspace-relative path made previewable by this successful call. */
  previewPath?: string;
  onPreviewPath?: (path: string) => void;
}

function statusLabel(item: ToolItem): string {
  if (item.approvalDecision === "denied" || item.status === "denied") {
    return "已拒绝";
  }
  if (item.approvalDecision === "allowed" && item.status === "running") {
    return "已允许，正在执行";
  }
  return STATUS_LABEL[item.status];
}

export const ToolCallCard = memo(function ToolCallCard({
  item,
  compactApproval = false,
  presentation: displayMode = "chat",
  submission,
  connectionReady = true,
  onResolve,
  previewPath,
  onPreviewPath,
}: ToolCallCardProps) {
  const [showResult, setShowResult] = useState(false);
  if (item.status === "awaiting_approval") {
    if (compactApproval) return <div className="tool-approval-pending-line" data-tool-status={item.status}>
      {item.name === "run_command" || item.name === "run_background_command" ? <SquareTerminal size={15} aria-hidden="true" /> : <Wrench size={15} aria-hidden="true" />}
      <span>{toolPresentation(item).title} · 等待确认</span>
    </div>;
    return <ApprovalCard key={`${item.turnId}:${item.id}:${item.approvalId}`} item={item} submission={submission} connectionReady={connectionReady} onResolve={onResolve} />;
  }
  if (displayMode === "legal" && item.status === "succeeded"
    && lawResultSummary(item)?.kind === "law_read") return null;
  if (item.name === "knowledge_quote" && item.status === "succeeded" && item.resultSummary?.kind === "knowledge_quote"
      && typeof item.resultSummary.quoteId === "string" && typeof item.resultSummary.threadId === "string") {
    return <KnowledgeQuoteCard quoteId={item.resultSummary.quoteId} threadId={item.resultSummary.threadId} />;
  }
  const cases = item.status === "succeeded" ? caseResultSummary(item) : undefined;
  if (cases) return <div className="case-results">
    {cases.cases.map((entry) => <CaseCard key={entry.sourceId} id={caseCardId(item.id, entry.sourceId)} summary={entry} sourceName={cases.sourceName} fetchedAt={cases.fetchedAt} />)}
    {cases.cases.length === 0 && <p>所选渠道未返回匹配案例。</p>}
    {cases.truncated && <p>当前仅展示部分案例，可缩小检索范围后继续查找。</p>}
  </div>;
  const hasResult = item.resultPreview !== undefined && item.resultPreview !== "";
  const webTool = isWebTool(item);
  const presentation = toolPresentation(item);
  const visibleWebArguments = webToolArguments(item);
  const visibleWebSummary = webToolSummary(item);
  const searchCandidates = webSearchCandidates(item);
  const webFailure = webTool && item.status === "failed"
    ? webErrorMessageFromPreview(item.resultPreview) ?? GENERIC_WEB_ERROR
    : null;
  const mcpFailure = item.status === "failed" && item.name.startsWith("mcp__")
    ? (typeof item.resultSummary?.reasonCode === "string"
      ? item.resultSummary.reasonCode
      : /^(mcp_[a-z0-9_]+):/.exec(item.resultPreview ?? "")?.[1])
    : undefined;
  return (
    <div
      className={`tool-card tool-${item.status}${
        item.status === "succeeded" ? " tool-quiet-row" : ""
      }`}
      data-tool-status={item.status}
    >
      <div className="tool-card-head">
        <span className="tool-status-dot" aria-hidden="true" />
        <span className="tool-name">{presentation.title}</span>
        <span className="tool-status-label">{statusLabel(item)}</span>
        {typeof item.durationMs === "number" && (
          <span className="tool-duration">{item.durationMs} ms</span>
        )}
      </div>
      {item.resultSummary?.kind === "case_unparsed" ? (
        <p>未能将渠道返回内容整理成案例卡，可查看本次回答中的说明或重新检索。</p>
      ) : null}
      {webTool && visibleWebArguments ? <div className="tool-args">{visibleWebArguments}</div> : null}
      {visibleWebSummary ? (
        <div className="web-tool-summary">{visibleWebSummary}</div>
      ) : null}
      {mcpFailure ? (
        <div className="tool-approval-error" role="alert">{mcpErrorCopy(mcpFailure)}</div>
      ) : null}
      {webFailure ? (
        <div className="tool-approval-error" role="alert">{webFailure}</div>
      ) : null}
      {previewPath && onPreviewPath && item.status === "succeeded" && (
        <div className="tool-preview-entry">
          <button type="button" onClick={() => onPreviewPath(previewPath)}>
            预览产物
          </button>
        </div>
      )}
      {hasResult && !webTool && (
        <div className="tool-result">
          <button
            type="button"
            className="tool-result-toggle"
            aria-expanded={showResult}
            onClick={() => setShowResult((value) => !value)}
          >
            {showResult ? "收起结果" : "查看结果"}
          </button>
          {showResult && (
            <pre className="tool-result-preview">
              {item.resultPreview}
              {item.resultTruncated && <span className="tool-result-more">…（已截断）</span>}
            </pre>
          )}
        </div>
      )}
      {item.name === "web_search" && searchCandidates.length > 0 ? (
        <div className="tool-result web-search-results">
          <button
            type="button"
            className="tool-result-toggle"
            aria-expanded={showResult}
            onClick={() => setShowResult((value) => !value)}
          >
            {showResult ? "收起候选网页" : "查看候选网页"}
          </button>
          {showResult ? (
            <ol aria-label="搜索候选网页">
              {searchCandidates.map((candidate, index) => (
                <li key={`${candidate.url}:${index}`}>
                  <a
                    href={candidate.url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {candidate.title}
                  </a>
                  <div className="web-search-site">{candidate.url}</div>
                  {candidate.snippet ? <p>{candidate.snippet}</p> : null}
                  <span>未读取正文，不可引用</span>
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}
    </div>
  );
});
