import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AgentComposer } from "../src/components/AgentComposer";

const nativeDrop = vi.hoisted(() => ({
  listen: vi.fn(),
  unlisten: vi.fn(),
}));
const nativeBridge = vi.hoisted(() => ({
  invoke: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => nativeBridge);

vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({
    onDragDropEvent: nativeDrop.listen,
  }),
}));

afterEach(() => {
  cleanup();
  delete (globalThis as typeof globalThis & { __TAURI_INTERNALS__?: object })
    .__TAURI_INTERNALS__;
  nativeDrop.listen.mockReset();
  nativeDrop.unlisten.mockReset();
  nativeBridge.invoke.mockReset();
});

function droppedFilePayload(bytes: number[], lastModified = 0): ArrayBuffer {
  const payload = new ArrayBuffer(8 + bytes.length);
  new DataView(payload).setBigUint64(0, BigInt(lastModified), true);
  new Uint8Array(payload, 8).set(bytes);
  return payload;
}

test("orders context, model, reasoning, then send", () => {
  render(
    <AgentComposer
      hasModel={true}
      value="hello"
      isRunning={false}
      modelLabel="GPT-5.6"
      contextUsage={{
        used_tokens: 7200,
        context_window_tokens: 100000,
        effective_window_tokens: 90000,
        used_percent: 8,
        estimated: true,
        warning: "normal",
        breakdown: { messages: 4000, system_and_tools: 2000, skills: 700, attachments: 500, other: 0 },
        updated_at: "2026-08-24T00:00:00Z",
      }}
      reasoningEffort="auto"
      providerKind="openai_compatible"
      supportedReasoningEfforts={["low", "medium", "high"]}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );
  const toolbar = screen.getByTestId("composer-primary-controls");
  expect(within(toolbar).getAllByRole("button").map((button) => button.getAttribute("aria-label"))).toEqual([
    "上下文量，已用 8%",
    "选择模型，当前 GPT-5.6",
    "推理强度，当前自动",
    "发送",
  ]);
});

test("web access is closed by default and toggles without sending", () => {
  const onWebAccessChange = vi.fn();
  const onSend = vi.fn();
  render(
    <AgentComposer
      hasModel={true}
      value="问题"
      isRunning={false}
      onWebAccessChange={onWebAccessChange}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={onSend}
      onStop={() => {}}
    />,
  );

  const button = screen.getByRole("button", { name: "联网已关闭" });
  expect(button.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(button);

  expect(onWebAccessChange).toHaveBeenCalledWith(true);
  expect(onSend).not.toHaveBeenCalled();
});

test("web access cannot be switched while a turn is running", () => {
  const onWebAccessChange = vi.fn();
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={true}
      webAccess={{
        enabled: true,
        granted: true,
        loading: false,
        updating: false,
        error: null,
      }}
      onWebAccessChange={onWebAccessChange}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  const button = screen.getByRole("button", { name: "联网已授权" });
  expect(button.hasAttribute("disabled")).toBe(true);
  fireEvent.click(button);
  expect(onWebAccessChange).not.toHaveBeenCalled();
});

test("web access loading state does not announce an unverified closed value", () => {
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      webAccess={{
        enabled: false,
        granted: false,
        loading: true,
        updating: false,
        error: null,
      }}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: "正在读取联网状态" }).hasAttribute("disabled"))
    .toBe(true);
  expect(screen.queryByRole("button", { name: "联网已关闭" })).toBeNull();
});

test("calls onSend when Enter is pressed without Shift", () => {
  const onSend = vi.fn();
  render(
    <AgentComposer
      hasModel={true}
      value="hello"
      isRunning={false}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={onSend}
      onStop={() => {}}
    />,
  );

  const textarea = screen.getByRole("textbox", { name: "向智能体提问" });
  fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });

  expect(onSend).toHaveBeenCalledTimes(1);
});

test("does not call onSend when Shift+Enter is pressed", () => {
  const onSend = vi.fn();
  render(
    <AgentComposer
      hasModel={true}
      value="hello"
      isRunning={false}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={onSend}
      onStop={() => {}}
    />,
  );

  const textarea = screen.getByRole("textbox", { name: "向智能体提问" });
  fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

  expect(onSend).not.toHaveBeenCalled();
});

