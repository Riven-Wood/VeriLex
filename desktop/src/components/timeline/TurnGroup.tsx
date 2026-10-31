import {runtimeRequest} from "../../runtime/api";
import {useUiPreferences} from "../../settings/preferences";
import { MessageActions } from "./MessageActions";
import { visibleAssistantText } from "../../timeline/assistantText";
import { caseSourcesFromItems, distinctCaseRows } from "../../timeline/caseCitations";
import { ReactNode, useEffect, useState } from "react";

import { orderLegalCards } from "../../timeline/legalOrder";
import { MarkdownBody } from "./MarkdownBody";
import { SourcesBlock } from "./SourcesBlock";
import type { ThreadArtifactRecord } from "../../runtime/contracts";
import type { AssistantItem, TimelineItem } from "../../timeline/items";
import { isWebTool, toolStepLine } from "../../timeline/toolPresentation";

interface TurnGroupProps {
  items: TimelineItem[];
  threadId?: string;
  caseContextItems?: TimelineItem[];
  /** Active turns show narration; repeated unsuccessful calls stay folded. */
  active: boolean;
  startedAt?: number;
  endedAt?: number;
  /** Deliverables published by this turn, attached under its own bubble. */
  artifacts?: ThreadArtifactRecord[];
  renderDeliverables?: (artifacts: ThreadArtifactRecord[]) => ReactNode;
  renderItem: (item: TimelineItem) => ReactNode;
  /**
   * "legal" hides the working process (duration header, reasoning, quiet
   * tool steps) and keeps statute cards visible even when collapsed —
   * the legal search page shows results, not the process (product
   * decision 2026-09-05).
   */
  presentation?: "chat" | "legal";
  regenerateItemId?: string;
  onRegenerate?: () => void;
  actionsEnabled?: boolean;
  onConvertOpinion?: (text: string) => boolean;
}

