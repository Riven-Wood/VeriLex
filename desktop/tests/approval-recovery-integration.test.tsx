import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

import type { ThreadSummary } from "../src/app/navigation";
import { AgentWorkspaceView } from "../src/views/AgentWorkspaceView";

class MockWebSocket {
  static OPEN = 1;
  static instances: MockWebSocket[] = [];
  static last: MockWebSocket | null = null;
  readyState = MockWebSocket.OPEN;
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

const THREAD_A: ThreadSummary = {
  id: "local-a",
  backendId: "thread-a",
  title: "任务 A",
  providerId: "provider-1",
  model: "model-1",
  workspaceId: "workspace-1",
  createdAt: "2026-08-22T00:00:00Z",
  updatedAt: "2026-08-22T00:00:00Z",
  status: "idle",
  isDraft: false,
};

const THREAD_B: ThreadSummary = {
  ...THREAD_A,
  id: "local-b",
  backendId: "thread-b",
  title: "任务 B",
};

interface HistoryOptions {
  errorCode?: string;
  sourceText?: string;
  attachmentIds?: string[];
  includePriorSideEffect?: boolean;
  artifactCount?: number;
}

function setupGlobals({
  errorCode = "provider_request_failed",
  sourceText = "请继续检查合同",
  attachmentIds = [],
  includePriorSideEffect = false,
  artifactCount = 0,
}: HistoryOptions = {}) {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("WebSocket", MockWebSocket);
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ) => {
    const url = String(input);
    const threadId = url.includes("thread-b") ? "thread-b" : "thread-a";
    if (init?.method === "POST" && url.includes("/attachment-uploads")) {
      return new Response(JSON.stringify({
        id: "upload-new",
        thread_id: threadId,
        display_name: "新附件.txt",
        media_type: "text/plain",
        size_bytes: 3,
        sha256: "hash",
        created_at: "2026-08-22T00:00:00Z",
        expires_at: "2026-08-23T00:00:00Z",
      }), { status: 200 });
    }
    const body = url.endsWith("/messages")
      ? threadId === "thread-a"
        ? [{
            id: "message-a",
            thread_id: threadId,
            turn_id: "turn-a",
            sequence: 1,
            role: "user",
            content: attachmentIds.length > 0
              ? { version: 1, text: sourceText, attachmentIds }
              : [{ type: "text", text: sourceText }],
            created_at: "2026-08-22T00:00:00Z",
          }]
        : []
      : url.endsWith("/turns")
        ? threadId === "thread-a"
          ? [{ id: "turn-a", status: "failed", error_code: errorCode }]
          : []
          : url.endsWith("/events")
            ? threadId === "thread-a"
            ? [
                ...(includePriorSideEffect
                  ? [
                      {
                        turn_id: "turn-a",
                        sequence: 1,
                        type: "tool_call_started",
                        payload: {
                          toolCallId: "tool-write",
                          name: "write_file",
                          riskLevel: "high",
                        },
                      },
                      {
                        turn_id: "turn-a",
                        sequence: 2,
                        type: "tool_call_completed",
                        payload: {
                          toolCallId: "tool-write",
                          status: "succeeded",
                        },
                      },
                    ]
                  : []),
                {
                  turn_id: "turn-a",
                  sequence: includePriorSideEffect ? 3 : 1,
                  type: "turn_failed",
                  payload: { errorCode, retryable: true },
                },
              ]
            : []
          : url.endsWith("/attachments")
            ? attachmentIds.map((id) => ({
                id,
                display_name: "历史附件.txt",
              }))
            : url.endsWith("/artifacts")
              ? { artifacts: Array.from({ length: artifactCount }, (_, index) => ({
                  kind: "artifact",
                  id: `artifact-${index}`,
                  turn_id: "turn-a",
                  name: `产物${index}.docx`,
                  published_workspace_id: "ws-a",
                  published_relative_path: `产物${index}.docx`,
                  operation: "document_create",
                  size_bytes: 1024,
                  structure_status: "passed",
                  render_status: "not_required",
                  vision_status: "not_run",
                  created_at: "2026-09-03T00:00:00Z",
                })) }
              : [];
    return new Response(JSON.stringify(body), { status: 200 });
  }));
  MockWebSocket.instances = [];
  MockWebSocket.last = null;
}

