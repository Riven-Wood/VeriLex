import {act, cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, expect, test, vi} from "vitest";
import * as React from "react";
import {App} from "../src/app/App";

vi.mock("@tauri-apps/api/core", () => ({invoke: vi.fn(), isTauri: () => false}));
vi.mock("@tauri-apps/api/webview", () => ({getCurrentWebview: () => ({onDragDropEvent: vi.fn().mockResolvedValue(() => {})})}));
vi.mock("../src/components/ContractAnnotationPreview", () => ({ContractAnnotationPreview: ({onQuote}: {onQuote: (text: string) => void}) => <button onClick={() => onQuote("违约金每日千分之三")}>选择条款文字</button>}));

const documentFixture={id:'wd',filename:'样本合同.docx',content:'ZWRpdGVk',revision:1,updated_at:'t'};
const editorControl=vi.hoisted(()=>({flush:vi.fn()}));
vi.mock("../src/components/wordEditor/WordDocumentEditor", () => ({WordDocumentEditor: React.forwardRef(({document,onReady}:any,ref:any)=>{ React.useEffect(()=>onReady?.(true),[]); React.useImperativeHandle(ref,()=>({flush:async()=>{await editorControl.flush();return {document,bytes:new TextEncoder().encode('edited')};}})); return <div>完整 Word 文档</div>; })}));

class Socket {
  static OPEN = 1;
  static last: Socket | null = null;
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((event: {data: string}) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  constructor() {Socket.last = this;}
  send(value: string) {this.sent.push(value);}
  close() {this.readyState = 3;}
}
const provider = {id: "p", name: "Test", kind: "openai_compatible", base_url: "https://models.test/v1", default_model: "m", auth_mode: "none", has_secret: false, enabled: true};
const thread = {id: "ct", title: "样本合同", provider_id: "p", model: "m", workspace_id: null, status: "idle", created_at: "t", updated_at: "t", archived_at: null, deleted_at: null, is_draft: false, reasoning_effort: "auto", scene: "contract-review"};
const review = {id: "cr", thread_id: "ct", filename: "样本合同.docx", skill: "通用合同审查", status: "completed", party: "neutral", strictness: "normal", signature: "V", retrieval_enabled: false, opinions: [], opinion_count: 0, created_at: "t", updated_at: "t"};
let exists = false;
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  exists = false; Socket.last = null; localStorage.clear();
  editorControl.flush.mockReset().mockResolvedValue(undefined);
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    let data: unknown = [];
    if (url.includes('/v1/contract-reviews/documents?') || url.endsWith('/word-document')) data=documentFixture;
    else if (url.endsWith("/v1/providers")) data = [provider];
    else if (url.endsWith("/models")) data = {models: ["m"], selected_model: "m"};
    else if (url.includes("/v1/contract-reviews?") && init?.method === "POST") {exists = true; data = {review_id: "cr", thread_id: "ct", upload_id: "upload", filename: "样本合同.docx"};}
    else if (url.endsWith("/v1/contract-reviews")) data = exists ? [review] : [];
    else if (url.endsWith("/v1/contract-reviews/cr")) data = review;
    else if (url.endsWith("/v1/threads/ct")) data = thread;
    else if (url.includes("/v1/threads?scene=contract-review")) data = exists ? [thread] : [];
    return new Response(JSON.stringify(data), {status: 200});
  });
  vi.stubGlobal("fetch", fetchMock); vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("__VERILEX_API_TOKEN__", "synthetic-test-token");
});

test.each(['sidebar-settings','new-thread','back','forward','command-settings'])("合同保存失败时 %s 保留文档与选中线程，重试成功后才离开",async target=>{
  exists=true;openApp();
  fireEvent.click(await screen.findByText('样本合同.docx'));
  await screen.findByText('完整 Word 文档');
  await waitFor(()=>expect(localStorage.getItem('verilex:last-thread:v1')).toBe('ct'));
  if(target==='sidebar-settings'||target==='new-thread')fireEvent.click(screen.getByRole('button',{name:'展开侧栏'}));
  if(target==='forward'){
    fireEvent.click(screen.getByRole('button',{name:'展开侧栏'}));fireEvent.click(screen.getByRole('button',{name:'设置',exact:true}));
    await waitFor(()=>expect(document.querySelector('.app-frame')?.getAttribute('data-view')).toBe('settings'));
    fireEvent.click(screen.getByRole('button',{name:'后退'}));await screen.findByText('完整 Word 文档');
  }
  const navigate=()=>{
    if(target==='sidebar-settings')fireEvent.click(screen.getByRole('button',{name:'设置',exact:true}));
    else if(target==='new-thread')fireEvent.click(screen.getByRole('button',{name:'新建会话',exact:true}));
    else if(target==='back')fireEvent.click(screen.getByRole('button',{name:'后退'}));
    else if(target==='forward')fireEvent.click(screen.getByRole('button',{name:'前进'}));
    else fireEvent.keyDown(window,{key:',',ctrlKey:true});
  };
  editorControl.flush.mockRejectedValueOnce(new Error('合同输入还未保存'));
  navigate();
  expect(await screen.findByText('合同输入还未保存')).toBeTruthy();
  expect(document.querySelector('.app-frame')?.getAttribute('data-view')).toBe('contract-review');
  expect(screen.getByText('完整 Word 文档')).toBeTruthy();
  expect(localStorage.getItem('verilex:last-thread:v1')).toBe('ct');
  navigate();
  await waitFor(()=>expect(document.querySelector('.app-frame')?.getAttribute('data-view')).toBe(target.includes('settings')||target==='forward'?'settings':'agent'));
});

