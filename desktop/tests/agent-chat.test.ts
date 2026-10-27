import {EditorDiscussionProvider,OrdinaryDiscussion,useEditorDiscussion,LitigationMaterialsContext} from "../src/components/editor/DiscussionContext";
import {ContractEditorProvider,useContractEditorContext} from "../src/components/contractEditor/ContractEditorContext";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { StrictMode, useLayoutEffect, useRef, createElement } from "react";

import { useAgentChat } from "../src/hooks/useAgentChat";

afterEach(() => {
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
});

class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];
  static last: MockWebSocket | null = null;

  readyState = 0;
  onmessage: ((event: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sentMessages: string[] = [];

  constructor(public url: string, public subprotocol: string) {
    MockWebSocket.instances.push(this);
    MockWebSocket.last = this;
    this.readyState = 1;
  }

  send(data: string) {
    this.sentMessages.push(data);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  receive(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

type FetchRoutes = Partial<
  Record<"messages" | "turns" | "events" | "attachments", unknown[]>
>;

function setupGlobals(routes: FetchRoutes = {}) {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("WebSocket", MockWebSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body =
        url.endsWith("/messages")
          ? (routes.messages ?? [])
          : url.endsWith("/turns")
            ? (routes.turns ?? [])
            : url.endsWith("/events")
              ? (routes.events ?? [])
              : url.endsWith("/attachments")
                ? (routes.attachments ?? [])
                : [];
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
  MockWebSocket.instances = [];
  MockWebSocket.last = null;
}

function assistantItem(result: { current: ReturnType<typeof useAgentChat> }) {
  return result.current.items.find((item) => item.kind === "assistant");
}

function assistantText(result: { current: ReturnType<typeof useAgentChat> }): string {
  const item = assistantItem(result);
  if (item?.kind !== "assistant") return "";
  return item.segments.map((segment) => segment.text).join("");
}

function toolItems(result: { current: ReturnType<typeof useAgentChat> }) {
  return result.current.items.filter((item) => item.kind === "tool");
}

function noticeItems(result: { current: ReturnType<typeof useAgentChat> }) {
  return result.current.items.filter((item) => item.kind === "notice");
}

function receiveAwaitingApproval(
  socket: MockWebSocket,
  {
    turnId = "turn-1",
    toolCallId = "tc-1",
    approvalId = "ap-1",
    sequence = 1,
    threadId,
    approvalBehavior,
  }: {
    turnId?: string;
    toolCallId?: string;
    approvalId?: string;
    sequence?: number;
    threadId?: string;
    approvalBehavior?: "risk_default" | "thread_first_use";
  } = {},
) {
  socket.receive({
    type: "event",
    event: {
      type: "turn_started",
      ...(threadId ? { threadId } : {}),
      turnId,
      sequence,
      payload: {},
    },
  });
  socket.receive({
    type: "event",
    event: {
      type: "tool_call_started",
      ...(threadId ? { threadId } : {}),
      turnId,
      sequence: sequence + 1,
      payload: {
        toolCallId,
        name: "write_file",
        riskLevel: "high",
        argumentsSummary: { path: "合同.md" },
      },
    },
  });
  socket.receive({
    type: "event",
    event: {
      type: "approval_requested",
      ...(threadId ? { threadId } : {}),
      turnId,
      sequence: sequence + 2,
      payload: {
        approvalId,
        toolCallId,
        name: "write_file",
        riskLevel: "high",
        argumentsSummary: { path: "合同.md" },
        ...(approvalBehavior ? { approvalBehavior } : {}),
      },
    },
  });
}

test("denying first-use web approval notifies the web-access controller", async () => {
  setupGlobals();
  const onWebAccessDenied = vi.fn();
  const { result } = renderHook(() =>
    useAgentChat("thread-1", { onWebAccessDenied }),
  );
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => receiveAwaitingApproval(MockWebSocket.last!, {
    approvalBehavior: "thread_first_use",
  }));

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "approval_resolved",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 4,
        payload: {
          approvalId: "ap-1",
          toolCallId: "tc-1",
          decision: "denied",
        },
      },
    });
  });

  expect(onWebAccessDenied).toHaveBeenCalledTimes(1);
});

test("loads message history on mount", async () => {
  setupGlobals({
    messages: [
      {
        id: "msg-1",
        thread_id: "thread-1",
        turn_id: null,
        role: "user",
        content: "hello",
        created_at: "2026-08-13T00:00:00Z",
      },
      {
        id: "msg-2",
        thread_id: "thread-1",
        turn_id: null,
        role: "assistant",
        content: "hi there",
        created_at: "2026-08-13T00:00:01Z",
      },
    ],
  });

  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => {
    expect(result.current.items).toHaveLength(2);
  });
  expect(result.current.items[0]).toMatchObject({ kind: "user", text: "hello" });
  await waitFor(() => {
    expect(assistantText(result)).toBe("hi there");
  });
});

test("sendMessage adds user message and sends turn.start via WebSocket", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    result.current.sendMessage("test question");
  });

  expect(result.current.items).toHaveLength(1);
  expect(result.current.items[0]).toMatchObject({ kind: "user", text: "test question" });
  expect(MockWebSocket.last!.sentMessages).toContain(
    JSON.stringify({
      type: "turn.start",
      threadId: "thread-1",
      content: "test question",
      attachmentUploadIds: [],
      approvalMode: "ask",
      reasoningEffort: "auto",
    }),
  );
});

test("sendMessage includes attachment uploads and labels the optimistic user message", async () => {
  setupGlobals();
  const accepted = vi.fn();
  const { result } = renderHook(() =>
    useAgentChat("thread-1", { onUserTurnAccepted: accepted }),
  );
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  act(() => MockWebSocket.last!.onopen?.());

  act(() => {
    result.current.sendMessage({
      text: "",
      attachmentUploadIds: ["upl-1"],
      attachmentNames: ["证据.pdf"],
    });
  });

  expect(MockWebSocket.last!.sentMessages).toContain(JSON.stringify({
    type: "turn.start",
    threadId: "thread-1",
    content: "",
    attachmentUploadIds: ["upl-1"],
    approvalMode: "ask",
    reasoningEffort: "auto",
  }));
  expect(result.current.items[0]).toMatchObject({
    kind: "user",
    text: "",
    attachmentNames: ["证据.pdf"],
  });

  act(() => {
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
  });
  expect(accepted).toHaveBeenCalledWith("turn-1", {
    text: "",
    attachmentUploadIds: ["upl-1"],
    attachmentNames: ["证据.pdf"],
  }, "thread-1");
});

test("accumulates streaming text deltas into assistant item", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_started", turnId: "turn-1", sequence: 1, payload: {} },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "message_delta", turnId: "turn-1", sequence: 2, payload: { kind: "text", delta: "Hello " } },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "message_delta", turnId: "turn-1", sequence: 3, payload: { kind: "text", delta: "world" } },
    });
  });

  await waitFor(() => {
    expect(assistantText(result)).toBe("Hello world");
  });
  const item = assistantItem(result);
  if (item?.kind === "assistant") {
    expect(item.isStreaming).toBe(true);
  }

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "message_completed", turnId: "turn-1", sequence: 4, payload: {} },
    });
  });

  if (item?.kind === "assistant") {
    expect(
      (assistantItem(result) as { isStreaming: boolean }).isStreaming,
    ).toBe(false);
  }

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_completed", turnId: "turn-1", sequence: 5, payload: {} },
    });
  });

  expect(result.current.isRunning).toBe(false);
});

test("high-frequency deltas are batched into periodic flushes", async () => {
  vi.useFakeTimers();
  setupGlobals();
  try {
    const { result } = renderHook(() => useAgentChat("thread-1"));
    expect(MockWebSocket.instances.length).toBe(1);
    await act(async () => {
      MockWebSocket.last!.onopen?.();
    });

    act(() => {
      MockWebSocket.last!.receive({
        type: "event",
        event: { type: "turn_started", turnId: "turn-1", sequence: 1, payload: {} },
      });
      for (let index = 0; index < 50; index += 1) {
        MockWebSocket.last!.receive({
          type: "event",
          event: {
            type: "message_delta",
            turnId: "turn-1",
            sequence: 2 + index,
            payload: { kind: "text", delta: "x" },
          },
        });
      }
    });

    // Deltas stay buffered instead of triggering a render storm.
    expect(result.current.items).toHaveLength(0);

    act(() => {
      vi.advanceTimersByTime(200);
    });

    expect(result.current.items).toHaveLength(1);
    expect(assistantText(result)).toBe("x".repeat(50));
  } finally {
    vi.useRealTimers();
  }
});