function isLawTool(item: Extract<TimelineItem, { kind: "tool" }>): boolean {
  return item.name === "law_search" || item.name === "law_read";
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.max(1, Math.round(ms / 1000));
  if (totalSeconds < 60) return `${totalSeconds} 秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds === 0 ? `${minutes} 分钟` : `${minutes} 分 ${seconds} 秒`;
}

function messageText(item: AssistantItem): string {
  return visibleAssistantText(item.segments
    .map((segment) => (segment.type === "text" ? segment.text : ""))
    .join(""), item.isStreaming)
    .trim();
}

function reasoningText(items: TimelineItem[]): { text: string; streaming: boolean } {
  let text = "";
  let streaming = false;
  for (const item of items) {
    if (item.kind !== "assistant") continue;
    for (const segment of item.segments) {
      if (segment.type !== "reasoning") continue;
      if (segment.text) text += (text ? "\n\n" : "") + segment.text;
      if (segment.isStreaming) streaming = true;
    }
  }
  return { text, streaming };
}

function AssistantNarrative({ item, allItems = [], threadId, onRegenerate, onConvertOpinion, actionsEnabled = true, process = false, legal = false }: { threadId?:string; onRegenerate?: () => void; onConvertOpinion?: (text: string) => boolean; actionsEnabled?: boolean; process?: boolean; legal?: boolean; item: AssistantItem; allItems?: TimelineItem[] }) {
  const position = allItems.findIndex((entry) => entry.id === item.id);
  const preceding = position < 0 ? allItems : allItems.slice(0, position);
  const availableIds = new Set(caseSourcesFromItems(preceding).map((source) => source.sourceId));
  const caseSources = caseSourcesFromItems(allItems, item.sources?.length ?? 0).filter((source) => availableIds.has(source.sourceId));
  const knowledge = preceding.filter(entry => entry.kind === "tool" && entry.status === "succeeded" && entry.name.startsWith("knowledge_"));
  const quoteCount = new Set(knowledge.flatMap(entry => entry.kind === "tool" && entry.name === "knowledge_quote" && entry.resultSummary?.kind === "knowledge_quote" && typeof entry.resultSummary.quoteId === "string" ? [entry.resultSummary.quoteId] : [])).size;
  const quoteLocations = new Map<string, { threadId: string; sourceName: string; first: number; last: number }>();
  for (const entry of knowledge) {
    if (entry.kind !== "tool" || entry.name !== "knowledge_quote") continue;
    const result = entry.resultSummary;
    if (result?.kind !== "knowledge_quote" || typeof result.quoteId !== "string" || typeof result.threadId !== "string" || (threadId && result.threadId !== threadId)
      || typeof result.sourceName !== "string" || typeof result.lineStart !== "number" || typeof result.lineEnd !== "number"
      || !Number.isInteger(result.lineStart) || !Number.isInteger(result.lineEnd) || result.lineStart < 1 || result.lineEnd < result.lineStart) continue;
    quoteLocations.set(result.quoteId, {threadId: result.threadId, sourceName: result.sourceName, first: result.lineStart, last: result.lineEnd});
  }
  const sourceNames:Record<string,string>={law_read:'法规读取',read_file:'文件读取',knowledge_read:'知识库读取',litigation_material_inspect:'案件材料读取'};
  const pluginIds = threadId ? preceding.filter(entry => entry.kind === 'tool' && entry.status === 'succeeded' && (entry.name.startsWith('mcp__') || entry.name === 'law_read')).map(entry => entry.id) : [];
  const pluginKey = JSON.stringify(pluginIds);
  const [pluginLinks, setPluginLinks] = useState<Record<string, {title:string;url:string} | null>>({});
  useEffect(() => {
    let cancelled = false;
    setPluginLinks({});
    if (threadId) for (const id of JSON.parse(pluginKey) as string[]) {
      void runtimeRequest<{links?: {title:string;url:string}[]}>(`/v1/threads/${encodeURIComponent(threadId)}/sources/${encodeURIComponent(id)}`)
        .then(result => {
          if (!cancelled) setPluginLinks(current => ({...current, [id]:result.links?.length === 1 ? result.links[0] : null}));
        }).catch(() => { if (!cancelled) setPluginLinks(current => ({...current, [id]:null})); });
    }
    return () => { cancelled = true; };
  }, [threadId, pluginKey]);
  const toolSources=threadId ? preceding.filter((entry): entry is Extract<TimelineItem,{kind:'tool'}> => entry.kind==='tool' && entry.status==='succeeded' && (entry.name in sourceNames || entry.name.startsWith('mcp__'))).map((entry,i)=>({sourceId:entry.id,title:pluginLinks[entry.id]?.title ?? sourceNames[entry.name] ?? '插件来源',plugin:entry.name.startsWith('mcp__') || entry.name === 'law_read',url:pluginLinks[entry.id]?.url,hidden:(entry.name.startsWith('mcp__') || entry.name === 'law_read') && !pluginLinks[entry.id],unavailable:legal && pluginLinks[entry.id] === null,number:(item.sources?.length ?? 0)+caseSources.length+quoteLocations.size+i+1})) : [];
  return (
    <div className={`turn-narrative${process ? " turn-narrative-process" : ""}`}>
      {!process && actionsEnabled && messageText(item) && <MessageActions text={visibleAssistantText(item.segments.filter(s => s.type === "text").map(s => s.text).join(""), item.isStreaming)} createdAt={item.createdAt} onRegenerate={onRegenerate} onConvertOpinion={!item.isStreaming ? onConvertOpinion : undefined} />}
      {knowledge.length > 0 && <p className="knowledge-analysis-label">模型分析 · {quoteCount > 0 ? `已生成 ${quoteCount} 张原文卡，原文及位置见卡片` : "尚未生成原文卡"}</p>}
      {quoteLocations.size > 0 && <div className="knowledge-locations" aria-label="程序核对的原文位置">
        {[...quoteLocations].map(([quoteId, location]) => <button key={quoteId} type="button" className="knowledge-location" onClick={() => {
          const card = document.getElementById(`knowledge-quote-${location.threadId}-${quoteId}`);
          card?.scrollIntoView({block: "center", behavior: "smooth"});
          card?.focus({preventScroll: true});
        }}>{location.sourceName} · 第 {location.first === location.last ? location.first : `${location.first}–${location.last}`} 行</button>)}
      </div>}
      {item.segments
        .filter((segment) => segment.type === "text" && segment.text.trim())
        .map((segment, index) => (
          <div className="turn-narrative-text" key={index}>
            <MarkdownBody
              text={(segment as { text: string }).text}
              sources={item.sources}
              caseSources={caseSources.length ? caseSources : undefined}
              knowledgeSources={quoteLocations.size ? [...quoteLocations].map(([quoteId,location],i)=>({sourceId:quoteId,cardId:`knowledge-quote-${location.threadId}-${quoteId}`,title:location.sourceName,number:(item.sources?.length ?? 0)+caseSources.length+i+1})) : undefined}
              toolSources={toolSources.length ? toolSources : undefined}
              threadId={threadId}
              streaming={item.isStreaming}
            />
            {segment.isStreaming && (
              <span className="streaming-cursor" aria-hidden="true">▋</span>
            )}
          </div>
        ))}
      {item.isStreaming ? null : (
        <SourcesBlock sources={item.sources ?? []} citationWarning={item.citationWarning} />
      )}
    </div>
  );
}

/**
 * Codex-style turn folding: one instruction is one log. The header shows the
 * elapsed time; collapsed it hides everything but the final answer, expanded
 * it reads as a plain work log — narration paragraphs flowing between quiet
 * single-line steps and one merged thinking disclosure.
 */
export function TurnGroup({
  items,
  threadId,
  caseContextItems,
  active,
  startedAt,
  endedAt,
  artifacts,
  renderDeliverables,
  renderItem,
  presentation = "chat",
  regenerateItemId, onRegenerate, onConvertOpinion, actionsEnabled = true,
}: TurnGroupProps) {
  const preferences=useUiPreferences();
  const [processOverride,setProcessOverride]=useState<boolean|null>(null);
  useEffect(()=>setProcessOverride(null),[preferences.executionDetail]);
  const hasAnswer = !reasoningText(items).streaming && items.some(item => item.kind === "assistant" && messageText(item).length > 0);
  const [answerStarted, setAnswerStarted] = useState(hasAnswer);
  if (hasAnswer !== answerStarted) {
    setAnswerStarted(hasAnswer);
    if (hasAnswer) setProcessOverride(false);
  }
  const showProcess=!active||(processOverride??(!hasAnswer && preferences.executionDetail==="detailed"));
  const legal = presentation === "legal";
  const originalItems = items;
  // In legal results, successful searches are transport details, not result cards.
  items = legal ? orderLegalCards(items.filter((item) => !(item.kind === "tool" && item.name === "law_search" && item.status === "succeeded"))) : items;
  const caseHistory = caseContextItems ?? items;
  const caseRows = distinctCaseRows(caseHistory);
  items = items.flatMap((item) => {
    const rows = caseRows.get(item.id);
    if (item.kind !== "tool" || !rows) return [item];
    if (rows.length === 0 && Array.isArray(item.resultSummary?.cases) && item.resultSummary.cases.length > 0) return [];
    return [{ ...item, resultSummary: { ...item.resultSummary, cases: rows } }];
  });
  const issues = items.filter((item) => item.kind === "tool"
    && item.name !== "run_background_command"
    && ["failed", "denied", "cancelled"].includes(item.status));
  const issueIds = new Set(issues.map(item => item.id));
  const renderVisibleItem = (item: TimelineItem) => {
    if (!issueIds.has(item.id)) return renderItem(item);
    return item.id === issues[0]?.id
      ? <ToolIssues key="tool-issues" items={issues} renderItem={renderItem} hasAnswer={hasAnswer} />
      : null;
  };
  const [open, setOpen] = useState(active);
  const [wasActive, setWasActive] = useState(active);
  const [now, setNow] = useState(() => Date.now());
  if (wasActive !== active) {
    setWasActive(active);
    if (!active) setOpen(false);
  }
  useEffect(() => {
    if (!active || startedAt === undefined) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, startedAt]);

  const durationLabel =
    startedAt === undefined
      ? null
      : `用时 ${formatDuration((endedAt ?? (active ? now : endedAt ?? startedAt)) - startedAt)}`;

  if (active || open) {
    const thinking = reasoningText(items);
    return (
      <div className="turn-group turn-group-open" data-turn-active={active}>
        {!legal && (
          <button
            type="button"
            className="turn-duration-toggle"
            aria-expanded={showProcess}
            onClick={() => active ? setProcessOverride(!showProcess) : setOpen(false)}
          >
            <span className="reasoning-chevron" aria-hidden="true">▾</span>
            {durationLabel} {active ? (showProcess ? "收起执行过程" : "查看执行过程") : "收起"}
          </button>
        )}
        {!legal && showProcess && thinking.text && (
          <ReasoningRow text={thinking.text} streaming={thinking.streaming} />
        )}
        {items.map((item) => {
          if (item.kind === "assistant") {
            const position = originalItems.findIndex(entry => entry.id === item.id);
            const process = originalItems.slice(position + 1).some(entry => entry.kind === "assistant" || entry.kind === "tool");
            return <AssistantNarrative key={item.id} item={item} process={process} legal={presentation === 'legal'} threadId={threadId} actionsEnabled={actionsEnabled} onConvertOpinion={onConvertOpinion} onRegenerate={item.id === regenerateItemId ? onRegenerate : undefined} allItems={caseHistory} />;
          }
          if (item.kind === "tool") {
            // Approvals, failures, web results and law statute cards keep
            // their full interactive cards; everything else is a quiet line
            // (hidden entirely in the legal presentation).
            if (
              !isWebTool(item)
              && !isLawTool(item)
              && !(legal && item.name.startsWith("mcp__"))
              && item.status !== "awaiting_approval"
              && item.status !== "failed"
              && item.status !== "denied"
              && item.status !== "cancelled"
              && item.name !== "knowledge_quote"
              && item.name !== "run_background_command"
            ) {
              if (legal || (!showProcess && (hasAnswer || item.status === "succeeded"))) return null;
              return (
                <div
                  key={item.id}
                  className={`tool-step-line${item.status === "running" ? " tool-step-running" : ""}`}
                >
                  {toolStepLine(item)}
                </div>
              );
            }
            return renderVisibleItem(item);
          }
          return renderItem(item);
        })}
        {renderDeliverables && artifacts && artifacts.length > 0
          ? renderDeliverables(artifacts)
          : null}
      </div>
    );
  }

  const lastAssistant = [...items]
    .reverse()
    .find((item): item is AssistantItem => item.kind === "assistant");

  return (
    <div className="turn-group" data-turn-active={active}>
      {!legal && durationLabel && (
        <button
          type="button"
          className="turn-duration-toggle"
          aria-expanded="false"
          onClick={() => setOpen(true)}
        >
          <span className="reasoning-chevron" aria-hidden="true">▸</span>
          {durationLabel}
        </button>
      )}
      {items
        .filter((item) => item.kind === "notice")
        .map((item) => renderVisibleItem(item))}
      {legal
        ? items
            .filter(
              (item) =>
                item.kind === "tool" &&
                (isLawTool(item) || item.name.startsWith("mcp__")) &&
                (item.status === "succeeded" || item.status === "failed"),
            )
            .map((item) => renderVisibleItem(item))
        : null}
      {items.filter(item => item.kind === "tool" && (
        ["awaiting_approval","failed","denied","cancelled"].includes(item.status)
        || item.name === "run_background_command"
      ) && !(legal && (isLawTool(item) || item.name.startsWith("mcp__")) && item.status === "failed")).map(renderVisibleItem)}
      {items.filter(item => item.kind === "tool" && item.name === "knowledge_quote" && item.status === "succeeded").map(renderItem)}
      {lastAssistant && <AssistantNarrative item={lastAssistant} legal={presentation === 'legal'} threadId={threadId} actionsEnabled={actionsEnabled} onConvertOpinion={onConvertOpinion} onRegenerate={lastAssistant.id === regenerateItemId ? onRegenerate : undefined} allItems={caseHistory} />}
      {renderDeliverables && artifacts && artifacts.length > 0
        ? renderDeliverables(artifacts)
        : null}
    </div>
  );
}

function ReasoningRow({ text, streaming }: { text: string; streaming: boolean }) {
  const [open, setOpen] = useState(streaming);
  const [wasStreaming, setWasStreaming] = useState(streaming);
  if (wasStreaming !== streaming) {
    setWasStreaming(streaming);
    if (!streaming) setOpen(false);
  }
  return (
    <div className="reasoning-block">
      <button
        type="button"
        className="reasoning-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="reasoning-chevron" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        {streaming && !text
          ? "正在深度思考…"
          : streaming
            ? `正在深度思考…（已 ${text.length} 字）`
            : `已深度思考 · ${text.length} 字`}
      </button>
      {open && (
        <div className="reasoning-text">
          {text || "…"}
          {streaming && <span className="streaming-cursor" aria-hidden="true">▋</span>}
        </div>
      )}
    </div>
  );
}

/** Keep retry noise out of the conversation, without losing any error detail. */
function ToolIssues({items, renderItem, hasAnswer}: {items: TimelineItem[]; renderItem: (item: TimelineItem) => ReactNode; hasAnswer: boolean}) {
  const [open, setOpen] = useState(false);
  const [answerStarted, setAnswerStarted] = useState(hasAnswer);
  if (hasAnswer !== answerStarted) {
    setAnswerStarted(hasAnswer);
    if (hasAnswer) setOpen(false);
  }
  const counts = (["failed", "denied", "cancelled"] as const).map((status, index) => {
    const count = items.filter(item => item.kind === "tool" && item.status === status).length;
    return count ? `${count} 次${["失败", "拒绝", "取消"][index]}` : null;
  }).filter(Boolean).join(" · ");
  return <div className="tool-issues">
    <button type="button" className="turn-duration-toggle" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <span aria-hidden="true">{open ? "▾" : "▸"}</span>执行记录 · {counts}
    </button>
    {open && <div className="tool-issues-details">{items.map(renderItem)}</div>}
  </div>;
}