test('保存等待或失败时选择其他会话不会提前改变页面或线程',async()=>{
  exists=true;
  const original=fetchMock.getMockImplementation()!;
  const other={...thread,id:'general-thread',title:'其他普通会话',scene:'general'};
  fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
    if(url.endsWith('/v1/threads?scene=general')||url.endsWith('/v1/threads/general-thread'))return Promise.resolve(new Response(JSON.stringify(url.endsWith('/general-thread')?other:[other])));
    return original(url,init);
  });
  openApp();fireEvent.click(await screen.findByText('样本合同.docx'));await screen.findByText('完整 Word 文档');
  fireEvent.click(screen.getByRole('button',{name:'展开侧栏'}));
  let reject!:(error:Error)=>void;editorControl.flush.mockImplementationOnce(()=>new Promise((_,no)=>{reject=no;}));
  fireEvent.click(await screen.findByText('其他普通会话'));
  expect(document.querySelector('.app-frame')?.getAttribute('data-view')).toBe('contract-review');
  expect(localStorage.getItem('verilex:last-thread:v1')).toBe('ct');
  await act(async()=>reject(new Error('合同输入还未保存')));
  expect(await screen.findByText('合同输入还未保存')).toBeTruthy();expect(screen.getByText('完整 Word 文档')).toBeTruthy();
  expect(localStorage.getItem('verilex:last-thread:v1')).toBe('ct');
  fireEvent.click(screen.getByText('其他普通会话'));
  await waitFor(()=>expect(document.querySelector('.app-frame')?.getAttribute('data-view')).toBe('agent'));
  expect(localStorage.getItem('verilex:last-thread:v1')).toBe('general-thread');
});
afterEach(() => {cleanup(); vi.unstubAllGlobals(); localStorage.clear();});
function openApp() {
  render(<App initialHealth={{ok: true, service: "verilex-runtime", version: "0.3.0", storage: "local", cloud_backend: false, configured: {models: 1, mcp_servers: 0, skills: 0, folders: 0}}} updaterClient={{supported: true, getCurrentVersion: async () => "0.3.0", check: async () => null, relaunch: async () => {}}} />);
  fireEvent.click(screen.getByRole("button", {name: "合同审查"}));
}

test("创建合同审查后将原件上传并实际发送带附件的首轮消息", async () => {
  openApp();
  const file = new File(["synthetic-docx"], "样本合同.docx");
  fireEvent.change(await screen.findByLabelText("选择合同文件"), {target: {files: [file]}});
  fireEvent.change(screen.getByLabelText("审查要求（选填）"), {target: {value: "只审查付款与解除条款\n尽量保留原有条款"}});
  fireEvent.click(await screen.findByRole("button", {name:"开始审查"}));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/v1/threads/ct/messages"))).toBe(true));
  await act(async () => Socket.last?.onopen?.());
  await waitFor(() => expect(Socket.last?.sent.some(value => JSON.parse(value).type === "turn.start")).toBe(true));
  const start = Socket.last!.sent.map(value => JSON.parse(value)).find(value => value.type === "turn.start");
  expect(start).toMatchObject({threadId: "ct", attachmentUploadIds: ["upload"]});
  expect(start.content).toContain("请审查这份合同");
  expect(start.content).toContain("本次审查要求：\n只审查付款与解除条款\n尽量保留原有条款");
  const create = fetchMock.mock.calls.find(([url, init]) => String(url).includes("/v1/contract-reviews?") && init?.method === "POST");
  expect(((create?.[1].body as FormData).get("file") as File).size).toBe(6);
  expect(String(create?.[0])).toContain("document_id=wd");
});

test("历史合同只显示Word编辑台，不恢复旧意见栏", async () => {
  exists = true;
  localStorage.setItem('verilex:contract-assistant-open:v1', 'true');
  openApp();
  fireEvent.click(await screen.findByText("样本合同.docx"));
  await screen.findByRole('button',{name:'按当前文档重新审查'});
  expect(screen.queryByRole("button", {name:"本次审查原文与批注"})).toBeNull();
  expect(screen.queryByRole("button", {name:"展开意见栏"})).toBeNull();
  expect(screen.queryByRole("textbox", {name:"向智能体提问"})).toBeNull();
});

test("上传期间离开合同页会停止未启动审查，避免阻塞下一份合同", async () => {
  const original = fetchMock.getMockImplementation()!;
  let release!: (response: Response) => void;
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url.includes("/v1/contract-reviews?") && init?.method === "POST") return new Promise(resolve => {release = resolve;});
    return original(url, init);
  });
  openApp();
  fireEvent.change(await screen.findByLabelText("选择合同文件"), {target: {files: [new File(["fixture"], "合同.docx")]}});
  fireEvent.click(await screen.findByRole("button", {name:"开始审查"}));
  await waitFor(() => expect(release).toBeTypeOf("function"));
  expect(screen.queryByRole("button", {name: "新建会话"})).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "展开侧栏"}));
  fireEvent.click(screen.getByRole("button", {name: "新建会话"}));
  await waitFor(()=>expect(document.querySelector('.app-frame')?.getAttribute('data-view')).toBe('agent'));
  await act(async () => release(new Response(JSON.stringify({review_id: "cr", thread_id: "ct", upload_id: "upload", filename: "合同.docx"}))));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/v1/contract-reviews/cr/stop") && init?.method === "POST")).toBe(true));
  expect(Socket.last?.sent.some(value => JSON.parse(value).type === "turn.start")).not.toBe(true);
});