test("buffered text is flushed before message and turn terminators", async () => {
  vi.useFakeTimers();
  setupGlobals();
  try {
    const { result } = renderHook(() => useAgentChat("thread-1"));
    await act(async () => {
      MockWebSocket.last!.onopen?.();
    });

    act(() => {
      MockWebSocket.last!.receive({
        type: "event",
        event: { type: "turn_started", turnId: "turn-1", sequence: 1, payload: {} },
      });
      MockWebSocket.last!.receive({
        type: "event",
        event: { type: "message_delta", turnId: "turn-1", sequence: 2, payload: { kind: "text", delta: "tail" } },
      });
      // No timer advance: the buffer must still be flushed by the terminator.
      MockWebSocket.last!.receive({
        type: "event",
        event: { type: "message_completed", turnId: "turn-1", sequence: 3, payload: {} },
      });
    });

    expect(assistantText(result)).toBe("tail");
    expect(
      (assistantItem(result) as { isStreaming: boolean }).isStreaming,
    ).toBe(false);
  } finally {
    vi.useRealTimers();
  }
});

test("reasoning deltas stream into a collapsible segment, not plain text", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_started", turnId: "turn-1", sequence: 1, payload: {} },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "message_delta", turnId: "turn-1", sequence: 2, payload: { kind: "reasoning", delta: "thinking..." } },
    });
  });

  await waitFor(() => {
    expect(result.current.isThinking).toBe(true);
  });

  const item = assistantItem(result);
  if (item?.kind === "assistant") {
    expect(item.segments.map((segment) => segment.type)).toEqual(["reasoning"]);
    expect(item.segments[0].text).toBe("thinking...");
  }

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "message_delta", turnId: "turn-1", sequence: 3, payload: { kind: "text", delta: "答案" } },
    });
  });

  await waitFor(() => {
    expect(result.current.isThinking).toBe(false);
  });
  const updated = assistantItem(result);
  if (updated?.kind === "assistant") {
    expect(updated.segments.map((segment) => segment.type)).toEqual(["reasoning", "text"]);
  }
});

test("turn_failed closes the turn and appends a failure notice", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_started", turnId: "turn-1", sequence: 1, payload: {} },
    });
  });
  expect(result.current.isRunning).toBe(true);

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_failed", turnId: "turn-1", sequence: 2, payload: { errorCode: "provider_timeout" } },
    });
  });

  expect(result.current.isRunning).toBe(false);
  expect(noticeItems(result)).toHaveLength(1);
  expect(noticeItems(result)[0]).toMatchObject({
    notice: "turn_failed",
    errorCode: "provider_timeout",
  });
});

test("tool events render tool cards with status and result preview", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "tool_call_started",
        turnId: "turn-1",
        sequence: 1,
        payload: { toolCallId: "tc-1", name: "read_file", argumentsSummary: { path: "a.txt" } },
      },
    });
  });
  expect(toolItems(result)).toHaveLength(1);
  expect(toolItems(result)[0]).toMatchObject({ id: "tc-1", status: "running" });

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "tool_call_completed",
        turnId: "turn-1",
        sequence: 2,
        payload: {
          toolCallId: "tc-1",
          status: "succeeded",
          durationMs: 9,
          resultPreview: "hello",
          resultTruncated: false,
        },
      },
    });
  });
  expect(toolItems(result)[0]).toMatchObject({
    status: "succeeded",
    durationMs: 9,
    resultPreview: "hello",
  });
});

test.each(["allowed", "denied"] as const)(
  "resolveApproval sends one %s decision for the exact awaiting tool",
  async (decision) => {
    setupGlobals();
    const { result } = renderHook(() => useAgentChat("thread-1"));

    await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
    act(() => MockWebSocket.last!.onopen?.());
    act(() => receiveAwaitingApproval(MockWebSocket.last!));

    const sentBefore = MockWebSocket.last!.sentMessages.length;
    act(() => {
      expect(result.current.resolveApproval({
        turnId: "turn-1",
        toolCallId: "tc-1",
        decision,
        reason: "已核对修改范围",
      })).toBe(true);
      // React state has not rendered again within this frame. The synchronous
      // guard must still reject a double click.
      expect(result.current.resolveApproval({
        turnId: "turn-1",
        toolCallId: "tc-1",
        decision,
        reason: "重复点击",
      })).toBe(false);
    });

    expect(MockWebSocket.last!.sentMessages.slice(sentBefore)).toEqual([
      JSON.stringify({
        type: "turn.resolve",
        turnId: "turn-1",
        toolCallId: "tc-1",
        decision,
        reason: "已核对修改范围",
      }),
    ]);
    expect(result.current.approvalSubmissions["turn-1:tc-1"]).toEqual({
      status: "submitting",
    });
  },
);

test("resolveApproval survives an initial history response that lands after turn_started", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("WebSocket", MockWebSocket);
  let releaseHistory!: () => void;
  const historyGate = new Promise<void>((resolve) => {
    releaseHistory = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async () => {
      await historyGate;
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
  MockWebSocket.instances = [];
  MockWebSocket.last = null;

  const { result } = renderHook(() => useAgentChat("thread-1"));
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  act(() => MockWebSocket.last!.onopen?.());
  act(() => {
    expect(result.current.sendMessage("请联网核验")).toBe(true);
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_started",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 1,
        payload: {},
      },
    });
  });

  releaseHistory();
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "tool_call_started",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 2,
        payload: {
          toolCallId: "tc-1",
          name: "web_search",
          riskLevel: "high",
          argumentsSummary: { query: "OpenAI official website" },
        },
      },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "approval_requested",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 3,
        payload: {
          approvalId: "ap-1",
          toolCallId: "tc-1",
          name: "web_search",
          riskLevel: "high",
          approvalBehavior: "thread_first_use",
        },
      },
    });
  });

  const sentBefore = MockWebSocket.last!.sentMessages.length;
  act(() => {
    expect(result.current.resolveApproval({
      turnId: "turn-1",
      toolCallId: "tc-1",
      decision: "allowed",
      reason: "",
    })).toBe(true);
  });
  expect(MockWebSocket.last!.sentMessages.slice(sentBefore)).toEqual([
    JSON.stringify({
      type: "turn.resolve",
      turnId: "turn-1",
      toolCallId: "tc-1",
      decision: "allowed",
      reason: "",
    }),
  ]);
});

test("a completed live reply survives an older initial history response", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("WebSocket", MockWebSocket);
  let releaseHistory!: () => void;
  const historyGate = new Promise<void>((resolve) => {
    releaseHistory = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async () => {
      await historyGate;
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
  MockWebSocket.instances = [];
  MockWebSocket.last = null;

  const { result } = renderHook(() => useAgentChat("thread-1"));
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  act(() => MockWebSocket.last!.onopen?.());
  act(() => {
    expect(result.current.sendMessage("请联网核验")).toBe(true);
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_started",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 1,
        payload: {},
      },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "message_delta",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 2,
        payload: { kind: "text", delta: "核验完成" },
      },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_completed",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 3,
        payload: {},
      },
    });
  });

  expect(result.current.items[0]).toMatchObject({
    kind: "user",
    text: "请联网核验",
  });
  expect(assistantText(result)).toBe("核验完成");

  releaseHistory();
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));

  expect(result.current.items[0]).toMatchObject({
    kind: "user",
    text: "请联网核验",
  });
  expect(assistantText(result)).toBe("核验完成");
});

test("resolveApproval truncates the reason at the 500 character protocol boundary", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => receiveAwaitingApproval(MockWebSocket.last!));

  act(() => {
    expect(result.current.resolveApproval({
      turnId: "turn-1",
      toolCallId: "tc-1",
      decision: "allowed",
      reason: `${"字".repeat(500)}尾`,
    })).toBe(true);
  });

  const frame = JSON.parse(MockWebSocket.last!.sentMessages.at(-1)!) as {
    reason: string;
  };
  expect(frame.reason).toBe("字".repeat(500));
  expect(frame.reason).toHaveLength(500);
});

test("resolveApproval requires an open socket and the exact tool to still await approval", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => receiveAwaitingApproval(MockWebSocket.last!));

  expect(result.current.resolveApproval({
    turnId: "another-turn",
    toolCallId: "tc-1",
    decision: "allowed",
    reason: "",
  })).toBe(false);
  expect(result.current.resolveApproval({
    turnId: "turn-1",
    toolCallId: "another-tool",
    decision: "allowed",
    reason: "",
  })).toBe(false);

  MockWebSocket.last!.readyState = MockWebSocket.CLOSED;
  expect(result.current.resolveApproval({
    turnId: "turn-1",
    toolCallId: "tc-1",
    decision: "allowed",
    reason: "",
  })).toBe(false);
  expect(MockWebSocket.last!.sentMessages).toHaveLength(0);
});

