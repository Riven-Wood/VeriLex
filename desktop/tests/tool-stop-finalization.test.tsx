import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { applyTimelineEvent, initialTimeline } from "../src/timeline/reducer";
import { buildTimelineHistory } from "../src/timeline/history";
import { groupTurns } from "../src/timeline/turnGroups";
import { TurnGroup } from "../src/components/timeline/TurnGroup";
import { ToolCallCard } from "../src/components/timeline/ToolCallCard";
import type { AgentEvent } from "../src/runtime/contracts";
import type { ToolItem } from "../src/timeline/items";

afterEach(cleanup);

function events(): AgentEvent[] {
  return [
    {type: "turn_started", turnId: "turn", sequence: 1, payload: {}},
    {type: "tool_call_started", turnId: "turn", sequence: 2, payload: {toolCallId: "saved", name: "litigation_work_save"}},
    {type: "tool_call_completed", turnId: "turn", sequence: 3, payload: {toolCallId: "saved", status: "succeeded"}},
    {type: "tool_call_started", turnId: "turn", sequence: 4, payload: {toolCallId: "preparing", name: "litigation_work_save"}},
    {type: "tool_call_started", turnId: "turn", sequence: 5, payload: {toolCallId: "pending", name: "write_file"}},
    {type: "approval_requested", turnId: "turn", sequence: 6, payload: {toolCallId: "pending", approvalId: "approval"}},
  ];
}

test.each(["turn_cancelled", "turn_failed", "turn_completed"] as const)("%s settles unfinished tools without changing successful saves", type => {
  let state = events().reduce(applyTimelineEvent, initialTimeline());
  state = applyTimelineEvent(state, {type, turnId: "turn", sequence: 7, payload: {errorCode: "turn_limit_exceeded"}});
  const tools = state.items.filter((item): item is ToolItem => item.kind === "tool");
  expect(tools.map(item => item.status)).toEqual(["succeeded", type === "turn_failed" ? "failed" : "cancelled", type === "turn_failed" ? "failed" : "cancelled"]);
  expect(groupTurns(state.items, state.activeTurnId).every(group => group.kind !== "turn" || !group.isActive)).toBe(true);
  render(<TurnGroup items={state.items} active={false} renderItem={item => item.kind === "tool" ? <ToolCallCard item={item} onResolve={vi.fn()} /> : null} />);
  expect(screen.queryByText(/正在保存工作台成果/)).toBeNull();
  expect(screen.queryByRole("button", {name: "保存这次修改"})).toBeNull();
});

test.each(["failed", "cancelled", "interrupted", "completed"])("history with %s status settles tools even when the terminal event was missed", status => {
  const state = buildTimelineHistory({messages: [], turns: [{id: "turn", status}],
    events: events().map(event => ({type: event.type, turn_id: event.turnId, sequence: event.sequence, payload: event.payload}))}).state;
  const tools = state.items.filter((item): item is ToolItem => item.kind === "tool");
  expect(tools.map(item => item.status)).toEqual(["succeeded", status === "failed" ? "failed" : "cancelled", status === "failed" ? "failed" : "cancelled"]);
  expect(groupTurns(state.items, state.activeTurnId).every(group => group.kind !== "turn" || !group.isActive)).toBe(true);
});

test("ending one turn never settles another turn's pending tool", () => {
  let state = events().reduce(applyTimelineEvent, initialTimeline());
  state = applyTimelineEvent(state, {type: "tool_call_started", turnId: "other", sequence: 1, payload: {toolCallId: "other-tool", name: "read_file"}});
  state = applyTimelineEvent(state, {type: "turn_cancelled", turnId: "turn", sequence: 7, payload: {}});
  expect(state.items.find(item => item.kind === "tool" && item.id === "other-tool")).toMatchObject({status: "running"});
});