function Harness({
  initialDraft = "",
  onOpenSettings = () => undefined,
}: {
  initialDraft?: string;
  onOpenSettings?: () => void;
}) {
  const [thread, setThread] = useState<ThreadSummary>(THREAD_A);
  const [draft, setDraft] = useState(initialDraft);
  // The right-rail toggle lives in the App toolbar (2026-08-31); the harness
  // reproduces it here as the controlled owner of the rail visibility.
  const [previewOpen, setPreviewOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setThread(THREAD_B)}>切换任务</button>
      <button type="button" onClick={() => setPreviewOpen((open) => !open)}>
        {previewOpen ? "关闭右侧栏" : "打开右侧栏"}
      </button>
      <AgentWorkspaceView
        hasModel={true}
        thread={thread}
        workspaceName="工作区"
        composerContext={{
          providerId: "provider-1",
          model: "model-1",
          workspaceId: "workspace-1",
        }}
        draft={draft}
        onDraftChange={setDraft}
        pendingAgentMessage={null}
        onPendingAgentMessageSent={() => undefined}
        onSendWithoutThread={() => undefined}
        onOpenSettings={onOpenSettings}
        onThreadsRefresh={() => undefined}
        onUserTurnAccepted={() => undefined}
        previewOpen={previewOpen}
        onPreviewOpenChange={setPreviewOpen}
      />
    </>
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
});

test("restores failed text into an empty composer without sending it", async () => {
  setupGlobals();
  render(<Harness />);
  const restore = await screen.findByRole("button", { name: "恢复原指令" });
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  expect(MockWebSocket.last!.sentMessages).toHaveLength(0);

  fireEvent.click(restore);

  expect((screen.getByLabelText("向智能体提问") as HTMLTextAreaElement).value)
    .toBe("请继续检查合同");
  expect(document.activeElement).toBe(screen.getByLabelText("向智能体提问"));
  expect(MockWebSocket.last!.sentMessages).toHaveLength(0);
});

test("does not overwrite a current draft", async () => {
  setupGlobals();
  render(<Harness initialDraft="我的新草稿" />);

  fireEvent.click(await screen.findByRole("button", { name: "恢复原指令" }));

  expect((screen.getByLabelText("向智能体提问") as HTMLTextAreaElement).value)
    .toBe("我的新草稿");
  expect(screen.getByText("输入框已有内容，请先处理后再恢复。")).toBeDefined();
});

test("does not overwrite a current attachment", async () => {
  setupGlobals();
  render(<Harness />);
  const restore = await screen.findByRole("button", { name: "恢复原指令" });
  const fileInput = screen.getByLabelText("选择附件文件");
  fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "添加附件" }));
  fireEvent.change(fileInput, {
    target: { files: [new File(["new"], "新附件.txt", { type: "text/plain" })] },
  });
  await screen.findByText("新附件.txt");

  fireEvent.click(restore);

  expect((screen.getByLabelText("向智能体提问") as HTMLTextAreaElement).value)
    .toBe("");
  expect(screen.getByText("输入框已有内容，请先处理后再恢复。")).toBeDefined();
});

test("does not overwrite an empty composer while another message is queued", async () => {
  setupGlobals();
  render(<Harness />);
  const restore = await screen.findByRole("button", { name: "恢复原指令" });
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => MockWebSocket.last!.onopen?.());
  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_started",
        threadId: "thread-a",
        turnId: "turn-live",
        sequence: 1,
        payload: {},
      },
    });
  });
  fireEvent.change(screen.getByLabelText("向智能体提问"), {
    target: { value: "排队内容" },
  });
  fireEvent.click(screen.getByRole("button", { name: "提交下一条" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "排队" }));
  expect((screen.getByLabelText("向智能体提问") as HTMLTextAreaElement).value)
    .toBe("");

  fireEvent.click(restore);

  expect((screen.getByLabelText("向智能体提问") as HTMLTextAreaElement).value)
    .toBe("");
  expect(screen.getByText("输入框已有内容，请先处理后再恢复。")).toBeDefined();
});

