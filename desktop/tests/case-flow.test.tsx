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
  id: "case-1",
  title: "示例合同案件",
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
  scene: "case-retrieval",
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
      return Promise.resolve(new Response(JSON.stringify([{id:"plugin-1",displayName:"示例插件",transport:"stdio",command:"/usr/bin/echo",args:[],env:{},headers:{},secretHeaderKeys:[],secretEnvKeys:[]}]), { status: 200 }));
    }
    if (url.endsWith("/v1/threads") && init?.method === "POST") {
      return Promise.resolve(
        new Response(JSON.stringify({...legalThread, id: "case-new", is_draft: true}), { status: 201 }),
      );
    }
    if (url.includes("/v1/threads?scene=case-retrieval")) {
      return Promise.resolve(
        new Response(JSON.stringify([legalThread]), { status: 200 }),
      );
    }
    if (url.includes("/v1/threads?")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    if (url.includes("/v1/threads/case-1/") || url.includes("/v1/threads/case-new/")) {
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

test("检索会把案例会话接入内嵌结果视图（历史加载即接线证据）", async () => {
  render(
    <StrictMode>
      <App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()} />
    </StrictMode>,
  );

  fireEvent.click(screen.getByRole("button", { name: "案例检索" }));

  const input = await screen.findByRole("textbox", {name:"向智能体提问"});
  const composer = screen.getByTestId("agent-composer");
  expect(within(composer).getByRole("group", {name:"检索渠道"})).toBeTruthy();
  expect(within(composer).getByRole("combobox", {name:"检索技能"})).toBeTruthy();
  expect(document.querySelector(".legal-search-view .legal-search-channel-row")).toBeNull();
  fireEvent.click(within(composer).getByRole("button", {name:"更多选项"}));
  expect(screen.queryByRole("menuitem", {name:"全部技能"})).toBeNull();
  expect(screen.queryByRole("menuitem", {name:"插件"})).toBeNull();
  expect(screen.getByRole("menuitem", {name:"添加附件"})).toBeTruthy();
  fireEvent.click(within(composer).getByRole("button", {name:"更多选项"}));

  fireEvent.click(within(screen.getByRole("group", {name:"检索渠道"})).getByRole("button", { name: "插件" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: "示例插件" }));
  fireEvent.change(input, { target: { value: "示例合同案件" } });
  await waitFor(() => expect(screen.getByRole("button", {name:"发送"}).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", {name:"发送"}));

  await waitFor(() => {
    const createCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        `${url}`.endsWith("/v1/threads") && (init as RequestInit).method === "POST",
    );
    expect(createCall).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url, init]) => `${url}`.endsWith("/v1/threads/case-new/mcp/plugin-1") && (init as RequestInit)?.method === "PUT")).toBe(true);
    expect(JSON.parse((createCall![1] as RequestInit).body as string)).toMatchObject({
      scene: "case-retrieval",
      legal_channel: "plugin",
      legal_skills: [],
    });
  });

  // The embedded workspace must adopt the created thread: it requests the
  // thread's history. Before the fix, activeThread never resolved and these
  // requests never fired.
  await waitFor(() => {
    const historyCall = fetchMock.mock.calls.find(
      ([url]) => `${url}`.includes("/v1/threads/case-new/messages"),
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

  fireEvent.click(screen.getByRole("button", { name: "案例检索" }));

  const chip = await screen.findByRole("button", { name: /示例合同案件/ });
  fireEvent.click(chip);

  await waitFor(() => {
    const historyCall = fetchMock.mock.calls.find(
      ([url]) => `${url}`.includes("/v1/threads/case-1/messages"),
    );
    expect(historyCall).toBeTruthy();
  });
});

test("未选择具体渠道时保留输入且不能发送", async () => {
  render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()} />);
  fireEvent.click(screen.getByRole("button", {name:"案例检索"}));
  const input = await screen.findByRole("textbox", {name:"向智能体提问"});
  fireEvent.change(input, {target:{value:"保留案件问题"}});
  await waitFor(() => expect(screen.getByRole("button", {name:"发送"}).hasAttribute("disabled")).toBe(true));
  expect((input as HTMLTextAreaElement).value).toBe("保留案件问题");
  expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/v1/threads") && init?.method === "POST")).toBe(false);
});


test("输入框始终显示，离开案例页释放检索会话", async () => {
  render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()} />);
  fireEvent.click(screen.getByRole("button", { name: "案例检索" }));
  fireEvent.click(await screen.findByRole("button", { name: /示例合同案件/ }));
  expect(screen.getByRole("textbox", { name: "向智能体提问" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "后退" }));
  await waitFor(() => expect(screen.getByRole("button", {name: /示例合同案件/}).getAttribute("aria-current")).toBeNull());
  expect(screen.queryByRole("button", { name: "继续追问" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "案例检索" }));
  expect(await screen.findByRole("button", { name: /示例合同案件/ })).toBeTruthy();
});

