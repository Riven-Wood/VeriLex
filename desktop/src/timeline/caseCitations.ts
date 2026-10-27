import type { TimelineItem } from "./items";
import { caseResultSummary, type CaseResultSummary } from "./caseResults";

export interface CaseCitationSource {
  number: number; sourceId: string; cardId: string; title: string;
  sourceName: string; fetchedAt: string; truncated: boolean;
}
/** DOM IDs derive from local tool identity plus the returned case identity; no URL. */
export function caseCardId(toolId: string, caseId: string): string {
  return `case-card-${encodeURIComponent(toolId)}-${encodeURIComponent(caseId)}`;
}
/** Newer nonempty fields enrich the first card; its anchor stays stable. */
function aggregate(items: readonly TimelineItem[]) {
  const entries = new Map<string, { toolId: string; row: CaseResultSummary; truncated: boolean }>();
  for (const item of items) {
    if (item.kind !== "tool" || item.status !== "succeeded") continue;
    const summary = caseResultSummary(item);
    if (!summary) continue;
    for (const row of summary.cases) {
      const previous = entries.get(row.sourceId);
      const merged = previous ? { ...previous.row } : { ...row };
      for (const key of ["title", "caseNumber", "court", "cause", "judgedAt", "gist", "excerpt", "level"] as const) {
        if (row[key] !== null) merged[key] = row[key];
      }
      merged.sourceName = summary.sourceName;
      merged.fetchedAt = summary.fetchedAt;
      entries.set(row.sourceId, { toolId: previous?.toolId ?? item.id, row: merged, truncated: summary.truncated });
    }
  }
  return entries;
}
export function caseSourcesFromItems(items: readonly TimelineItem[], offset = 0): CaseCitationSource[] {
  return [...aggregate(items).values()].map(({ toolId, row, truncated }, index) => ({
    number: offset + index + 1, sourceId: row.sourceId, cardId: caseCardId(toolId, row.sourceId),
    title: row.title, sourceName: row.sourceName!, fetchedAt: row.fetchedAt!, truncated,
  }));
}
export function distinctCaseRows(items: readonly TimelineItem[]): Map<string, CaseResultSummary[]> {
  const result = new Map<string, CaseResultSummary[]>();
  for (const item of items) if (item.kind === "tool" && item.status === "succeeded" && caseResultSummary(item)) result.set(item.id, []);
  for (const entry of aggregate(items).values()) result.get(entry.toolId)!.push(entry.row);
  return result;
}
export function focusCaseSource(source: CaseCitationSource): boolean {
  const target = document.getElementById(source.cardId);
  if (!target) return false;
  for (const details of target.querySelectorAll("details")) details.open = true;
  target.scrollIntoView?.({ block: "center", behavior: "auto" });
  target.focus();
  return true;
}
export type CaseCitationPart = { kind: "text"; text: string } | { kind: "case"; source: CaseCitationSource };
/** Unknown ids stay unverified. Never derive a destination from assistant text. */
export function renderCaseCitationText(text: string, sources: readonly CaseCitationSource[], streaming = false): CaseCitationPart[] {
  const parts: CaseCitationPart[] = [];
  const known = new Map(sources.map((source) => [source.sourceId, source]));
  const regex = /\[\[case:([^\]\n]{1,400})\]\]/g;
  let cursor = 0;
  for (const match of text.matchAll(regex)) {
    if (match.index! > cursor) parts.push({ kind: "text", text: text.slice(cursor, match.index) });
    const source = known.get(match[1]);
    if (!streaming) parts.push(source ? { kind: "case", source } : { kind: "text", text: "〔案例引用未验证〕" });
    cursor = match.index! + match[0].length;
  }
  let tail = text.slice(cursor);
  if (streaming) tail = tail.replace(/\[\[(?:c(?:a(?:s(?:e(?::[^\]]*)?)?)?)?)?$/, "");
  if (tail) parts.push({ kind: "text", text: tail });
  return parts;
}
