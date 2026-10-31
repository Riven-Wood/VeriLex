import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

// Count every markdown render so the test can prove completed messages are
// not re-parsed while another message is still streaming.
const markdownRenders: string[] = [];
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => {
    markdownRenders.push(typeof children === "string" ? children : "");
    return <div data-testid="markdown">{children}</div>;
  },
}));

import { AgentWorkspaceView } from "../src/views/AgentWorkspaceView";

afterEach(() => {
  // Vitest globals are off, so testing-library cannot auto-clean; unmount
  // explicitly or a previous test's view keeps handling scroll events.
  cleanup();
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
  markdownRenders.length = 0;
});

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static last: MockWebSocket | null = null;
  readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sentMessages: string[] = [];
  constructor(public url: string, public subprotocol: string) {
    MockWebSocket.instances.push(this);
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

function setupGlobals() {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("WebSocket", MockWebSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async () => new Response("[]", { status: 200 })),
  );
  MockWebSocket.instances = [];
  MockWebSocket.last = null;
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

function delta(turnId: string, sequence: number, text: string) {
  return {
    type: "event",
    event: {
      type: "message_delta",
      turnId,
      sequence,
      payload: { kind: "text", delta: text },
    },
  };
}

test("completed messages are not re-rendered while another message streams", async () => {
  setupGlobals();
  render(<AgentWorkspaceView {...viewProps()} />);
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  // First assistant message streams and completes.
  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_started", turnId: "t1", sequence: 1, payload: {} },
    });
    MockWebSocket.last!.receive(delta("t1", 2, "第一段"));
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "message_completed", turnId: "t1", sequence: 3, payload: {} },
    });
  });
  await waitFor(() => {
    expect(markdownRenders).toContain("第一段");
  });
  const completedRenderCount = markdownRenders.filter(
    (text) => text === "第一段",
  ).length;

  // A second message streams with several flushes.
  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "tool_call_started", turnId: "t1", sequence: 4, payload: { toolCallId: "tc-1", name: "read_file" } },
    });
    MockWebSocket.last!.receive(delta("t1", 5, "第二"));
  });
  await waitFor(() => {
    expect(markdownRenders).toContain("第二");
  });
  act(() => {
    MockWebSocket.last!.receive(delta("t1", 6, "段继续"));
  });
  await waitFor(() => {
    expect(markdownRenders).toContain("第二段继续");
  });

  // The completed message must not be re-parsed by later flushes.
  expect(
    markdownRenders.filter((text) => text === "第一段").length,
  ).toBe(completedRenderCount);
});

test("workspace layout keeps the message list as the scroll container", async () => {
  const { readFile } = await import("node:fs/promises");
  const { resolve } = await import("node:path");
  const css = await readFile(resolve(process.cwd(), "src/styles/views.css"), "utf-8");
  // The section must be height-capped so .message-list scrolls and the
  // composer stays pinned; min-height:100% regressed this once already.
  const section = css.match(/\.agent-workspace\s*\{[^}]*\}/)?.[0] ?? "";
  expect(section).toContain("height: 100%");
  expect(section).not.toContain("min-height: 100%");
  const list = css.match(/\.message-list\s*\{[^}]*\}/)?.[0] ?? "";
  expect(list).toContain("overflow-y: auto");
  expect(list).toContain("min-height: 0");
});

test("an approval arriving while reading earlier messages shows the approval card without forcing scroll", async () => {
  setupGlobals();
  const scrollSpy = vi.fn();
  Element.prototype.scrollIntoView = scrollSpy;
  render(<AgentWorkspaceView {...viewProps()} variant="litigation" />);
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => MockWebSocket.last!.onopen?.());
  act(() => {
    MockWebSocket.last!.receive({type: "event", event: {type: "turn_started", turnId: "t1", sequence: 1, payload: {}}});
    MockWebSocket.last!.receive({type: "event", event: {type: "tool_call_started", turnId: "t1", sequence: 2,
      payload: {toolCallId: "save", name: "litigation_work_save"}}});
  });
  const list = await screen.findByRole("log", {name: "对话时间线"});
  Object.defineProperty(list, "scrollHeight", {value: 1000, configurable: true});
  Object.defineProperty(list, "clientHeight", {value: 200, configurable: true});
  list.scrollTop = 0;
  fireEvent.scroll(list);
  scrollSpy.mockClear();
  act(() => MockWebSocket.last!.receive({type: "event", event: {type: "approval_requested", turnId: "t1", sequence: 3,
    payload: {toolCallId: "save", approvalId: "approval", name: "litigation_work_save"}}}));
  expect(await screen.findByRole("button", {name: "保存这次修改"})).toBeTruthy();
  expect(screen.getByRole("button", {name: "拒绝"})).toBeTruthy();
  expect(scrollSpy).not.toHaveBeenCalled();
  expect(list.scrollTop).toBe(0);
  expect(MockWebSocket.last!.sentMessages.some(message => message.includes('"resolve"'))).toBe(false);
});