test("a precise resolve error fails only its approval card without rejecting the turn", async () => {
  setupGlobals();
  const rejected = vi.fn();
  const { result } = renderHook(() =>
    useAgentChat("thread-1", { onSubmissionRejected: rejected }),
  );

  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => {
    result.current.sendMessage("请修改合同");
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
    receiveAwaitingApproval(MockWebSocket.last!);
    receiveAwaitingApproval(MockWebSocket.last!, {
      toolCallId: "tc-2",
      approvalId: "ap-2",
      sequence: 4,
    });
  });
  act(() => {
    expect(result.current.resolveApproval({
      turnId: "turn-1",
      toolCallId: "tc-1",
      decision: "allowed",
      reason: "",
    })).toBe(true);
    expect(result.current.resolveApproval({
      turnId: "turn-1",
      toolCallId: "tc-2",
      decision: "denied",
      reason: "",
    })).toBe(true);
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "error",
      requestType: "turn.resolve",
      turnId: "turn-1",
      toolCallId: "tc-1",
      code: "invalid_turn_request",
      message: "Turn request is invalid.",
      retryable: false,
    });
  });

  expect(result.current.approvalSubmissions["turn-1:tc-1"]).toEqual({
    status: "failed",
    code: "invalid_turn_request",
    retryable: false,
  });
  expect(result.current.approvalSubmissions["turn-1:tc-2"]).toEqual({
    status: "submitting",
  });
  expect(result.current.items).toContainEqual(expect.objectContaining({
    kind: "user",
    text: "请修改合同",
    turnId: "turn-1",
  }));
  expect(result.current.isRunning).toBe(true);
  expect(result.current.error).toBeNull();
  expect(rejected).not.toHaveBeenCalled();
});

test("approval_resolved clears only the matching temporary submission", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => {
    receiveAwaitingApproval(MockWebSocket.last!);
    receiveAwaitingApproval(MockWebSocket.last!, {
      toolCallId: "tc-2",
      approvalId: "ap-2",
      sequence: 4,
    });
  });
  act(() => {
    result.current.resolveApproval({
      turnId: "turn-1",
      toolCallId: "tc-1",
      decision: "allowed",
      reason: "",
    });
    result.current.resolveApproval({
      turnId: "turn-1",
      toolCallId: "tc-2",
      decision: "denied",
      reason: "",
    });
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "approval_resolved",
        turnId: "turn-1",
        sequence: 7,
        payload: {
          approvalId: "ap-1",
          toolCallId: "tc-1",
          decision: "allowed",
        },
      },
    });
  });

  expect(result.current.approvalSubmissions["turn-1:tc-1"]).toBeUndefined();
  expect(result.current.approvalSubmissions["turn-1:tc-2"]).toEqual({
    status: "submitting",
  });
  expect(toolItems(result)).toContainEqual(expect.objectContaining({
    id: "tc-1",
    approvalDecision: "allowed",
    status: "running",
  }));
});

test("a late resolve error cannot reopen a card after its persisted resolution", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => receiveAwaitingApproval(MockWebSocket.last!));
  act(() => {
    expect(result.current.resolveApproval({
      turnId: "turn-1",
      toolCallId: "tc-1",
      decision: "allowed",
      reason: "",
    })).toBe(true);
  });
  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "approval_resolved",
        turnId: "turn-1",
        sequence: 4,
        payload: {
          approvalId: "ap-1",
          toolCallId: "tc-1",
          decision: "allowed",
        },
      },
    });
    MockWebSocket.last!.receive({
      type: "error",
      requestType: "turn.resolve",
      turnId: "turn-1",
      toolCallId: "tc-1",
      code: "approval_not_pending",
      message: "迟到的重复决定错误",
      retryable: false,
    });
  });

  expect(result.current.approvalSubmissions["turn-1:tc-1"]).toBeUndefined();
  expect(toolItems(result)).toContainEqual(expect.objectContaining({
    id: "tc-1",
    approvalDecision: "allowed",
    status: "running",
  }));
});

test("a persisted resolution clears an earlier resolve error for the same card", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => receiveAwaitingApproval(MockWebSocket.last!));
  act(() => {
    expect(result.current.resolveApproval({
      turnId: "turn-1",
      toolCallId: "tc-1",
      decision: "allowed",
      reason: "",
    })).toBe(true);
    MockWebSocket.last!.receive({
      type: "error",
      requestType: "turn.resolve",
      turnId: "turn-1",
      toolCallId: "tc-1",
      code: "approval_not_pending",
      message: "决定正在由持久化事件确认",
      retryable: false,
    });
  });
  expect(result.current.approvalSubmissions["turn-1:tc-1"]).toMatchObject({
    status: "failed",
    code: "approval_not_pending",
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "approval_resolved",
        turnId: "turn-1",
        sequence: 4,
        payload: {
          approvalId: "ap-1",
          toolCallId: "tc-1",
          decision: "allowed",
        },
      },
    });
  });

  expect(result.current.approvalSubmissions["turn-1:tc-1"]).toBeUndefined();
  expect(toolItems(result)).toContainEqual(expect.objectContaining({
    id: "tc-1",
    approvalDecision: "allowed",
    status: "running",
  }));
});

test("socket close clears a submitting lock while the persisted awaiting card remains retryable", async () => {
  vi.useFakeTimers();
  setupGlobals();
  try {
    const { result } = renderHook(() => useAgentChat("thread-1"));
    await act(async () => Promise.resolve());
    act(() => MockWebSocket.last!.onopen?.());
    act(() => receiveAwaitingApproval(MockWebSocket.last!));
    act(() => {
      result.current.resolveApproval({
        turnId: "turn-1",
        toolCallId: "tc-1",
        decision: "allowed",
        reason: "",
      });
    });

    act(() => MockWebSocket.last!.onclose?.());

    expect(result.current.approvalSubmissions).toEqual({});
    expect(toolItems(result)).toContainEqual(expect.objectContaining({
      id: "tc-1",
      status: "awaiting_approval",
    }));
  } finally {
    vi.useRealTimers();
  }
});

test("reconnect keeps awaiting approval retryable when no persisted resolution exists", async () => {
  vi.useFakeTimers();
  setupGlobals();
  try {
    const { result } = renderHook(() => useAgentChat("thread-1"));
    await act(async () => Promise.resolve());
    act(() => MockWebSocket.last!.onopen?.());
    act(() => {
      MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
      receiveAwaitingApproval(MockWebSocket.last!);
    });
    act(() => {
      expect(result.current.resolveApproval({
        turnId: "turn-1",
        toolCallId: "tc-1",
        decision: "allowed",
        reason: "",
      })).toBe(true);
      MockWebSocket.last!.onclose?.();
      vi.advanceTimersByTime(600);
    });
    act(() => MockWebSocket.last!.onopen?.());

    expect(result.current.approvalSubmissions).toEqual({});
    expect(toolItems(result)).toContainEqual(expect.objectContaining({
      id: "tc-1",
      status: "awaiting_approval",
    }));
    act(() => {
      expect(result.current.resolveApproval({
        turnId: "turn-1",
        toolCallId: "tc-1",
        decision: "allowed",
        reason: "重试",
      })).toBe(true);
    });
  } finally {
    vi.useRealTimers();
  }
});

test("reconnect adopts a persisted approval resolution as the final card state", async () => {
  vi.useFakeTimers();
  setupGlobals();
  try {
    const { result } = renderHook(() => useAgentChat("thread-1"));
    await act(async () => Promise.resolve());
    act(() => MockWebSocket.last!.onopen?.());
    act(() => {
      MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
      receiveAwaitingApproval(MockWebSocket.last!);
    });
    act(() => {
      expect(result.current.resolveApproval({
        turnId: "turn-1",
        toolCallId: "tc-1",
        decision: "denied",
        reason: "不允许修改",
      })).toBe(true);
      MockWebSocket.last!.onclose?.();
      vi.advanceTimersByTime(600);
    });
    const reconnected = MockWebSocket.last!;
    act(() => reconnected.onopen?.());
    act(() => {
      reconnected.receive({
        type: "event",
        event: {
          type: "approval_resolved",
          turnId: "turn-1",
          sequence: 4,
          payload: {
            approvalId: "ap-1",
            toolCallId: "tc-1",
            decision: "denied",
          },
        },
      });
    });

    expect(result.current.approvalSubmissions).toEqual({});
    expect(toolItems(result)).toContainEqual(expect.objectContaining({
      id: "tc-1",
      approvalDecision: "denied",
      status: "running",
    }));
  } finally {
    vi.useRealTimers();
  }
});

