import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "../src/app/App";
import {
  derivedThreadTitle,
  isDefaultThreadTitle,
} from "../src/app/navigation";
import type { HealthResponse } from "../src/runtime/contracts";
import type { UpdaterClient } from "../src/updater/contracts";

const nativeDrop = vi.hoisted(() => ({
  listen: vi.fn(),
  unlisten: vi.fn(),
}));
const nativeBridge = vi.hoisted(() => ({
  isTauri: () => false,
  invoke: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => nativeBridge);
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({ onDragDropEvent: nativeDrop.listen }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
  delete (globalThis as typeof globalThis & { __TAURI_INTERNALS__?: object })
    .__TAURI_INTERNALS__;
  nativeDrop.listen.mockReset();
  nativeDrop.unlisten.mockReset();
  nativeBridge.invoke.mockReset();
  window.localStorage.clear();
});

const zeroHealth: HealthResponse = {
  ok: true,
  service: "verilex-runtime",
  version: "0.3.0",
  storage: "local",
  cloud_backend: false,
  configured: { models: 0, mcp_servers: 0, skills: 0, folders: 0 },
};

function createUpToDateUpdaterClient(): UpdaterClient {
  return {
    supported: true,
    async getCurrentVersion() {
      return "0.3.0";
    },
    async check() {
      return null;
    },
    async relaunch() {},
  };
}

test("an up-to-date check preserves the real session sidebar", () => {
  render(
    <App
      initialHealth={zeroHealth}
      updaterClient={createUpToDateUpdaterClient()}
    />,
  );

  // Only real capabilities appear; retired placeholder entries are gone,
  // while the owner-ordered workbench entries (文书写作/知识库/插件/定时任务)
  // stay visible.
  expect(screen.getByRole("button", { name: "新建会话" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "搜索会话" }));
  expect(screen.getByRole("searchbox", { name: "搜索会话" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "设置" })).toBeTruthy();
  for (const label of [
    "材料脱敏",
    "学习迭代",
    "法律调研",
  ]) {
    expect(screen.queryByRole("button", { name: label })).toBeNull();
  }
  // The sidebar carries no connection status any more (product decision
  // 2026-08-31); the workspace empty state proves the app reached readiness.
  expect(
    screen.getByRole("heading", { name: "开启今天的法律工作" }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: /发现新版本/ })).toBeNull();
});

test("does not draw fake macOS window controls in the sidebar", () => {
  render(<App initialHealth={zeroHealth} />);

  expect(document.querySelectorAll(".traffic-dot")).toHaveLength(0);
});

test("opens settings from the sidebar footer", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("fetch", stubThreadFetch([]));

  render(<App initialHealth={zeroHealth} />);

  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  expect(await screen.findByRole("heading", {name:"通用"})).toBeTruthy();
});

test("settings rail shows a back button that returns to the app", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("fetch", stubThreadFetch([]));

  render(<App initialHealth={zeroHealth} />);

  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  await screen.findByRole("heading", {name:"通用"});

  const back = screen.getByRole("button", { name: /返回应用/ });
  expect(back).toBeTruthy();
  expect(document.querySelector(".app-frame")?.getAttribute("data-view"))
    .toBe("settings");

  fireEvent.click(back);
  expect(document.querySelector(".app-frame")?.getAttribute("data-view"))
    .toBe("agent");
  expect(screen.getByRole("button", { name: "设置" })).toBeTruthy();
});

class ChatSocketStub {
  static OPEN = 1;
  static CLOSED = 3;
  static instances: ChatSocketStub[] = [];
  readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sentMessages: string[] = [];

  constructor(public url: string, public subprotocol: string) {
    ChatSocketStub.instances.push(this);
  }

  send(data: string) {
    this.sentMessages.push(data);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }
}

const oneModelHealth: HealthResponse = {
  ...zeroHealth,
  configured: { models: 1, mcp_servers: 0, skills: 0, folders: 0 },
};

const zeroStepProvider = {
  id: "provider-1",
  name: "Test",
  kind: "openai_compatible" as const,
  base_url: "https://models.test/v1",
  default_model: "test-model",
  auth_mode: "none" as const,
  has_secret: false,
  enabled: true,
  created_at: "2026-08-21T00:00:00Z",
  updated_at: "2026-08-21T00:00:00Z",
};

const zeroStepWorkspace = {
  id: "workspace-1",
  root_path: "/Users/test/Contracts",
  display_name: "合同项目",
  allow_read: true,
  allow_write: true,
  created_at: "2026-08-21T00:00:00Z",
  last_used_at: "2026-08-21T00:00:00Z",
};