test("send button is disabled when value is empty", () => {
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: "发送" }).hasAttribute("disabled")).toBe(true);
});

test("send button is disabled when no model is configured", () => {
  render(
    <AgentComposer
      hasModel={false}
      value="hello"
      isRunning={false}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: "发送" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByText("连接模型后即可发送")).toBeTruthy();
});

test("running empty composer shows one stop action", () => {
  const onStop = vi.fn();
  const { container } = render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={true}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={onStop}
    />,
  );

  const stopButton = screen.getByRole("button", { name: "停止" });
  fireEvent.click(stopButton);

  expect(onStop).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "提交下一条" })).toBeNull();
  expect(container.querySelectorAll(".send-button")).toHaveLength(1);
});

test("running draft replaces stop with one submit-next action", () => {
  const { container } = render(
    <AgentComposer
      hasModel={true}
      value="下一步"
      isRunning={true}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: "提交下一条" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "停止" })).toBeNull();
  expect(container.querySelectorAll(".send-button")).toHaveLength(1);
});

test("running ready attachment replaces stop with submit-next", () => {
  render(
    <AgentComposer
      hasModel={true}
      value=""
      hasReadyAttachments={true}
      isRunning={true}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: "提交下一条" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "停止" })).toBeNull();
});

test("textarea stays editable while running", () => {
  render(
    <AgentComposer
      hasModel={true}
      value="hello"
      isRunning={true}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  expect(screen.getByRole("textbox", { name: "向智能体提问" }).hasAttribute("disabled")).toBe(false);
});

test("running send offers one-message queue or interrupt-and-send", () => {
  const onQueue = vi.fn();
  const onInterruptAndSend = vi.fn();
  render(
    <AgentComposer
      hasModel={true}
      value="next"
      isRunning={true}
      onQueue={onQueue}
      onInterruptAndSend={onInterruptAndSend}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "提交下一条" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "排队" }));
  expect(onQueue).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: "提交下一条" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "打断并发送" }));
  expect(onInterruptAndSend).toHaveBeenCalledTimes(1);
});

test("Enter does not stop or open submission choices while running empty", () => {
  const onStop = vi.fn();
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={true}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={onStop}
    />,
  );

  fireEvent.keyDown(screen.getByRole("textbox", { name: "向智能体提问" }), {
    key: "Enter",
    shiftKey: false,
  });

  expect(onStop).not.toHaveBeenCalled();
  expect(screen.queryByRole("menu", { name: "运行中提交方式" })).toBeNull();
});

test("Enter opens submission choices while running with a draft", () => {
  render(
    <AgentComposer
      hasModel={true}
      value="下一步"
      isRunning={true}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  fireEvent.keyDown(screen.getByRole("textbox", { name: "向智能体提问" }), {
    key: "Enter",
    shiftKey: false,
  });

  expect(screen.getByRole("menu", { name: "运行中提交方式" })).toBeTruthy();
});

test("running submission menu closes when the draft is cleared", () => {
  const shared = {
    hasModel: true,
    isRunning: true,
    onChange: () => {},
    onOpenSettings: () => {},
    onSend: () => {},
    onStop: () => {},
  };
  const { rerender } = render(<AgentComposer {...shared} value="下一步" />);
  fireEvent.click(screen.getByRole("button", { name: "提交下一条" }));
  expect(screen.getByRole("menu", { name: "运行中提交方式" })).toBeTruthy();

  rerender(<AgentComposer {...shared} value="" />);

  expect(screen.queryByRole("menu", { name: "运行中提交方式" })).toBeNull();
  expect(screen.getByRole("button", { name: "停止" })).toBeTruthy();
});

test("running submission menu closes when the task changes", () => {
  const shared = {
    hasModel: true,
    value: "下一步",
    isRunning: true,
    onChange: () => {},
    onOpenSettings: () => {},
    onSend: () => {},
    onStop: () => {},
  };
  const { rerender } = render(
    <AgentComposer {...shared} actionScopeKey="thread-a" />,
  );
  fireEvent.click(screen.getByRole("button", { name: "提交下一条" }));

  rerender(<AgentComposer {...shared} actionScopeKey="thread-b" />);

  expect(screen.queryByRole("menu", { name: "运行中提交方式" })).toBeNull();
});

test("running submission menu closes when sending becomes blocked", () => {
  const onQueue = vi.fn();
  const shared = {
    hasModel: true,
    value: "下一步",
    isRunning: true,
    onQueue,
    onChange: () => {},
    onOpenSettings: () => {},
    onSend: () => {},
    onStop: () => {},
  };
  const { rerender } = render(<AgentComposer {...shared} />);
  fireEvent.click(screen.getByRole("button", { name: "提交下一条" }));
  expect(screen.getByRole("menu", { name: "运行中提交方式" })).toBeTruthy();

  rerender(
    <AgentComposer {...shared} sendBlockedReason="附件正在上传..." />,
  );

  expect(screen.queryByRole("menu", { name: "运行中提交方式" })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: "排队" })).toBeNull();
  expect(onQueue).not.toHaveBeenCalled();
});

