import type { ToolItem } from "./items";

const WORK_NAMES: Record<string, string> = {
  facts: "事实梳理", evidence: "证据整理", issues: "争议焦点",
  strategy: "策略备忘", documents: "文书草稿",
};

export interface LitigationApprovalPreview {
  kind: string;
  scope: "product" | "section";
  title: string;
  expected_revision: number;
  section_count: number;
  sections: {
    title: string;
    body: string;
    sources: { path: string; page: number | null; quote: string }[];
    pending: string[];
  }[];
  truncated: boolean;
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

function text(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length <= max;
}

function integer(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

/** Only consume the Runtime's separate, bounded review projection. Never try
 * to recover a payload_json that the privacy layer removed or truncated. */
export function litigationApprovalPreview(item: ToolItem): LitigationApprovalPreview | null {
  if (item.name !== "litigation_work_save") return null;
  const value = record(item.argumentsSummary?.work_preview);
  if (!value || typeof value.kind !== "string" || !Object.hasOwn(WORK_NAMES, value.kind)
      || !["product", "section"].includes(String(value.scope))
      || !text(value.title, 300) || !integer(value.expected_revision)
      || !integer(value.section_count) || value.section_count > 300
      || !Array.isArray(value.sections) || value.sections.length > 300
      || value.sections.length > value.section_count
      || typeof value.truncated !== "boolean") return null;
  let characters = value.title.length;
  const sections: LitigationApprovalPreview["sections"] = [];
  for (const sectionValue of value.sections) {
    const section = record(sectionValue);
    if (!section || !text(section.title, 300) || !text(section.body, 20_000)
        || !Array.isArray(section.sources) || section.sources.length > 30
        || !Array.isArray(section.pending) || section.pending.length > 30
        || !section.pending.every(pending => text(pending, 8_000))) return null;
    const sources: LitigationApprovalPreview["sections"][number]["sources"] = [];
    characters += section.title.length + section.body.length;
    for (const sourceValue of section.sources) {
      const source = record(sourceValue);
      if (!source || !text(source.path, 500) || !text(source.quote, 4_000)
          || !(source.page === null || (integer(source.page) && source.page >= 1 && source.page <= 10_000))) return null;
      sources.push({path: source.path, page: source.page, quote: source.quote});
      characters += source.path.length + source.quote.length;
    }
    const pending = section.pending as string[];
    characters += pending.reduce((count, entry) => count + entry.length, 0);
    if (characters > 32_768) return null;
    sections.push({title: section.title, body: section.body, sources, pending});
  }
  return {...value, sections} as unknown as LitigationApprovalPreview;
}

export function litigationWorkName(kind: string): string {
  return Object.hasOwn(WORK_NAMES, kind) ? WORK_NAMES[kind] : "工作台成果";
}
