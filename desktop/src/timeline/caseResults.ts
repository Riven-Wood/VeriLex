import type { ToolItem } from "./items";

export interface CaseResultSummary {
  sourceId: string; title: string; sourceName?: string; fetchedAt?: string; caseNumber: string | null; court: string | null;
  cause: string | null; judgedAt: string | null; gist: string | null;
  excerpt: string | null; level: string | null;
}
export interface CaseResultsSummary {
  kind: "case_results"; cases: CaseResultSummary[]; count: number; truncated: boolean;
  sourceName: string; fetchedAt: string;
}
function text(value: unknown, max: number): value is string {
  return typeof value === "string" && !!value.trim() && value.length <= max && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value);
}
export function caseResultSummary(item: Pick<ToolItem, "resultSummary">): CaseResultsSummary | undefined {
  const value = item.resultSummary;
  if (!value || value.kind !== "case_results" || !Array.isArray(value.cases) || value.cases.length > 20 ||
      !text(value.sourceName, 128) || !text(value.fetchedAt, 64) || typeof value.count !== "number" ||
      !Number.isInteger(value.count) || value.count < value.cases.length || value.count > 1_000_000_000 || typeof value.truncated !== "boolean") return undefined;
  const limits: Record<string, number> = { caseNumber: 64, court: 128, cause: 128, judgedAt: 32, gist: 600, excerpt: 600, level: 64 };
  for (const row of value.cases) {
    if (!row || typeof row !== "object" || !text(row.sourceId, 400) || !text(row.title, 120)) return undefined;
    for (const [key, limit] of Object.entries(limits)) if (row[key] !== null && !text(row[key], limit)) return undefined;
  }
  return value as unknown as CaseResultsSummary;
}