test("running submission menu closes when the answer finishes", () => {
  const shared = {
    hasModel: true,
    value: "下一步",
    onChange: () => {},
    onOpenSettings: () => {},
    onSend: () => {},
    onStop: () => {},
  };
  const { rerender } = render(<AgentComposer {...shared} isRunning={true} />);
  fireEvent.click(screen.getByRole("button", { name: "提交下一条" }));
  expect(screen.getByRole("menu", { name: "运行中提交方式" })).toBeTruthy();

  rerender(<AgentComposer {...shared} isRunning={false} />);

  expect(screen.queryByRole("menu", { name: "运行中提交方式" })).toBeNull();
  expect(screen.getByRole("button", { name: "发送" })).toBeTruthy();
});

test("queued summary exposes edit and cancel actions", () => {
  const onEditQueue = vi.fn();
  const onCancelQueue = vi.fn();
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={true}
      queuedLabel="已排队：下一步检查证据"
      onEditQueue={onEditQueue}
      onCancelQueue={onCancelQueue}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  expect(screen.getByText("已排队：下一步检查证据")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "编辑排队消息" }));
  fireEvent.click(screen.getByRole("button", { name: "取消排队消息" }));
  expect(onEditQueue).toHaveBeenCalledTimes(1);
  expect(onCancelQueue).toHaveBeenCalledTimes(1);
});

test("calls onChange when typing", () => {
  const onChange = vi.fn();
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onChange={onChange}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  const textarea = screen.getByRole("textbox", { name: "向智能体提问" });
  fireEvent.change(textarea, { target: { value: "new text" } });

  expect(onChange).toHaveBeenCalledWith("new text");
});

test("focus request only focuses the composer after its token increments", () => {
  const shared = {
    hasModel: true,
    value: "",
    isRunning: false,
    onChange: () => {},
    onOpenSettings: () => {},
    onSend: () => {},
    onStop: () => {},
  };
  const { rerender } = render(
    <AgentComposer {...shared} focusRequestToken={0} />,
  );
  const textarea = screen.getByRole("textbox", { name: "向智能体提问" });
  expect(document.activeElement).not.toBe(textarea);

  rerender(<AgentComposer {...shared} focusRequestToken={1} />);

  expect(document.activeElement).toBe(textarea);
});

test("file picker request only opens the existing hidden input after its token increments", () => {
  const inputClick = vi
    .spyOn(HTMLInputElement.prototype, "click")
    .mockImplementation(() => undefined);
  const shared = {
    hasModel: true,
    value: "",
    isRunning: false,
    onAddFiles: () => {},
    onChange: () => {},
    onOpenSettings: () => {},
    onSend: () => {},
    onStop: () => {},
  };
  const { rerender } = render(
    <AgentComposer {...shared} filePickerRequestToken={0} />,
  );
  expect(inputClick).not.toHaveBeenCalled();

  rerender(<AgentComposer {...shared} filePickerRequestToken={1} />);

  expect(inputClick).toHaveBeenCalledTimes(1);
  inputClick.mockRestore();
});