test("a replaced socket cannot fail an approval submitted through the live socket", async () => {
  vi.useFakeTimers();
  setupGlobals();
  try {
    const { result } = renderHook(() => useAgentChat("thread-1"));
    await act(async () => Promise.resolve());
    const stale = MockWebSocket.last!;
    act(() => stale.onopen?.());
    act(() => receiveAwaitingApproval(stale));
    act(() => stale.onclose?.());
    act(() => vi.advanceTimersByTime(600));

    const live = MockWebSocket.last!;
    act(() => live.onopen?.());
    act(() => {
      expect(result.current.resolveApproval({
        turnId: "turn-1",
        toolCallId: "tc-1",
        decision: "allowed",
        reason: "在新连接提交",
      })).toBe(true);
    });
    act(() => {
      stale.receive({
        type: "error",
        requestType: "turn.resolve",
        turnId: "turn-1",
        toolCallId: "tc-1",
        code: "invalid_turn_request",
        message: "旧连接迟到错误",
        retryable: false,
      });
    });

    expect(result.current.approvalSubmissions["turn-1:tc-1"]).toEqual({
      status: "submitting",
    });
  } finally {
    vi.useRealTimers();
  }
});

test("switching threads clears approval submissions and ignores the old thread's late error", async () => {
  setupGlobals();
  const { result, rerender } = renderHook(
    ({ threadId }) => useAgentChat(threadId),
    { initialProps: { threadId: "thread-1" } },
  );
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => receiveAwaitingApproval(MockWebSocket.last!));
  act(() => {
    result.current.resolveApproval({
      turnId: "turn-1",
      toolCallId: "tc-1",
      decision: "allowed",
      reason: "",
    });
  });

  rerender({ threadId: "thread-2" });
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-2"));
  act(() => {
    MockWebSocket.last!.receive({
      type: "error",
      requestType: "turn.resolve",
      turnId: "turn-1",
      toolCallId: "tc-1",
      code: "invalid_turn_request",
      message: "迟到的旧任务错误",
      retryable: false,
    });
  });

  expect(result.current.approvalSubmissions).toEqual({});
  expect(result.current.error).toBeNull();
  expect(result.current.items).toEqual([]);
});

test("queued ack binds the optimistic user message to its turn for precise recovery", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());

  act(() => {
    result.current.sendMessage("请继续检查合同");
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_failed",
        turnId: "turn-1",
        sequence: 1,
        payload: { errorCode: "provider_timeout", retryable: true },
      },
    });
  });

  expect(noticeItems(result)).toContainEqual(expect.objectContaining({
    turnId: "turn-1",
    sourceText: "请继续检查合同",
    recoveryKind: "restore_text",
  }));
});

test("replayed duplicate events do not duplicate content", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    result.current.sendMessage("用于验证事件去重");
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "message_delta", turnId: "turn-1", sequence: 2, payload: { kind: "text", delta: "abc" } },
    });
    // The subscribe-then-replay window redelivers sequence 2.
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "message_delta", turnId: "turn-1", sequence: 2, payload: { kind: "text", delta: "abc" } },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "message_delta", turnId: "turn-1", sequence: 3, payload: { kind: "text", delta: "def" } },
    });
  });

  await waitFor(() => {
    expect(assistantText(result)).toBe("abcdef");
  });
});

test("history with a running turn subscribes after the newest sequence", async () => {
  setupGlobals({
    messages: [
      {
        id: "msg-1",
        thread_id: "thread-1",
        turn_id: "turn-7",
        role: "user",
        content: [{ type: "text", text: "go" }],
        created_at: "2026-08-18T00:00:00Z",
      },
    ],
    turns: [
      {
        id: "turn-7",
        thread_id: "thread-1",
        status: "running",
        error_code: null,
        started_at: null,
        finished_at: null,
        created_at: "2026-08-18T00:00:00Z",
      },
    ],
    events: [
      { turn_id: "turn-7", sequence: 1, type: "turn_started", payload: {} },
      { turn_id: "turn-7", sequence: 2, type: "message_delta", payload: { kind: "text", delta: "部分" } },
    ],
  });

  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  await waitFor(() => {
    expect(assistantText(result)).toBe("部分");
  });
  expect(result.current.isRunning).toBe(true);
  expect(MockWebSocket.last!.sentMessages).toContain(
    JSON.stringify({ type: "turn.subscribe", turnId: "turn-7", after: 2 }),
  );
});

test("stop sends turn.stop for the running turn", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
  });
  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_started", turnId: "turn-1", sequence: 1, payload: {} },
    });
  });

  act(() => {
    result.current.stop();
  });

  expect(MockWebSocket.last!.sentMessages).toContain(
    JSON.stringify({ type: "turn.stop", turnId: "turn-1" }),
  );
});

test("stop requested before the queued ack is sent once the turn id arrives", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    result.current.sendMessage("test question");
    result.current.stop();
  });

  expect(result.current.isRunning).toBe(true);
  expect(MockWebSocket.last!.sentMessages).not.toContain(
    JSON.stringify({ type: "turn.stop", turnId: "turn-1" }),
  );

  act(() => {
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
  });

  expect(MockWebSocket.last!.sentMessages).toContain(
    JSON.stringify({ type: "turn.stop", turnId: "turn-1" }),
  );
});

test("stop after a turn ends no longer sends turn.stop", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_started", turnId: "turn-1", sequence: 1, payload: {} },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_completed", turnId: "turn-1", sequence: 2, payload: {} },
    });
  });

  const sentBefore = MockWebSocket.last!.sentMessages.length;
  act(() => {
    result.current.stop();
  });

  expect(MockWebSocket.last!.sentMessages.length).toBe(sentBefore);
});

test("does nothing without a threadId", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat(null));

  expect(result.current.items).toHaveLength(0);
  expect(result.current.isRunning).toBe(false);
});

test("error event sets error message", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "error",
      code: "invalid_turn_request",
      message: "Turn request is invalid.",
    });
  });

  expect(result.current.error).toBe("Turn request is invalid.");
});

test("a late-closed stale socket must not clear the live connection", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"), {
    wrapper: StrictMode,
  });

  await waitFor(() => expect(MockWebSocket.instances.length).toBe(2));
  const [stale, live] = MockWebSocket.instances;

  // Real browsers deliver close events asynchronously: the socket closed by
  // StrictMode's simulated unmount reports back after the remount connected.
  await act(async () => {
    stale.onclose?.();
  });

  act(() => {
    result.current.sendMessage("你好");
  });

  expect(result.current.error).toBeNull();
  expect(live.sentMessages.length).toBe(1);
  expect(live.sentMessages[0]).toContain("turn.start");
});

test("a stale socket cannot become ready, report errors, or resume a live turn", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"), {
    wrapper: StrictMode,
  });

  await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2));
  const [stale, live] = MockWebSocket.instances;
  act(() => {
    result.current.sendMessage("建立当前 Turn 归属");
    live.receive({ type: "ack", turnId: "turn-live", status: "queued" });
  });

  act(() => stale.onopen?.());
  expect(result.current.connectionReady).toBe(false);
  expect(stale.sentMessages).toEqual([]);

  act(() => stale.onerror?.());
  expect(result.current.error).toBeNull();
});

test("reconnects after an unexpected close and resumes the running turn", async () => {
  vi.useFakeTimers();
  setupGlobals();
  try {
    renderHook(() => useAgentChat("thread-1"));
    expect(MockWebSocket.instances.length).toBe(1);
    await act(async () => {
      MockWebSocket.last!.onopen?.();
    });

    act(() => {
      MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
      MockWebSocket.last!.receive({
        type: "event",
        event: { type: "turn_started", turnId: "turn-1", sequence: 1, payload: {} },
      });
      MockWebSocket.last!.receive({
        type: "event",
        event: { type: "message_delta", turnId: "turn-1", sequence: 2, payload: { kind: "text", delta: "部分" } },
      });
    });

    act(() => {
      MockWebSocket.instances[0].onclose?.();
    });
    act(() => {
      vi.advanceTimersByTime(600);
    });

    expect(MockWebSocket.instances.length).toBe(2);
    act(() => {
      MockWebSocket.instances[1].onopen?.();
    });

    expect(MockWebSocket.instances[1].sentMessages).toContain(
      JSON.stringify({ type: "turn.subscribe", turnId: "turn-1", after: 2 }),
    );
  } finally {
    vi.useRealTimers();
  }
});

test.each(["completed", "failed", "cancelled", "interrupted"])(
  "lost queued ack recovers a %s turn without resending it", async (status) => {
    const oldTurn = {id: "old", status: "completed", created_at: "2020-01-01T00:00:00Z"};
    const oldMessage = {id: "old-message", turn_id: "old", role: "user", content: "same question"};
    const routes: FetchRoutes = {turns: [oldTurn], messages: [oldMessage]};
    setupGlobals(routes);
    const accepted = vi.fn();
    const settled = vi.fn();
    const {result} = renderHook(() => useAgentChat("thread-1", {
      onUserTurnAccepted: accepted, onTurnSettled: settled,
    }));
    await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
    act(() => {MockWebSocket.last!.onopen?.(); result.current.sendMessage("same question");});
    // A stale history response containing only an older identical prompt must
    // not consume this submission.
    act(() => result.current.retryHistory());
    await waitFor(() => expect(result.current.historyToken).toBe(2));
    expect(accepted).not.toHaveBeenCalled();
    routes.turns = [oldTurn, {id: "new", status, created_at: new Date().toISOString()}];
    routes.messages = [oldMessage, {...oldMessage, id: "new-message", turn_id: "new"}];
    act(() => MockWebSocket.last!.onclose?.());
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2), {timeout: 1500});
    await act(async () => MockWebSocket.last!.onopen?.());
    await waitFor(() => expect(result.current.isRunning).toBe(false));
    expect(accepted).toHaveBeenCalledTimes(1);
    expect(accepted.mock.calls[0][0]).toBe("new");
    expect(settled).toHaveBeenCalledTimes(1);
    expect(MockWebSocket.last!.sentMessages).toEqual([]);
    expect(result.current.items.filter(item => item.kind === "user")).toHaveLength(2);
    let sent = false;
    act(() => {sent = result.current.sendMessage("next question");});
    expect(sent).toBe(true);
  },
);

