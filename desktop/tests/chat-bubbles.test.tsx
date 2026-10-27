import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AgentWorkspaceView } from "../src/views/AgentWorkspaceView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.querySelector("style[data-chat-bubbles-test]")?.remove();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
});

async function loadChatStyles() {
  const stylesheet = document.createElement("style");
  stylesheet.dataset.chatBubblesTest = "true";
  stylesheet.textContent = await readFile(
    resolve(process.cwd(), "src/styles/views.css"),
    "utf8",
  );
  document.head.append(stylesheet);
}

function findStyleRule(selector: string): CSSStyleDeclaration | null {
  const sheet = document.querySelector<HTMLStyleElement>(
    "style[data-chat-bubbles-test]",
  )?.sheet;
  if (!sheet) return null;
  for (const rule of Array.from(sheet.cssRules)) {
    if (rule instanceof CSSStyleRule && rule.selectorText === selector) {
      return rule.style;
    }
  }
  return null;
}

test("user messages are right-aligned chat bubbles with a tail corner", async () => {
  await loadChatStyles();

  const userMessage = findStyleRule(".message-user");
  const bubble = findStyleRule(".message-user .message-text");

  expect(userMessage?.getPropertyValue("align-self")).toBe("flex-end");
  expect(userMessage?.getPropertyValue("max-width")).toContain("78%");
  expect(bubble?.getPropertyValue("border-radius")).toBe("16px 16px 6px 16px");
});

test("the user role label stays in the DOM for screen readers but is hidden", async () => {
  await loadChatStyles();

  const role = findStyleRule(".message-user .message-role");

  expect(role?.position).toBe("absolute");
});

test("layout invariants keep the composer pinned and kill stray scrollbars", async () => {
  await loadChatStyles();

  // The message list is a column flex item with horizontal auto margins.
  // Without an explicit width, short tool/reasoning cards make the whole
  // timeline shrink to their content instead of following the window.
  expect(findStyleRule(".message-list")?.getPropertyValue("width")).toBe(
    "100%",
  );

  // The visually-hidden user label is absolutely positioned; its containing
  // block must be the message itself, otherwise its static position deep in
  // the scrolled list leaks into the main area's overflow and scrolls the
  // composer out of view.
  expect(findStyleRule(".message-user")?.getPropertyValue("position")).toBe(
    "relative",
  );

  // The workspace clips any escaping overflow so .workspace (main) can never
  // grow a scrollbar of its own.
  expect(findStyleRule(".agent-workspace")?.getPropertyValue("overflow")).toBe(
    "hidden",
  );

  // Live background output wraps like result previews instead of widening
  // the card and forcing a horizontal scrollbar above the composer.
  const output = findStyleRule(".background-task-output");
  expect(output?.getPropertyValue("white-space")).toBe("pre-wrap");
  expect(output?.getPropertyValue("max-height")).toBe("240px");
});

test("assistant messages sit on a left avatar spine", async () => {
  await loadChatStyles();

  const assistant = findStyleRule(".message-assistant");
  const avatar = findStyleRule(".message-assistant .message-avatar");

  expect(assistant?.getPropertyValue("grid-template-columns")).toBe("30px 1fr");
  expect(avatar?.getPropertyValue("border-radius")).toBe("50%");
});

test("tool cards and notices align with the assistant content column", async () => {
  await loadChatStyles();

  const toolCard = findStyleRule(".message-list .tool-card");

  expect(toolCard?.getPropertyValue("margin-left")).toBe("40px");
});

class MockWebSocket {
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
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async () => new Response("[]", { status: 200 })),
  );
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

test("assistant replies render as plain log entries without an avatar bubble", async () => {
  Element.prototype.scrollIntoView = () => {};
  setupViewGlobals();
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
        payload: { kind: "text", delta: "你好" },
      },
    });
  });

  await waitFor(() => {
    // Work-log flow: the reply text shows without an avatar bubble.
    expect(screen.getByText("你好")).toBeDefined();
    expect(document.querySelector(".message-assistant .message-avatar img")).toBeNull();
  });
});

test("a detached conversation keeps its draft and asks for a model before sending", async () => {
  Element.prototype.scrollIntoView = () => {};
  setupViewGlobals();
  const props = viewProps();
  const onSelect = vi.fn();
  render(<AgentWorkspaceView {...props}
    thread={{ ...props.thread, providerId: null }}
    draft="Keep my unsent question"
    composerContext={{ providerId: "replacement", model: "new-model", workspaceId: null, reasoningEffort: "auto" }}
    onComposerContextChange={onSelect} />);
  expect(screen.getByRole("button", { name: "选择模型，当前 请选择模型" })).toBeTruthy();
  expect((screen.getByRole("button", { name: "发送", exact: true }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("原模型连接已删除，会话内容已保留。请选择模型后继续。")).toBeTruthy();
  expect(screen.getByRole("textbox").textContent || (screen.getByRole("textbox") as HTMLTextAreaElement).value).toContain("Keep my unsent question");
});