test("a file picker opened for an old task discards a selection that arrives after switching tasks", () => {
  const onAddFiles = vi.fn();
  const inputClick = vi
    .spyOn(HTMLInputElement.prototype, "click")
    .mockImplementation(() => undefined);
  const shared = {
    hasModel: true,
    value: "",
    isRunning: false,
    onAddFiles,
    onChange: () => {},
    onOpenSettings: () => {},
    onSend: () => {},
    onStop: () => {},
  };
  const { rerender } = render(
    <AgentComposer
      {...shared}
      actionScopeKey="thread-a"
      filePickerRequestToken={0}
    />,
  );
  rerender(
    <AgentComposer
      {...shared}
      actionScopeKey="thread-a"
      filePickerRequestToken={1}
    />,
  );
  expect(inputClick).toHaveBeenCalledTimes(1);

  rerender(
    <AgentComposer
      {...shared}
      actionScopeKey="thread-b"
      filePickerRequestToken={1}
    />,
  );
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: { files: [new File(["new"], "迟到附件.txt", { type: "text/plain" })] },
  });

  expect(onAddFiles).not.toHaveBeenCalled();
  inputClick.mockRestore();
});

test("a file picker selection still uses the existing add-files path in its current task", () => {
  const onAddFiles = vi.fn();
  const inputClick = vi
    .spyOn(HTMLInputElement.prototype, "click")
    .mockImplementation(() => undefined);
  const shared = {
    hasModel: true,
    value: "",
    isRunning: false,
    actionScopeKey: "thread-a",
    onAddFiles,
    onChange: () => {},
    onOpenSettings: () => {},
    onSend: () => {},
    onStop: () => {},
  };
  const { rerender } = render(
    <AgentComposer {...shared} filePickerRequestToken={0} />,
  );
  rerender(<AgentComposer {...shared} filePickerRequestToken={1} />);
  const file = new File(["new"], "当前附件.txt", { type: "text/plain" });
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: { files: [file] },
  });

  expect(onAddFiles).toHaveBeenCalledWith([file]);
  inputClick.mockRestore();
});

test("calls onOpenSettings when model status button is clicked", () => {
  const onOpenSettings = vi.fn();
  render(
    <AgentComposer
      hasModel={false}
      value=""
      isRunning={false}
      onChange={() => {}}
      onOpenSettings={onOpenSettings}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "连接模型" }));

  expect(onOpenSettings).toHaveBeenCalledTimes(1);
});

test("shows the concrete model and workspace inside the composer", () => {
  render(
    <AgentComposer
      hasModel={true}
      modelLabel="gpt-5.4"
      workspaceLabel="合同项目"
      value=""
      isRunning={false}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: "选择模型，当前 gpt-5.4" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "选择工作区，当前 合同项目" })).toBeTruthy();
});

test("allows sending an attachment without text", () => {
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      hasReadyAttachments={true}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: "发送" }).hasAttribute("disabled")).toBe(false);
});

test("adds dropped files and pasted images without intercepting ordinary text", () => {
  const onAddFiles = vi.fn();
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onAddFiles={onAddFiles}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  const composer = screen.getByTestId("agent-composer");
  const dropped = new File(["memo"], "memo.txt", { type: "text/plain" });
  fireEvent.drop(composer, { dataTransfer: { files: [dropped] } });
  expect(onAddFiles).toHaveBeenLastCalledWith([dropped]);

  const image = new File(["image"], "paste.png", { type: "image/png" });
  const preventDefault = vi.fn();
  fireEvent.paste(screen.getByRole("textbox", { name: "向智能体提问" }), {
    clipboardData: { files: [image] },
    preventDefault,
  });
  expect(onAddFiles).toHaveBeenLastCalledWith([image]);

  fireEvent.paste(screen.getByRole("textbox", { name: "向智能体提问" }), {
    clipboardData: { files: [] },
  });
  expect(onAddFiles).toHaveBeenCalledTimes(2);
});

test("listens for Tauri native file drops in the packaged desktop app", async () => {
  (globalThis as typeof globalThis & { __TAURI_INTERNALS__?: object })
    .__TAURI_INTERNALS__ = {};
  nativeDrop.listen.mockResolvedValue(nativeDrop.unlisten);

  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onAddFiles={() => {}}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  await waitFor(() => expect(nativeDrop.listen).toHaveBeenCalledTimes(1));
});

