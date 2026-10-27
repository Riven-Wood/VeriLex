import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { BackgroundTaskCard } from "../src/components/timeline/BackgroundTaskCard";
import { AgentWorkspaceView } from "../src/views/AgentWorkspaceView";
import { toolPresentation } from "../src/timeline/toolPresentation";
import type { BackgroundTaskRecord } from "../src/runtime/contracts";
import type { ToolItem } from "../src/timeline/items";
import * as api from "../src/runtime/api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function task(overrides: Partial<BackgroundTaskRecord> = {}): BackgroundTaskRecord {
  return {
    id: "bt-1",
    toolCallId: "tc-1",
    command: "python3 convert.py",
    description: "批量转换文档",
    status: "running",
    exitCode: null,
    omittedBytes: 0,
    startedAt: "2026-08-25T10:00:00Z",
    finishedAt: null,
    createdAt: "2026-08-25T10:00:00Z",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// toolPresentation copy for the six new tools

function presentationFor(name: string): { title: string; impact: string } {
  const item = {
    kind: "tool",
    id: "tc-1",
    name,
    status: "awaiting_approval",
    argumentsSummary:
      name === "delete_path"
        ? { path: "ws/草稿.docx" }
        : name === "copy_path" || name === "move_path"
          ? { source: "ws/合同.txt", destination: "ws/archive/合同.txt" }
          : { command: "python3 convert.py" },
  } as unknown as ToolItem;
  return toolPresentation(item);
}

test("new tools have Chinese display names", () => {
  expect(presentationFor("run_background_command").title).toBe("后台运行命令");
  expect(presentationFor("background_task_output").title).toBe("查看后台命令进度");
  expect(presentationFor("background_task_stop").title).toBe("停止后台命令");
  expect(presentationFor("copy_path").title).toContain("复制文件");
  expect(presentationFor("move_path").title).toContain("移动/重命名");
  expect(presentationFor("delete_path").title).toContain("删除文件");
});

test("background start impact mentions running in background and stopping", () => {
  const { impact } = presentationFor("run_background_command");
  expect(impact).toContain("后台");
  expect(impact).toContain("随时停止");
});

test("delete impact explains the trash semantics", () => {
  expect(presentationFor("delete_path").impact).toContain("将移到废纸篓，可从废纸篓恢复");
});

test("copy and move impacts mention workspace file changes", () => {
  expect(presentationFor("copy_path").impact).toContain("复制");
  expect(presentationFor("move_path").impact).toContain("移动");
});

// ---------------------------------------------------------------------------
// BackgroundTaskCard behavior

test("running card shows command, description, elapsed time, output and stop", () => {
  const onStop = vi.fn();
  render(
    <BackgroundTaskCard
      threadId="thread-1"
      task={task()}
      live={{ outputText: "converted 3/10", status: "running", error: null }}
      onStop={onStop}
      nowMs={() => Date.parse("2026-08-25T10:02:30Z")}
    />,
  );

  expect(screen.getByText(/python3 convert.py/)).toBeDefined();
  expect(screen.getByText(/批量转换文档/)).toBeDefined();
  expect(screen.getByText(/已运行 2 分 30 秒/)).toBeDefined();
  expect(screen.getByText(/converted 3\/10/)).toBeDefined();

  const stop = screen.getByRole("button", { name: "停止" });
  fireEvent.click(stop);
  expect(onStop).toHaveBeenCalledWith("bt-1");
});

test("stop button is disabled while a stop request is pending", () => {
  render(
    <BackgroundTaskCard
      threadId="thread-1"
      task={task()}
      live={{ outputText: "", status: "running", error: null }}
      stopPending
    />,
  );
  expect(screen.getByRole("button", { name: "停止" }).hasAttribute("disabled")).toBe(
    true,
  );
});

test("terminal statuses map to Chinese labels", () => {
  const labels: Array<[BackgroundTaskRecord["status"], RegExp]> = [
    ["completed", /成功/],
    ["failed", /失败/],
    ["stopped", /已停止/],
    ["expired", /已超时停止/],
    ["interrupted", /随应用退出中断/],
  ];
  for (const [status, label] of labels) {
    const { unmount } = render(
      <BackgroundTaskCard
      threadId="thread-1"
      task={task({ status, finishedAt: "2026-08-25T10:01:00Z" })}
    />,
    );
    expect(screen.getByText(label)).toBeDefined();
    expect(screen.queryByRole("button", { name: "停止" })).toBeNull();
    unmount();
  }
});

test("interrupted card explains the interruption instead of pretending to run", () => {
  render(
    <BackgroundTaskCard
      threadId="thread-1"
      task={task({ status: "interrupted", finishedAt: "2026-08-25T10:01:00Z" })}
    />,
  );
  expect(screen.getByText(/应用退出而中断/)).toBeDefined();
  expect(screen.queryByText(/执行中/)).toBeNull();
});

test("terminal card can expand retained output with the omission marker", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:8766";
  globalThis.__VERILEX_API_TOKEN__ = "token";
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ...task({ status: "completed", exitCode: 0, finishedAt: "2026-08-25T10:01:00Z" }),
          outputText: "…[278543 bytes omitted]…\nDONE",
        }),
        { status: 200 },
      ),
    ),
  );

  render(
    <BackgroundTaskCard
      threadId="thread-1"
      task={task({ status: "completed", exitCode: 0, finishedAt: "2026-08-25T10:01:00Z" })}
    />,
  );

  const toggle = screen.getByRole("button", { name: /查看最终输出/ });
  await act(async () => {
    fireEvent.click(toggle);
  });

  await waitFor(() => {
    expect(screen.getByText(/278543 bytes omitted/)).toBeDefined();
    expect(screen.getByText(/DONE/)).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Timeline wiring: run_background_command card becomes the live task card

test("workspace renders the background task card bound to the tool call", async () => {
  Element.prototype.scrollIntoView = () => {};
  setupViewGlobals();
  let started = false;
  const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/background-tasks")) {
      return new Response(JSON.stringify(started ? [task()] : []), { status: 200 });
    }
    if (url.endsWith("/messages") || url.endsWith("/turns") || url.endsWith("/events")) {
      return new Response(JSON.stringify([]), { status: 200 });
    }
    return new Response(JSON.stringify([]), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<AgentWorkspaceView {...viewProps()} />);
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "ack",
      turnId: "turn-1",
      status: "queued",
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_started",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 1,
        payload: { turnId: "turn-1" },
      },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "tool_call_started",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 2,
        payload: {
          toolCallId: "tc-1",
          name: "run_background_command",
          riskLevel: "high",
          argumentsSummary: { command: "python3 convert.py" },
        },
      },
    });
  });
  // Approval can leave the initial list empty for an arbitrary time. The
  // same tool card later changes status without changing the card count.
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) =>
    String(url).includes("/background-tasks")).length).toBe(2));
  await act(async () => {});
  expect(screen.queryByLabelText("后台命令实时输出")).toBeNull();
  started = true;
  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "tool_call_completed",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 3,
        payload: {
          toolCallId: "tc-1",
          status: "succeeded",
          resultPreview: "task_id: bt-1",
        },
      },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_completed",
        threadId: "thread-1",
        turnId: "turn-1",
        sequence: 4,
        payload: {},
      },
    });
  });

  // The finished turn folds under its duration header; unfold the log first.
  const processToggle = await waitFor(() =>
    screen.getByRole("button", { name: /用时/ }),
  );
  await act(async () => {
    processToggle.click();
  });
  await waitFor(() => {
    expect(screen.getByText(/后台运行命令/)).toBeDefined();
    expect(screen.getByText(/python3 convert.py/)).toBeDefined();
    expect(screen.getByLabelText("后台命令实时输出")).toBeDefined();
    expect(screen.getByRole("button", { name: "停止" })).toBeDefined();
  });
});

class MockWebSocket {
  static OPEN = 1;
  static last: MockWebSocket | null = null;
  readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sentMessages: string[] = [];
  constructor(public url: string, public subprotocol: string) {
    MockWebSocket.last = this;
  }
  send(data: string) {
    this.sentMessages.push(data);
  }
  close() {
    this.readyState = 3;
  }
  receive(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

function setupViewGlobals() {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:8766";
  globalThis.__VERILEX_API_TOKEN__ = "token";
  vi.stubGlobal("WebSocket", MockWebSocket);
}

function viewProps() {
  return {
    hasModel: true,
    thread: {
      id: "local-1",
      backendId: "thread-1",
      title: "会话",
      createdAt: "2026-08-18T00:00:00Z",
      updatedAt: "2026-08-18T00:00:00Z",
      status: "idle",
    },
    workspaceName: "工作区",
    draft: "",
    onDraftChange: () => {},
    pendingAgentMessage: null,
    onPendingAgentMessageSent: () => {},
    onSendWithoutThread: () => {},
    onOpenSettings: () => {},
    onThreadsRefresh: () => {},
    onUserTurnAccepted: () => {},
  };
}