test("a recovery callback captured by an old thread cannot change the new thread", async () => {
  setupGlobals();
  render(<Harness />);
  const staleRestore = await screen.findByRole("button", { name: "恢复原指令" });

  fireEvent.click(screen.getByRole("button", { name: "切换任务" }));
  await screen.findByText("任务 B");
  fireEvent.click(staleRestore);

  expect((screen.getByLabelText("向智能体提问") as HTMLTextAreaElement).value)
    .toBe("");
});

test("workspace routes settings and attachment recovery through existing controls", async () => {
  const onOpenSettings = vi.fn();
  setupGlobals({ errorCode: "provider_auth_failed" });
  const first = render(<Harness onOpenSettings={onOpenSettings} />);
  fireEvent.click(await screen.findByRole("button", { name: "打开模型设置" }));
  expect(onOpenSettings).toHaveBeenCalledTimes(1);
  first.unmount();

  setupGlobals({ errorCode: "attachment_blob_missing", sourceText: "请检查附件" });
  const fileClick = vi
    .spyOn(HTMLInputElement.prototype, "click")
    .mockImplementation(() => undefined);
  render(<Harness />);
  fireEvent.click(await screen.findByRole("button", { name: "重新选择附件" }));
  expect(fileClick).toHaveBeenCalledTimes(1);
  expect((screen.getByLabelText("向智能体提问") as HTMLTextAreaElement).value)
    .toBe("");
  expect(MockWebSocket.last!.sentMessages).toHaveLength(0);
});

test("settings and attachment actions captured by an old task cannot act on the new task", async () => {
  const onOpenSettings = vi.fn();
  setupGlobals({ errorCode: "provider_auth_failed" });
  const settingsView = render(<Harness onOpenSettings={onOpenSettings} />);
  const staleSettings = await screen.findByRole("button", { name: "打开模型设置" });
  fireEvent.click(screen.getByRole("button", { name: "切换任务" }));
  await screen.findByText("任务 B");
  fireEvent.click(staleSettings);
  expect(onOpenSettings).not.toHaveBeenCalled();
  settingsView.unmount();

  setupGlobals({ errorCode: "attachment_blob_missing" });
  const pickerClick = vi
    .spyOn(HTMLInputElement.prototype, "click")
    .mockImplementation(() => undefined);
  render(<Harness />);
  const staleAttachment = await screen.findByRole("button", { name: "重新选择附件" });
  fireEvent.click(screen.getByRole("button", { name: "切换任务" }));
  await screen.findByText("任务 B");
  fireEvent.click(staleAttachment);
  expect(pickerClick).not.toHaveBeenCalled();
});

test("a file selected after switching tasks is discarded instead of uploaded to the new task", async () => {
  setupGlobals({ errorCode: "attachment_blob_missing" });
  const fetchMock = vi.mocked(globalThis.fetch);
  vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => undefined);
  render(<Harness />);
  fireEvent.click(await screen.findByRole("button", { name: "重新选择附件" }));
  fireEvent.click(screen.getByRole("button", { name: "切换任务" }));
  await screen.findByText("任务 B");

  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: { files: [new File(["late"], "迟到附件.txt", { type: "text/plain" })] },
  });

  await act(async () => undefined);
  expect(fetchMock.mock.calls.some(([url, init]) =>
    String(url).includes("/attachment-uploads") && init?.method === "POST"
  )).toBe(false);
  expect(screen.queryByText("迟到附件.txt")).toBeNull();
});