test("turns the macOS Tauri byte-array response into an uploadable File", async () => {
  (globalThis as typeof globalThis & { __TAURI_INTERNALS__?: object })
    .__TAURI_INTERNALS__ = {};
  let handler: ((event: {
    payload: { type: string; paths?: string[] };
  }) => void) | undefined;
  nativeDrop.listen.mockImplementation(async (callback) => {
    handler = callback;
    return nativeDrop.unlisten;
  });
  nativeBridge.invoke.mockResolvedValue(
    Array.from(new Uint8Array(droppedFilePayload([1, 2, 3, 4]))),
  );
  const onAddFiles = vi.fn();

  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onAddFiles={onAddFiles}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );
  await waitFor(() => expect(handler).toBeTypeOf("function"));

  handler?.({
    payload: { type: "drop", paths: ["/Users/demo/Desktop/evidence.png"] },
  });

  await waitFor(() => expect(onAddFiles).toHaveBeenCalledTimes(1));
  const [files] = onAddFiles.mock.calls[0] as [File[]];
  expect(files).toHaveLength(1);
  expect(files[0]).toMatchObject({
    name: "evidence.png",
    type: "image/png",
    size: 4,
  });
  expect(nativeBridge.invoke).toHaveBeenCalledWith("read_dropped_file", {
    path: "/Users/demo/Desktop/evidence.png",
  });
});

test("keeps the native listener connected while a file is hovering", async () => {
  (globalThis as typeof globalThis & { __TAURI_INTERNALS__?: object })
    .__TAURI_INTERNALS__ = {};
  let handler: ((event: {
    payload: { type: string; paths?: string[] };
  }) => void) | undefined;
  nativeDrop.listen.mockImplementation(async (callback) => {
    handler = callback;
    return nativeDrop.unlisten;
  });

  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onAddFiles={() => {}}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );
  await waitFor(() => expect(handler).toBeTypeOf("function"));

  handler?.({ payload: { type: "enter", paths: ["/tmp/evidence.pdf"] } });
  await waitFor(() => {
    expect(screen.getByTestId("agent-composer").className).toContain(
      "agent-composer-dragging",
    );
  });

  expect(nativeDrop.listen).toHaveBeenCalledTimes(1);
});

test("rejects an unsupported native drop before reading its contents", async () => {
  (globalThis as typeof globalThis & { __TAURI_INTERNALS__?: object })
    .__TAURI_INTERNALS__ = {};
  let handler: ((event: {
    payload: { type: string; paths?: string[] };
  }) => void) | undefined;
  nativeDrop.listen.mockImplementation(async (callback) => {
    handler = callback;
    return nativeDrop.unlisten;
  });
  nativeBridge.invoke.mockResolvedValue(droppedFilePayload([1, 2, 3, 4]));
  const onAddFiles = vi.fn();

  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onAddFiles={onAddFiles}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );
  await waitFor(() => expect(handler).toBeTypeOf("function"));
  handler?.({ payload: { type: "drop", paths: ["/tmp/archive.zip"] } });

  await waitFor(() => {
    expect(screen.getByRole("alert").textContent).toContain("格式不支持");
  });
  expect(nativeBridge.invoke).not.toHaveBeenCalled();
  expect(onAddFiles).not.toHaveBeenCalled();
});

test("accepts a runtime-supported source file from a native drop", async () => {
  (globalThis as typeof globalThis & { __TAURI_INTERNALS__?: object })
    .__TAURI_INTERNALS__ = {};
  let handler: ((event: {
    payload: { type: string; paths?: string[] };
  }) => void) | undefined;
  nativeDrop.listen.mockImplementation(async (callback) => {
    handler = callback;
    return nativeDrop.unlisten;
  });
  nativeBridge.invoke.mockResolvedValue(
    droppedFilePayload([112, 114, 105, 110, 116, 40, 41, 10]),
  );
  const onAddFiles = vi.fn();

  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onAddFiles={onAddFiles}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );
  await waitFor(() => expect(handler).toBeTypeOf("function"));
  handler?.({ payload: { type: "drop", paths: ["/tmp/example.py"] } });

  await waitFor(() => expect(onAddFiles).toHaveBeenCalledTimes(1));
  const [files] = onAddFiles.mock.calls[0] as [File[]];
  expect(files[0]).toMatchObject({ name: "example.py", type: "text/plain" });
});

test("approval mode defaults to ask and can be changed to full", () => {
  const onApprovalModeChange = vi.fn();
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
      approvalMode="ask"
      onApprovalModeChange={onApprovalModeChange}
    />,
  );

  expect(screen.getByRole("button", { name: "请求审批" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "请求审批" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: "完全权限" }));
  expect(onApprovalModeChange).toHaveBeenCalledWith("full");
});