test("sending before history loads cannot adopt an old identical completed prompt", async () => {
  const oldTurn = {id: "old", status: "completed", created_at: "2020-01-01T00:00:00Z"};
  const oldMessage = {id: "old-message", turn_id: "old", role: "user", content: "same"};
  const routes: FetchRoutes = {turns: [oldTurn], messages: [oldMessage]};
  setupGlobals(routes);
  const originalFetch = globalThis.fetch;
  let release!: () => void;
  const gate = new Promise<void>(resolve => {release = resolve;});
  vi.stubGlobal("fetch", vi.fn(async (...args: Parameters<typeof fetch>) => {
    await gate; return originalFetch(...args);
  }));
  const accepted = vi.fn();
  const {result} = renderHook(() => useAgentChat("thread-1", {onUserTurnAccepted: accepted}));
  act(() => {MockWebSocket.last!.onopen?.(); result.current.sendMessage("same");});
  await act(async () => release());
  await waitFor(() => expect(result.current.historyToken).toBe(1));
  act(() => MockWebSocket.last!.onclose?.());
  await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2), {timeout: 1500});
  await act(async () => MockWebSocket.last!.onopen?.());
  await waitFor(() => expect(result.current.historyToken).toBe(2));
  expect(accepted).not.toHaveBeenCalled();
  routes.turns = [oldTurn, {id: "new", status: "completed", created_at: new Date().toISOString()}];
  routes.messages = [oldMessage, {...oldMessage, id: "new-message", turn_id: "new"}];
  act(() => result.current.retryHistory());
  await waitFor(() => expect(accepted).toHaveBeenCalledTimes(1));
  expect(accepted.mock.calls[0][0]).toBe("new");
  expect(result.current.isRunning).toBe(false);
});

test("reconnects before the queued ack and recovers an early stop request", async () => {
  const routes: FetchRoutes = { turns: [], messages: [], events: [] };
  setupGlobals(routes);
  const accepted = vi.fn();
  const { result } = renderHook(() =>
    useAgentChat("thread-1", { onUserTurnAccepted: accepted }),
  );

  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  await act(async () => {
    MockWebSocket.instances[0].onopen?.();
  });

  act(() => {
    result.current.sendMessage("test question");
    result.current.stop();
  });

  routes.turns = [
    {
      id: "turn-1",
      thread_id: "thread-1",
      status: "running",
      error_code: null,
      started_at: "2026-08-22T00:00:00Z",
      finished_at: null,
      created_at: "2026-08-22T00:00:00Z",
    },
  ];
  routes.messages = [
    {
      id: "message-1",
      thread_id: "thread-1",
      turn_id: "turn-1",
      role: "user",
      content: [{ type: "text", text: "test question" }],
      created_at: "2026-08-22T00:00:00Z",
    },
  ];
  routes.events = [
    {
      turn_id: "turn-1",
      sequence: 1,
      type: "turn_started",
      payload: {},
    },
  ];

  act(() => {
    MockWebSocket.instances[0].onclose?.();
  });
  await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2), {
    timeout: 1_500,
  });
  await act(async () => {
    MockWebSocket.instances[1].onopen?.();
  });

  await waitFor(() => {
    expect(MockWebSocket.instances[1].sentMessages).toContain(
      JSON.stringify({ type: "turn.stop", turnId: "turn-1" }),
    );
  });
  expect(accepted).toHaveBeenCalledTimes(1);
  expect(accepted).toHaveBeenCalledWith(
    "turn-1",
    {
      text: "test question",
      attachmentUploadIds: [],
      attachmentNames: [],
    },
    "thread-1",
  );
});

test("reconnect before the queued ack replays an approval that was never rendered", async () => {
  const routes: FetchRoutes = { turns: [], messages: [], events: [] };
  setupGlobals(routes);
  const { result } = renderHook(() => useAgentChat("thread-1"));

  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  await act(async () => {
    MockWebSocket.instances[0].onopen?.();
  });
  act(() => {
    expect(result.current.sendMessage("请联网核验")).toBe(true);
  });

  routes.turns = [
    {
      id: "turn-1",
      thread_id: "thread-1",
      status: "running",
      error_code: null,
      started_at: "2026-08-22T00:00:00Z",
      finished_at: null,
      created_at: "2026-08-22T00:00:00Z",
    },
  ];
  routes.messages = [
    {
      id: "message-1",
      thread_id: "thread-1",
      turn_id: "turn-1",
      role: "user",
      content: [{ type: "text", text: "请联网核验" }],
      created_at: "2026-08-22T00:00:00Z",
    },
  ];
  routes.events = [
    {
      turn_id: "turn-1",
      sequence: 1,
      type: "turn_started",
      payload: {},
    },
    {
      turn_id: "turn-1",
      sequence: 2,
      type: "tool_call_started",
      payload: {
        toolCallId: "tc-1",
        name: "web_search",
        riskLevel: "high",
        argumentsSummary: { query: "OpenAI official website" },
      },
    },
    {
      turn_id: "turn-1",
      sequence: 3,
      type: "approval_requested",
      payload: {
        approvalId: "ap-1",
        toolCallId: "tc-1",
        name: "web_search",
        riskLevel: "high",
        approvalBehavior: "thread_first_use",
      },
    },
  ];

  act(() => {
    MockWebSocket.instances[0].onclose?.();
  });
  await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2), {
    timeout: 1_500,
  });
  await act(async () => {
    MockWebSocket.instances[1].onopen?.();
  });

  await waitFor(() => {
    expect(MockWebSocket.instances[1].sentMessages).toContain(
      JSON.stringify({ type: "turn.subscribe", turnId: "turn-1", after: 0 }),
    );
  });
  act(() => {
    receiveAwaitingApproval(MockWebSocket.instances[1], {
      approvalBehavior: "thread_first_use",
    });
  });

  expect(toolItems(result)).toContainEqual(expect.objectContaining({
    id: "tc-1",
    status: "awaiting_approval",
  }));
  act(() => {
    expect(result.current.resolveApproval({
      turnId: "turn-1",
      toolCallId: "tc-1",
      decision: "allowed",
      reason: "",
    })).toBe(true);
  });
});

test("an older same-thread history failure cannot overwrite a newer reconnect load", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("WebSocket", MockWebSocket);
  let releaseInitialHistory!: () => void;
  const initialHistoryGate = new Promise<void>((resolve) => {
    releaseInitialHistory = resolve;
  });
  let fetchCount = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async () => {
      fetchCount += 1;
      if (fetchCount <= 4) {
        await initialHistoryGate;
        throw new Error("stale history request failed");
      }
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
  MockWebSocket.instances = [];
  MockWebSocket.last = null;

  const { result } = renderHook(() => useAgentChat("thread-1"));
  await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
  act(() => {
    MockWebSocket.instances[0].onopen?.();
    expect(result.current.sendMessage("等待重连恢复")).toBe(true);
    MockWebSocket.instances[0].onclose?.();
  });

  await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2), {
    timeout: 1_500,
  });
  act(() => {
    MockWebSocket.instances[1].onopen?.();
  });
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));

  await act(async () => {
    releaseInitialHistory();
    await new Promise((resolve) => globalThis.setTimeout(resolve, 10));
  });
  expect(result.current.error).toBeNull();
  expect(result.current.historyError).toBeNull();
});

test("unmount close does not schedule reconnection", () => {
  vi.useFakeTimers();
  setupGlobals();
  try {
    const { unmount } = renderHook(() => useAgentChat("thread-1"));
    expect(MockWebSocket.instances.length).toBe(1);

    unmount();
    act(() => {
      vi.advanceTimersByTime(30_000);
    });

    expect(MockWebSocket.instances.length).toBe(1);
  } finally {
    vi.useRealTimers();
  }
});

