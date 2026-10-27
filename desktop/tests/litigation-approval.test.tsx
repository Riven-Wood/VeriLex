import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ToolCallCard } from "../src/components/timeline/ToolCallCard";
import { litigationApprovalPreview } from "../src/timeline/litigationApproval";
import type { ToolItem } from "../src/timeline/items";
import type { AgentEvent } from "../src/runtime/contracts";
import { applyTimelineEvent, initialTimeline } from "../src/timeline/reducer";
import { buildTimelineHistory } from "../src/timeline/history";

afterEach(cleanup);

const preview = {
  kind: "evidence", scope: "section", title: "审计意见核对", expected_revision: 1,
  section_count: 1, truncated: false,
  sections: [{title: "审计报告", body: "70亿元与40亿元不能直接推出20亿元损失，应分别核对口径。",
    sources: [{path: "模拟案卷.pdf", page: 45, quote: "损失20亿元"}], pending: ["损失计算口径待核实"]}],
};
function item(workPreview: unknown = preview): ToolItem {
  return {kind: "tool", id: "save", turnId: "turn", approvalId: "approval", name: "litigation_work_save",
    status: "awaiting_approval", argumentsSummary: {expected_revision: 1, work_preview: workPreview}};
}

test("a save approval lets the lawyer review the bound item, version, content and sources", () => {
  const resolve = vi.fn();
  const {container} = render(<ToolCallCard item={item()} onResolve={resolve} />);
  expect(screen.getByText("保存工作台成果")).toBeDefined();
  expect(screen.getByText("证据整理 · 审计意见核对")).toBeDefined();
  expect(screen.getByText(/只修改当前讨论的条目/)).toBeDefined();
  expect(screen.getByText(/第 1 版 → 第 2 版，旧版保留/)).toBeDefined();
  expect(screen.getByText(/70亿元与40亿元/)).toBeDefined();
  expect(screen.getByText("损失计算口径待核实")).toBeDefined();
  fireEvent.click(screen.getByText("出处与摘录（1）"));
  expect(screen.getByText("模拟案卷.pdf · PDF 第 45 页")).toBeDefined();
  expect(container.textContent).not.toContain("expected_revision");
  expect(container.textContent).not.toContain("payload_json");
  fireEvent.click(screen.getByRole("button", {name: "保存这次修改"}));
  expect(resolve).toHaveBeenCalledWith("allowed", "");
});

test("full product and truncated previews describe exactly what is visible", () => {
  render(<ToolCallCard item={item({...preview, scope: "product", expected_revision: 0, section_count: 20, truncated: true})} onResolve={vi.fn()} />);
  expect(screen.getByText(/更新整份成果，共 20 个条目/)).toBeDefined();
  expect(screen.getByText("首次保存为第 1 版。")).toBeDefined();
  expect(screen.getByText(/这里只展示部分/)).toBeDefined();
});

test("legacy approvals do not attempt to reveal truncated or redacted raw JSON", () => {
  const legacy = item(undefined);
  legacy.argumentsSummary = {expected_revision: 3, payload_json: '{"title":"never expose raw JSON"…'};
  render(<ToolCallCard item={legacy} onResolve={vi.fn()} />);
  expect(screen.getByText(/没有可审阅的内容预览/)).toBeDefined();
  expect(document.body.textContent).not.toContain("never expose");
  expect(screen.getByText(/第 3 版 → 第 4 版/)).toBeDefined();
});

test("malformed or unbounded projections fail closed while review text remains plain", () => {
  expect(litigationApprovalPreview(item({...preview, expected_revision: true}))).toBeNull();
  expect(litigationApprovalPreview(item({...preview, kind: "unknown"}))).toBeNull();
  expect(litigationApprovalPreview(item({...preview, kind: "__proto__"}))).toBeNull();
  expect(litigationApprovalPreview(item({...preview, sections: [{...preview.sections[0], body: "x".repeat(20_001)}]}))).toBeNull();
  const {container} = render(<ToolCallCard item={item({...preview, sections: [{...preview.sections[0], body: '<script>alert(1)</script> [链接](https://example.com)'}]})} />);
  expect(container.querySelector("script, a")).toBeNull();
  expect(container.textContent).toContain("<script>alert(1)</script>");
});

test("pending preview is removed after a decision and disconnected save controls stay locked", () => {
  const {rerender} = render(<ToolCallCard item={item()} onResolve={vi.fn()} connectionReady={false} />);
  expect(screen.getByRole("button", {name: "保存这次修改"}).hasAttribute("disabled")).toBe(true);
  rerender(<ToolCallCard item={{...item(), status: "succeeded"}} onResolve={vi.fn()} />);
  expect(screen.queryByLabelText("本次保存内容")).toBeNull();
});

function approvalEvents(): AgentEvent[] {
  return [
    {type: "turn_started", turnId: "turn", sequence: 1, payload: {}},
    {type: "tool_call_started", turnId: "turn", sequence: 2, payload: {
      name: "litigation_work_save", toolCallId: "save", argumentsSummary: {expected_revision: 1, payload_json: "[内容已脱敏]"},
    }},
    {type: "approval_requested", turnId: "turn", sequence: 3, payload: {
      name: "litigation_work_save", toolCallId: "save", approvalId: "approval",
      argumentsSummary: {expected_revision: 1, work_preview: preview},
    }},
  ];
}

test.each(["live", "history"])("%s event pipeline retains the preview first attached at approval", mode => {
  const events = approvalEvents();
  const state = mode === "live"
    ? events.reduce(applyTimelineEvent, initialTimeline())
    : buildTimelineHistory({messages: [], turns: [{id: "turn", status: "awaiting_approval"}],
      events: events.map(event => ({type: event.type, turn_id: event.turnId, sequence: event.sequence, payload: event.payload}))}).state;
  const tool = state.items.find((entry): entry is ToolItem => entry.kind === "tool");
  expect(tool?.argumentsSummary?.work_preview).toEqual(preview);
  render(<ToolCallCard item={tool!} onResolve={vi.fn()} />);
  expect(screen.getByText("证据整理 · 审计意见核对")).toBeDefined();
  expect(screen.getByText(/70亿元与40亿元/)).toBeDefined();
  expect(screen.queryByText(/没有可审阅的内容预览/)).toBeNull();
});

test("an old approval without a new summary retains its start metadata", () => {
  const events = approvalEvents();
  delete events[2].payload.argumentsSummary;
  const state = events.reduce(applyTimelineEvent, initialTimeline());
  const tool = state.items.find((entry): entry is ToolItem => entry.kind === "tool");
  expect(tool?.argumentsSummary).toEqual({expected_revision: 1, payload_json: "[内容已脱敏]"});
});