test("approval mode prop drives the displayed label", () => {
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
      approvalMode="help"
    />,
  );

  expect(screen.getByRole("button", { name: "帮我审批" })).toBeTruthy();
});

test("open popovers dismiss when clicking blank space, even inside the composer", () => {
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onSend={() => {}}
      onStop={() => {}}
      onApprovalModeChange={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
  expect(screen.getByRole("menu", { name: "更多选项" })).toBeTruthy();

  // A pointerdown on the textarea (blank space within the composer) closes it.
  fireEvent.pointerDown(screen.getByRole("textbox", { name: "向智能体提问" }));
  expect(screen.queryByRole("menu", { name: "更多选项" })).toBeNull();

  // Same behavior for the approval popover, with the ➕ toggle still usable.
  fireEvent.click(screen.getByRole("button", { name: "请求审批" }));
  expect(screen.getByRole("menu", { name: "审批权限" })).toBeTruthy();

  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("menu", { name: "审批权限" })).toBeNull();

  // The toggle itself keeps toggling: open, then pointerdown on it must not
  // be swallowed, and the click closes it again.
  fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
  fireEvent.pointerDown(screen.getByRole("button", { name: "更多选项" }));
  fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
  expect(screen.queryByRole("menu", { name: "更多选项" })).toBeNull();
});

test("plugin menu opens the current conversation picker instead of settings", () => {
  const onOpenPlugins = vi.fn();
  const onOpenSettings = vi.fn();
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onChange={() => {}}
      onOpenPlugins={onOpenPlugins}
      onOpenSettings={onOpenSettings}
      onApprovalModeChange={vi.fn()}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "插件" }));

  expect(onOpenPlugins).toHaveBeenCalledTimes(1);
  expect(onOpenSettings).not.toHaveBeenCalled();
});

test("plugin focus token returns focus to the existing plus button", async () => {
  const props = {
    hasModel: true,
    value: "",
    isRunning: false,
    onChange: () => {},
    onOpenPlugins: () => {},
    onOpenSettings: () => {},
    onSend: () => {},
    onStop: () => {},
  };
  const { rerender } = render(
    <AgentComposer {...props} pluginFocusRequestToken={0} />,
  );
  const plus = screen.getByRole("button", { name: "更多选项" });
  (screen.getByRole("textbox", { name: "向智能体提问" }) as HTMLElement).focus();

  rerender(<AgentComposer {...props} pluginFocusRequestToken={1} />);

  await waitFor(() => expect(document.activeElement).toBe(plus));
});

test("Escape closes the plus menu and returns focus to the plus button", async () => {
  render(
    <AgentComposer
      hasModel={true}
      value=""
      isRunning={false}
      onChange={() => {}}
      onOpenSettings={() => {}}
      onApprovalModeChange={vi.fn()}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );
  const plus = screen.getByRole("button", { name: "更多选项" });
  fireEvent.click(plus);
  const plugin = screen.getByRole("menuitem", { name: "插件" });
  plugin.focus();
  fireEvent.keyDown(document, { key: "Escape" });

  await waitFor(() => expect(screen.queryByRole("menuitem", { name: "插件" })).toBeNull());
  expect(document.activeElement).toBe(plus);
});

test('template upload dialog owns native drops even with a mounted chat composer',async()=>{
 Reflect.set(globalThis,'__TAURI_INTERNALS__',{});
 nativeDrop.listen.mockResolvedValue(nativeDrop.unlisten);
 const onAddFiles=vi.fn();
 render(<><dialog open aria-label="上传模板"/><AgentComposer hasModel value="" isRunning={false} onAddFiles={onAddFiles} onChange={()=>{}} onOpenSettings={()=>{}} onSend={()=>{}} onStop={()=>{}}/></>);
 await waitFor(()=>expect(nativeDrop.listen).toHaveBeenCalled());
 nativeDrop.listen.mock.calls[0][0]({payload:{type:'drop',paths:['/tmp/template.docx']}});
 expect(nativeBridge.invoke).not.toHaveBeenCalled();expect(onAddFiles).not.toHaveBeenCalled();
});
