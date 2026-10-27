/**
 * App-level wiring for the legal search page: starting a search must route
 * the created scene thread into the embedded workspace view (the regression
 * this locks: activeThread must resolve from the legal thread list).
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  id: "litigation-1",
  title: "示例合同案件",
  provider_id: "provider-1",
  model: "test-model",
  workspace_id: "workspace-1",
  status: "idle",
  created_at: "2026-09-05T00:00:00Z",
  updated_at: "2026-09-05T00:00:00Z",
  archived_at: null,
  deleted_at: null,
  is_draft: false,
  reasoning_effort: "auto",
  scene: "litigation-work",
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
        new Response(JSON.stringify(legalThread), { status: 201 }),
      );
    }
    if (url.includes("/v1/threads?scene=case-retrieval")) {
      return Promise.resolve(
        new Response("[]", { status: 200 }),
      );
    }
    if (url.includes("/v1/threads?")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    if (url.includes("/v1/threads/case-1/")) {
      return Promise.resolve(new Response("[]", { status: 200 }));
    }
    return Promise.resolve(new Response("[]", { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
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


const workspace={id:"workspace-1",display_name:"合成测试案件",root_path:"/tmp/synthetic-case",allow_read:true,allow_write:true};
const empty={revision:0,data:null};
const snapshot={case:empty,facts:empty,evidence:empty,issues:empty,strategy:empty};
function installLitigationResponses(){
  const original=fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
    if(url.endsWith('/diagrams'))return Promise.resolve(new Response(JSON.stringify({diagrams:[],errors:[],truncated:false})));
    if(url.includes('/work-products/'))return Promise.resolve(new Response(JSON.stringify({revision:0,data:null})));
    if(url.includes('/editor-products?'))return Promise.resolve(new Response(JSON.stringify({products:[],errors:[]})));
    if(url.endsWith('/work-discussions'))return Promise.resolve(new Response(JSON.stringify({links:{}})));
    if(url.includes('/work-discussions/'))return Promise.resolve(new Response(init?.body as string));
    if(url.endsWith('/v1/workspaces'))return Promise.resolve(new Response(JSON.stringify([workspace])));
    if(url.includes('/v1/litigation/workspaces/workspace-1/files?'))return Promise.resolve(new Response(JSON.stringify({entries:[{path:'事实.txt',name:'事实.txt',directory:false}],truncated:false})));
    if(url.endsWith('/v1/litigation/workspaces/workspace-1/materials'))return Promise.resolve(new Response(JSON.stringify({materials:[{path:"事实.txt",name:"事实.txt",supported:true,reason:""}],truncated:false})));
    if(url.endsWith('/v1/litigation/workspaces/workspace-1'))return Promise.resolve(new Response(JSON.stringify(snapshot)));
    return original(url,init);
  });
}
test("诉讼工作绑定所选工作区，创建独立会话并接入实际消息视图",async()=>{
  installLitigationResponses();
  render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
  fireEvent.click(screen.getByRole('button',{name:'诉讼工作'}));
  expect(screen.getByRole('region',{name:'诉讼工作台'})).toBeTruthy();
  expect(screen.queryByRole('button',{name:'新建会话'})).toBeNull();
  const picker=await screen.findByRole('combobox',{name:'案件工作区'});
  await screen.findByRole('option',{name:'合成测试案件'});
  fireEvent.change(picker,{target:{value:'workspace-1'}});
  fireEvent.click(await screen.findByRole('button',{name:'事实梳理',exact:true}));
  const action=await screen.findByRole('button',{name:'生成时间轴'});
  await waitFor(()=>expect(action.hasAttribute('disabled')).toBe(false));
  fireEvent.click(action);
  await waitFor(()=>{
    const call=fetchMock.mock.calls.find(([url,init])=>String(url).endsWith('/v1/threads') && init?.method==='POST');
    expect(call).toBeTruthy();
    expect(JSON.parse(call![1].body)).toMatchObject({scene:'litigation-work',workspace_id:'workspace-1'});
    expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/v1/litigation/workspaces/workspace-1/work-discussions/litigation-1'))).toBe(true);
    expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/v1/threads/litigation-1/messages'))).toBe(true);
  });
  expect(screen.queryByRole('button',{name:'新建会话'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'返回主页面'}));
  fireEvent.click(screen.getByRole('button',{name:'新建会话'}));
  await waitFor(()=>expect(screen.queryByRole('region',{name:'诉讼工作'})).toBeNull());
  fireEvent.click(screen.getByRole('button',{name:'诉讼工作'}));
  expect(screen.queryByText('正在恢复会话…')).toBeNull();
});

test('同案可创建两次讨论并回到指定记录，新讨论不复制插件选择',async()=>{
  installLitigationResponses();
  const previous=fetchMock.getMockImplementation()!;
  const records: typeof legalThread[]=[];
  fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
    if(url.endsWith('/v1/threads') && init?.method==='POST') {
      const input=JSON.parse(String(init.body));
      const row={...legalThread,id:`discussion-${records.length+1}`,title:input.title};
      records.push(row);
      return Promise.resolve(new Response(JSON.stringify(row),{status:201}));
    }
    if(url.includes('/v1/threads?scene=litigation-work'))return Promise.resolve(new Response(JSON.stringify(records)));
    return previous(url,init);
  });
  render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
  fireEvent.click(screen.getByRole('button',{name:'诉讼工作'}));
  expect(screen.getByRole('region',{name:'诉讼工作台'})).toBeTruthy();
  expect(screen.queryByRole('button',{name:'新建会话'})).toBeNull();
  const picker=await screen.findByRole('combobox',{name:'案件工作区'});
  await screen.findByRole('option',{name:'合成测试案件'});
  fireEvent.change(picker,{target:{value:'workspace-1'}});
  fireEvent.click(await screen.findByRole('button',{name:'案件讨论',exact:true}));
  fireEvent.change(await screen.findByRole('textbox',{name:'案件讨论内容'}),{target:{value:'梳理付款事实'}});
  fireEvent.click(screen.getByRole('button',{name:'开始讨论'}));
  await waitFor(()=>expect(records).toHaveLength(1));
  await screen.findByRole('option',{name:'梳理付款事实'});
  await waitFor(()=>expect(screen.getByRole('button',{name:'新讨论'})).toHaveProperty('disabled',false));
  fireEvent.click(screen.getByRole('button',{name:'新讨论'}));
  fireEvent.change(await screen.findByRole('textbox',{name:'案件讨论内容'}),{target:{value:'讨论诉讼请求'}});
  fireEvent.click(screen.getByRole('button',{name:'开始讨论'}));
  await waitFor(()=>expect(records).toHaveLength(2));
  await screen.findByRole('option',{name:'讨论诉讼请求'});
  await waitFor(()=>expect(screen.getByRole('combobox',{name:'本案讨论记录'})).toHaveProperty('disabled',false));
  fireEvent.change(screen.getByRole('combobox',{name:'本案讨论记录'}),{target:{value:'discussion-1'}});
  expect(screen.getByRole('combobox',{name:'本案讨论记录'})).toHaveProperty('value','discussion-1');
  expect(localStorage.getItem('verilex:litigation-discussion:v1:workspace-1')).toBe('discussion-1');
  const creations=fetchMock.mock.calls.filter(([url,init])=>String(url).endsWith('/v1/threads') && init?.method==='POST');
  for(const [,init] of creations){
    const input=JSON.parse(init.body);
    expect(input).toMatchObject({scene:'litigation-work',workspace_id:'workspace-1'});
    expect(input.mcp_servers).toBeUndefined();
  }
});

test('没有讨论的案件也能在重新启动时恢复工作台',async()=>{
 installLitigationResponses();
 localStorage.setItem('verilex:litigation-last-workspace:v1','workspace-1');
 render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
 expect(await screen.findByRole('region',{name:'诉讼工作台'})).toBeTruthy();
 expect(screen.queryByRole('button',{name:'新建会话'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'返回主页面'}));
 expect(localStorage.getItem('verilex:litigation-last-workspace:v1')).toBeNull();
});

test('未配置模型时保留案件页面并引导设置，不误报本地后台离线',async()=>{
  installLitigationResponses();
  const original=fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url:string,init?:RequestInit)=>url.endsWith('/v1/providers') ? Promise.resolve(new Response('[]')) : original(url,init));
  render(<App initialHealth={{...oneModelHealth,configured:{...oneModelHealth.configured,models:0}}} updaterClient={upToDateUpdater()}/>);
  fireEvent.click(screen.getByRole('button',{name:'诉讼工作'}));
  await screen.findByRole('option',{name:'合成测试案件'});
  fireEvent.change(screen.getByRole('combobox',{name:'案件工作区'}),{target:{value:'workspace-1'}});
  fireEvent.click(await screen.findByRole('button',{name:'事实梳理',exact:true}));
  const action=await screen.findByRole('button',{name:'生成时间轴'});
  await waitFor(()=>expect(action.hasAttribute('disabled')).toBe(false));
  fireEvent.click(action);
  expect(await screen.findByRole('button',{name:'打开模型设置'})).toBeTruthy();
  expect(screen.queryByText('本机后台暂时无法连接')).toBeNull();
  expect(screen.getByRole('region',{name:'诉讼工作台'})).toBeTruthy();
  expect(fetchMock.mock.calls.some(([url,init])=>String(url).endsWith('/v1/threads') && init?.method==='POST')).toBe(false);
});

test('真实消息确认后锁定工作台，结束后解除，不能重复启动或切换案件讨论', async () => {
  globalThis.__VERILEX_HTTP__ = 'http://127.0.0.1:9999';
  globalThis.__VERILEX_API_TOKEN__ = 'test-token';
  class ChatSocket {
    static OPEN = 1;
    static instances: ChatSocket[] = [];
    readyState = 1;
    onmessage: ((event: {data: string}) => void) | null = null;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    sent: string[] = [];
    constructor() { ChatSocket.instances.push(this); }
    send(data: string) { this.sent.push(data); }
    close() { this.readyState = 3; this.onclose?.(); }
    receive(data: unknown) { this.onmessage?.({data: JSON.stringify(data)}); }
  }
  vi.stubGlobal('WebSocket', ChatSocket);
  installLitigationResponses();
  const previous = fetchMock.getMockImplementation()!;
  const records = [legalThread];
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith('/v1/threads') && init?.method === 'POST') {
      const created = {...legalThread, id: 'litigation-2'};
      records.push(created);
      return Promise.resolve(new Response(JSON.stringify(created), {status: 201}));
    }
    if (url.includes('/v1/threads?scene=litigation-work'))
      return Promise.resolve(new Response(JSON.stringify(records)));
    return previous(url, init);
  });
  try {
    render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
    fireEvent.click(screen.getByRole('button', {name:'诉讼工作'}));
    await screen.findByRole('option', {name:'合成测试案件'});
    fireEvent.change(screen.getByRole('combobox', {name:'案件工作区'}), {target:{value:'workspace-1'}});
    fireEvent.click(await screen.findByRole('button', {name:'事实梳理', exact:true}));
    const start = await screen.findByRole('button', {name:'生成时间轴'});
    await waitFor(() => expect(start).toHaveProperty('disabled', false));
    fireEvent.click(start);
    const socket = await waitFor(() => {
      const found = ChatSocket.instances.find(s => s.sent.some(m => m.includes('turn.start')));
      expect(found).toBeTruthy(); return found!;
    });
    act(() => socket.receive({type:'ack', turnId:'turn-qa', status:'queued'}));
    await waitFor(() => expect(start).toHaveProperty('disabled', true));
    expect(screen.getByRole('combobox', {name:'案件工作区'})).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', {name:'新讨论'})).toHaveProperty('disabled', true);
    // The running state is represented by the disabled actions, without a
    // separate status strip in the central document area.
    expect(screen.queryByText('案件讨论正在处理。已保存的成果会自动更新到这里。')).toBeNull();
    act(() => {
      socket.receive({type:'event', event:{type:'turn_started', turnId:'turn-qa', sequence:1, payload:{}}});
      socket.receive({type:'event', event:{type:'turn_failed', turnId:'turn-qa', sequence:2, payload:{code:'provider_protocol_error'}}});
    });
    await waitFor(() => expect(start).toHaveProperty('disabled', false));
    expect(screen.getByRole('button', {name:'新讨论'})).toHaveProperty('disabled', false);
  } finally {
    vi.unstubAllGlobals();
    delete globalThis.__VERILEX_HTTP__;
    delete globalThis.__VERILEX_API_TOKEN__;
  }
});
