import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { CaseCard } from "../src/components/timeline/CaseCard";
import { caseResultSummary, type CaseResultSummary } from "../src/timeline/caseResults";
afterEach(cleanup);
const row: CaseResultSummary = { sourceId: "synthetic", title: "虚构示例案件", caseNumber: null, court: null, cause: null, judgedAt: null, gist: null, excerpt: "渠道片段", level: null };
test("missing fields are honest and generic excerpts are not called gist", () => {
  render(<CaseCard summary={row} sourceName="示例插件" fetchedAt="2026-09-07T00:00:00Z" />);
  expect(screen.getByText("未能确认案号")).toBeTruthy(); expect(screen.queryByText("裁判要旨")).toBeNull();
  expect(screen.getByText("查看渠道返回片段")).toBeTruthy(); expect(screen.queryByText("法院")).toBeNull();
});
test("summary validates all event fields before rendering", () => {
  const value = { kind: "case_results", cases: [row], sourceName: "示例插件", fetchedAt: "2026-09-07T00:00:00Z", count: 1, truncated: false };
  expect(caseResultSummary({ resultSummary: value })).toBeTruthy();
  expect(caseResultSummary({ resultSummary: { ...value, cases: [{ ...row, title: "" }] } })).toBeUndefined();
  expect(caseResultSummary({ resultSummary: { ...value, count: -1 } })).toBeUndefined();
  expect(caseResultSummary({ resultSummary: { ...value, cases: [{ ...row, level: { text: "指导性案例" } }] } })).toBeUndefined();
});