function zeroStepComposerFetch(url: string, init?: RequestInit): Promise<Response> {
  if (url.endsWith("/v1/providers")) {
    return Promise.resolve(
      new Response(JSON.stringify([zeroStepProvider]), { status: 200 }),
    );
  }
  if (url.endsWith("/v1/workspaces")) {
    return Promise.resolve(
      new Response(JSON.stringify([zeroStepWorkspace]), { status: 200 }),
    );
  }
  if (url.endsWith("/v1/providers/provider-1/models")) {
    return Promise.resolve(new Response(JSON.stringify({
      models: ["test-model", "vision-model"],
      selected_model: "test-model",
    }), { status: 200 }));
  }
  if (
    url.endsWith("/v1/providers/provider-1/model-capabilities")
    && init?.method === "PUT"
  ) {
    const body = JSON.parse(init.body as string) as {
      model: string;
      supports_vision: boolean;
    };
    return Promise.resolve(new Response(JSON.stringify({
      provider_id: "provider-1",
      model: body.model,
      supports_vision: body.supports_vision,
      supports_native_pdf: false,
      source: "user_declared",
      created_at: "2026-08-21T00:00:00Z",
      updated_at: "2026-08-21T00:00:00Z",
    }), { status: 200 }));
  }
  if (url.endsWith("/v1/providers/provider-1/model-capabilities")) {
    return Promise.resolve(new Response("[]", { status: 200 }));
  }
  if (url.endsWith("/v1/threads/draft-1/context") && init?.method === "PUT") {
    const body = JSON.parse(init.body as string) as {
      provider_id: string;
      model: string;
      workspace_id: string | null;
    };
    return Promise.resolve(new Response(JSON.stringify({
      id: "draft-1",
      title: "新会话",
      provider_id: body.provider_id,
      model: body.model,
      workspace_id: body.workspace_id,
      status: "idle",
      created_at: "2026-08-21T00:00:00Z",
      updated_at: "2026-08-21T00:00:00Z",
      archived_at: null,
      deleted_at: null,
      is_draft: true,
    }), { status: 200 }));
  }
  if (url.endsWith("/v1/threads") && init?.method === "POST") {
    return Promise.resolve(new Response(JSON.stringify({
      id: "draft-1",
      title: "新会话",
      provider_id: "provider-1",
      model: "test-model",
      workspace_id: null,
      status: "idle",
      created_at: "2026-08-21T00:00:00Z",
      updated_at: "2026-08-21T00:00:00Z",
      archived_at: null,
      deleted_at: null,
      is_draft: true,
    }), { status: 201 }));
  }
  if (url.includes("/attachment-uploads") && init?.method === "POST") {
    return Promise.resolve(new Response(JSON.stringify({
      id: "upl-1",
      thread_id: "draft-1",
      display_name: "证据.txt",
      media_type: "text/plain",
      size_bytes: 6,
      sha256: "a".repeat(64),
      created_at: "2026-08-21T00:00:00Z",
      expires_at: "2026-08-22T00:00:00Z",
    }), { status: 201 }));
  }
  return Promise.resolve(new Response("[]", { status: 200 }));
}

function attachmentDraftFetch(pendingUploadNumbers: readonly number[] = []) {
  let uploadNumber = 0;
  const pendingUploads = new Set(pendingUploadNumbers);
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.includes("/attachment-uploads") && init?.method === "POST") {
      uploadNumber += 1;
      if (pendingUploads.has(uploadNumber)) {
        return new Promise<Response>(() => {});
      }
      const file = init.body as File;
      return Promise.resolve(new Response(JSON.stringify({
        id: `upl-${uploadNumber}`,
        thread_id: "draft-1",
        display_name: file.name,
        media_type: file.type,
        size_bytes: file.size,
        sha256: `${uploadNumber}`.repeat(64),
        created_at: "2026-08-21T00:00:00Z",
        expires_at: "2026-08-22T00:00:00Z",
      }), { status: 201 }));
    }
    return zeroStepComposerFetch(url, init);
  });
}

function nativeDroppedFilePayload(bytes: number[], lastModified = 0): number[] {
  const payload = new ArrayBuffer(8 + bytes.length);
  new DataView(payload).setBigUint64(0, BigInt(lastModified), true);
  new Uint8Array(payload, 8).set(bytes);
  return Array.from(new Uint8Array(payload));
}

test("blank composer accepts an attachment without clicking new task", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  const fetchMock = vi.fn().mockImplementation(zeroStepComposerFetch);
  vi.stubGlobal("fetch", fetchMock);

  render(<App initialHealth={oneModelHealth} />);
  await screen.findByRole("button", { name: "选择模型，当前 test-model" });

  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "请分析这份证据" },
  });
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: {
      files: [new File(["证据"], "证据.txt", { type: "text/plain" })],
    },
  });

  expect(await screen.findByText("证据.txt")).toBeTruthy();
  await waitFor(() => {
    expect(fetchMock.mock.calls.filter(
      ([url, init]) =>
        `${url}`.endsWith("/v1/threads")
        && (init as RequestInit).method === "POST",
    )).toHaveLength(1);
  });
  expect(
    (screen.getByRole("textbox", { name: "向智能体提问" }) as HTMLTextAreaElement)
      .value,
  ).toBe("请分析这份证据");
  expect(screen.queryByRole("button", { name: /^新会话/ })).toBeNull();
});

test("a hidden draft first send preserves its complete attachment submission", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  ChatSocketStub.instances = [];
  vi.stubGlobal("WebSocket", ChatSocketStub as unknown as typeof WebSocket);
  vi.stubGlobal("fetch", attachmentDraftFetch());

  render(<App initialHealth={oneModelHealth} />);
  await screen.findByRole("button", { name: "选择模型，当前 test-model" });
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: {
      files: [new File(["旧证据"], "旧证据.txt", { type: "text/plain" })],
    },
  });
  expect(await screen.findByText("旧证据.txt")).toBeTruthy();
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"))
      .toBe(false);
  });
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "分析旧证据" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  const startFrame = await waitFor(() => {
    const frames = ChatSocketStub.instances.flatMap((socket) => socket.sentMessages)
      .filter((message) => JSON.parse(message).type === "turn.start");
    expect(frames).toHaveLength(1);
    return JSON.parse(frames[0]) as Record<string, unknown>;
  });
  expect(startFrame).toMatchObject({
    type: "turn.start",
    threadId: "draft-1",
    content: "分析旧证据",
    attachmentUploadIds: ["upl-1"],
    approvalMode: "ask",
    reasoningEffort: "auto",
  });
  expect(await screen.findByRole("button", {
    name: "预览附件 旧证据.txt",
  })).toBeTruthy();
});

