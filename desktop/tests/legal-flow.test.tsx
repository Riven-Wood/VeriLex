/**
 * App-level wiring for the legal search page: starting a search must route
 * the created scene thread into the embedded workspace view (the regression
 * this locks: activeThread must resolve from the legal thread list).
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { App } from "../src/app/App";
import type { HealthResponse } from "../src/runtime/contracts";
import type { UpdaterClient } from "../src/updater/contracts";

const nativeDrop = vi.hoisted(() => ({ listen: vi.fn(), unlisten: vi.fn() }));
const nativeBridge = vi.hoisted(() => ({ invoke: vi.fn(), isTauri: () => false }));

vi.mock("@tauri-apps/api/core", () => nativeBridge);
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({ onDragDropEvent: nativeDrop.listen }),
}));

const legalThread = {
  id: "legal-1",
  title: "民法典第二百条",
  provider_id: "provider-1",
  model: "test-model",
  workspace_id: null,
  status: "idle",
  created_at: "2026-09-05T00:00:00Z",
  updated_at: "2026-09-05T00:00:00Z",
  archived_at: null,
  deleted_at: null,
  is_draft: false,
  reasoning_effort: "auto",
  scene: "legal-retrieval",
};

const provider = {
  id: "provider-1",
  name: "Test",
  kind: "openai_compatible" as const,
  base_url: "https://models.test/v1",
  default_model: "test-model",
  auth_mode: "none" as const,
  has_secret: false,
  enabled: true,
  created_at: "2026-09-05T00:00:00Z",
  updated_at: "2026-09-05T00:00:00Z",
};

const oneModelHealth: HealthResponse = {
  ok: true,
  service: "verilex-runtime",
  version: "0.3.0",
  storage: "local",
  cloud_backend: false,
  configured: { models: 1, mcp_servers: 0, skills: 0, folders: 0 },
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  window.localStorage.clear();
  fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/v1/providers")) {
      return Promise.resolve(
        new Response(JSON.stringify([provider]), { status: 200 }),
      );
    }
    if (url.endsWith("/v1/providers/provider-1/models")) {
      return Promise.resolve(
        new Response(
          JSON.stringify({ models: ["test-model"], selected_model: "test-model" }),
          { status: 200 },
        ),
      );
    }
    if (url.endsWith("/v1/providers/provider-1/model-capabilities")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    if (url.endsWith("/v1/mcp/servers")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    if (url.endsWith("/v1/threads") && init?.method === "POST") {
      return Promise.resolve(
        new Response(JSON.stringify(legalThread), { status: 201 }),
      );
    }
    if (url.includes("/v1/threads?scene=legal-retrieval")) {
      return Promise.resolve(
        new Response(JSON.stringify([legalThread]), { status: 200 }),
      );
    }
    if (url.includes("/v1/threads?")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    if (url.includes("/v1/threads/legal-1/")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    return Promise.resolve(new Response("[]", { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function upToDateUpdater(): UpdaterClient {
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

test("检索会把法律会话接入内嵌结果视图（历史加载即接线证据）", async () => {
  render(
    <StrictMode>
      <App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()} />
    </StrictMode>,
  );

  fireEvent.click(screen.getByRole("button", { name: "法律检索" }));

  const input = await screen.findByPlaceholderText(
    "输入法律问题，例如：劳动合同解除的赔偿标准",
  );
  fireEvent.change(input, { target: { value: "民法典第二百条" } });
  fireEvent.submit(input.closest("form")!);

  await waitFor(() => {
    const createCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        `${url}`.endsWith("/v1/threads") && (init as RequestInit).method === "POST",
    );
    expect(createCall).toBeTruthy();
    expect(JSON.parse((createCall![1] as RequestInit).body as string)).toMatchObject({
      title: "民法典第二百条",
      scene: "legal-retrieval",
      legal_channel: "web",
      legal_skills: [],
    });
  });

  // The embedded workspace must adopt the created thread: it requests the
  // thread's history. Before the fix, activeThread never resolved and these
  // requests never fired.
  await waitFor(() => {
    const historyCall = fetchMock.mock.calls.find(
      ([url]) => `${url}`.includes("/v1/threads/legal-1/messages"),
    );
    expect(historyCall).toBeTruthy();
  });
});

test("检索记录点击同样接回内嵌视图", async () => {
  render(
    <StrictMode>
      <App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()} />
    </StrictMode>,
  );

  fireEvent.click(screen.getByRole("button", { name: "法律检索" }));

  const chip = await screen.findByRole("button", { name: "民法典第二百条" });
  fireEvent.click(chip);

  await waitFor(() => {
    const historyCall = fetchMock.mock.calls.find(
      ([url]) => `${url}`.includes("/v1/threads/legal-1/messages"),
    );
    expect(historyCall).toBeTruthy();
  });
});


test("追问默认收起，离开法律页释放检索会话", async () => {
  render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()} />);
  fireEvent.click(screen.getByRole("button", { name: "法律检索" }));
  fireEvent.click(await screen.findByRole("button", { name: "民法典第二百条" }));
  expect(screen.queryByRole("textbox", { name: "向智能体提问" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "继续追问" }));
  expect(screen.getByRole("textbox", { name: "向智能体提问" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "后退" }));
  await waitFor(() => expect(screen.queryByText("民法典第二百条")).toBeNull());
  expect(screen.queryByRole("button", { name: "继续追问" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "法律检索" }));
  expect(await screen.findByRole("button", { name: "民法典第二百条" })).toBeTruthy();
});


test("知识库首次检索无需 MCP，所选库进入实际 turn.start 请求", async () => {
  class Socket {
    static OPEN = 1;
    static sent: string[] = [];
    readyState = 1;
    onopen: (() => void) | null = null;
    onmessage: ((event: {data: string}) => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor() { queueMicrotask(() => this.onopen?.()); }
    send(value: string) { Socket.sent.push(value); }
    close() { this.readyState = 3; }
  }
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("__VERILEX_API_TOKEN__", "synthetic-test-token");
  const fetchExisting = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/v1/knowledge")) return Promise.resolve(new Response(JSON.stringify([{id:"library-1",name:"自有资料",status:"ready"}]),{status:200}));
    if (url.endsWith("/v1/mcp/servers")) return Promise.resolve(new Response("[]",{status:200}));
    return fetchExisting(url, init);
  });
  render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()} />);
  fireEvent.click(screen.getByRole("button", {name: "法律检索"}));
  const input = await screen.findByPlaceholderText("输入法律问题，例如：劳动合同解除的赔偿标准");
  fireEvent.click(within(screen.getByRole("group", {name:"检索渠道"})).getByRole("button", {name:"知识库"}));
  fireEvent.click(await screen.findByRole("checkbox", {name:"自有资料"}));
  fireEvent.change(input, {target:{value:"检索自有资料"}});
  fireEvent.submit(input.closest("form")!);
  await waitFor(() => {
    const start = Socket.sent.map(value => JSON.parse(value)).find(value => value.type === "turn.start");
    expect(start).toMatchObject({content:"检索自有资料", knowledgeLibraryIds:["library-1"]});
  });
  const create = fetchMock.mock.calls.find(([url,init]) => url.endsWith('/v1/threads') && init?.method === 'POST');
  expect(JSON.parse(create![1].body)).not.toHaveProperty('legal_channel');
});