test("the recovery warning clears when a blocking draft is later cleared", async () => {
  setupGlobals();
  render(<Harness initialDraft="现有草稿" />);
  fireEvent.click(await screen.findByRole("button", { name: "恢复原指令" }));
  expect(screen.getByText("输入框已有内容，请先处理后再恢复。")).toBeDefined();

  fireEvent.change(screen.getByLabelText("向智能体提问"), { target: { value: "" } });

  await waitFor(() => {
    expect(screen.queryByText("输入框已有内容，请先处理后再恢复。")).toBeNull();
  });
});

test("the recovery warning waits for every blocker before clearing", async () => {
  setupGlobals();
  render(<Harness initialDraft="现有草稿" />);
  const restore = await screen.findByRole("button", { name: "恢复原指令" });
  fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "添加附件" }));
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: { files: [new File(["new"], "待移除.txt", { type: "text/plain" })] },
  });
  await screen.findByText("待移除.txt");
  fireEvent.click(restore);
  expect(screen.getByText("输入框已有内容，请先处理后再恢复。")).toBeDefined();

  fireEvent.change(screen.getByLabelText("向智能体提问"), { target: { value: "" } });
  expect(screen.getByText("输入框已有内容，请先处理后再恢复。")).toBeDefined();

  fireEvent.click(screen.getByRole("button", { name: "移除 待移除.txt" }));

  await waitFor(() => expect(screen.queryByText("待移除.txt")).toBeNull());
  await waitFor(() => {
    expect(screen.queryByText("输入框已有内容，请先处理后再恢复。")).toBeNull();
  });
});

test("the recovery warning clears after a blocking queue advances", async () => {
  setupGlobals();
  render(<Harness />);
  const restore = await screen.findByRole("button", { name: "恢复原指令" });
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => MockWebSocket.last!.onopen?.());
  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_started",
        threadId: "thread-a",
        turnId: "turn-running",
        sequence: 1,
        payload: {},
      },
    });
  });
  fireEvent.change(screen.getByLabelText("向智能体提问"), {
    target: { value: "排队内容" },
  });
  fireEvent.click(screen.getByRole("button", { name: "提交下一条" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "排队" }));
  fireEvent.click(restore);
  expect(screen.getByText("输入框已有内容，请先处理后再恢复。")).toBeDefined();

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_completed",
        threadId: "thread-a",
        turnId: "turn-running",
        sequence: 2,
        payload: {},
      },
    });
  });

  await waitFor(() => expect(screen.queryByText(/已排队：排队内容/)).toBeNull());
  await waitFor(() => {
    expect(screen.queryByText("输入框已有内容，请先处理后再恢复。")).toBeNull();
  });
});

test("live turn failure restores the accepted user text without another WebSocket send", async () => {
  setupGlobals({ sourceText: "" });
  render(<Harness />);
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => MockWebSocket.last!.onopen?.());
  fireEvent.change(screen.getByLabelText("向智能体提问"), {
    target: { value: "实时检查合同" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(MockWebSocket.last!.sentMessages).toHaveLength(1);
  act(() => {
    MockWebSocket.last!.receive({ type: "ack", turnId: "turn-live", status: "queued" });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_started",
        threadId: "thread-a",
        turnId: "turn-live",
        sequence: 1,
        payload: {},
      },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "turn_failed",
        threadId: "thread-a",
        turnId: "turn-live",
        sequence: 2,
        payload: { errorCode: "provider_timeout", retryable: true },
      },
    });
  });
  expect((screen.getByLabelText("向智能体提问") as HTMLTextAreaElement).value)
    .toBe("");

  fireEvent.click(await screen.findByRole("button", { name: "恢复原指令" }));

  expect((screen.getByLabelText("向智能体提问") as HTMLTextAreaElement).value)
    .toBe("实时检查合同");
  expect(MockWebSocket.last!.sentMessages).toHaveLength(1);
});