test("an old hidden-draft ack keeps attachments added while its connection waits", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  ChatSocketStub.instances = [];
  vi.stubGlobal("WebSocket", ChatSocketStub as unknown as typeof WebSocket);
  vi.stubGlobal("fetch", attachmentDraftFetch([2]));

  render(<App initialHealth={oneModelHealth} />);
  await screen.findByRole("button", { name: "选择模型，当前 test-model" });
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: {
      files: [new File(["旧证据"], "旧证据.txt", { type: "text/plain" })],
    },
  });
  expect(await screen.findByText("旧证据.txt")).toBeTruthy();
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"))
      .toBe(false);
  });
  // The mounted conversation connects before the separate project runner.
  const sendingSocket = ChatSocketStub.instances[0]!;
  sendingSocket.readyState = 0;
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "分析旧证据" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: {
      files: [new File(["新证据"], "新证据.txt", { type: "text/plain" })],
    },
  });
  const selectedAttachments = await screen.findByLabelText("已选附件");
  await waitFor(() => {
    expect(within(selectedAttachments).getByText("新证据.txt")).toBeTruthy();
    expect(within(selectedAttachments).getByLabelText("上传中")).toBeTruthy();
  });

  sendingSocket.readyState = ChatSocketStub.OPEN;
  act(() => sendingSocket.onopen?.());
  await waitFor(() => {
    expect(sendingSocket.sentMessages.filter(
      (message) => JSON.parse(message).type === "turn.start",
    )).toHaveLength(1);
  });
  act(() => {
    sendingSocket.onmessage?.({
      data: JSON.stringify({ type: "ack", turnId: "turn-old", status: "queued" }),
    });
  });

  await waitFor(() => {
    const currentAttachments = screen.getByLabelText("已选附件");
    expect(within(currentAttachments).getByText("新证据.txt")).toBeTruthy();
    expect(within(currentAttachments).getByLabelText("上传中")).toBeTruthy();
    expect(within(currentAttachments).queryByText("旧证据.txt")).toBeNull();
  });
});

test("a rejected hidden-draft submission does not overwrite newer text or attachments", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  ChatSocketStub.instances = [];
  vi.stubGlobal("WebSocket", ChatSocketStub as unknown as typeof WebSocket);
  vi.stubGlobal("fetch", attachmentDraftFetch([2]));

  render(<App initialHealth={oneModelHealth} />);
  await screen.findByRole("button", { name: "选择模型，当前 test-model" });
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: {
      files: [new File(["旧证据"], "旧证据.txt", { type: "text/plain" })],
    },
  });
  expect(await screen.findByText("旧证据.txt")).toBeTruthy();
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"))
      .toBe(false);
  });
  const composer = screen.getByRole("textbox", { name: "向智能体提问" });
  fireEvent.change(composer, { target: { value: "分析旧证据" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  const sendingSocket = await waitFor(() => {
    const socket = ChatSocketStub.instances.find(candidate => candidate.sentMessages.some(
      message => JSON.parse(message).type === "turn.start"));
    expect(socket).toBeTruthy();
    return socket!;
  });
  fireEvent.change(composer, { target: { value: "这是拒绝发生前输入的新正文" } });
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: {
      files: [new File(["新证据"], "新证据.txt", { type: "text/plain" })],
    },
  });
  const selectedAttachments = await screen.findByLabelText("已选附件");
  await waitFor(() => {
    expect(within(selectedAttachments).getByText("新证据.txt")).toBeTruthy();
    expect(within(selectedAttachments).getByLabelText("上传中")).toBeTruthy();
  });

  act(() => {
    sendingSocket.onmessage?.({
      data: JSON.stringify({
        type: "error",
        code: "turn_rejected",
        message: "旧消息发送失败，请重试",
      }),
    });
  });

  expect((composer as HTMLTextAreaElement).value)
    .toBe("这是拒绝发生前输入的新正文");
  expect(screen.getByText("旧消息发送失败，请重试")).toBeTruthy();
  const currentAttachments = screen.getByLabelText("已选附件");
  expect(within(currentAttachments).getByText("旧证据.txt")).toBeTruthy();
  expect(within(currentAttachments).getByRole("button", { name: "移除 旧证据.txt" }))
    .toBeTruthy();
  expect(within(currentAttachments).getByText("新证据.txt")).toBeTruthy();
  expect(within(currentAttachments).getByLabelText("上传中")).toBeTruthy();
});

test("blank composer accepts a Finder drop without clicking new task", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  (globalThis as typeof globalThis & { __TAURI_INTERNALS__?: object })
    .__TAURI_INTERNALS__ = {};
  let dropHandler: ((event: {
    payload: { type: string; paths: string[] };
  }) => void) | undefined;
  nativeDrop.listen.mockImplementation(async (handler) => {
    dropHandler = handler;
    return nativeDrop.unlisten;
  });
  nativeBridge.invoke.mockResolvedValue(
    nativeDroppedFilePayload([109, 101, 109, 111]),
  );
  const fetchMock = vi.fn().mockImplementation(zeroStepComposerFetch);
  vi.stubGlobal("fetch", fetchMock);

  render(<App initialHealth={oneModelHealth} />);
  await screen.findByRole("button", { name: "选择模型，当前 test-model" });
  await waitFor(() => expect(dropHandler).toBeTypeOf("function"));

  dropHandler?.({
    payload: {
      type: "drop",
      paths: ["/Users/test/Desktop/证据.txt"],
    },
  });

  expect(await screen.findByText("证据.txt")).toBeTruthy();
  await waitFor(() => {
    expect(fetchMock.mock.calls.filter(
      ([url, init]) =>
        `${url}`.endsWith("/v1/threads")
        && (init as RequestInit).method === "POST",
    )).toHaveLength(1);
  });
  expect(nativeBridge.invoke).toHaveBeenCalledWith("read_dropped_file", {
    path: "/Users/test/Desktop/证据.txt",
  });
  expect(screen.queryByRole("button", { name: /^新会话/ })).toBeNull();
});

