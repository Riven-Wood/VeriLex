import { describe, expect, test, vi } from "vitest";

import type { MessageRecord } from "../src/runtime/contracts";
import { buildTimelineHistory } from "../src/timeline/history";

function message(
  id: string,
  turnId: string | null,
  role: "user" | "assistant" | "tool",
  content: unknown,
): MessageRecord {
  return {
    id,
    thread_id: "th1",
    turn_id: turnId,
    sequence: Number(id.split("-")[1]),
    role,
    content: content as MessageRecord["content"],
    created_at: "2026-08-18T10:00:00Z",
  };
}

function turn(id: string, status: string, errorCode: string | null = null) {
  return { id, status, error_code: errorCode };
}

function threadEvent(
  turnId: string,
  sequence: number,
  type: string,
  payload: Record<string, unknown> = {},
) {
  return { turn_id: turnId, sequence, type, payload };
}

describe("timeline history builder", () => {
  test("restores verified sources and warning blocks from SQLite without fetching", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "查证" }]),
        message("m-2", "t1", "assistant", [
          { type: "text", text: "事实[[cite:source-a]]" },
          {
            type: "sources",
            sources: [{
              number: 1,
              sourceId: "source-a",
              title: "示例来源",
              url: "https://example.com/a",
              site: "example.com",
              fetchedAt: "2026-08-27T10:00:00Z",
              truncated: true,
            }],
          },
          { type: "citation_warning" },
        ]),
      ],
      turns: [turn("t1", "completed")],
      events: [],
    });

    const assistant = build.state.items.find((item) => item.kind === "assistant");
    expect(assistant).toMatchObject({
      kind: "assistant",
      sources: [{ sourceId: "source-a", url: "https://example.com/a" }],
      citationWarning: true,
    });
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  test("replayed completion events produce the same source metadata as message blocks", () => {
    const build = buildTimelineHistory({
      messages: [],
      turns: [turn("t1", "completed")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "message_delta", {
          kind: "text",
          delta: "事实[[cite:source-a]]",
        }),
        threadEvent("t1", 3, "message_completed", {
          sources: [{
            number: 1,
            sourceId: "source-a",
            title: "示例来源",
            url: "https://example.com/a",
            site: "example.com",
            fetchedAt: "2026-08-27T10:00:00Z",
            truncated: false,
          }],
          citationWarning: false,
          webAccessOffer: false,
        }),
        threadEvent("t1", 4, "turn_completed"),
      ],
    });

    expect(build.state.items[0]).toMatchObject({
      kind: "assistant",
      isStreaming: false,
      sources: [{ sourceId: "source-a", url: "https://example.com/a" }],
    });
  });

  test("supplements replayed assistant content with final persisted source blocks", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "assistant", [
          { type: "text", text: "事实[[cite:source-a]]" },
          {
            type: "sources",
            sources: [{
              number: 1,
              sourceId: "source-a",
              title: "数据库中的来源",
              url: "https://example.com/a",
              site: "example.com",
              fetchedAt: "2026-08-27T10:00:00Z",
              truncated: false,
            }],
          },
          { type: "citation_warning" },
          { type: "web_access_offer" },
        ]),
      ],
      turns: [turn("t1", "completed")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "message_delta", {
          kind: "text",
          delta: "事实[[cite:source-a]]",
        }),
        // Older runtimes and interrupted persistence windows can close the
        // turn without a message_completed metadata event.
        threadEvent("t1", 3, "turn_completed"),
      ],
    });

    const assistants = build.state.items.filter((item) => item.kind === "assistant");
    expect(assistants).toHaveLength(1);
    expect(assistants[0]).toMatchObject({
      kind: "assistant",
      sources: [{ sourceId: "source-a", title: "数据库中的来源" }],
      citationWarning: true,
      webAccessOffer: true,
    });
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  test("malformed persisted source blocks fail closed", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "assistant", [
          { type: "text", text: "伪造[[cite:source-a]]" },
          {
            type: "sources",
            sources: [{
              number: 1,
              sourceId: "source-a",
              title: "危险来源",
              url: "javascript:alert(1)",
              site: "example.com",
              fetchedAt: "not-a-time",
              truncated: false,
            }],
          },
        ]),
      ],
      turns: [turn("t1", "completed")],
      events: [],
    });

    const assistant = build.state.items.find((item) => item.kind === "assistant");
    expect(assistant).not.toHaveProperty("sources");
  });

  test("rebuilds a bounded web-search summary from the local tool message", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "assistant", [{
          type: "tool_call",
          id: "web-call",
          name: "web_search",
          arguments: { query: "本地历史" },
        }]),
        message("m-2", "t1", "tool", [{
          type: "tool_result",
          toolCallId: "web-call",
          name: "web_search",
          content: JSON.stringify({
            provider: "duckduckgo",
            query: "本地历史",
            results: [{
              title: "候选",
              url: "https://example.com/a?id=42#section",
              snippet: "摘要",
            }],
            result_count: 1,
          }),
        }]),
      ],
      turns: [turn("t1", "completed")],
      events: [],
    });

    expect(build.state.items.find((item) => item.kind === "tool")).toMatchObject({
      kind: "tool",
      name: "web_search",
      resultSummary: {
        kind: "web_search",
        resultCount: 1,
        results: [{
          title: "候选",
          url: "https://example.com/a?id=42#section",
          site: "example.com",
          snippet: "摘要",
        }],
      },
    });
  });

  test("valid local tool output overrides malformed replayed result metadata", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "tool", [{
          type: "tool_result",
          toolCallId: "web-call",
          name: "web_search",
          content: JSON.stringify({
            provider: "duckduckgo",
            query: "本地历史",
            results: [{
              title: "数据库候选",
              url: "https://example.com/a?id=42",
              snippet: "完整摘要",
            }],
            result_count: 1,
          }),
        }]),
      ],
      turns: [turn("t1", "completed")],
      events: [
        threadEvent("t1", 1, "tool_call_started", {
          toolCallId: "web-call",
          name: "web_search",
        }),
        threadEvent("t1", 2, "tool_call_completed", {
          toolCallId: "web-call",
          name: "web_search",
          status: "succeeded",
          resultSummary: { kind: "web_search", results: "tampered" },
        }),
        threadEvent("t1", 3, "turn_completed"),
      ],
    });

    expect(build.state.items.find((item) => item.kind === "tool")).toMatchObject({
      kind: "tool",
      resultSummary: {
        kind: "web_search",
        results: [{
          title: "数据库候选",
          url: "https://example.com/a?id=42",
          snippet: "完整摘要",
        }],
      },
    });
  });

  test("keeps an unpersisted legacy approval awaiting after reconnect replay", () => {
    const build = buildTimelineHistory({
      messages: [message("m-1", "t1", "user", [{ type: "text", text: "创建文件" }])],
      turns: [turn("t1", "awaiting_approval")],
      events: [
        threadEvent("t1", 1, "tool_call_started", {
          toolCallId: "tc1",
          name: "write_file",
        }),
        threadEvent("t1", 2, "approval_requested", {
          approvalId: "ap1",
          toolCallId: "tc1",
          name: "write_file",
        }),
        // A duplicate and an older frame from the reconnect window must not
        // invent a persisted decision or replace the original approval id.
        threadEvent("t1", 2, "approval_requested", {
          approvalId: "ap-other",
          toolCallId: "tc1",
          name: "write_file",
        }),
        threadEvent("t1", 1, "approval_resolved", {
          approvalId: "ap1",
          toolCallId: "tc1",
          decision: "allowed",
        }),
      ],
    });

    expect(build.activeTurnId).toBe("t1");
    expect(build.state.items.find((item) => item.kind === "tool")).toMatchObject({
      kind: "tool",
      id: "tc1",
      turnId: "t1",
      approvalId: "ap1",
      riskLevel: "high",
      status: "awaiting_approval",
    });
  });

  test("adopts the persisted approval decision despite duplicate and late replay frames", () => {
    const build = buildTimelineHistory({
      messages: [message("m-1", "t1", "user", [{ type: "text", text: "创建文件" }])],
      turns: [turn("t1", "running")],
      events: [
        threadEvent("t1", 1, "tool_call_started", {
          toolCallId: "tc1",
          name: "write_file",
          riskLevel: "high",
        }),
        threadEvent("t1", 2, "approval_requested", {
          approvalId: "ap1",
          toolCallId: "tc1",
          name: "write_file",
          riskLevel: "high",
        }),
        threadEvent("t1", 3, "approval_resolved", {
          approvalId: "ap1",
          toolCallId: "tc1",
          decision: "allowed",
        }),
        threadEvent("t1", 3, "approval_resolved", {
          approvalId: "ap1",
          toolCallId: "tc1",
          decision: "denied",
        }),
        threadEvent("t1", 2, "approval_requested", {
          approvalId: "ap1",
          toolCallId: "tc1",
          name: "write_file",
        }),
      ],
    });

    expect(build.activeTurnId).toBe("t1");
    expect(build.state.items.find((item) => item.kind === "tool")).toMatchObject({
      kind: "tool",
      id: "tc1",
      approvalId: "ap1",
      approvalDecision: "allowed",
      status: "running",
    });
  });

  test("rebuilds a completed turn from events with user message first", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", {
          version: 1,
          text: "读一下合同",
          attachmentIds: ["att-1"],
        }),
        message("m-2", "t1", "assistant", [
          { type: "reasoning", text: "先找文件" },
          { type: "text", text: "现在读取" },
          { type: "tool_call", id: "call-9", name: "read_file", arguments: { path: "a.txt" } },
        ]),
        message("m-3", "t1", "tool", [
          { type: "tool_result", toolCallId: "call-9", name: "read_file", content: "hello" },
        ]),
        message("m-4", "t1", "assistant", [{ type: "text", text: "内容是 hello" }]),
      ],
      turns: [turn("t1", "completed")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "message_delta", { kind: "reasoning", delta: "先找文件" }),
        threadEvent("t1", 3, "message_delta", { kind: "text", delta: "现在读取" }),
        threadEvent("t1", 4, "message_completed", { messageId: "m-2", role: "assistant" }),
        threadEvent("t1", 5, "tool_call_started", {
          toolCallId: "rec-1",
          name: "read_file",
          argumentsSummary: { path: "a.txt" },
        }),
        threadEvent("t1", 6, "tool_call_completed", {
          toolCallId: "rec-1",
          status: "succeeded",
          durationMs: 12,
          resultPreview: "hello",
          resultTruncated: false,
        }),
        threadEvent("t1", 7, "message_delta", { kind: "text", delta: "内容是 hello" }),
        threadEvent("t1", 8, "turn_completed", { messageId: "m-4" }),
      ],
      attachmentNamesById: { "att-1": "合同扫描件.png" },
    });

    expect(build.activeTurnId).toBeNull();
    const kinds = build.state.items.map((item) => item.kind);
    expect(kinds).toEqual(["user", "assistant", "tool", "assistant"]);

    expect(build.state.items[0]).toMatchObject({
      kind: "user",
      text: "读一下合同",
      attachmentNames: ["合同扫描件.png"],
    });

    const first = build.state.items[1];
    if (first?.kind === "assistant") {
      expect(first.segments.map((segment) => segment.type)).toEqual(["reasoning", "text"]);
      expect(first.isStreaming).toBe(false);
    }

    expect(build.state.items[2]).toMatchObject({
      kind: "tool",
      status: "succeeded",
      durationMs: 12,
      resultPreview: "hello",
    });

    // the tool role message must never surface as an assistant message
    const texts = build.state.items
      .filter((item) => item.kind === "assistant")
      .flatMap((item) => (item.kind === "assistant" ? item.segments.map((s) => s.text) : []));
    expect(texts.join("")).not.toContain("hello\n");
  });

  test("interrupted turn renders a notice; failed turn renders its error code", () => {
    const failed = buildTimelineHistory({
      messages: [message("m-1", "t1", "user", [{ type: "text", text: "hi" }])],
      turns: [turn("t1", "failed")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "turn_failed", { errorCode: "provider_auth_failed", retryable: false }),
      ],
    });
    expect(failed.state.items.at(-1)).toMatchObject({
      kind: "notice",
      notice: "turn_failed",
      errorCode: "provider_auth_failed",
    });

    const interrupted = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "hi" }]),
        message("m-2", "t1", "assistant", [{ type: "text", text: "一半" }]),
      ],
      turns: [turn("t1", "interrupted")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "message_delta", { kind: "text", delta: "一半" }),
      ],
    });
    expect(interrupted.state.items.at(-1)).toMatchObject({
      kind: "notice",
      notice: "turn_interrupted",
      turnId: "t1",
      sourceText: "hi",
      recoveryKind: "restore_text",
      hasPriorSideEffect: false,
    });
    const assistant = interrupted.state.items.find((item) => item.kind === "assistant");
    if (assistant?.kind === "assistant") {
      expect(assistant.isStreaming).toBe(false);
    }
  });

  test("rebuilds failure recovery and its side-effect warning from the same turn", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "请修改合同" }]),
      ],
      turns: [turn("t1", "failed", "provider_request_failed")],
      events: [
        threadEvent("t1", 1, "tool_call_started", {
          toolCallId: "tc1",
          name: "write_file",
          riskLevel: "high",
        }),
        threadEvent("t1", 2, "tool_call_completed", {
          toolCallId: "tc1",
          status: "succeeded",
        }),
        threadEvent("t1", 3, "turn_failed", {
          errorCode: "provider_request_failed",
          retryable: true,
        }),
      ],
    });

    expect(build.state.items[0]).toMatchObject({
      kind: "user",
      turnId: "t1",
      text: "请修改合同",
    });
    expect(build.state.items.at(-1)).toMatchObject({
      kind: "notice",
      notice: "turn_failed",
      turnId: "t1",
      errorCode: "provider_request_failed",
      sourceText: "请修改合同",
      retryable: true,
      recoveryKind: "restore_text",
      hasPriorSideEffect: true,
    });
  });

  test("failed turn without a terminal event falls back to its persisted error exactly once", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "继续检查" }]),
      ],
      turns: [turn("t1", "failed", "provider_timeout")],
      events: [threadEvent("t1", 1, "turn_started")],
    });

    const notices = build.state.items.filter(
      (item) => item.kind === "notice" && item.notice === "turn_failed",
    );
    expect(notices).toHaveLength(1);
    expect(notices[0]).toMatchObject({
      turnId: "t1",
      errorCode: "provider_timeout",
      sourceText: "继续检查",
      recoveryKind: "restore_text",
      hasPriorSideEffect: false,
    });
  });

  test("failed persisted turn closes an approval even when its terminal event was missed", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "请写入文件" }]),
      ],
      turns: [turn("t1", "failed", "turn_limit_exceeded")],
      events: [
        threadEvent("t1", 1, "tool_call_started", {
          toolCallId: "tc1",
          name: "write_file",
          riskLevel: "high",
        }),
        threadEvent("t1", 2, "approval_requested", {
          approvalId: "ap1",
          toolCallId: "tc1",
          name: "write_file",
          riskLevel: "high",
        }),
      ],
    });

    expect(build.activeTurnId).toBeNull();
    expect(build.state.items.find((item) => item.kind === "tool")).toMatchObject({
      kind: "tool",
      id: "tc1",
      approvalId: "ap1",
      status: "failed",
    });
  });

  test("completed persisted turn never rebuilds a missed approval as actionable", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "请写入文件" }]),
      ],
      turns: [turn("t1", "completed")],
      events: [
        threadEvent("t1", 1, "tool_call_started", {
          toolCallId: "tc1",
          name: "write_file",
          riskLevel: "high",
        }),
        threadEvent("t1", 2, "approval_requested", {
          approvalId: "ap1",
          toolCallId: "tc1",
          name: "write_file",
          riskLevel: "high",
        }),
      ],
    });

    expect(build.activeTurnId).toBeNull();
    expect(build.state.items.find((item) => item.kind === "tool")).toMatchObject({
      kind: "tool",
      id: "tc1",
      approvalId: "ap1",
      status: "cancelled",
    });
  });

  test("failed turn fallback does not duplicate an existing terminal event notice", () => {
    const build = buildTimelineHistory({
      messages: [message("m-1", "t1", "user", [{ type: "text", text: "检查" }])],
      turns: [turn("t1", "failed", "provider_timeout")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "turn_failed", {
          errorCode: "provider_timeout",
          retryable: true,
        }),
      ],
    });

    expect(
      build.state.items.filter(
        (item) => item.kind === "notice" && item.notice === "turn_failed",
      ),
    ).toHaveLength(1);
  });

  test("interrupted history warns only after a high-risk tool succeeded", () => {
    const build = buildTimelineHistory({
      messages: [message("m-1", "t1", "user", [{ type: "text", text: "执行操作" }])],
      turns: [turn("t1", "interrupted")],
      events: [
        threadEvent("t1", 1, "tool_call_started", {
          toolCallId: "tc1",
          name: "run_command",
          riskLevel: "high",
        }),
        threadEvent("t1", 2, "tool_call_completed", {
          toolCallId: "tc1",
          status: "succeeded",
        }),
      ],
    });

    expect(build.state.items.at(-1)).toMatchObject({
      kind: "notice",
      notice: "turn_interrupted",
      turnId: "t1",
      sourceText: "执行操作",
      recoveryKind: "restore_text",
      hasPriorSideEffect: true,
    });
  });

  test("failed no-event history infers side effects from a paired legacy write result", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "修改条款" }]),
        message("m-2", "t1", "assistant", [
          {
            type: "tool_call",
            id: "call-1",
            name: "apply_file_patch",
            arguments: { path: "合同.md" },
          },
        ]),
        message("m-3", "t1", "tool", [
          {
            type: "tool_result",
            toolCallId: "call-1",
            name: "apply_file_patch",
            content: "updated",
          },
        ]),
      ],
      turns: [turn("t1", "failed", "provider_timeout")],
      events: [],
    });

    expect(build.state.items.find((item) => item.kind === "tool")).toMatchObject({
      name: "apply_file_patch",
      riskLevel: "high",
      status: "succeeded",
    });
    expect(build.state.items.at(-1)).toMatchObject({
      notice: "turn_failed",
      hasPriorSideEffect: true,
    });
  });

  test("interrupted no-event history infers side effects from a standalone legacy command result", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "运行检查" }]),
        message("m-2", "t1", "tool", [
          {
            type: "tool_result",
            toolCallId: "call-1",
            name: "run_command",
            content: "done",
          },
        ]),
      ],
      turns: [turn("t1", "interrupted")],
      events: [],
    });

    expect(build.state.items.find((item) => item.kind === "tool")).toMatchObject({
      name: "run_command",
      riskLevel: "high",
      status: "succeeded",
    });
    expect(build.state.items.at(-1)).toMatchObject({
      notice: "turn_interrupted",
      hasPriorSideEffect: true,
    });
  });

  test("no-event history keeps unknown succeeded tools unclassified", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "运行未来工具" }]),
        message("m-2", "t1", "tool", [
          {
            type: "tool_result",
            toolCallId: "call-1",
            name: "future_tool",
            content: "done",
          },
        ]),
      ],
      turns: [turn("t1", "failed", "provider_timeout")],
      events: [],
    });

    const tool = build.state.items.find((item) => item.kind === "tool");
    expect(tool).not.toHaveProperty("riskLevel");
    expect(build.state.items.at(-1)).toMatchObject({
      notice: "turn_failed",
      hasPriorSideEffect: false,
    });
  });

  test("failure history repairs a legacy write completion that is missing its start event", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-0", "t0", "user", [{ type: "text", text: "只读检查" }]),
        message("m-1", "t1", "user", [{ type: "text", text: "写入合同" }]),
        message("m-2", "t1", "tool", [
          {
            type: "tool_result",
            toolCallId: "call-1",
            name: "write_file",
            content: "written",
          },
        ]),
      ],
      turns: [
        turn("t0", "failed", "provider_timeout"),
        turn("t1", "failed", "provider_timeout"),
      ],
      events: [
        threadEvent("t0", 1, "turn_failed", {
          errorCode: "provider_timeout",
          retryable: true,
        }),
        threadEvent("t1", 1, "tool_call_completed", {
          toolCallId: "call-1",
          status: "succeeded",
        }),
        threadEvent("t1", 2, "turn_failed", {
          errorCode: "provider_timeout",
          retryable: true,
        }),
      ],
    });

    expect(build.state.items.find((item) => item.kind === "tool")).toMatchObject({
      id: "call-1",
      turnId: "t1",
      name: "write_file",
      riskLevel: "high",
      status: "succeeded",
    });
    expect(build.state.items.at(-1)).toMatchObject({
      kind: "notice",
      notice: "turn_failed",
      turnId: "t1",
      hasPriorSideEffect: true,
    });
    expect(
      build.state.items.find(
        (item) =>
          item.kind === "notice" &&
          item.notice === "turn_failed" &&
          item.turnId === "t0",
      ),
    ).toMatchObject({ hasPriorSideEffect: false });
    expect(
      build.state.items.filter(
        (item) =>
          item.kind === "notice" &&
          item.notice === "turn_failed" &&
          item.turnId === "t1",
      ),
    ).toHaveLength(1);
  });

  test("failure history repairs an unknown completion without inventing a side effect", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "运行未来工具" }]),
        message("m-2", "t1", "tool", [
          {
            type: "tool_result",
            toolCallId: "call-1",
            name: "future_tool",
            content: "done",
          },
        ]),
      ],
      turns: [turn("t1", "failed", "provider_timeout")],
      events: [
        threadEvent("t1", 1, "tool_call_completed", {
          toolCallId: "call-1",
          status: "succeeded",
        }),
        threadEvent("t1", 2, "turn_failed", {
          errorCode: "provider_timeout",
          retryable: true,
        }),
      ],
    });

    const tool = build.state.items.find((item) => item.kind === "tool");
    expect(tool).toMatchObject({
      id: "call-1",
      turnId: "t1",
      name: "future_tool",
      status: "succeeded",
    });
    expect(tool).not.toHaveProperty("riskLevel");
    expect(build.state.items.at(-1)).toMatchObject({
      kind: "notice",
      notice: "turn_failed",
      turnId: "t1",
      hasPriorSideEffect: false,
    });
    expect(
      build.state.items.filter(
        (item) => item.kind === "notice" && item.notice === "turn_failed",
      ),
    ).toHaveLength(1);
  });

  test("recovered interrupted turn cannot remain running when its terminal event is missing", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "hi" }]),
        message("m-2", "t1", "assistant", [{ type: "text", text: "一半" }]),
      ],
      turns: [turn("t1", "interrupted")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "message_delta", { kind: "text", delta: "一半" }),
      ],
    });

    expect(build.activeTurnId).toBeNull();
    expect(build.state.activeTurnId).toBeNull();
    expect(build.state.isRunning).toBe(false);
  });

  test("recovered interrupted turn clears trailing reasoning when its terminal event is missing", () => {
    const build = buildTimelineHistory({
      messages: [message("m-1", "t1", "user", [{ type: "text", text: "hi" }])],
      turns: [turn("t1", "interrupted")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "message_delta", { kind: "reasoning", delta: "还在想" }),
      ],
    });

    expect(build.state.isThinking).toBe(false);
  });

  test("missing event preview is supplemented from the paired tool message", () => {
    const long = "x".repeat(600);
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "go" }]),
        message("m-2", "t1", "assistant", [
          { type: "tool_call", id: "call-9", name: "read_file", arguments: {} },
        ]),
        message("m-3", "t1", "tool", [
          { type: "tool_result", toolCallId: "call-9", name: "read_file", content: long },
        ]),
        message("m-4", "t1", "assistant", [{ type: "text", text: "done" }]),
      ],
      turns: [turn("t1", "completed")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        // pre-resultPreview era event: no resultPreview key
        threadEvent("t1", 2, "tool_call_started", { toolCallId: "rec-1", name: "read_file" }),
        threadEvent("t1", 3, "tool_call_completed", {
          toolCallId: "rec-1",
          status: "succeeded",
          durationMs: 5,
        }),
        threadEvent("t1", 4, "message_delta", { kind: "text", delta: "done" }),
        threadEvent("t1", 5, "turn_completed", { messageId: "m-4" }),
      ],
    });

    const card = build.state.items.find((item) => item.kind === "tool");
    expect(card).toMatchObject({
      status: "succeeded",
      resultPreview: "x".repeat(400),
      resultTruncated: true,
    });
  });

  test("provider-id tool results pair with event cards by name and order", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "go" }]),
        message("m-2", "t1", "assistant", [
          { type: "tool_call", id: "call-a", name: "read_file", arguments: {} },
          { type: "tool_call", id: "call-b", name: "search_files", arguments: {} },
        ]),
        message("m-3", "t1", "tool", [
          { type: "tool_result", toolCallId: "call-a", name: "read_file", content: "first" },
        ]),
        message("m-4", "t1", "tool", [
          { type: "tool_result", toolCallId: "call-b", name: "search_files", content: "second" },
        ]),
        message("m-5", "t1", "assistant", [{ type: "text", text: "done" }]),
      ],
      turns: [turn("t1", "completed")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "tool_call_started", { toolCallId: "rec-1", name: "read_file" }),
        threadEvent("t1", 3, "tool_call_completed", {
          toolCallId: "rec-1",
          status: "succeeded",
          durationMs: 1,
        }),
        threadEvent("t1", 4, "tool_call_started", { toolCallId: "rec-2", name: "search_files" }),
        threadEvent("t1", 5, "tool_call_completed", {
          toolCallId: "rec-2",
          status: "succeeded",
          durationMs: 2,
        }),
        threadEvent("t1", 6, "message_delta", { kind: "text", delta: "done" }),
        threadEvent("t1", 7, "turn_completed", { messageId: "m-5" }),
      ],
    });

    const cards = build.state.items.filter((item) => item.kind === "tool");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toMatchObject({ name: "read_file", resultPreview: "first" });
    expect(cards[1]).toMatchObject({ name: "search_files", resultPreview: "second" });
  });

  test("unmatched tool result renders a standalone card", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "go" }]),
        message("m-2", "t1", "assistant", [
          { type: "text", text: "ok" },
        ]),
        message("m-3", "t1", "tool", [
          { type: "tool_result", toolCallId: "call-x", name: "no_such_tool", content: "unknown tool: no_such_tool" },
        ]),
        message("m-4", "t1", "assistant", [{ type: "text", text: "done" }]),
      ],
      turns: [turn("t1", "completed")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "message_delta", { kind: "text", delta: "ok" }),
        threadEvent("t1", 3, "message_delta", { kind: "text", delta: "done" }),
        threadEvent("t1", 4, "turn_completed", { messageId: "m-4" }),
      ],
    });

    const card = build.state.items.find((item) => item.kind === "tool");
    expect(card).toMatchObject({
      id: "call-x",
      name: "no_such_tool",
      status: "succeeded",
      resultPreview: "unknown tool: no_such_tool",
    });
  });

  test("denied standalone result is marked denied", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "go" }]),
        message("m-2", "t1", "tool", [
          { type: "tool_result", toolCallId: "call-d", name: "write_file", content: "denied" },
        ]),
        message("m-3", "t1", "assistant", [{ type: "text", text: "done" }]),
      ],
      turns: [turn("t1", "completed")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "message_delta", { kind: "text", delta: "done" }),
        threadEvent("t1", 3, "turn_completed", { messageId: "m-3" }),
      ],
    });
    const card = build.state.items.find((item) => item.kind === "tool");
    expect(card).toMatchObject({ status: "denied", name: "write_file" });
  });

  test("active turn is reported and its sequences feed replay dedupe", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "go" }]),
        message("m-2", "t1", "assistant", [{ type: "text", text: "进行中" }]),
      ],
      turns: [turn("t1", "running")],
      events: [
        threadEvent("t1", 1, "turn_started"),
        threadEvent("t1", 2, "message_delta", { kind: "text", delta: "进行中" }),
      ],
    });
    expect(build.activeTurnId).toBe("t1");
    expect(build.state.maxSequenceByTurn["t1"]).toBe(2);
    expect(build.state.isRunning).toBe(true);
  });

  test("turn without events falls back to message blocks", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", [{ type: "text", text: "go" }]),
        message("m-2", "t1", "assistant", [
          { type: "reasoning", text: "想一想" },
          { type: "text", text: "调用工具" },
          { type: "tool_call", id: "call-1", name: "read_file", arguments: { path: "a" } },
        ]),
        message("m-3", "t1", "tool", [
          { type: "tool_result", toolCallId: "call-1", name: "read_file", content: "data" },
        ]),
        message("m-4", "t1", "assistant", [{ type: "text", text: "结论" }]),
      ],
      turns: [turn("t1", "completed")],
      events: [],
    });

    expect(build.state.items.map((item) => item.kind)).toEqual([
      "user",
      "assistant",
      "tool",
      "assistant",
    ]);
    const first = build.state.items[1];
    if (first?.kind === "assistant") {
      expect(first.segments.map((segment) => segment.type)).toEqual(["reasoning", "text"]);
    }
    expect(build.state.items[2]).toMatchObject({
      kind: "tool",
      id: "call-1",
      name: "read_file",
      status: "succeeded",
      resultPreview: "data",
    });
  });

  test("null-turn messages render before turn content", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", null, "user", [{ type: "text", text: "旧消息" }]),
        message("m-2", "t1", "user", [{ type: "text", text: "新消息" }]),
      ],
      turns: [turn("t1", "completed")],
      events: [threadEvent("t1", 1, "turn_started"), threadEvent("t1", 2, "turn_completed")],
    });
    expect(build.state.items.map((item) => item.kind)).toEqual(["user", "user"]);
    expect(build.state.items[0]).toMatchObject({ text: "旧消息" });
    expect(build.state.items[1]).toMatchObject({ text: "新消息" });
  });

  test("attachment ids without metadata degrade to a count label source", () => {
    const build = buildTimelineHistory({
      messages: [
        message("m-1", "t1", "user", {
          version: 1,
          text: "看图",
          attachmentIds: ["a", "b"],
        }),
      ],
      turns: [turn("t1", "completed")],
      events: [threadEvent("t1", 1, "turn_completed")],
    });
    expect(build.state.items[0]).toMatchObject({
      kind: "user",
      text: "看图",
      attachmentCount: 2,
    });
  });
});