test("without a launch token the hook reports an error and never connects", () => {
  vi.useFakeTimers();
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("[]", { status: 200 })));
  vi.stubGlobal("WebSocket", MockWebSocket);
  MockWebSocket.instances = [];
  MockWebSocket.last = null;
  try {
    const { result } = renderHook(() => useAgentChat("thread-1"));

    expect(result.current.error).toContain("对话连接");
    expect(MockWebSocket.instances.length).toBe(0);

    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    expect(MockWebSocket.instances.length).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});

test("reports accepted user turns and settled turns through callbacks", async () => {
  setupGlobals();
  const accepted: Array<{ turnId: string; text: string }> = [];
  const settled: string[] = [];
  const { result } = renderHook(() =>
    useAgentChat("thread-1", {
      onUserTurnAccepted: (turnId, submission) =>
        accepted.push({ turnId, text: submission.text }),
      onTurnSettled: () => settled.push("settled"),
    }),
  );

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    result.current.sendMessage("帮我整理合同要点");
  });
  MockWebSocket.last!.receive({ type: "ack", turnId: "turn-9", status: "queued" });

  expect(accepted).toEqual([
    { turnId: "turn-9", text: "帮我整理合同要点" },
  ]);

  MockWebSocket.last!.receive({
    type: "event",
    event: {
      type: "turn_completed",
      turnId: "turn-9",
      sequence: 2,
      payload: {},
    },
  });

  expect(settled).toEqual(["settled"]);
});

test("a rejected turn.start never reports an accepted user turn", async () => {
  setupGlobals();
  const accepted: Array<{ turnId: string; text: string }> = [];
  const rejected = vi.fn();
  const { result } = renderHook(() =>
    useAgentChat("thread-1", {
      onUserTurnAccepted: (turnId, submission) =>
        accepted.push({ turnId, text: submission.text }),
      onSubmissionRejected: rejected,
    }),
  );

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    result.current.sendMessage("不会被接受的消息");
  });
  act(() => {
    MockWebSocket.last!.receive({
      type: "error",
      code: "thread_has_no_provider",
      message: "The thread has no provider configured.",
    });
  });

  expect(accepted).toEqual([]);
  expect(rejected).toHaveBeenCalledWith(
    { text: "不会被接受的消息", attachmentUploadIds: [], attachmentNames: [] },
    "thread_has_no_provider",
    "thread-1",
  );
  expect(result.current.items).toHaveLength(0);
});

test("a queued ack keeps the originating thread when the user switches views", async () => {
  setupGlobals();
  const accepted = vi.fn();
  const { result, rerender } = renderHook(
    ({ threadId }) => useAgentChat(threadId, { onUserTurnAccepted: accepted }),
    { initialProps: { threadId: "draft-1" } },
  );
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  act(() => MockWebSocket.last!.onopen?.());
  act(() => {
    result.current.sendMessage("旧任务的首条消息");
  });

  rerender({ threadId: "thread-2" });
  act(() => {
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-old", status: "queued" });
  });

  expect(accepted).toHaveBeenCalledWith(
    "turn-old",
    {
      text: "旧任务的首条消息",
      attachmentUploadIds: [],
      attachmentNames: [],
    },
    "draft-1",
  );
});

test("a queued ack from the previous thread reports acceptance without adopting its turn", async () => {
  setupGlobals();
  const accepted = vi.fn();
  const { result, rerender } = renderHook(
    ({ threadId }) => useAgentChat(threadId, { onUserTurnAccepted: accepted }),
    { initialProps: { threadId: "thread-1" } },
  );
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => {
    expect(result.current.sendMessage("旧任务消息")).toBe(true);
  });

  rerender({ threadId: "thread-2" });
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-2"));
  act(() => {
    MockWebSocket.last!.receive({
      type: "ack",
      turnId: "turn-old",
      status: "queued",
    });
  });

  expect(accepted).toHaveBeenCalledWith(
    "turn-old",
    { text: "旧任务消息", attachmentUploadIds: [], attachmentNames: [] },
    "thread-1",
  );
  expect(result.current.runningTurnId).toBeNull();
  expect(result.current.isRunning).toBe(false);
  expect(result.current.items).toEqual([]);
});

test("a storage error from the previous thread cannot consume the selected thread's pending start", async () => {
  setupGlobals();
  const accepted = vi.fn();
  const rejected = vi.fn();
  const { result, rerender } = renderHook(
    ({ threadId }) => useAgentChat(threadId, {
      onUserTurnAccepted: accepted,
      onSubmissionRejected: rejected,
    }),
    { initialProps: { threadId: "thread-a" } },
  );
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-a"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => {
    expect(result.current.sendMessage("A任务消息")).toBe(true);
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-a", status: "queued" });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_started",
        threadId: "thread-a",
        turnId: "turn-a",
        sequence: 1,
        payload: {},
      },
    });
  });
  accepted.mockClear();

  rerender({ threadId: "thread-b" });
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-b"));
  act(() => {
    expect(result.current.sendMessage("B任务待确认消息")).toBe(true);
  });
  act(() => {
    MockWebSocket.last!.receive({
      type: "error",
      turnId: "turn-a",
      code: "internal_error",
      message: "A任务本地记录失败",
      retryable: false,
    });
  });

  expect(result.current.items).toContainEqual(expect.objectContaining({
    kind: "user",
    text: "B任务待确认消息",
  }));
  expect(result.current.error).toBeNull();
  expect(rejected).not.toHaveBeenCalled();

  act(() => {
    MockWebSocket.last!.receive({
      type: "ack",
      turnId: "turn-b",
      status: "queued",
    });
  });
  expect(accepted).toHaveBeenCalledWith(
    "turn-b",
    {
      text: "B任务待确认消息",
      attachmentUploadIds: [],
      attachmentNames: [],
    },
    "thread-b",
  );
  expect(result.current.items).toContainEqual(expect.objectContaining({
    kind: "user",
    text: "B任务待确认消息",
    turnId: "turn-b",
  }));
});

test("a start rejection for the previous thread cannot stop the selected thread restored from history", async () => {
  const routes: FetchRoutes = { messages: [], turns: [], events: [] };
  setupGlobals(routes);
  const rejected = vi.fn();
  const { result, rerender } = renderHook(
    ({ threadId }) => useAgentChat(threadId, { onSubmissionRejected: rejected }),
    { initialProps: { threadId: "thread-a" } },
  );
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-a"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => {
    expect(result.current.sendMessage("A任务等待确认")).toBe(true);
  });

  routes.messages = [
    {
      id: "message-b",
      thread_id: "thread-b",
      turn_id: "turn-b",
      role: "user",
      content: [{ type: "text", text: "B任务正在运行" }],
      created_at: "2026-08-22T00:00:00Z",
    },
  ];
  routes.turns = [
    {
      id: "turn-b",
      thread_id: "thread-b",
      status: "running",
      error_code: null,
      started_at: "2026-08-22T00:00:00Z",
      finished_at: null,
      created_at: "2026-08-22T00:00:00Z",
    },
  ];
  routes.events = [
    {
      turn_id: "turn-b",
      sequence: 1,
      type: "turn_started",
      payload: {},
    },
    {
      turn_id: "turn-b",
      sequence: 2,
      type: "message_delta",
      payload: { kind: "text", delta: "B正在处理" },
    },
  ];

  rerender({ threadId: "thread-b" });
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-b"));
  expect(result.current.runningTurnId).toBe("turn-b");
  expect(result.current.isRunning).toBe(true);
  const itemsBeforeError = result.current.items;

  act(() => {
    MockWebSocket.last!.receive({
      type: "error",
      code: "thread_has_no_provider",
      message: "A任务没有配置模型",
      retryable: false,
    });
  });

  expect(rejected).toHaveBeenCalledWith(
    {
      text: "A任务等待确认",
      attachmentUploadIds: [],
      attachmentNames: [],
    },
    "thread_has_no_provider",
    "thread-a",
  );
  expect(result.current.runningTurnId).toBe("turn-b");
  expect(result.current.isRunning).toBe(true);
  expect(result.current.items).toEqual(itemsBeforeError);
  expect(result.current.error).toBeNull();

  const sentBeforeStop = MockWebSocket.last!.sentMessages.length;
  act(() => result.current.stop());
  expect(MockWebSocket.last!.sentMessages.slice(sentBeforeStop)).toEqual([
    JSON.stringify({ type: "turn.stop", turnId: "turn-b" }),
  ]);
});