test("blank composer opens the model menu without clicking new task", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  const fetchMock = vi.fn().mockImplementation(zeroStepComposerFetch);
  vi.stubGlobal("fetch", fetchMock);

  render(<App initialHealth={oneModelHealth} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "选择模型，当前 test-model" }),
  );

  expect(await screen.findByRole("dialog", { name: "选择模型" })).toBeTruthy();
  expect(fetchMock.mock.calls.filter(
    ([url, init]) =>
      `${url}`.endsWith("/v1/threads")
      && (init as RequestInit).method === "POST",
  )).toHaveLength(0);
});

test("model menu renders inside the actions row so it anchors above the model button", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("fetch", vi.fn().mockImplementation(zeroStepComposerFetch));

  render(<App initialHealth={oneModelHealth} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "选择模型，当前 test-model" }),
  );

  const menu = await screen.findByRole("dialog", { name: "选择模型" });
  expect(menu.closest(".composer-actions")).not.toBeNull();
});

test("image flow sends unknown vision models without a declaration step", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  const fetchMock = vi.fn().mockImplementation(zeroStepComposerFetch);
  vi.stubGlobal("fetch", fetchMock);

  render(<App initialHealth={oneModelHealth} />);
  await screen.findByRole("button", { name: "选择模型，当前 test-model" });
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: {
      files: [new File(["image"], "证据.png", { type: "image/png" })],
    },
  });

  expect(await screen.findByText("证据.png")).toBeTruthy();
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"))
      .toBe(false);
  });
  expect(screen.queryByText(/尚未确认支持图片/)).toBeNull();
  fireEvent.click(screen.getByRole("button", {
    name: "选择模型，当前 test-model",
  }));
  fireEvent.click(await screen.findByRole("button", { name: "vision-model" }));

  await screen.findByRole("button", { name: "选择模型，当前 vision-model" });
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "选择模型" })).toBeNull();
  });
  expect(fetchMock.mock.calls.some(
    ([url, init]) =>
      `${url}`.endsWith("/v1/providers/provider-1/model-capabilities")
      && (init as RequestInit | undefined)?.method === "PUT",
  )).toBe(false);
});

test("image flow blocks only an explicitly unsupported model", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (
      url.endsWith("/v1/providers/provider-1/model-capabilities")
      && init?.method !== "PUT"
    ) {
      return Promise.resolve(new Response(JSON.stringify([{
        provider_id: "provider-1",
        model: "test-model",
        supports_vision: false,
        supports_native_pdf: false,
        source: "user_declared",
        created_at: "2026-08-22T00:00:00Z",
        updated_at: "2026-08-22T00:00:00Z",
      }]), { status: 200 }));
    }
    return zeroStepComposerFetch(url, init);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<App initialHealth={oneModelHealth} />);
  await screen.findByRole("button", { name: "选择模型，当前 test-model" });
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: {
      files: [new File(["image"], "证据.png", { type: "image/png" })],
    },
  });

  expect(await screen.findByText("证据.png")).toBeTruthy();
  expect(await screen.findByText("该模型不支持图片，请更换模型。")).toBeTruthy();
  expect(screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"))
    .toBe(true);
});

test("blank composer selects a workspace without creating a thread", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  const fetchMock = vi.fn().mockImplementation(zeroStepComposerFetch);
  vi.stubGlobal("fetch", fetchMock);

  render(<App initialHealth={oneModelHealth} />);
  fireEvent.click(
    await screen.findByRole("button", {
      name: "选择工作区，当前 选择工作区",
    }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "合同项目" }));

  expect(
    await screen.findByRole("button", { name: "选择工作区，当前 合同项目" }),
  ).toBeTruthy();
  expect(fetchMock.mock.calls.filter(
    ([url, init]) =>
      `${url}`.endsWith("/v1/threads")
      && (init as RequestInit).method === "POST",
  )).toHaveLength(0);
});

test("blank composer presents direct-start guidance", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("fetch", vi.fn().mockImplementation(zeroStepComposerFetch));

  render(<App initialHealth={oneModelHealth} />);
  await screen.findByRole("button", { name: "选择模型，当前 test-model" });

  expect(screen.getByRole("heading", { name: "开启今天的法律工作" })).toBeTruthy();
  expect(screen.getByText("还没有会话，点「新建会话」或直接在右侧输入即可开始。")).toBeTruthy();
  expect(screen.queryByText(/点击“新建会话”开始/)).toBeNull();
});

test("new task stays client-only and does not occupy the sidebar", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  const provider = {
    id: "provider-1",
    name: "Test",
    kind: "openai_compatible" as const,
    base_url: "https://models.test/v1",
    default_model: "test-model",
    auth_mode: "none" as const,
    has_secret: false,
    enabled: true,
    created_at: "2026-08-13T00:00:00Z",
    updated_at: "2026-08-13T00:00:00Z",
  };
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/v1/providers")) {
      return Promise.resolve(new Response(JSON.stringify([provider]), { status: 200 }));
    }
    if (url.endsWith("/v1/threads")) {
      return Promise.resolve(new Response(JSON.stringify({
        id: "thread-1",
        title: "新会话",
        provider_id: "provider-1",
        model: "test-model",
        workspace_id: null,
        status: "idle",
        created_at: "2026-08-13T00:00:00Z",
        updated_at: "2026-08-13T00:00:00Z",
      }), { status: 201 }));
    }
    if (url.includes("/attachment-uploads") && init?.method === "POST") {
      return Promise.resolve(new Response(JSON.stringify({
        id: "upl-1",
        thread_id: "thread-1",
        display_name: "证据.txt",
        media_type: "text/plain",
        size_bytes: 4,
        sha256: "a".repeat(64),
        created_at: "2026-08-13T00:00:00Z",
        expires_at: "2026-08-14T00:00:00Z",
      }), { status: 201 }));
    }
    return Promise.resolve(new Response("{}", { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<App initialHealth={zeroHealth} />);

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:9999/v1/providers",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });
  await act(async () => { await Promise.resolve(); });

  fireEvent.click(screen.getByRole("button", { name: "新建会话" }));

  expect(await screen.findByRole("heading", { name: "开启今天的法律工作" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^新会话/ })).toBeNull();
  expect(
    fetchMock.mock.calls.filter(
      ([url, init]) =>
        `${url}`.endsWith("/v1/threads") && (init as RequestInit).method === "POST",
    ),
  ).toHaveLength(0);

  const file = new File(["证据"], "证据.txt", { type: "text/plain" });
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: { files: [file] },
  });
  await waitFor(() => {
    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) =>
          `${url}`.endsWith("/v1/threads") && (init as RequestInit).method === "POST",
      ),
    ).toHaveLength(1);
  });
  expect(await screen.findByText("证据.txt")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^新会话/ })).toBeNull();
});

