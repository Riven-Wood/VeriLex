import type { ToolItem } from "./items";

export interface LawSearchRowSummary {
  bbbs: string;
  title: string;
  flxz: string;
  zdjg: string;
  effectivity: string;
  gbrq: string;
  sxrq: string;
}

export interface LawSearchSummary {
  kind: "law_search";
  rows: LawSearchRowSummary[];
  total: number;
  page: number;
}

export interface LawReadSummary {
  kind: "law_read";
  bbbs: string;
  title: string;
  clause?: string;
  heading?: string;
  clauseText?: string[];
  before?: string[];
  after?: string[];
  effectivity: string;
  gbrq: string;
  sxrq: string;
  fetchedAt: string;
  cached: boolean;
  truncated: boolean;
  source: string;
}

export type LawResultSummary = LawSearchSummary | LawReadSummary;

function boundedText(value: unknown, maximum: number): string | null {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value.length > maximum ||
    [...value].some(
      (character) =>
        character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    )
  ) {
    return null;
  }
  return value;
}

function parseRow(value: unknown): LawSearchRowSummary | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  const title = boundedText(record.title, 512);
  const bbbs = boundedText(record.bbbs, 64);
  const effectivity = boundedText(record.effectivity, 32);
  if (title === null || bbbs === null || effectivity === null) return null;
  if (
    typeof record.flxz !== "string" ||
    record.flxz.length > 64 ||
    typeof record.zdjg !== "string" ||
    record.zdjg.length > 128 ||
    typeof record.gbrq !== "string" ||
    record.gbrq.length > 32 ||
    typeof record.sxrq !== "string" ||
    record.sxrq.length > 32
  ) {
    return null;
  }
  return {
    bbbs,
    title,
    flxz: record.flxz,
    zdjg: record.zdjg,
    effectivity,
    gbrq: record.gbrq,
    sxrq: record.sxrq,
  };
}

function parseWindow(values: unknown, clause = false): string[] | null {
  if (!Array.isArray(values) || values.length > (clause ? 200 : 3)) return null;
  const bounded: string[] = [];
  for (const value of values) {
    const text = boundedText(value, clause ? 12_000 : 2_000);
    if (text === null) return null;
    bounded.push(text);
  }
  if (clause && bounded.reduce((size, text) => size + text.length, 0) > 12_000) return null;
  return bounded;
}

/** Parse the engine-provided structured law summary; undefined for others. */
export function lawResultSummary(item: ToolItem): LawResultSummary | undefined {
  if (item.name !== "law_search" && item.name !== "law_read") return undefined;
  const summary = item.resultSummary;
  if (typeof summary !== "object" || summary === null) return undefined;

  if (summary.kind === "law_search") {
    if (!Array.isArray(summary.rows) || summary.rows.length > 10) return undefined;
    if (
      typeof summary.total !== "number" ||
      typeof summary.page !== "number"
    ) {
      return undefined;
    }
    const rows: LawSearchRowSummary[] = [];
    for (const value of summary.rows) {
      const row = parseRow(value);
      if (row === null) return undefined;
      rows.push(row);
    }
    return { kind: "law_search", rows, total: summary.total, page: summary.page };
  }

  if (summary.kind !== "law_read") return undefined;
  const title = boundedText(summary.title, 512);
  const bbbs = boundedText(summary.bbbs, 64);
  const effectivity = boundedText(summary.effectivity, 32);
  const fetchedAt = boundedText(summary.fetchedAt, 64);
  const source = boundedText(summary.source, 64);
  if (
    title === null ||
    bbbs === null ||
    effectivity === null ||
    fetchedAt === null ||
    source === null ||
    typeof summary.cached !== "boolean" ||
    typeof summary.truncated !== "boolean" ||
    typeof summary.gbrq !== "string" ||
    summary.gbrq.length > 32 ||
    typeof summary.sxrq !== "string" ||
    summary.sxrq.length > 32
  ) {
    return undefined;
  }
  const read: LawReadSummary = {
    kind: "law_read",
    bbbs,
    title,
    effectivity,
    gbrq: summary.gbrq,
    sxrq: summary.sxrq,
    fetchedAt,
    cached: summary.cached,
    truncated: summary.truncated,
    source,
  };
  if (typeof summary.clause === "string") {
    const clause = boundedText(summary.clause, 32);
    const heading =
      typeof summary.heading === "string" && summary.heading.length <= 128
        ? summary.heading
        : "";
    const clauseText = parseWindow(summary.clauseText, true);
    const before = parseWindow(summary.before);
    const after = parseWindow(summary.after);
    if (clause === null || clauseText === null || before === null || after === null) {
      return undefined;
    }
    read.clause = clause;
    read.heading = heading;
    read.clauseText = clauseText;
    read.before = before;
    read.after = after;
  }
  return read;
}
