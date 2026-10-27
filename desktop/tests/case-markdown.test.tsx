import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { MarkdownBody } from "../src/components/timeline/MarkdownBody";
import { CaseCard } from "../src/components/timeline/CaseCard";
import { caseCardId, caseSourcesFromItems } from "../src/timeline/caseCitations";
import type { CaseResultSummary } from "../src/timeline/caseResults";
import type { ToolItem } from "../src/timeline/items";
afterEach(cleanup);
const row: CaseResultSummary = { sourceId: "returned-id", title: "虚构示例案件", caseNumber: null, court: null, cause: null, judgedAt: null, gist: null, excerpt: "真实返回位置的测试文字", level: null };
const item: ToolItem = { kind: "tool", id: "tool-id", name: "mcp__example", status: "succeeded", resultSummary: {
  kind: "case_results", cases: [row], count: 1, truncated: false, sourceName: "示例渠道", fetchedAt: "2026-09-07T00:00:00Z",
} };
test("numbered case marker renders button that expands the real local card", () => {
  render(<><MarkdownBody text="案件比较 **结论** [[case:returned-id]]" caseSources={caseSourcesFromItems([item])} />
    <CaseCard id={caseCardId(item.id, row.sourceId)} summary={row} sourceName="示例渠道" fetchedAt="2026-09-07T00:00:00Z" /></>);
  const card = screen.getByRole("article"); card.scrollIntoView = vi.fn();
  fireEvent.click(screen.getByRole("button", { name: "案例来源 1：虚构示例案件" }));
  expect(card.querySelector("details")!.open).toBe(true);
  expect(document.activeElement).toBe(card);
  expect(screen.queryByRole("link")).toBeNull();
});
test("invented marker cannot produce verified case button", () => {
  render(<MarkdownBody text="[[case:invented-id]]" caseSources={caseSourcesFromItems([item])} />);
  expect(screen.getByText("〔案例引用未验证〕")).toBeTruthy(); expect(screen.queryByRole("button")).toBeNull();
});