test("keeps sending blocked until a model is configured", () => {
  render(<App initialHealth={zeroHealth} />);

  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "整理材料" },
  });

  expect(screen.getByRole("button", { name: "发送" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByText("连接模型后即可发送")).toBeTruthy();
});

test("sending without a session auto-creates one and delivers the message", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  ChatSocketStub.instances = [];
  vi.stubGlobal("WebSocket", ChatSocketStub as unknown as typeof WebSocket);
  const provider = {
    id: "provider-1",
    name: "Test",
    kind: "openai_compatible" as const,
    base_url: "https://models.test/v1",
    default_model: "test-model",
    auth_mode: "none" as const,
    has_secret: false,
    enabled: true,
    created_at: "2026-08-13T00:00:00Z",
    updated_at: "2026-08-13T00:00:00Z",
  };
  let materialized = false;
  let resolveDraftCreate: ((response: Response) => void) | null = null;
  const draftCreateResponse = new Promise<Response>((resolve) => {
    resolveDraftCreate = resolve;
  });
  const formalThread = {
    id: "thread-1",
    title: "新会话",
    provider_id: "provider-1",
    model: "test-model",
    workspace_id: null,
    status: "idle",
    created_at: "2026-08-13T00:00:00Z",
    updated_at: "2026-08-13T00:00:00Z",
    archived_at: null,
    deleted_at: null,
    is_draft: false,
  };
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/v1/providers")) {
      return Promise.resolve(new Response(JSON.stringify([provider]), { status: 200 }));
    }
    if (url.endsWith("/v1/threads") && init?.method === "POST") {
      return draftCreateResponse;
    }
    if ((url.endsWith("/v1/threads") || url.includes("/v1/threads?")) && !init?.method) {
      return Promise.resolve(
        new Response(JSON.stringify(materialized ? [formalThread] : []), { status: 200 }),
      );
    }
    if (url.includes("scope=archived") || url.includes("scope=trash")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    if (
      url.includes("/messages") ||
      url.includes("/turns") ||
      url.includes("/events") ||
      url.includes("/attachments")
    ) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    return Promise.resolve(new Response("{}", { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <App
      initialHealth={oneModelHealth}
      updaterClient={createUpToDateUpdaterClient()}
    />,
  );

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:9999/v1/providers",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });
  await act(async () => { await Promise.resolve(); });

  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "你好" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  expect(fetchMock.mock.calls.filter(
    ([url, init]) =>
      `${url}`.endsWith("/v1/threads") && (init as RequestInit).method === "POST",
  )).toHaveLength(1);
  act(() => {
    resolveDraftCreate?.(new Response(JSON.stringify({
        ...formalThread,
        is_draft: true,
      }), { status: 201 }));
  });

  const frame = await waitFor(() => {
    const sent = ChatSocketStub.instances
      .flatMap((socket) => socket.sentMessages)
      .find((message) => message.includes("turn.start"));
    expect(sent).toBeDefined();
    return sent!;
  });
  expect(frame).toContain('"threadId":"thread-1"');
  expect(frame).toContain("你好");
  const createCall = fetchMock.mock.calls.find(
    ([url, init]) =>
      `${url}`.endsWith("/v1/threads") && (init as RequestInit).method === "POST",
  );
  expect(JSON.parse((createCall?.[1] as RequestInit).body as string)).toMatchObject({
    provider_id: "provider-1",
    model: "test-model",
    is_draft: true,
  });
  expect(screen.queryByRole("button", { name: /^新会话/ })).toBeNull();

  materialized = true;
  const socket = ChatSocketStub.instances.find((candidate) =>
    candidate.sentMessages.some((message) => message.includes("turn.start")),
  );
  act(() => {
    socket?.onopen?.();
    socket?.onmessage?.({
      data: JSON.stringify({ type: "ack", turnId: "turn-1", status: "queued" }),
    });
  });
  expect(await screen.findByRole("button", { name: /^你好/ })).toBeTruthy();
  expect(screen.getAllByRole("button", { name: /^你好/ })).toHaveLength(1);
  expect(
    (screen.getByRole("textbox", { name: "向智能体提问" }) as HTMLTextAreaElement).value,
  ).toBe("");

  act(() => {
    socket?.onmessage?.({
      data: JSON.stringify({
        type: "event",
        event: {
          type: "turn_started",
          turnId: "turn-1",
          sequence: 1,
          payload: {},
        },
      }),
    });
  });
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "继续检查附件" },
  });
  fireEvent.click(screen.getByRole("button", { name: "提交下一条" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "打断并发送" }));
  expect(socket?.sentMessages.some((message) => message.includes("turn.stop"))).toBe(true);
  expect(socket?.sentMessages.filter((message) => message.includes("turn.start"))).toHaveLength(1);

  act(() => {
    socket?.onmessage?.({
      data: JSON.stringify({
        type: "event",
        event: {
          type: "turn_cancelled",
          turnId: "turn-1",
          sequence: 2,
          payload: {},
        },
      }),
    });
  });
  await waitFor(() => {
    expect(socket?.sentMessages.filter((message) => message.includes("turn.start"))).toHaveLength(2);
  });
  expect(socket?.sentMessages.at(-1)).toContain("继续检查附件");
});