test("知识库导航在未配置模型时也能进入资料管理", async () => {
  fetchMock.mockImplementation(() => Promise.resolve(new Response('[]', {status: 200})));
  render(<App initialHealth={{...oneModelHealth, configured:{models:0,mcp_servers:0,skills:0,folders:0}}} updaterClient={upToDateUpdater()} />);
  fireEvent.click(screen.getByRole('button', {name:'知识库',exact:true}));
  fireEvent.click(await screen.findByRole('button', {name:'建立知识库'}));
  expect(await screen.findByRole('button', {name:'关联文件夹'})).toBeTruthy();
  await screen.findByText(/关联本机文件夹或接入外部知识库/);
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
  fireEvent.click(screen.getByRole("button", {name: "案例检索"}));
  const input = await screen.findByRole("textbox", {name:"向智能体提问"});
  fireEvent.click(within(screen.getByRole("group", {name:"检索渠道"})).getByRole("button", {name:"知识库"}));
  fireEvent.click(await screen.findByRole("checkbox", {name:"自有资料"}));
  fireEvent.change(input, {target:{value:"检索自有资料"}});
  await waitFor(() => expect(screen.getByRole("button", {name:"发送"}).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", {name:"发送"}));
  await waitFor(() => {
    const start = Socket.sent.map(value => JSON.parse(value)).find(value => value.type === "turn.start");
    expect(start).toMatchObject({content:"检索自有资料", knowledgeLibraryIds:["library-1"]});
  });
  const create = fetchMock.mock.calls.find(([url,init]) => url.endsWith('/v1/threads') && init?.method === 'POST');
  expect(JSON.parse(create![1].body)).not.toHaveProperty('legal_channel');
});


test("choosing Yuan Dian enables its saved services for a case search", async () => {
  const originalFetch = fetchMock.getMockImplementation()!;
  const services = ["law", "case", "company", "securities"].map(name => ({
    id: `yuan-${name}`, displayName: `元典-${name}`, transport: "http",
    url: `https://open.chineselaw.com/mcp/${name}/stream`, args: [], env: {}, headers: {}, secretHeaderKeys: [], secretEnvKeys: [],
  }));
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/v1/mcp/servers")) return Promise.resolve(new Response(JSON.stringify(services), { status: 200 }));
    return originalFetch(url, init);
  });
  render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()} />);
  fireEvent.click(screen.getByRole("button", { name: "案例检索" }));
  const input = await screen.findByRole("textbox", {name:"向智能体提问"});
  fireEvent.click(screen.getByRole("button", { name: "插件" }));
  fireEvent.click(await screen.findByRole("menuitemradio", { name: "元典智库" }));
  fireEvent.change(input, { target: { value: "示例案件" } });
  await waitFor(() => expect(screen.getByRole("button", {name:"发送"}).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", {name:"发送"}));
  await waitFor(() => {
    for (const item of services) expect(fetchMock.mock.calls.some(([url, init]) => `${url}`.endsWith(`/v1/threads/case-new/mcp/${item.id}`) && (init as RequestInit)?.method === "PUT")).toBe(true);
  });
});

test("已有检索会话更换插件和技能后，下一次提问发送新的选择", async () => {
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
  const original = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/v1/mcp/servers")) return Promise.resolve(new Response(JSON.stringify([{id:"server-new",displayName:"新插件",transport:"stdio"}]),{status:200}));
    if (url.endsWith("/v1/skills")) return Promise.resolve(new Response(JSON.stringify([{name:"新技能",category:"case-retrieval",description:"检索方法",valid:true,enabled:true,path:"/skill"}]),{status:200}));
    return original(url, init);
  });
  render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()} />);
  fireEvent.click(screen.getByRole("button", {name:"案例检索"}));
  fireEvent.click(await screen.findByRole("button", {name:/示例合同案件/}));
  fireEvent.click(screen.getByRole("button", {name:"插件"}));
  fireEvent.click(await screen.findByRole("menuitemradio", {name:"新插件"}));
  fireEvent.click(screen.getByRole("combobox", {name:"检索技能"}));
  fireEvent.click(await screen.findByRole("option", {name:/新技能/}));
  fireEvent.keyDown(screen.getByRole("combobox", {name:"检索技能"}), {key:"Escape"});
  fireEvent.change(screen.getByRole("textbox", {name:"向智能体提问"}), {target:{value:"继续检索"}});
  await waitFor(() => expect(screen.getByRole("button", {name:"发送"}).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", {name:"发送"}));
  await waitFor(() => expect(Socket.sent.map(value => JSON.parse(value)).find(value => value.type === "turn.start")).toMatchObject({
    threadId:"case-1", content:"继续检索", searchSelection:{channel:"plugin",serverIds:["server-new"],skills:["新技能"]},
  }));
});
