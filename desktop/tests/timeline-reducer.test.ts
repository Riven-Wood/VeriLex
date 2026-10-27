import { describe, expect, test } from "vitest";

import type { AgentEvent, AgentEventType } from "../src/runtime/contracts";
import { recoveryKindFor } from "../src/timeline/recovery";
import {
  appendUserMessage,
  applyTimelineEvent,
  bindUserMessageToTurn,
  bufferDeltas,
  initialTimeline,
} from "../src/timeline/reducer";

function event(
  type: AgentEventType,
  turnId: string,
  sequence: number,
  payload: Record<string, unknown> = {},
): AgentEvent {
  return { type, turnId, sequence, payload };
}

function stripped<T extends { isStreaming?: boolean }>(value: T): Omit<T, "isStreaming"> {
  const { isStreaming: _ignored, ...rest } = value;
  return rest;
}

describe("timeline reducer", () => {
  test("message reset removes only the matching turn's streaming citation draft", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(state, event("turn_started", "t1", 1));
    state = bufferDeltas(state, { text: "上一条已完成回答" });
    state = applyTimelineEvent(
      state,
      event("message_completed", "t1", 2, { sources: [] }),
    );
    state = applyTimelineEvent(state, event("turn_started", "t2", 1));
    state = bufferDeltas(state, { text: "待修正[[cite:internal-source]]" });

    state = applyTimelineEvent(
      state,
      event("message_reset" as AgentEventType, "t2", 2),
    );

    const assistants = state.items.filter((item) => item.kind === "assistant");
    expect(assistants).toHaveLength(1);
    expect(assistants[0]).toMatchObject({ turnId: "t1", isStreaming: false });
    expect(state.activeTurnId).toBe("t2");
  });

  test("message completion attaches verified source metadata to its own turn", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(state, event("turn_started", "t1", 1));
    state = bufferDeltas(state, { text: "事实[[cite:source-a]]" });
    state = applyTimelineEvent(
      state,
      event("message_completed", "t1", 2, {
        sources: [{
          number: 1,
          sourceId: "source-a",
          title: "示例来源",
          url: "https://example.com/a",
          site: "example.com",
          fetchedAt: "2026-08-27T10:00:00Z",
          truncated: false,
        }],
        citationWarning: true,
        webAccessOffer: false,
      }),
    );

    expect(state.items[0]).toMatchObject({
      kind: "assistant",
      turnId: "t1",
      isStreaming: false,
      sources: [{ sourceId: "source-a", url: "https://example.com/a" }],
      citationWarning: true,
    });
  });

  test("a stale completion never assigns sources to another turn's assistant", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(state, event("turn_started", "t1", 1));
    state = bufferDeltas(state, { text: "第一条" });
    state = applyTimelineEvent(state, event("turn_started", "t2", 1));
    state = bufferDeltas(state, { text: "第二条仍在生成" });

    state = applyTimelineEvent(
      state,
      event("message_completed", "t1", 2, {
        sources: [{
          number: 1,
          sourceId: "source-a",
          title: "第一条来源",
          url: "https://example.com/a",
          site: "example.com",
          fetchedAt: "2026-08-27T10:00:00Z",
          truncated: false,
        }],
      }),
    );

    const first = state.items.find(
      (item) => item.kind === "assistant" && item.turnId === "t1",
    );
    const second = state.items.find(
      (item) => item.kind === "assistant" && item.turnId === "t2",
    );
    expect(first).toMatchObject({ sources: [{ sourceId: "source-a" }] });
    expect(second).toMatchObject({ isStreaming: true });
    expect(second).not.toHaveProperty("sources");
  });

  test("web approval and completion keep only the structured display metadata", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 1, {
        toolCallId: "web-1",
        name: "web_search",
        argumentsSummary: { query: "公开检索词" },
      }),
    );
    state = applyTimelineEvent(
      state,
      event("approval_requested", "t1", 2, {
        approvalId: "approval-1",
        toolCallId: "web-1",
        name: "web_search",
        approvalBehavior: "thread_first_use",
        externalRecipient: "DuckDuckGo",
        externalValue: "公开检索词",
      }),
    );
    state = applyTimelineEvent(
      state,
      event("tool_call_completed", "t1", 3, {
        toolCallId: "web-1",
        status: "succeeded",
        resultPreview: "{truncated-private-payload",
        resultTruncated: true,
        resultSummary: {
          kind: "web_search",
          resultCount: 1,
          results: [{
            title: "候选",
            url: "https://example.com/a",
            site: "example.com",
            snippet: "摘要",
          }],
        },
      }),
    );

    expect(state.items[0]).toMatchObject({
      kind: "tool",
      approvalBehavior: "thread_first_use",
      externalRecipient: "DuckDuckGo",
      externalValue: "公开检索词",
      resultTruncated: true,
      resultSummary: {
        kind: "web_search",
        results: [{ url: "https://example.com/a" }],
      },
    });
  });

  test.each([
    ["provider_auth_failed", "原指令", "open_settings"],
    ["provider_model_invalid", "原指令", "open_settings"],
    ["provider_request_failed", "原指令", "restore_text"],
    ["provider_overloaded", "原指令", "restore_text"],
    ["provider_overloaded", "", "none"],
    ["provider_timeout", "原指令", "restore_text"],
    ["provider_timeout", "", "none"],
    ["provider_rate_limited", "原指令", "restore_text"],
    ["provider_rate_limited", "", "none"],
    ["turn_limit_exceeded", "原指令", "restore_text"],
    ["turn_limit_exceeded", "", "none"],
    ["approval_wait_expired", "原指令", "restore_text"],
    ["approval_wait_expired", "", "none"],
    ["attachment_blob_missing", "原指令", "reselect_attachment"],
    ["internal_error", "原指令", "restart_app"],
    ["unknown_code", "原指令", "restore_text"],
    ["unknown_code", "", "none"],
  ] as const)(
    "maps %s with source text %j to %s recovery",
    (errorCode, sourceText, expected) => {
      expect(recoveryKindFor(errorCode, sourceText)).toBe(expected);
    },
  );

  test("full live lifecycle builds ordered items", () => {
    let state = initialTimeline();
    state = appendUserMessage(state, "帮我读一下 a.txt");
    state = applyTimelineEvent(state, event("turn_started", "t1", 1));

    state = bufferDeltas(state, { reasoning: "用户想读文件，" });
    state = bufferDeltas(state, { reasoning: "先调用 read_file。" });
    state = bufferDeltas(state, { text: "现在读取文件内容" });
    state = applyTimelineEvent(state, event("message_completed", "t1", 5, { messageId: "m1", role: "assistant" }));

    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 6, {
        toolCallId: "tc1",
        name: "read_file",
        argumentsSummary: { path: "a.txt" },
      }),
    );
    state = applyTimelineEvent(
      state,
      event("tool_call_completed", "t1", 7, {
        toolCallId: "tc1",
        status: "succeeded",
        durationMs: 42,
        resultPreview: "hello content",
        resultTruncated: false,
      }),
    );

    state = bufferDeltas(state, { text: "文件内容是 hello content" });
    state = applyTimelineEvent(state, event("turn_completed", "t1", 9, { messageId: "m2" }));

    expect(state.items.map((item) => item.kind)).toEqual([
      "user",
      "assistant",
      "tool",
      "assistant",
    ]);
    expect(state.items[0]).toMatchObject({ kind: "user", text: "帮我读一下 a.txt" });

    const first = state.items[1];
    expect(first.kind).toBe("assistant");
    if (first.kind === "assistant") {
      expect(first.segments.map((segment) => segment.type)).toEqual(["reasoning", "text"]);
      expect(first.segments[0]).toMatchObject({ type: "reasoning", text: "用户想读文件，先调用 read_file。" });
      expect(first.segments[1]).toMatchObject({ type: "text", text: "现在读取文件内容" });
      expect(first.isStreaming).toBe(false);
      expect(first.segments.every((segment) => !segment.isStreaming)).toBe(true);
    }

    expect(state.items[2]).toEqual({
      kind: "tool",
      id: "tc1",
      turnId: "t1",
      name: "read_file",
      argumentsSummary: { path: "a.txt" },
      status: "succeeded",
      durationMs: 42,
      resultPreview: "hello content",
      resultTruncated: false,
    });

    expect(state.isRunning).toBe(false);
    expect(state.isThinking).toBe(false);
    expect(state.activeTurnId).toBeNull();
    expect(state.error).toBeNull();
  });

  test("reasoning keeps streaming flag and thinking indicator until text arrives", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(state, event("turn_started", "t1", 1));
    state = bufferDeltas(state, { reasoning: "思考中" });

    expect(state.isThinking).toBe(true);
    const assistant = state.items[0];
    expect(assistant?.kind).toBe("assistant");
    if (assistant?.kind === "assistant") {
      expect(assistant.isStreaming).toBe(true);
      expect(assistant.segments[0].isStreaming).toBe(true);
    }

    state = bufferDeltas(state, { text: "答案" });
    expect(state.isThinking).toBe(false);
    if (assistant?.kind === "assistant") {
      const latest = state.items[0];
      if (latest?.kind === "assistant") {
        expect(latest.segments.map((segment) => segment.type)).toEqual(["reasoning", "text"]);
        expect(latest.segments[0].isStreaming).toBe(false);
        expect(latest.segments[1].isStreaming).toBe(true);
      }
    }
  });

  test("new assistant item starts after a tool item", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(state, event("turn_started", "t1", 1));
    state = bufferDeltas(state, { text: "第一步" });
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 3, { toolCallId: "tc1", name: "read_file" }),
    );
    state = bufferDeltas(state, { text: "第二步" });

    expect(state.items.map((item) => item.kind)).toEqual(["assistant", "tool", "assistant"]);
  });

  test("approval round-trip moves the matching tool card through statuses", () => {
    let state = initialTimeline();
    state = appendUserMessage(state, "请修改合同", "user-1");
    state = bindUserMessageToTurn(state, "user-1", "t1");
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 2, {
        toolCallId: "tc1",
        name: "write_file",
        riskLevel: "high",
      }),
    );
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 3, { toolCallId: "tc2", name: "read_file" }),
    );

    state = applyTimelineEvent(
      state,
      event("approval_requested", "t1", 4, {
        approvalId: "ap1",
        toolCallId: "tc1",
        name: "write_file",
        riskLevel: "high",
      }),
    );
    expect(state.items.find((item) => item.kind === "user" && item.id === "user-1")).toMatchObject({
      turnId: "t1",
    });
    const writeCard = state.items.find((item) => item.kind === "tool" && item.id === "tc1");
    expect(writeCard).toMatchObject({
      turnId: "t1",
      approvalId: "ap1",
      riskLevel: "high",
      status: "awaiting_approval",
    });

    state = applyTimelineEvent(
      state,
      event("approval_resolved", "t1", 5, { approvalId: "ap1", decision: "allowed" }),
    );
    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc1")).toMatchObject({
      status: "running",
      approvalDecision: "allowed",
    });
    // the untouched card keeps its own state
    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc2")).toMatchObject({
      status: "running",
    });
  });

  test("approval events require the same turn, tool call, and approval", () => {
    let state = initialTimeline();
    for (const turnId of ["t1", "t2"] as const) {
      state = applyTimelineEvent(
        state,
        event("tool_call_started", turnId, 1, {
          toolCallId: "shared-tool-id",
          name: "write_file",
        }),
      );
    }

    state = applyTimelineEvent(
      state,
      event("approval_requested", "t1", 2, {
        approvalId: "ap1",
        toolCallId: "shared-tool-id",
        name: "write_file",
      }),
    );
    state = applyTimelineEvent(
      state,
      event("approval_requested", "t2", 2, {
        approvalId: "ap2",
        toolCallId: "shared-tool-id",
        name: "write_file",
      }),
    );

    const toolFor = (turnId: string) =>
      state.items.find(
        (item) => item.kind === "tool" && item.turnId === turnId && item.id === "shared-tool-id",
      );
    expect(toolFor("t1")).toMatchObject({ status: "awaiting_approval", approvalId: "ap1" });
    expect(toolFor("t2")).toMatchObject({ status: "awaiting_approval", approvalId: "ap2" });

    state = applyTimelineEvent(
      state,
      event("approval_resolved", "t2", 3, {
        approvalId: "ap1",
        toolCallId: "shared-tool-id",
        decision: "allowed",
      }),
    );
    expect(toolFor("t1")).toMatchObject({ status: "awaiting_approval" });
    expect(toolFor("t2")).toMatchObject({ status: "awaiting_approval" });

    state = applyTimelineEvent(
      state,
      event("approval_resolved", "t2", 4, {
        approvalId: "ap2",
        toolCallId: "different-tool-id",
        decision: "allowed",
      }),
    );
    expect(toolFor("t2")).toMatchObject({ status: "awaiting_approval" });

    state = applyTimelineEvent(
      state,
      event("approval_resolved", "t2", 5, {
        approvalId: "ap2",
        toolCallId: "shared-tool-id",
        decision: "allowed",
      }),
    );
    expect(toolFor("t1")).toMatchObject({ status: "awaiting_approval" });
    expect(toolFor("t2")).toMatchObject({
      status: "running",
      approvalDecision: "allowed",
    });
  });

  test("invalid or missing approval decisions keep the matching card awaiting", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 1, { toolCallId: "tc1", name: "run_command" }),
    );
    state = applyTimelineEvent(
      state,
      event("approval_requested", "t1", 2, {
        approvalId: "ap1",
        toolCallId: "tc1",
        name: "run_command",
      }),
    );

    state = applyTimelineEvent(
      state,
      event("approval_resolved", "t1", 3, {
        approvalId: "ap1",
        toolCallId: "tc1",
        decision: "later",
      }),
    );
    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc1")).toMatchObject({
      status: "awaiting_approval",
      approvalId: "ap1",
    });

    state = applyTimelineEvent(
      state,
      event("approval_resolved", "t1", 4, {
        approvalId: "ap1",
        toolCallId: "tc1",
      }),
    );
    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc1")).toMatchObject({
      status: "awaiting_approval",
    });
  });

  test("a later approval decision cannot rewrite an already resolved card", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 1, { toolCallId: "tc1", name: "write_file" }),
    );
    state = applyTimelineEvent(
      state,
      event("approval_requested", "t1", 2, {
        approvalId: "ap1",
        toolCallId: "tc1",
        name: "write_file",
      }),
    );
    state = applyTimelineEvent(
      state,
      event("approval_resolved", "t1", 3, {
        approvalId: "ap1",
        toolCallId: "tc1",
        decision: "allowed",
      }),
    );
    state = applyTimelineEvent(
      state,
      event("approval_resolved", "t1", 4, {
        approvalId: "ap1",
        toolCallId: "tc1",
        decision: "denied",
      }),
    );

    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc1")).toMatchObject({
      status: "running",
      approvalDecision: "allowed",
    });
  });

  test.each([
    ["resolved", "running", "allowed"],
    ["succeeded", "succeeded", "allowed"],
    ["cancelled", "cancelled", undefined],
    ["failed", "failed", undefined],
  ] as const)(
    "a late approval request cannot reopen a %s tool card",
    (terminalKind, expectedStatus, expectedDecision) => {
      let state = initialTimeline();
      state = applyTimelineEvent(
        state,
        event("tool_call_started", "t1", 1, { toolCallId: "tc1", name: "write_file" }),
      );
      state = applyTimelineEvent(
        state,
        event("approval_requested", "t1", 2, {
          approvalId: "ap1",
          toolCallId: "tc1",
          name: "write_file",
        }),
      );

      if (terminalKind === "resolved" || terminalKind === "succeeded") {
        state = applyTimelineEvent(
          state,
          event("approval_resolved", "t1", 3, {
            approvalId: "ap1",
            toolCallId: "tc1",
            decision: "allowed",
          }),
        );
      }
      if (terminalKind === "succeeded") {
        state = applyTimelineEvent(
          state,
          event("tool_call_completed", "t1", 4, {
            toolCallId: "tc1",
            status: "succeeded",
          }),
        );
      } else if (terminalKind === "cancelled") {
        state = applyTimelineEvent(state, event("turn_cancelled", "t1", 3));
      } else if (terminalKind === "failed") {
        state = applyTimelineEvent(
          state,
          event("turn_failed", "t1", 3, {
            errorCode: "provider_request_failed",
            retryable: true,
          }),
        );
      }

      const bindingBefore = state.toolCallByApproval.ap1;
      const lateSequence = (state.maxSequenceByTurn.t1 ?? 0) + 1;
      const next = applyTimelineEvent(
        state,
        event("approval_requested", "t1", lateSequence, {
          approvalId: "ap1",
          toolCallId: "tc1",
          name: "write_file",
        }),
      );

      expect(next.maxSequenceByTurn.t1).toBe(lateSequence);
      expect(next.toolCallByApproval.ap1).toBe(bindingBefore);
      const nextTool = next.items.find((item) => item.kind === "tool" && item.id === "tc1");
      expect(nextTool).toMatchObject({
        status: expectedStatus,
        approvalId: "ap1",
      });
      expect(nextTool?.kind === "tool" ? nextTool.approvalDecision : undefined).toBe(
        expectedDecision,
      );
    },
  );

  test("denied completion finalizes the card without a result preview", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 2, { toolCallId: "tc1", name: "write_file" }),
    );
    state = applyTimelineEvent(
      state,
      event("approval_requested", "t1", 3, { approvalId: "ap1", toolCallId: "tc1", name: "write_file" }),
    );
    state = applyTimelineEvent(
      state,
      event("approval_resolved", "t1", 4, { approvalId: "ap1", decision: "denied" }),
    );
    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc1")).toMatchObject({
      status: "running",
      approvalDecision: "denied",
    });
    state = applyTimelineEvent(
      state,
      event("tool_call_completed", "t1", 5, { toolCallId: "tc1", status: "denied", durationMs: 0 }),
    );

    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc1")).toMatchObject({
      status: "denied",
      durationMs: 0,
      resultPreview: undefined,
    });
  });

  test("legacy write and command tools infer high risk while unknown tools stay unclassified", () => {
    let state = initialTimeline();
    for (const [sequence, toolCallId, name] of [
      [1, "tc-write", "write_file"],
      [2, "tc-patch", "apply_file_patch"],
      [3, "tc-command", "run_command"],
      [4, "tc-unknown", "future_tool"],
    ] as const) {
      state = applyTimelineEvent(
        state,
        event("tool_call_started", "t1", sequence, { toolCallId, name }),
      );
    }

    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc-write")).toMatchObject({
      riskLevel: "high",
    });
    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc-patch")).toMatchObject({
      riskLevel: "high",
    });
    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc-command")).toMatchObject({
      riskLevel: "high",
    });
    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc-unknown")).not.toHaveProperty(
      "riskLevel",
    );
  });

  test.each([
    ["turn_cancelled", "cancelled"],
    ["turn_failed", "failed"],
  ] as const)("%s closes only awaiting approvals from the same turn", (terminalEvent, expectedStatus) => {
    let state = initialTimeline();
    for (const [turnId, toolCallId, approvalId] of [
      ["t1", "tc1", "ap1"],
      ["t2", "tc2", "ap2"],
    ] as const) {
      state = applyTimelineEvent(
        state,
        event("tool_call_started", turnId, 1, { toolCallId, name: "write_file" }),
      );
      state = applyTimelineEvent(
        state,
        event("approval_requested", turnId, 2, { approvalId, toolCallId, name: "write_file" }),
      );
    }

    state = applyTimelineEvent(
      state,
      event(
        terminalEvent,
        "t1",
        3,
        terminalEvent === "turn_failed"
          ? { errorCode: "provider_request_failed", retryable: true }
          : {},
      ),
    );

    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc1")).toMatchObject({
      status: expectedStatus,
    });
    expect(state.items.find((item) => item.kind === "tool" && item.id === "tc2")).toMatchObject({
      status: "awaiting_approval",
    });
  });

  test.each([
    ["turn_cancelled", "cancelled"],
    ["turn_failed", "failed"],
  ] as const)("%s closes unfinished tools but preserves completed same-turn statuses", (terminalEvent, expectedStatus) => {
    let state = initialTimeline();
    for (const [sequence, toolCallId] of [
      [1, "awaiting"],
      [3, "running"],
      [4, "succeeded"],
    ] as const) {
      state = applyTimelineEvent(
        state,
        event("tool_call_started", "t1", sequence, { toolCallId, name: "write_file" }),
      );
      if (toolCallId === "awaiting") {
        state = applyTimelineEvent(
          state,
          event("approval_requested", "t1", 2, {
            approvalId: "ap-awaiting",
            toolCallId,
            name: "write_file",
          }),
        );
      }
    }
    state = applyTimelineEvent(
      state,
      event("tool_call_completed", "t1", 5, {
        toolCallId: "succeeded",
        status: "succeeded",
      }),
    );

    state = applyTimelineEvent(
      state,
      event(
        terminalEvent,
        "t1",
        6,
        terminalEvent === "turn_failed"
          ? { errorCode: "provider_request_failed", retryable: true }
          : {},
      ),
    );

    const statusFor = (toolCallId: string) =>
      state.items.find((item) => item.kind === "tool" && item.id === toolCallId);
    expect(statusFor("awaiting")).toMatchObject({ status: expectedStatus });
    expect(statusFor("running")).toMatchObject({ status: expectedStatus });
    expect(statusFor("succeeded")).toMatchObject({ status: "succeeded" });
  });

  test("tool completion only changes the matching turn and tool call", () => {
    let state = initialTimeline();
    for (const turnId of ["t1", "t2"] as const) {
      state = applyTimelineEvent(
        state,
        event("tool_call_started", turnId, 1, {
          toolCallId: "shared-tool-id",
          name: "read_file",
        }),
      );
    }

    state = applyTimelineEvent(
      state,
      event("tool_call_completed", "t2", 2, {
        toolCallId: "shared-tool-id",
        status: "succeeded",
      }),
    );

    expect(
      state.items.find(
        (item) => item.kind === "tool" && item.turnId === "t1" && item.id === "shared-tool-id",
      ),
    ).toMatchObject({ status: "running" });
    expect(
      state.items.find(
        (item) => item.kind === "tool" && item.turnId === "t2" && item.id === "shared-tool-id",
      ),
    ).toMatchObject({ status: "succeeded" });
  });

  test("duplicate and stale approval events preserve state identity", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 1, { toolCallId: "tc1", name: "write_file" }),
    );
    state = applyTimelineEvent(
      state,
      event("approval_requested", "t1", 2, {
        approvalId: "ap1",
        toolCallId: "tc1",
        name: "write_file",
      }),
    );
    const awaiting = state;

    expect(
      applyTimelineEvent(
        state,
        event("approval_requested", "t1", 2, {
          approvalId: "ap1",
          toolCallId: "tc1",
          name: "write_file",
        }),
      ),
    ).toBe(awaiting);
    expect(
      applyTimelineEvent(
        state,
        event("tool_call_completed", "t1", 1, {
          toolCallId: "tc1",
          status: "succeeded",
        }),
      ),
    ).toBe(awaiting);
  });

  test("completion without a start event still renders a defensive card", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(
      state,
      event("tool_call_completed", "t1", 2, {
        toolCallId: "ghost",
        status: "failed",
        durationMs: 3,
        resultPreview: "boom",
        resultTruncated: false,
      }),
    );
    expect(state.items[0]).toMatchObject({
      kind: "tool",
      id: "ghost",
      status: "failed",
    });
  });

  test("duplicate and stale replayed sequences are idempotent", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 2, { toolCallId: "tc1", name: "read_file" }),
    );
    const afterFirst = state;

    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 2, { toolCallId: "tc1", name: "read_file" }),
    );
    expect(state).toBe(afterFirst);

    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 1, { toolCallId: "tc0", name: "stale" }),
    );
    expect(state).toBe(afterFirst);
  });

  test("sequence windows are tracked per turn", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 2, { toolCallId: "tc1", name: "read_file" }),
    );
    // another turn's sequence numbering is independent
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t2", 2, { toolCallId: "tc2", name: "read_file" }),
    );
    expect(state.items.filter((item) => item.kind === "tool")).toHaveLength(2);
  });

  test("attachment degraded event renders exactly one notice", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(
      state,
      event("attachment_degraded", "t1", 1, {
        reason: "model_vision_unsupported",
        total: 2,
        truncated: false,
        attachments: [
          { id: "a1", name: "contract.png" },
          { id: "a2", name: "scan.jpg" },
        ],
      }),
    );
    state = applyTimelineEvent(
      state,
      event("attachment_degraded", "t1", 1, {
        reason: "model_vision_unsupported",
        total: 2,
        truncated: false,
        attachments: [],
      }),
    );

    const notices = state.items.filter((item) => item.kind === "notice");
    expect(notices).toHaveLength(1);
    expect(notices[0]).toMatchObject({
      notice: "attachment_degraded",
      total: 2,
      truncated: false,
      attachmentNames: ["contract.png", "scan.jpg"],
    });
  });

  test("reasoning effort fallback renders exactly one notice", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(
      state,
      event("reasoning_effort_degraded", "t1", 1, {
        from: "high",
        to: "auto",
        reason: "provider_unsupported",
      }),
    );
    state = applyTimelineEvent(
      state,
      event("reasoning_effort_degraded", "t1", 1, {
        from: "high",
        to: "auto",
      }),
    );

    expect(state.items.filter((item) => item.kind === "notice")).toEqual([
      expect.objectContaining({
        notice: "reasoning_effort_degraded",
        reasoningEffortFrom: "high",
        reasoningEffortTo: "auto",
      }),
    ]);
  });

  test("turn failure closes streaming and appends an error notice", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(state, event("turn_started", "t1", 1));
    state = bufferDeltas(state, { text: "部分回答" });
    state = applyTimelineEvent(
      state,
      event("turn_failed", "t1", 3, { errorCode: "provider_auth_failed", retryable: false }),
    );

    expect(state.isRunning).toBe(false);
    const notices = state.items.filter((item) => item.kind === "notice");
    expect(notices).toHaveLength(1);
    expect(notices[0]).toMatchObject({ notice: "turn_failed", errorCode: "provider_auth_failed" });
    const assistant = state.items.find((item) => item.kind === "assistant");
    if (assistant?.kind === "assistant") {
      expect(assistant.isStreaming).toBe(false);
    }
  });

  test("turn failure keeps the exact source instruction and warns after a succeeded high-risk tool", () => {
    let state = appendUserMessage(initialTimeline(), "请修改合同", "user-1");
    state = bindUserMessageToTurn(state, "user-1", "t1");
    state = applyTimelineEvent(
      state,
      event("tool_call_started", "t1", 1, {
        toolCallId: "tc1",
        name: "write_file",
        riskLevel: "high",
      }),
    );
    state = applyTimelineEvent(
      state,
      event("tool_call_completed", "t1", 2, {
        toolCallId: "tc1",
        status: "succeeded",
      }),
    );
    state = applyTimelineEvent(
      state,
      event("turn_failed", "t1", 3, {
        errorCode: "provider_request_failed",
        retryable: true,
      }),
    );

    expect(state.items.at(-1)).toMatchObject({
      kind: "notice",
      notice: "turn_failed",
      turnId: "t1",
      errorCode: "provider_request_failed",
      retryable: true,
      recoveryKind: "restore_text",
      sourceText: "请修改合同",
      hasPriorSideEffect: true,
    });
  });

  test.each([
    ["high", "failed"],
    ["medium", "succeeded"],
    ["low", "succeeded"],
  ] as const)(
    "%s-risk tool with %s status does not trigger the prior-side-effect warning",
    (riskLevel, status) => {
      let state = appendUserMessage(initialTimeline(), "继续处理", "user-1");
      state = bindUserMessageToTurn(state, "user-1", "t1");
      state = applyTimelineEvent(
        state,
        event("tool_call_started", "t1", 1, {
          toolCallId: "tc1",
          name: "future_tool",
          riskLevel,
        }),
      );
      state = applyTimelineEvent(
        state,
        event("tool_call_completed", "t1", 2, { toolCallId: "tc1", status }),
      );
      state = applyTimelineEvent(
        state,
        event("turn_failed", "t1", 3, {
          errorCode: "provider_request_failed",
          retryable: true,
        }),
      );

      expect(state.items.at(-1)).toMatchObject({
        kind: "notice",
        hasPriorSideEffect: false,
      });
    },
  );

  test("turn cancelled closes streaming and records a neutral stop notice", () => {
    let state = initialTimeline();
    state = applyTimelineEvent(state, event("turn_started", "t1", 1));
    state = bufferDeltas(state, { text: "部分回答" });
    state = applyTimelineEvent(state, event("turn_cancelled", "t1", 2));

    expect(state.isRunning).toBe(false);
    expect(state.items.at(-1)).toMatchObject({kind: "notice", notice: "turn_cancelled"});
  });

  test("message delta events only advance the sequence window", () => {
    const state = initialTimeline();
    const next = applyTimelineEvent(
      state,
      event("message_delta", "t1", 2, { kind: "text", delta: "abc" }),
    );
    expect(next).not.toBe(state);
    expect(next.items).toEqual([]);
    expect(next.maxSequenceByTurn["t1"]).toBe(2);
    // A replayed duplicate must be a full no-op so the caller can skip
    // re-buffering the same delta.
    expect(
      applyTimelineEvent(next, event("message_delta", "t1", 2, { kind: "text", delta: "abc" })),
    ).toBe(next);
  });

  test("user message appends with stable shape", () => {
    const state = appendUserMessage(initialTimeline(), "你好");
    expect(state.items).toHaveLength(1);
    expect(stripped(state.items[0] as never)).toMatchObject({
      kind: "user",
      text: "你好",
    });
  });
});