test("a late hidden-draft response cannot override newer task navigation", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  ChatSocketStub.instances = [];
  vi.stubGlobal("WebSocket", ChatSocketStub as unknown as typeof WebSocket);
  const provider = {
    id: "provider-1",
    name: "Test",
    kind: "openai_compatible" as const,
    base_url: "https://models.test/v1",
    default_model: "test-model",
    auth_mode: "none" as const,
    has_secret: false,
    enabled: true,
    created_at: "2026-08-13T00:00:00Z",
    updated_at: "2026-08-13T00:00:00Z",
  };
  const existing = {
    id: "thread-existing",
    title: "已有任务",
    provider_id: "provider-1",
    model: "test-model",
    workspace_id: null,
    status: "idle",
    created_at: "2026-08-13T00:00:00Z",
    updated_at: "2026-08-13T00:00:00Z",
    archived_at: null,
    deleted_at: null,
    is_draft: false,
  };
  let resolveDraftCreate: ((response: Response) => void) | null = null;
  const draftCreateResponse = new Promise<Response>((resolve) => {
    resolveDraftCreate = resolve;
  });
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/v1/providers")) {
      return Promise.resolve(new Response(JSON.stringify([provider]), { status: 200 }));
    }
    if (url.endsWith("/v1/threads") && init?.method === "POST") {
      return draftCreateResponse;
    }
    if ((url.endsWith("/v1/threads") || url.includes("/v1/threads?")) && !init?.method) {
      return Promise.resolve(new Response(JSON.stringify([existing]), { status: 200 }));
    }
    if (url.includes("scope=archived") || url.includes("scope=trash")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    if (init?.method === "DELETE" && url.endsWith("/draft")) {
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    if (
      url.includes("/messages") || url.includes("/turns")
      || url.includes("/events") || url.includes("/attachments")
    ) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    return Promise.resolve(new Response("{}", { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App initialHealth={oneModelHealth} updaterClient={createUpToDateUpdaterClient()} />);

  await screen.findByRole("button", { name: /^已有任务/ });
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "会被放弃的首条消息" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  fireEvent.click(screen.getByRole("button", { name: /^已有任务/ }));

  act(() => {
    resolveDraftCreate?.(new Response(JSON.stringify({
      ...existing,
      id: "draft-late",
      title: "新会话",
      is_draft: true,
    }), { status: 201 }));
  });

  await waitFor(() => {
    expect(
      screen.getByRole("button", { name: /^已有任务/ }).getAttribute("aria-current"),
    ).toBe("true");
    expect(fetchMock.mock.calls.some(
      ([url, init]) => `${url}`.endsWith("/v1/threads/draft-late/draft")
        && (init as RequestInit).method === "DELETE",
    )).toBe(true);
  });
});

test("a failed provider listing surfaces a visible error", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith("/v1/providers")) {
      return Promise.resolve(new Response("{}", { status: 503 }));
    }
    return Promise.resolve(new Response("[]", { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <App
      initialHealth={oneModelHealth}
      updaterClient={createUpToDateUpdaterClient()}
    />,
  );

  expect(await screen.findByText(/无法加载模型配置/)).toBeTruthy();
});

test("new session without a usable provider shows guidance instead of silence", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith("/v1/providers")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    return Promise.resolve(new Response("[]", { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <App
      initialHealth={oneModelHealth}
      updaterClient={createUpToDateUpdaterClient()}
    />,
  );
  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:9999/v1/providers",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });
  await act(async () => { await Promise.resolve(); });

  fireEvent.click(screen.getByRole("button", { name: "新建会话" }));

  expect(
    await screen.findByText("尚未配置可用模型，请先在设置页连接模型。"),
  ).toBeTruthy();
  expect(
    fetchMock.mock.calls.filter(
      ([url, init]) =>
        `${url}`.endsWith("/v1/threads") && (init as RequestInit).method === "POST",
    ),
  ).toHaveLength(0);
});

test("new session retries the provider list and heals a transient failure", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  const provider = {
    id: "provider-1",
    name: "Test",
    kind: "openai_compatible" as const,
    base_url: "https://models.test/v1",
    default_model: "test-model",
    auth_mode: "none" as const,
    has_secret: false,
    enabled: true,
    created_at: "2026-08-13T00:00:00Z",
    updated_at: "2026-08-13T00:00:00Z",
  };
  let providerCalls = 0;
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith("/v1/providers")) {
      providerCalls += 1;
      if (providerCalls === 1) {
        return Promise.resolve(new Response("{}", { status: 503 }));
      }
      return Promise.resolve(new Response(JSON.stringify([provider]), { status: 200 }));
    }
    if (url.endsWith("/v1/threads")) {
      return Promise.resolve(new Response(JSON.stringify({
        id: "thread-1",
        title: "新会话",
        provider_id: "provider-1",
        model: "test-model",
        workspace_id: null,
        status: "idle",
        created_at: "2026-08-13T00:00:00Z",
        updated_at: "2026-08-13T00:00:00Z",
      }), { status: 201 }));
    }
    return Promise.resolve(new Response("[]", { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <App
      initialHealth={oneModelHealth}
      updaterClient={createUpToDateUpdaterClient()}
    />,
  );
  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:9999/v1/providers",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });
  await act(async () => { await Promise.resolve(); });

  fireEvent.click(screen.getByRole("button", { name: "新建会话" }));

  expect(await screen.findByRole("heading", { name: "开启今天的法律工作" })).toBeTruthy();
  expect(
    fetchMock.mock.calls.filter(
      ([url, init]) =>
        `${url}`.endsWith("/v1/threads") && (init as RequestInit).method === "POST",
    ),
  ).toHaveLength(0);
});

const lastThreadStorageKey = "verilex:last-thread:v1";

const threadRecord = (id: string, title: string, updatedAt: string) => ({
  id,
  title,
  provider_id: "provider-1",
  model: "test-model",
  workspace_id: null,
  status: "idle",
  created_at: "2026-08-13T00:00:00Z",
  updated_at: updatedAt,
  archived_at: null,
  deleted_at: null,
  is_draft: false,
});

function stubThreadFetch(active: unknown[], archived: unknown[] = [], trash: unknown[] = []) {
  return vi.fn().mockImplementation((url: string) => {
    if (url.endsWith("/v1/providers")) {
      return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
    }
    // The bare list endpoint (no scope query) returns the active list too;
    // the sidebar asks for the general scene, the legal page for its own.
    if (
      url === "http://127.0.0.1:9999/v1/threads"
      || url.endsWith("/v1/threads?scope=active")
      || url.endsWith("/v1/threads?scene=general")
    ) {
      return Promise.resolve(new Response(JSON.stringify(active), { status: 200 }));
    }
    if (
      url.endsWith("/v1/threads?scope=archived")
      || url.endsWith("/v1/threads?scope=archived&scene=general")
    ) {
      return Promise.resolve(new Response(JSON.stringify(archived), { status: 200 }));
    }
    if (
      url.endsWith("/v1/threads?scope=trash")
      || url.endsWith("/v1/threads?scope=trash&scene=general")
    ) {
      return Promise.resolve(new Response(JSON.stringify(trash), { status: 200 }));
    }
    if (url.endsWith("/v1/threads?scene=legal-retrieval")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    if (url.endsWith("/v1/threads")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    return Promise.resolve(new Response("[]", { status: 200 }));
  });
}

test("loads the thread list after startup and restores the last session", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  window.localStorage.setItem(lastThreadStorageKey, "thread-b");
  vi.stubGlobal(
    "fetch",
    stubThreadFetch([
      threadRecord("thread-a", "旧合同整理", "2026-08-16T00:00:00Z"),
      threadRecord("thread-b", "劳动合同审查", "2026-08-18T00:00:00Z"),
    ]),
  );

  render(
    <App
      initialHealth={oneModelHealth}
      updaterClient={createUpToDateUpdaterClient()}
    />,
  );

  const rowB = await screen.findByRole("button", { name: /劳动合同审查/ });
  expect(screen.getByRole("button", { name: /旧合同整理/ })).toBeTruthy();
  expect(rowB.getAttribute("aria-current")).toBe("true");
  // Restored session is selected: sidebar row and conversation header.
  expect(screen.getAllByText("劳动合同审查").length).toBeGreaterThanOrEqual(2);
});

test("a failed thread listing surfaces a retryable error and heals on refresh", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  let listCalls = 0;
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    const isThreadListCall =
      url === "http://127.0.0.1:9999/v1/threads" ||
      url.includes("/v1/threads?scope=") ||
      url.includes("/v1/threads?scene=");
    if (isThreadListCall && url !== "http://127.0.0.1:9999/v1/threads?scope=archived" && url !== "http://127.0.0.1:9999/v1/threads?scope=trash") {
      listCalls += 1;
      if (listCalls === 1) {
        return Promise.resolve(new Response("{}", { status: 503 }));
      }
      return Promise.resolve(
        new Response(
          JSON.stringify([threadRecord("thread-a", "恢复后的会话", "2026-08-18T00:00:00Z")]),
          { status: 200 },
        ),
      );
    }
    if (url.endsWith("/v1/providers")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    return Promise.resolve(new Response("[]", { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <App
      initialHealth={oneModelHealth}
      updaterClient={createUpToDateUpdaterClient()}
    />,
  );

  expect(await screen.findByText(/无法加载会话列表/)).toBeTruthy();

  await act(async () => {
    window.dispatchEvent(new Event("focus"));
  });

  expect(await screen.findByRole("button", { name: /恢复后的会话/ })).toBeTruthy();
});

test("thread title derivation trims whitespace and caps length", () => {
  expect(derivedThreadTitle("  帮我整理   合同要点  ")).toBe("帮我整理 合同要点");
  expect(derivedThreadTitle("一".repeat(30))).toBe(`${"一".repeat(20)}…`);
  expect(derivedThreadTitle("", "证据目录.pdf")).toBe("证据目录.pdf");
  expect(isDefaultThreadTitle("新会话")).toBe(true);
  expect(isDefaultThreadTitle("新会话 3")).toBe(true);
  expect(isDefaultThreadTitle("合同审查")).toBe(false);
});

test("sidebar archive action calls the runtime and refreshes all scopes", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  const calls: string[] = [];
  let archivedNow = false;
  let restoredLegal = false;
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const path = url.replace("http://127.0.0.1:9999", "");
    calls.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/v1/threads/thread-a/archive" && init?.method === "POST") {
      archivedNow = true;
      return Promise.resolve(
        new Response(
          JSON.stringify(threadRecord("thread-a", "劳动合同审查", "2026-08-18T01:00:00Z")),
          { status: 200 },
        ),
      );
    }
    if (path === "/v1/threads/legal-trash/restore" && init?.method === "POST") {
      restoredLegal = true;
      return Promise.resolve(new Response(JSON.stringify({...threadRecord("legal-trash", "检索记录", "2026-08-18T01:00:00Z"),scene:"legal-retrieval"}), {status:200}));
    }
    if (path === "/v1/providers") {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    if (
      path === "/v1/threads"
      || path === "/v1/threads?scope=active"
      || path === "/v1/threads?scene=general"
    ) {
      const active = archivedNow
        ? []
        : [threadRecord("thread-a", "劳动合同审查", "2026-08-18T00:00:00Z")];
      return Promise.resolve(new Response(JSON.stringify(active), { status: 200 }));
    }
    if (path === "/v1/threads?scope=archived" || path === "/v1/threads?scope=archived&scene=general") {
      const archived = archivedNow
        ? [threadRecord("thread-a", "劳动合同审查", "2026-08-18T01:00:00Z")]
        : [];
      return Promise.resolve(new Response(JSON.stringify(archived), { status: 200 }));
    }
    if (path === "/v1/threads?scope=trash") {
      const trash = restoredLegal ? [] : [{...threadRecord("legal-trash", "检索记录", "2026-08-18T01:00:00Z"),scene:"legal-retrieval"}];
      return Promise.resolve(new Response(JSON.stringify(trash), { status: 200 }));
    }
    return Promise.resolve(new Response("[]", { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <App
      initialHealth={oneModelHealth}
      updaterClient={createUpToDateUpdaterClient()}
    />,
  );

  await screen.findByRole("button", { name: /劳动合同审查/ });
  fireEvent.click(screen.getAllByRole("button", { name: "会话操作" })[0]);
  fireEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "归档" }));

  await waitFor(() => {
    expect(calls).toContain("POST /v1/threads/thread-a/archive");
  });
  await waitFor(() => {
    expect(screen.queryByRole("button", { name: /劳动合同审查/ })).toBeNull();
  });
  fireEvent.click(screen.getByRole("button", {name:"设置"}));
  fireEvent.click(screen.getByRole("button", {name:"对话"}));
  expect(await screen.findByText("劳动合同审查")).toBeTruthy();
  expect(screen.getByText("检索记录")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name:"恢复回收站：检索记录"}));
  await waitFor(()=>expect(calls).toContain("POST /v1/threads/legal-trash/restore"));
  await waitFor(()=>expect(screen.queryByText("检索记录")).toBeNull());
});

test("command panel opens real settings, help and session search with collapsed navigation", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("fetch", stubThreadFetch([]));
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  render(<App initialHealth={zeroHealth} />);
  fireEvent.click(screen.getByRole('button',{name:'收起侧栏'}));
  const choose = async (text: string) => {
    fireEvent.click(screen.getByRole('button',{name:'打开命令面板'}));
    fireEvent.change(screen.getByRole('combobox',{name:'搜索命令'}),{target:{value:text}});
    fireEvent.keyDown(screen.getByRole('combobox',{name:'搜索命令'}),{key:'Enter'});
    await waitFor(()=>expect(screen.queryByRole('dialog',{name:'命令面板'})).toBeNull());
  };
  await choose('搜索会话');
  await waitFor(()=>expect(document.activeElement).toBe(screen.getByRole('searchbox',{name:'搜索会话'})));
  await choose('查看快捷键设置');
  expect(await screen.findByRole('heading',{name:'通用'})).toBeTruthy();
  expect(screen.getByRole('heading',{name:'快捷键'})).toBeTruthy();
  await choose('帮助与反馈');
  expect(await screen.findByRole('heading',{name:'帮助与反馈'})).toBeTruthy();
});

test("deleting a used connection refreshes the conversation and lets its draft continue with another model", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("WebSocket", ChatSocketStub as unknown as typeof WebSocket);
  window.localStorage.setItem(lastThreadStorageKey, "retained-thread");
  let deleted = false;
  let rebound = false;
  const replacement = { ...zeroStepProvider, id: "provider-2", name: "Replacement", default_model: "model-2" };
  const currentThread = () => ({ ...threadRecord("retained-thread", "Retained history", "2026-10-25T00:00:00Z"),
    provider_id: rebound ? "provider-2" : deleted ? null : "provider-1", model: rebound ? "model-2" : "test-model" });
  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/v1/providers/provider-1") && init?.method === "DELETE") {
      deleted = true;
      return new Response(null, { status: 204 });
    }
    if (url.endsWith("/v1/providers")) return new Response(JSON.stringify(deleted ? [replacement] : [zeroStepProvider, replacement]));
    if (url.endsWith("/v1/threads?scene=general")) return new Response(JSON.stringify([currentThread()]));
    if (url.endsWith("/v1/threads/retained-thread/context") && init?.method === "PUT") {
      expect(JSON.parse(init.body as string)).toMatchObject({ provider_id: "provider-2", model: "model-2", workspace_id: null });
      rebound = true;
      return new Response(JSON.stringify(currentThread()));
    }
    if (url.endsWith("/v1/providers/provider-2/models")) return new Response(JSON.stringify({ models: ["model-2"], selected_model: "model-2" }));
    return new Response("[]");
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App initialHealth={{ ...oneModelHealth, configured: { ...oneModelHealth.configured, models: 2 } }}
    refreshHealth={async () => oneModelHealth} />);
  await screen.findByRole("button", { name: /Retained history/ });
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), { target: { value: "Preserve this draft" } });
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "模型", exact: true }));
  fireEvent.click(await screen.findByRole("button", { name: "删除 Test" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "删除 Test" })).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "返回应用" }));
  fireEvent.click(await screen.findByRole("button", { name: "选择模型，当前 请选择模型" }));
  fireEvent.click(await screen.findByRole("button", { name: "model-2", exact: true }));
  expect(await screen.findByRole("button", { name: "选择模型，当前 model-2" })).toBeTruthy();
  expect(rebound).toBe(true);
  expect((screen.getByRole("textbox", { name: "向智能体提问" }) as HTMLTextAreaElement).value).toBe("Preserve this draft");
  expect(screen.queryByText("原模型连接已删除，会话内容已保留。请选择模型后继续。")).toBeNull();
});