test("a current turn storage error leaves an unrelated pending start awaiting its ack", async () => {
  setupGlobals();
  const accepted = vi.fn();
  const rejected = vi.fn();
  const { result } = renderHook(() => useAgentChat("thread-1", {
    onUserTurnAccepted: accepted,
    onSubmissionRejected: rejected,
  }));
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => {
    result.current.sendMessage("第一轮");
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_started",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 1,
        payload: {},
      },
    });
  });
  accepted.mockClear();
  act(() => {
    expect(result.current.sendMessage("下一轮待确认")).toBe(true);
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "error",
      turnId: "turn-1",
      code: "internal_error",
      message: "当前轮本地记录失败",
      retryable: false,
    });
  });

  expect(result.current.error).toBe("当前轮本地记录失败");
  expect(result.current.isRunning).toBe(true);
  expect(rejected).not.toHaveBeenCalled();
  expect(result.current.items).toContainEqual(expect.objectContaining({
    kind: "user",
    text: "下一轮待确认",
  }));

  act(() => {
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-2", status: "queued" });
  });
  expect(accepted).toHaveBeenCalledWith(
    "turn-2",
    { text: "下一轮待确认", attachmentUploadIds: [], attachmentNames: [] },
    "thread-1",
  );
});

test("a queued ack without pending submission or known ownership is not adopted", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());

  act(() => {
    MockWebSocket.last!.receive({
      type: "ack",
      turnId: "turn-unknown",
      status: "queued",
    });
  });

  expect(result.current.runningTurnId).toBeNull();
  expect(result.current.isRunning).toBe(false);
  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_started",
        threadId: "thread-other",
        turnId: "turn-unknown",
        sequence: 1,
        payload: {},
      },
    });
  });
  expect(result.current.runningTurnId).toBeNull();
  expect(result.current.items).toEqual([]);
});

test("events from a previous thread cannot add an approval card to the selected thread", async () => {
  setupGlobals();
  const { result, rerender } = renderHook(
    ({ threadId }) => useAgentChat(threadId),
    { initialProps: { threadId: "thread-1" } },
  );
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());

  rerender({ threadId: "thread-2" });
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-2"));
  act(() => {
    receiveAwaitingApproval(MockWebSocket.last!, { threadId: "thread-1" });
  });

  expect(result.current.items).toEqual([]);
  expect(result.current.isRunning).toBe(false);
  expect(result.current.resolveApproval({
    turnId: "turn-1",
    toolCallId: "tc-1",
    decision: "allowed",
    reason: "不应发送",
  })).toBe(false);
  expect(MockWebSocket.last!.sentMessages).toEqual([]);
});

test("events from an older turn cannot replace the selected thread's active turn", async () => {
  setupGlobals();
  const { result } = renderHook(() => useAgentChat("thread-1"));
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => {
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-current", status: "queued" });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_started",
        threadId: "thread-1",
        turnId: "turn-current",
        sequence: 1,
        payload: {},
      },
    });
  });

  act(() => {
    receiveAwaitingApproval(MockWebSocket.last!, {
      threadId: "thread-1",
      turnId: "turn-old",
    });
  });

  expect(result.current.runningTurnId).toBe("turn-current");
  expect(result.current.items).toEqual([]);
});

test("an approval callback captured before a thread switch is rejected before passive cleanup", async () => {
  setupGlobals();
  const attempted = vi.fn();
  const request = {
    turnId: "turn-1",
    toolCallId: "tc-1",
    decision: "allowed" as const,
    reason: "旧页面点击",
  };
  const { result, rerender } = renderHook(
    ({ threadId }) => {
      const chat = useAgentChat(threadId);
      const previousResolve = useRef(chat.resolveApproval);
      useLayoutEffect(() => {
        if (threadId === "thread-2") {
          attempted(previousResolve.current(request));
        } else {
          previousResolve.current = chat.resolveApproval;
        }
      }, [chat.resolveApproval, threadId]);
      return chat;
    },
    { initialProps: { threadId: "thread-1" } },
  );
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  act(() => MockWebSocket.last!.onopen?.());
  act(() => receiveAwaitingApproval(MockWebSocket.last!, { threadId: "thread-1" }));
  const sentBefore = MockWebSocket.last!.sentMessages.length;

  rerender({ threadId: "thread-2" });

  expect(attempted).toHaveBeenCalledWith(false);
  expect(MockWebSocket.last!.sentMessages).toHaveLength(sentBefore);
});

test("a pending ack in another thread produces visible guidance instead of a silent send", async () => {
  setupGlobals();
  const { result, rerender } = renderHook(
    ({ threadId }) => useAgentChat(threadId),
    { initialProps: { threadId: "thread-1" } },
  );
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  act(() => MockWebSocket.last!.onopen?.());
  act(() => {
    expect(result.current.sendMessage("等待确认")).toBe(true);
  });

  rerender({ threadId: "thread-2" });
  act(() => {
    expect(result.current.sendMessage("第二个任务")).toBe(false);
  });

  expect(result.current.error).toBe("另一个任务的消息仍在确认，请稍后再试。");
  expect(result.current.pendingAckToken).toBe(0);

  act(() => {
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-1", status: "queued" });
  });
  expect(result.current.pendingAckToken).toBe(1);
  act(() => {
    expect(result.current.sendMessage("第二个任务")).toBe(true);
  });
});

test('selected knowledge sources are copied into this turn start', async () => {
  setupGlobals();
  const {result} = renderHook(() => useAgentChat('thread-1'));
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {MockWebSocket.last?.onopen?.();});
  const selected = ['library-one'];
  act(() => {result.current.sendMessage({text:'引用原文',attachmentUploadIds:[],attachmentNames:[],knowledgeLibraryIds:selected});});
  selected.push('not-selected');
  const sent = MockWebSocket.last!.sentMessages.map(message => JSON.parse(message)).find(message => message.type === 'turn.start');
  expect(sent.knowledgeLibraryIds).toEqual(['library-one']);
});

test("history retry clears only history failure without starting a model turn", async () => {
  setupGlobals();
  const workingFetch = globalThis.fetch;
  let fail = true;
  vi.stubGlobal("fetch", vi.fn(async (...args: Parameters<typeof fetch>) => {
    if (fail) throw new TypeError("Failed to fetch");
    return workingFetch(...args);
  }));
  const {result} = renderHook(() => useAgentChat("thread-1"));
  await waitFor(() => expect(result.current.historyError).toContain("历史消息未能加载"));
  expect(result.current.error).toBeNull();
  act(() => { MockWebSocket.last!.onerror?.(); });
  expect(result.current.error).toBe("WebSocket 连接错误");
  fail = false;
  act(() => result.current.retryHistory());
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe("thread-1"));
  expect(result.current.historyError).toBeNull();
  expect(result.current.historyLoading).toBe(false);
  expect(result.current.error).toBe("WebSocket 连接错误");
  expect(MockWebSocket.last!.sentMessages.some(value => value.includes("turn.start"))).toBe(false);
});


test("litigation send captures unsaved editor text and consumes selection once", async()=>{
 setupGlobals();
 const document={type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'未保存文字'}]}]};
 let selection:{from:number;to:number;text:string}|null={from:1,to:4,text:'未保存'};
 const wrapper=({children}:{children:import('react').ReactNode})=>createElement(EditorDiscussionProvider,{workspaceId:'case',threadId:'thread-1',children});
 const {result}=renderHook(()=>{
   const discussion=useEditorDiscussion();
   useLayoutEffect(()=>{discussion?.setTarget({id:'product',title:'文书',kind:'documents',capture:()=>({product_id:'product',base_hash:'a'.repeat(64),document,selection}),clearSelection:()=>{selection=null;},apply:()=>{}});},[]);
   return useAgentChat('thread-1');
 },{wrapper});
 await waitFor(()=>expect(MockWebSocket.last).not.toBeNull());
 act(()=>{expect(result.current.sendMessage('润色选中文字')).toBe(true);});
 const sent=MockWebSocket.last!.sentMessages.map(x=>JSON.parse(x)).find(x=>x.type==='turn.start');
 expect(sent.content).toBe('润色选中文字');
 expect(sent.editorContext.document).toEqual(document);
 expect(sent.editorContext.selection.text).toBe('未保存');
 expect(selection).toBeNull();
 document.content[0].content[0].text='发送后又修改';
 expect(sent.editorContext.document.content[0].content[0].text).toBe('未保存文字');
});

test("contract selection is carried by the normal chat transport and cleared after send", async () => {
  setupGlobals();
  const snapshot = {review_id:'review-1',revision:3,paragraph_id:'/w:document/w:body/w:p[2]',start:0,end:2,selected_text:'付款'};
  const sent = vi.fn();
  const wrapper = ({children}: {children: import('react').ReactNode}) => createElement(ContractEditorProvider, {children});
  const {result} = renderHook(() => {
    const context = useContractEditorContext();
    useLayoutEffect(() => context.register('thread-1', {capture: () => snapshot, sent}), [context]);
    return useAgentChat('thread-1');
  }, {wrapper});
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  act(() => MockWebSocket.last!.onopen?.());
  act(() => expect(result.current.sendMessage('修改这处')).toBe(true));
  const message = MockWebSocket.last!.sentMessages.map(value => JSON.parse(value)).find(value => value.type === 'turn.start');
  expect(message.contractEditorContext).toEqual(snapshot);
  expect(sent).toHaveBeenCalledOnce();
});