test("historical attachment recovery never reuses its old id", async () => {
  setupGlobals({
    errorCode: "attachment_blob_missing",
    sourceText: "检查历史附件",
    attachmentIds: ["attachment-old"],
  });
  const fetchMock = vi.mocked(globalThis.fetch);
  vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => undefined);
  render(<Harness />);
  expect(await screen.findByText(/历史附件.txt/)).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "恢复文字" }));
  expect((screen.getByLabelText("向智能体提问") as HTMLTextAreaElement).value)
    .toBe("检查历史附件");
  expect(MockWebSocket.last!.sentMessages).toHaveLength(0);

  fireEvent.click(screen.getByRole("button", { name: "重新选择附件" }));
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: { files: [new File(["new"], "新附件.txt", { type: "text/plain" })] },
  });
  await screen.findByText("新附件.txt");

  const uploadCall = fetchMock.mock.calls.find(([url, init]) =>
    String(url).includes("/attachment-uploads") && init?.method === "POST"
  );
  expect(uploadCall).toBeDefined();
  expect(String(uploadCall?.[0])).not.toContain("attachment-old");
  expect(MockWebSocket.last!.sentMessages).toHaveLength(0);
});

test("a side-effect warning never turns recovery into an automatic replay", async () => {
  setupGlobals({ includePriorSideEffect: true });
  render(<Harness />);

  expect(await screen.findByText("部分操作可能已经完成，请检查后再发送。"))
    .toBeDefined();
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  const sentBeforeRestore = MockWebSocket.last!.sentMessages.length;

  fireEvent.click(screen.getByRole("button", { name: "恢复原指令" }));

  expect((screen.getByLabelText("向智能体提问") as HTMLTextAreaElement).value)
    .toBe("请继续检查合同");
  expect(MockWebSocket.last!.sentMessages).toHaveLength(sentBeforeRestore);
  expect(MockWebSocket.last!.sentMessages).not.toContainEqual(
    expect.stringContaining('"type":"turn.start"'),
  );
  expect(MockWebSocket.last!.sentMessages).not.toContainEqual(
    expect.stringContaining('"type":"turn.resolve"'),
  );
});

test.each([
  ["provider_model_not_found", "打开模型设置"],
  ["provider_unreachable", "恢复原指令"],
  ["model_vision_unsupported", "重新选择附件"],
])("production error %s receives its safe recovery action", async (
  errorCode,
  actionName,
) => {
  setupGlobals({ errorCode });
  const view = render(<Harness />);
  expect(await screen.findByRole("button", { name: actionName })).toBeDefined();
  view.unmount();
});

test("deliverables attach to their turn bubble instead of the timeline tail", async () => {
  setupGlobals({ artifactCount: 3, errorCode: "" });
  const view = render(<Harness />);
  // The per-turn card shows the produced files under the turn's bubble.
  const head = await screen.findByRole("button", { name: /产出的文件/ });
  expect(head).toBeDefined();
  // The legacy global tail entry is gone.
  expect(view.container.querySelector(".artifact-summary-entry")).toBeNull();
  view.unmount();
});

test("no deliverables card renders when the conversation produced no files", async () => {
  setupGlobals({ artifactCount: 0, errorCode: "" });
  const view = render(<Harness />);
  await waitFor(() =>
    expect(view.container.querySelector(".deliverables-card")).toBeNull(),
  );
  view.unmount();
});

test("preview rail toggle opens and closes the side panel", async () => {
  setupGlobals({ artifactCount: 2, errorCode: "" });
  const view = render(<Harness />);
  // Closed by default: the rail is not rendered.
  expect(view.container.querySelector(".preview-rail")).toBeNull();

  const openButton = screen.getByRole("button", { name: "打开右侧栏" });
  fireEvent.click(openButton);
  await waitFor(() =>
    expect(view.container.querySelector(".preview-rail")).not.toBeNull(),
  );
  fireEvent.click(screen.getByRole("button", { name: "会话成果" }));
  await waitFor(() =>
    expect(view.container.querySelector(".preview-artifact-list")).not.toBeNull(),
  );

  const closeButton = screen.getByRole("button", { name: "关闭右侧栏" });
  fireEvent.click(closeButton);
  await waitFor(() =>
    expect(view.container.querySelector(".preview-rail")).toBeNull(),
  );
  view.unmount();
});
