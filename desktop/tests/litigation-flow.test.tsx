/**
 * App-level wiring for the legal search page: starting a search must route
 * the created scene thread into the embedded workspace view (the regression
 * this locks: activeThread must resolve from the legal thread list).
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    if(url.endsWith('/editor-templates'))return Promise.resolve(new Response(JSON.stringify({templates:[]})));
    if(url.endsWith('/work-discussions'))return Promise.resolve(new Response(JSON.stringify({links:{}})));
    if(url.includes('/work-discussions/'))return Promise.resolve(new Response(init?.body as string));
    if(url.endsWith('/v1/workspaces'))return Promise.resolve(new Response(JSON.stringify([workspace])));
    if(url.includes('/v1/litigation/workspaces/workspace-1/files?'))return Promise.resolve(new Response(JSON.stringify({entries:[{path:'事实.txt',name:'事实.txt',directory:false}],truncated:false})));
    if(url.endsWith('/v1/litigation/workspaces/workspace-1/materials'))return Promise.resolve(new Response(JSON.stringify({materials:[{path:"事实.txt",name:"事实.txt",supported:true,reason:""}],truncated:false})));
    if(url.endsWith('/v1/litigation/workspaces/workspace-1'))return Promise.resolve(new Response(JSON.stringify(snapshot)));
    return original(url,init);
  });
}

test('项目切换后显示文件树、普通对话和事实梳理入口',async()=>{
 installLitigationResponses();
 const original=fetchMock.getMockImplementation()!;
 fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
  if(url.endsWith('/project-facts'))return Promise.resolve(new Response(JSON.stringify({results:[],errors:[]})));
  return original(url,init);
 });
 render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
 fireEvent.click(screen.getByRole('tab',{name:'项目'}));
 fireEvent.click(await screen.findByRole('button',{name:'合成测试案件'}));
 fireEvent.click(screen.getByRole('button',{name:'项目菜单：合成测试案件'}));
 fireEvent.click(screen.getByRole('menuitem',{name:'打开项目文件夹'}));
 expect(await screen.findByRole('button',{name:'事实.txt'})).toBeTruthy();
 expect(await screen.findByRole('textbox',{name:'向智能体提问'})).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'事实梳理'}));
 expect(await screen.findByRole('tab',{name:'时间轴'})).toBeTruthy();
 expect(await screen.findByRole('button',{name:'生成时间轴'})).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'生成时间轴'}));
 await waitFor(()=>{
  const binding=fetchMock.mock.calls.find(([url,init])=>String(url).includes('/work-discussions/') && init?.method==='PUT');
  expect(binding).toBeTruthy();
  expect(JSON.parse(binding![1]!.body as string)).toMatchObject({project_chart_kind:'timeline',materials:['事实.txt']});
 });
 fireEvent.click(screen.getByRole('button',{name:'首页'}));
 expect(screen.getByRole('textbox',{name:'向智能体提问'})).toBeTruthy();
});

test('展开项目中的文件夹并选择文件后，工作区显示该文件内容',async()=>{
 installLitigationResponses();
 const original=fetchMock.getMockImplementation()!;
 fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
  if(url.endsWith('/project-facts'))return Promise.resolve(new Response(JSON.stringify({results:[],errors:[]})));
  if(url.includes('/material-inspection?'))return Promise.resolve(new Response(JSON.stringify({status:'text_available',gaps:[],notice:''})));
  if(url.includes('/v1/litigation/workspaces/workspace-1/files?')) {
   const path=new URL(url).searchParams.get('path');
   return Promise.resolve(new Response(JSON.stringify({entries:path==='材料'?[{path:'材料/证据.txt',name:'证据.txt',directory:false}]:[{path:'材料',name:'材料',directory:true}],truncated:false})));
  }
  if(url.includes('/v1/workspaces/workspace-1/file-preview?'))return Promise.resolve(new Response(JSON.stringify({kind:'text',name:'证据.txt',path:'材料/证据.txt',size_bytes:12,modified_at:'',truncated:false,content:'虚构的证据内容'})));
  return original(url,init);
 });
 render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
 fireEvent.click(screen.getByRole('tab',{name:'项目'}));
 fireEvent.click(await screen.findByRole('button',{name:'合成测试案件'}));
 fireEvent.click(screen.getByRole('button',{name:'项目菜单：合成测试案件'}));
 fireEvent.click(screen.getByRole('menuitem',{name:'打开项目文件夹'}));
 fireEvent.click(await screen.findByRole('button',{name:'材料'}));
 fireEvent.click(await screen.findByRole('button',{name:'证据.txt'}));
 expect(await screen.findByText('虚构的证据内容')).toBeTruthy();
 expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/v1/workspaces/workspace-1/file-preview?path=%E6%9D%90%E6%96%99%2F%E8%AF%81%E6%8D%AE.txt'))).toBe(true);
});

test('勾选项目文件会传入新项目对话',async()=>{
 class Socket {
  static OPEN=1; static sockets:Socket[]=[];
  readyState=1; sent:string[]=[]; onopen:(()=>void)|null=null; onclose:(()=>void)|null=null; onmessage:((event:{data:string})=>void)|null=null; onerror:(()=>void)|null=null;
  constructor(){Socket.sockets.push(this);}
  send(value:string){this.sent.push(value);}
  close(){this.readyState=3;}
 }
 vi.stubGlobal('WebSocket',Socket);
 globalThis.__VERILEX_API_TOKEN__='test-token';
 installLitigationResponses();
 const original=fetchMock.getMockImplementation()!;
 fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
  if(url.endsWith('/project-facts'))return Promise.resolve(new Response(JSON.stringify({results:[],errors:[]})));
  if(url.endsWith('/v1/threads') && init?.method==='POST')return Promise.resolve(new Response(JSON.stringify({...legalThread,id:'project-chat',scene:'general'}),{status:201}));
  return original(url,init);
 });
 try {
  render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  fireEvent.click(await screen.findByRole('button',{name:'合成测试案件'}));
 fireEvent.click(screen.getByRole('button',{name:'项目菜单：合成测试案件'}));
 fireEvent.click(screen.getByRole('menuitem',{name:'打开项目文件夹'}));
  fireEvent.click(await screen.findByRole('checkbox',{name:'选取材料 事实.txt'}));
  expect(screen.getByText('已选 1 个文件')).toBeTruthy();
  fireEvent.change(await screen.findByRole('textbox',{name:'向智能体提问'}),{target:{value:'请阅读选中的文件'}});
  fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(Socket.sockets.length).toBeGreaterThan(0));
  await act(async()=>{Socket.sockets.forEach(socket=>socket.onopen?.());});
  await waitFor(()=>expect(Socket.sockets.some(socket=>socket.sent.some(value=>JSON.parse(value).type==='turn.start'))).toBe(true));
  const sent=Socket.sockets.flatMap(socket=>socket.sent.map(value=>JSON.parse(value))).find(value=>value.type==='turn.start');
  expect(sent.litigationMaterials).toEqual({workspaceId:'workspace-1',paths:['事实.txt']});
 } finally {delete globalThis.__VERILEX_API_TOKEN__;vi.unstubAllGlobals();}
});

test('勾选的项目文件进入事实梳理生成范围',async()=>{
 installLitigationResponses();
 const original=fetchMock.getMockImplementation()!;
 fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
  if(url.endsWith('/project-facts'))return Promise.resolve(new Response(JSON.stringify({results:[],errors:[]})));
  if(url.includes('/v1/litigation/workspaces/workspace-1/files?'))return Promise.resolve(new Response(JSON.stringify({entries:[{path:'甲.txt',name:'甲.txt',directory:false},{path:'乙.txt',name:'乙.txt',directory:false}],truncated:false})));
  if(url.endsWith('/v1/litigation/workspaces/workspace-1/materials'))return Promise.resolve(new Response(JSON.stringify({materials:[{path:'甲.txt',name:'甲.txt',supported:true,reason:''},{path:'乙.txt',name:'乙.txt',supported:true,reason:''}],truncated:false})));
  return original(url,init);
 });
 render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
 fireEvent.click(screen.getByRole('tab',{name:'项目'}));
 fireEvent.click(await screen.findByRole('button',{name:'合成测试案件'}));
 fireEvent.click(screen.getByRole('button',{name:'项目菜单：合成测试案件'}));
 fireEvent.click(screen.getByRole('menuitem',{name:'打开项目文件夹'}));
 fireEvent.click(await screen.findByRole('checkbox',{name:'选取材料 乙.txt'}));
 fireEvent.click(screen.getByRole('button',{name:'事实梳理'}));
 expect(screen.getByText('已勾选 1 个文件，开始生成时使用所选文件')).toBeTruthy();
 fireEvent.click(await screen.findByRole('button',{name:'生成时间轴'}));
 await waitFor(()=>{
  const binding=fetchMock.mock.calls.find(([url,init])=>String(url).includes('/work-discussions/') && init?.method==='PUT');
  expect(binding).toBeTruthy();
  expect(JSON.parse(binding![1]!.body as string).materials).toEqual(['乙.txt']);
 });
});

test('切换项目不会把上个项目的文件勾选带过去',async()=>{
 installLitigationResponses();
 const original=fetchMock.getMockImplementation()!;
 fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
  if(url.endsWith('/v1/workspaces'))return Promise.resolve(new Response(JSON.stringify([workspace,{...workspace,id:'workspace-2',display_name:'另一项目',root_path:'/tmp/another'}])));
  if(url.endsWith('/project-facts'))return Promise.resolve(new Response(JSON.stringify({results:[],errors:[]})));
  if(url.includes('/v1/litigation/workspaces/workspace-2/files?'))return Promise.resolve(new Response(JSON.stringify({entries:[{path:'事实.txt',name:'事实.txt',directory:false}],truncated:false})));
  return original(url,init);
 });
 render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
 fireEvent.click(screen.getByRole('tab',{name:'项目'}));
 fireEvent.click(await screen.findByRole('button',{name:'合成测试案件'}));
 fireEvent.click(screen.getByRole('button',{name:'项目菜单：合成测试案件'}));
 fireEvent.click(screen.getByRole('menuitem',{name:'打开项目文件夹'}));
 fireEvent.click(await screen.findByRole('checkbox',{name:'选取材料 事实.txt'}));
 expect(screen.getByText('已选 1 个文件')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'切换项目 展开'}));
 fireEvent.click(screen.getByRole('button',{name:'另一项目'}));
 expect((await screen.findByRole('checkbox',{name:'选取材料 事实.txt'}) as HTMLInputElement).checked).toBe(false);
});

test('准备项目生成任务时停止，不会继续发送请求',async()=>{
 installLitigationResponses();
 const original=fetchMock.getMockImplementation()!;
 let release!:(response:Response)=>void;
 fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
  if(url.endsWith('/project-facts'))return Promise.resolve(new Response(JSON.stringify({results:[],errors:[]})));
  if(url.includes('/work-discussions/') && init?.method==='PUT')return new Promise(resolve=>{release=resolve;});
  return original(url,init);
 });
 render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
 fireEvent.click(screen.getByRole('tab',{name:'项目'}));
 fireEvent.click(await screen.findByRole('button',{name:'合成测试案件'}));
 fireEvent.click(screen.getByRole('button',{name:'事实梳理'}));
 fireEvent.click(await screen.findByRole('button',{name:'生成时间轴'}));
 await waitFor(()=>expect(release).toBeTypeOf('function'));
 fireEvent.click(screen.getByRole('button',{name:'停止生成'}));
 release(new Response('{}'));
 expect(await screen.findByRole('button',{name:'生成时间轴'})).toBeTruthy();
 expect(screen.queryByText('任务已结束，但没有保存新的成果。请检查材料和模型连接后重试。')).toBeNull();
});

test('没有讨论的案件也能在重新启动时恢复文书起草',async()=>{
 installLitigationResponses();
 localStorage.setItem('verilex:litigation-last-workspace:v1','workspace-1');
 render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
 expect(await screen.findByRole('region',{name:'文书起草'})).toBeTruthy();
 expect(await screen.findByRole('region',{name:'文书起草工作区'})).toBeTruthy();
 expect(screen.getByRole('button',{name:'新建会话'})).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'后退'}));
 await waitFor(()=>expect(localStorage.getItem('verilex:litigation-last-workspace:v1')).toBeNull());
});

test('文书起草入口可以选择已有案件并直接编辑',async()=>{
 installLitigationResponses();
 render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
 fireEvent.click(screen.getByRole('button',{name:'文书起草'}));
 await screen.findByRole('option',{name:'合成测试案件'});
 fireEvent.change(screen.getByRole('combobox',{name:'案件工作区'}),{target:{value:'workspace-1'}});
 expect(await screen.findByRole('region',{name:'文书起草工作区'})).toBeTruthy();
 expect(screen.getByRole('button',{name:'直接手动撰写'})).toBeTruthy();
 expect(screen.queryByRole('button',{name:'事实梳理'})).toBeNull();
 expect(screen.queryByRole('button',{name:'证据整理'})).toBeNull();
});

test('文书起草显示所选工作区的项目会话并在项目中新建会话',async()=>{
 installLitigationResponses();
 const original=fetchMock.getMockImplementation()!;
 const projectThread={...legalThread,id:'project-history',title:'本工作区历史',scene:'general'};
 const otherThread={...projectThread,id:'other-project',title:'其他工作区历史',workspace_id:'workspace-2'};
 fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
  if(url.includes('/v1/threads?scene=general'))return Promise.resolve(new Response(JSON.stringify([projectThread,otherThread])));
  if(url.endsWith('/v1/threads') && init?.method==='POST')return Promise.resolve(new Response(JSON.stringify({...projectThread,id:'project-new',title:'新建项目会话'}),{status:201}));
  return original(url,init);
 });
 render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
 fireEvent.click(screen.getByRole('button',{name:'文书起草'}));
 expect(screen.queryByRole('combobox',{name:'历史会话'})).toBeNull();
 await screen.findByRole('option',{name:'合成测试案件'});
 fireEvent.change(screen.getByRole('combobox',{name:'案件工作区'}),{target:{value:'workspace-1'}});
 expect(await screen.findByRole('option',{name:'本工作区历史'})).toBeTruthy();
 expect(screen.queryByRole('option',{name:'其他工作区历史'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'新建会话'}));
 await waitFor(()=>expect(screen.getByRole('combobox',{name:'历史会话'})).toHaveProperty('value',''));
 const input=screen.getByRole('textbox',{name:'向智能体提问'});
 fireEvent.change(input,{target:{value:'测试项目会话'}});
 fireEvent.click(screen.getByRole('button',{name:'发送'}));
 await waitFor(()=>{
  const call=fetchMock.mock.calls.find(([url,init])=>String(url).endsWith('/v1/threads') && init?.method==='POST');
  expect(call).toBeTruthy();
  const body=JSON.parse(call![1]!.body as string);
  expect(body.scene).toBe('general');
  expect(body.workspace_id).toBe('workspace-1');
 });
 expect(await screen.findByRole('option',{name:'新建项目会话'})).toBeTruthy();
});

test('未选工作区时上传附件创建无工作区会话并隐藏会话入口',async()=>{
 installLitigationResponses();
 const original=fetchMock.getMockImplementation()!;
 fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
  if(url.endsWith('/v1/threads') && init?.method==='POST')return Promise.resolve(new Response(JSON.stringify({...legalThread,id:'file-only',title:'新会话',scene:'general',workspace_id:null}),{status:201}));
  if(url.includes('/threads/file-only/attachment-uploads?'))return Promise.resolve(new Response(JSON.stringify({id:'upload-1'}),{status:201}));
  return original(url,init);
 });
 render(<App initialHealth={oneModelHealth} updaterClient={upToDateUpdater()}/>);
 fireEvent.click(screen.getByRole('button',{name:'文书起草'}));
 const input=await screen.findByLabelText('选择附件文件');
 fireEvent.change(input,{target:{files:[new File(['示例内容'],'虚构材料.txt',{type:'text/plain'})]}});
 await waitFor(()=>{
  const call=fetchMock.mock.calls.find(([url,init])=>String(url).endsWith('/v1/threads') && init?.method==='POST');
  expect(call).toBeTruthy();
  const body=JSON.parse(call![1]!.body as string);
  expect(body.scene).toBe('general');expect(body.workspace_id).toBeNull();
 });
 await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/threads/file-only/attachment-uploads?'))).toBe(true));
 expect(screen.queryByRole('combobox',{name:'历史会话'})).toBeNull();
 expect(screen.queryByRole('button',{name:'新建会话'})).toBeNull();
});