test("contract unsaved text blocks send without losing the composer draft or creating a user bubble", async () => {
  setupGlobals();
  const sent = vi.fn();
  const wrapper = ({children}: {children: import('react').ReactNode}) => createElement(ContractEditorProvider, {children});
  const {result} = renderHook(() => {
    const context = useContractEditorContext();
    useLayoutEffect(() => context.register('thread-1', {capture: () => {throw new Error('正文正在保存，请稍后重试。');}, sent}), [context]);
    return useAgentChat('thread-1');
  }, {wrapper});
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  act(() => MockWebSocket.last!.onopen?.());
  act(() => expect(result.current.sendMessage('润色')).toBe(false));
  expect(result.current.error).toBe('正文正在保存，请稍后重试。');
  expect(result.current.items).toHaveLength(0);
  expect(MockWebSocket.last!.sentMessages.some(value => JSON.parse(value).type === 'turn.start')).toBe(false);
  expect(sent).not.toHaveBeenCalled();
});

function editHistory() {
  const messages = ['before', 'old prompt', 'discarded followup'].map((text, i) => ({
    id: `m${i}`, thread_id: 'thread-edit', turn_id: `t${i}`, sequence: i + 1,
    role: 'user', content: [{type: 'text', text}], created_at: '2026-10-12T01:00:00Z',
  }));
  const turns = messages.map(m => ({id: m.turn_id, thread_id: 'thread-edit', status: 'completed', created_at: m.created_at}));
  return {messages: [...messages, {id: 'answer', thread_id: 'thread-edit', turn_id: 't1', sequence: 4,
    role: 'assistant', content: [{type: 'text', text: 'obsolete answer'}], created_at: '2026-10-12T01:00:01Z'}], turns};
}

test('edit keeps old history until ack, then removes its tail and ignores late old events', async () => {
  setupGlobals(editHistory());
  const {result, unmount} = renderHook(() => useAgentChat('thread-edit'));
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe('thread-edit'));
  const socket = MockWebSocket.last!;
  act(() => { expect(result.current.sendMessage({text: 'edited', replaceTurnId: 't1', attachmentUploadIds: [], attachmentNames: []})).toBe(true); });
  expect(JSON.parse(socket.sentMessages.at(-1)!)).toMatchObject({replaceTurnId: 't1', content: 'edited'});
  expect(result.current.items.some(i => i.kind === 'user' && i.text === 'old prompt')).toBe(true);
  act(() => socket.receive({type: 'ack', status: 'queued', turnId: 't-new'}));
  expect(result.current.items.filter(i => i.kind === 'user').map(i => i.text)).toEqual(['before', 'edited']);
  expect(result.current.items.some(i => i.kind === 'assistant')).toBe(false);
  act(() => socket.receive({type: 'event', event: {type: 'turn_completed', threadId: 'thread-edit', turnId: 't-new', sequence: 1, payload: {}}}));
  act(() => socket.receive({type: 'event', event: {type: 'turn_started', threadId: 'thread-edit', turnId: 't2', sequence: 10, payload: {}}}));
  expect(result.current.isRunning).toBe(false);
  expect(result.current.items.some(i => i.turnId === 't2')).toBe(false);
  unmount();
});

test('rejected edit preserves original history and edited submission for retry', async () => {
  setupGlobals(editHistory());
  const rejected = vi.fn();
  const {result, unmount} = renderHook(() => useAgentChat('thread-edit', {onSubmissionRejected: rejected}));
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe('thread-edit'));
  const socket = MockWebSocket.last!;
  act(() => { result.current.sendMessage({text: 'edited', replaceTurnId: 't1', preserveDraft: true, attachmentUploadIds: [], attachmentNames: []}); });
  act(() => socket.receive({type: 'error', code: 'attachment_blob_missing', message: '附件不可用'}));
  expect(result.current.items.filter(i => i.kind === 'user').map(i => i.text)).toEqual(['before', 'old prompt', 'discarded followup']);
  expect(rejected).toHaveBeenCalledWith(expect.objectContaining({text: 'edited', replaceTurnId: 't1'}), 'attachment_blob_missing', 'thread-edit');
  expect(result.current.isRunning).toBe(false);
  unmount();
});

test('lost edit ack reloads only the accepted branch without sending twice', async () => {
  const routes: FetchRoutes = editHistory();
  setupGlobals(routes);
  const accepted = vi.fn();
  const {result, unmount} = renderHook(() => useAgentChat('thread-edit', {onUserTurnAccepted: accepted}));
  await waitFor(() => expect(result.current.historyLoadedThreadId).toBe('thread-edit'));
  act(() => { MockWebSocket.last!.onopen?.(); result.current.sendMessage({text:'edited',replaceTurnId:'t1',attachmentUploadIds:[],attachmentNames:[]}); });
  routes.turns = [{id:'t0',status:'completed',created_at:'2026-10-12T01:00:00Z'}, {id:'new',status:'completed',created_at:new Date().toISOString()}];
  routes.messages = [{id:'m0',turn_id:'t0',role:'user',content:'before'}, {id:'new-message',turn_id:'new',role:'user',content:'edited'}];
  act(() => MockWebSocket.last!.onclose?.());
  await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2), {timeout:1500});
  await act(async () => MockWebSocket.last!.onopen?.());
  await waitFor(() => expect(result.current.isRunning).toBe(false));
  expect(accepted).toHaveBeenCalledOnce();
  expect(MockWebSocket.last!.sentMessages).toEqual([]);
  expect(result.current.items.filter(i => i.kind === 'user').map(i => i.text)).toEqual(['before','edited']);
  unmount();
});

test('explicit drafting snapshot survives starting a new discussion without an active target',async()=>{
 setupGlobals();
 const wrapper=({children}:{children:import('react').ReactNode})=>createElement(EditorDiscussionProvider,{workspaceId:'case',threadId:'thread-2',children});
 const {result}=renderHook(()=>useAgentChat('thread-2'),{wrapper});
 await waitFor(()=>expect(MockWebSocket.last).not.toBeNull());
 const editorContext={product_id:'draft',base_hash:'a'.repeat(64),document:{type:'doc',content:[{type:'paragraph'}]},selection:null,operation:'generate' as const};
 act(()=>{expect(result.current.sendMessage({text:'首次起草',editorContext,attachmentUploadIds:[],attachmentNames:[]})).toBe(true);});
 const sent=MockWebSocket.last!.sentMessages.map(x=>JSON.parse(x)).find(x=>x.type==='turn.start');
 expect(sent.editorContext).toEqual(editorContext);
});


test("ordinary case discussion does not capture the previous document selection",async()=>{
 setupGlobals();
 const capture=vi.fn(()=>({product_id:'document',base_hash:'a'.repeat(64),document:{type:'doc'},selection:null}));
 function Target({children}:{children:import('react').ReactNode}){
  const discussion=useEditorDiscussion();
  useLayoutEffect(()=>{discussion?.setTarget({id:'document',title:'答辩状',kind:'documents',capture,clearSelection:()=>{},apply:()=>{}});},[]);
  return createElement(OrdinaryDiscussion,{enabled:true,children});
 }
 const wrapper=({children}:{children:import('react').ReactNode})=>createElement(EditorDiscussionProvider,{workspaceId:'case',threadId:'thread-1',children:createElement(Target,{children})});
 const {result}=renderHook(()=>useAgentChat('thread-1'),{wrapper});
 await waitFor(()=>expect(MockWebSocket.last).not.toBeNull());
 act(()=>{expect(result.current.sendMessage('讨论案件整体思路')).toBe(true);});
 const sent=MockWebSocket.last!.sentMessages.map(x=>JSON.parse(x)).find(x=>x.type==='turn.start');
 expect(sent.content).toBe('讨论案件整体思路');expect(sent.editorContext).toBeUndefined();expect(capture).not.toHaveBeenCalled();
});


test('litigation file focus is sent separately from visible text and queued snapshot wins over current selection',async()=>{
 setupGlobals();
 const current={workspaceId:'w',paths:['current.txt']};
 const wrapper=({children}:any)=>createElement(LitigationMaterialsContext.Provider,{value:current},children);
 const {result}=renderHook(()=>useAgentChat('thread-1'),{wrapper});
 await waitFor(()=>expect(MockWebSocket.last).not.toBeNull());
 act(()=>MockWebSocket.last!.onopen?.());
 act(()=>result.current.sendMessage({text:'分析文件',attachmentUploadIds:[],attachmentNames:[],litigationMaterials:{workspaceId:'w',paths:['queued.txt']}}));
 const sent=MockWebSocket.last!.sentMessages.map(s=>JSON.parse(s)).find(m=>m.type==='turn.start');
 expect(sent.content).toBe('分析文件');expect(sent.litigationMaterials).toEqual({workspaceId:'w',paths:['queued.txt']});
});
