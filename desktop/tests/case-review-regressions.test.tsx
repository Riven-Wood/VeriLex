import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { TurnGroup } from "../src/components/timeline/TurnGroup";
import { ToolCallCard } from "../src/components/timeline/ToolCallCard";
import type { TimelineItem, ToolItem } from "../src/timeline/items";
afterEach(cleanup);
const tool: ToolItem = { kind: "tool", id: "case-tool-first", turnId: "first", name: "mcp__case", status: "succeeded", resultSummary: {
  kind: "case_results", cases: [{ sourceId: "actual-returned-id", title: "虚构示例案件", caseNumber: null, court: null, cause: null, judgedAt: null, gist: null, excerpt: "测试片段", level: null }],
  count: 1, truncated: false, sourceName: "元典智库", fetchedAt: "2026-09-07T00:00:00Z",
} };
const renderItem = (item: TimelineItem) => item.kind === "tool" ? <ToolCallCard key={item.id} item={item} presentation="legal" /> : null;
test("MCP business failure uses bounded reason metadata and never renders a case card", () => {
  render(<ToolCallCard item={{ ...tool, status: "failed", resultSummary: { reasonCode: "mcp_tool_error", byteCount: 200 } }} presentation="legal" />);
  expect(screen.getByRole("alert").textContent).toContain("插件返回执行失败");
  expect(screen.queryByRole("article")).toBeNull();
});
test("an unrecognized case response shows a fallback explanation without a case card", () => {
  render(<ToolCallCard item={{ ...tool, resultSummary: { kind: "case_unparsed", byteCount: 123 } }} presentation="legal" />);
  expect(screen.getByText(/未能将渠道返回内容整理成案例卡/)).toBeTruthy();
  expect(screen.queryByRole("article")).toBeNull();
});
test("review: a follow-up must be able to cite the case already returned in this conversation", () => {
  render(<>
    <TurnGroup caseContextItems={[tool]} items={[tool]} active={false} presentation="legal" renderItem={renderItem} />
    <TurnGroup caseContextItems={[tool]} items={[{ kind: "assistant", id: "followup-answer", turnId: "followup", isStreaming: false, segments: [{type: "text", text: "沿用刚才的案例 [[case:actual-returned-id]]", isStreaming: false}] }]} active={false} presentation="legal" renderItem={renderItem} />
  </>);
  expect(screen.getByRole("article", {name:"案例：虚构示例案件"})).toBeTruthy();
  expect(screen.queryByRole("button", {name:"案例来源 1：虚构示例案件"})).toBeTruthy();
});

function answer(id: string, turnId: string): TimelineItem {
  return { kind: "assistant", id, turnId, isStreaming: false,
    segments: [{ type: "text", text: "案例 [[case:actual-returned-id]]", isStreaming: false }] };
}

test("review: switching conversations drops the previous case citation directory", () => {
  const message = answer("reply-a", "reply-a");
  const { rerender } = render(<TurnGroup items={[message]} caseContextItems={[tool, message]} active={false} presentation="legal" renderItem={renderItem} />);
  expect(screen.getByRole("button", { name: "案例来源 1：虚构示例案件" })).toBeTruthy();
  const nextMessage = answer("reply-b", "reply-b");
  rerender(<TurnGroup items={[nextMessage]} caseContextItems={[nextMessage]} active={false} presentation="legal" renderItem={renderItem} />);
  expect(screen.queryByRole("button", { name: "案例来源 1：虚构示例案件" })).toBeNull();
  expect(screen.getByText(/案例引用未验证/)).toBeTruthy();
});

test("review: later turn details enrich the first card without a duplicate card", () => {
  const details: ToolItem = { ...tool, id: "case-tool-details", turnId: "later", resultSummary: {
    ...tool.resultSummary, fetchedAt: "2026-09-07T01:00:00Z",
    cases: [{ ...(tool.resultSummary!.cases as Array<Record<string, unknown>>)[0], gist: "后续读取的裁判要旨" }],
  } };
  const history = [tool, details];
  render(<>
    <TurnGroup items={[tool]} caseContextItems={history} active={false} presentation="legal" renderItem={renderItem} />
    <TurnGroup items={[details]} caseContextItems={history} active={false} presentation="legal" renderItem={renderItem} />
  </>);
  expect(screen.getAllByRole("article", { name: "案例：虚构示例案件" })).toHaveLength(1);
  expect(screen.getByText("后续读取的裁判要旨")).toBeTruthy();
  expect(screen.getByText("2026-09-07T01:00:00Z")).toBeTruthy();
});

test("review: a later retrieval must not retroactively verify an earlier invented citation", () => {
  const early = answer("early-answer", "early-turn");
  render(<TurnGroup items={[early]} caseContextItems={[early, tool]} active={false} presentation="legal" renderItem={renderItem} />);
  expect(screen.queryByRole("button", { name: "案例来源 1：虚构示例案件" })).toBeNull();
  expect(screen.getByText(/案例引用未验证/)).toBeTruthy();
});