test.each(["litigation", "chat"] as const)("%s refreshes parent status only for relevant approval transitions", async variant => {
  setupGlobals();
  const refresh = vi.fn();
  const props = {...viewProps(), onThreadsRefresh: refresh};
  const view = render(<AgentWorkspaceView {...props} variant={variant} />);
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => MockWebSocket.last!.onopen?.());
  expect(refresh).not.toHaveBeenCalled();
  act(() => {
    MockWebSocket.last!.receive({type: "event", event: {type: "turn_started", turnId: "t1", sequence: 1, payload: {}}});
    MockWebSocket.last!.receive({type: "event", event: {type: "tool_call_started", turnId: "t1", sequence: 2,
      payload: {toolCallId: "save", name: "litigation_work_save"}}});
    MockWebSocket.last!.receive({type: "event", event: {type: "approval_requested", turnId: "t1", sequence: 3,
      payload: {toolCallId: "save", approvalId: "approval", name: "litigation_work_save"}}});
  });
  await waitFor(() => expect(screen.getByRole("button", {name: "保存这次修改"})).toBeDefined());
  expect(refresh).toHaveBeenCalledTimes(variant === "litigation" ? 1 : 0);
  view.rerender(<AgentWorkspaceView {...props} draft="仍在阅读旧消息" variant={variant} />);
  expect(refresh).toHaveBeenCalledTimes(variant === "litigation" ? 1 : 0);
  act(() => MockWebSocket.last!.receive({type: "event", event: {type: "approval_resolved", turnId: "t1", sequence: 4,
    payload: {toolCallId: "save", approvalId: "approval", decision: "allowed"}}}));
  await waitFor(() => expect(screen.queryByRole("button", {name: "保存这次修改"})).toBeNull());
  expect(refresh).toHaveBeenCalledTimes(variant === "litigation" ? 2 : 0);
  act(() => {
    MockWebSocket.last!.receive({type: "event", event: {type: "tool_call_started", turnId: "t1", sequence: 5,
      payload: {toolCallId: "save-next", name: "litigation_work_save"}}});
    MockWebSocket.last!.receive({type: "event", event: {type: "approval_requested", turnId: "t1", sequence: 6,
      payload: {toolCallId: "save-next", approvalId: "approval-next"}}});
  });
  await waitFor(() => expect(screen.getByRole("button", {name: "保存这次修改"})).toBeDefined());
  expect(refresh).toHaveBeenCalledTimes(variant === "litigation" ? 3 : 0);
  act(() => MockWebSocket.last!.receive({type: "event", event: {type: "turn_cancelled", turnId: "t1", sequence: 7, payload: {}}}));
  await waitFor(() => expect(screen.queryByRole("button", {name: "保存这次修改"})).toBeNull());
  // The existing settled callback handles this transition once; the new
  // approval effect must not trigger an extra parent refresh for the stop.
  expect(refresh).toHaveBeenCalledTimes(variant === "litigation" ? 4 : 1);
});

test("switching litigation discussions never refreshes the new case for stale approval state", async () => {
  setupGlobals();
  const refresh = vi.fn();
  const props = {...viewProps(), onThreadsRefresh: refresh};
  const view = render(<AgentWorkspaceView {...props} variant="litigation" />);
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => MockWebSocket.last!.onopen?.());
  const oldSocket = MockWebSocket.last!;
  act(() => {
    oldSocket.receive({type: "event", event: {type: "turn_started", turnId: "t1", sequence: 1, payload: {}}});
    oldSocket.receive({type: "event", event: {type: "tool_call_started", turnId: "t1", sequence: 2, payload: {toolCallId: "save", name: "litigation_work_save"}}});
    oldSocket.receive({type: "event", event: {type: "approval_requested", turnId: "t1", sequence: 3, payload: {toolCallId: "save", approvalId: "approval"}}});
  });
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  view.rerender(<AgentWorkspaceView {...props} thread={{...props.thread, id: "local-2", backendId: "thread-2"}} variant="litigation" />);
  // The chat hook intentionally keeps its connection while switching threads.
  await waitFor(() => expect(screen.queryByRole("button", {name: "保存这次修改"})).toBeNull());
  await act(async () => {});
  expect(MockWebSocket.last).toBe(oldSocket);
  expect(refresh).toHaveBeenCalledTimes(1);
  act(() => oldSocket.receive({type: "event", event: {type: "approval_resolved", turnId: "t1", sequence: 4, payload: {toolCallId: "save", approvalId: "approval", decision: "allowed"}}}));
  expect(refresh).toHaveBeenCalledTimes(1);
});

test("scrolling up mid-stream pauses following until 回到最新", async () => {
  setupGlobals();
  const scrollSpy = vi.fn();
  Element.prototype.scrollIntoView = scrollSpy;
  render(<AgentWorkspaceView {...viewProps()} />);
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    MockWebSocket.last!.receive(delta("t1", 1, "持续输出中"));
  });
  await waitFor(() => {
    expect(markdownRenders.length).toBeGreaterThan(0);
  });
  scrollSpy.mockClear();

  // The user scrolls far away from the bottom.
  const list = document.querySelector(".message-list") as HTMLElement;
  Object.defineProperty(list, "scrollHeight", { value: 2000, configurable: true });
  Object.defineProperty(list, "clientHeight", { value: 400, configurable: true });
  list.scrollTop = 100;
  fireEvent.scroll(list);
  expect(screen.getByRole("button", { name: /回到最新/ })).toBeDefined();

  // Further streamed content must not re-pin the view.
  act(() => {
    MockWebSocket.last!.receive(delta("t1", 2, "更多内容"));
  });
  await waitFor(() => {
    expect(markdownRenders.at(-1)).toBe("持续输出中更多内容");
  });
  expect(scrollSpy).not.toHaveBeenCalled();
});
