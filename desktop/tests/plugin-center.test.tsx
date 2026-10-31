Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, writable: true, value() { this.setAttribute('open', ''); } });
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { PluginCenterView } from "../src/views/PluginCenterView";
import * as api from "../src/runtime/api";
import type { McpServerRecord, McpToolRecord } from "../src/runtime/contracts";
import { YUANDIAN_SERVICES } from "../src/mcp/yuandian";

beforeEach(() => { vi.spyOn(api, "listSkillCategories").mockResolvedValue([]); });

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const server = (overrides: Partial<McpServerRecord> = {}): McpServerRecord => ({
  id: "one", displayName: "本地检索", transport: "stdio", command: "node", args: [], cwd: null,
  url: null, env: {}, headers: {}, secretEnvKeys: [], secretHeaderKeys: [], connectTimeoutMs: 10000,
  requestTimeoutMs: 30000, configRevision: 1, secretRevision: 0, configReviewState: "reviewed",
  riskFindings: [], connectionStatus: "idle", errorCode: null, retryable: false,
  lastTestStatus: "passed", lastTestedAt: "2026-09-17T01:00:00Z", selectedThreadCount: 2,
  createdAt: "2026-09-17T01:00:00Z", updatedAt: "2026-09-17T01:00:00Z", ...overrides,
});
const tool: McpToolRecord = { serverId: "one", toolName: "find", description: "查找资料",
  descriptionHash: "a".repeat(64), schemaHash: "b".repeat(64), inputSchema: {}, enabled: false,
  reviewState: "new", firstSeenAt: "2026-09-17", lastSeenAt: "2026-09-17" };
function setup(records = [server()]) {
  vi.spyOn(api, "listMcpServers").mockResolvedValue(records);
  vi.spyOn(api, "listMcpServerTools").mockImplementation(async id => [{ ...tool, serverId: id }]);
  vi.spyOn(api, "listSkills").mockResolvedValue([
    { name: "合同审查", description: "审查合同", instructions: "逐条检查", enabled: true, valid: true, error: null, path: "/fake/SKILL.md" },
    { name: "案例分析", description: "分析案例", instructions: "", enabled: false, valid: false, error: "缺少说明", path: "/fake2/SKILL.md" },
  ]);
}
test("browsing and searching never test or connect, detail reviews with exact hashes", async () => {
  setup();
  const probe = vi.spyOn(api, "refreshMcpServerTools");
  const review = vi.spyOn(api, "reviewMcpServerTool").mockResolvedValue({ ...tool, enabled: true, reviewState: "known" });
  render(<PluginCenterView />);
  expect(screen.getByRole("tab", { name: "外部工具" }).getAttribute("aria-selected")).toBe("true");
  expect(await screen.findByText("本地检索")).toBeTruthy();
  expect(await screen.findByText(/1 个工具/)).toBeTruthy();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "不存在" } });
  expect(screen.queryByText("元典智库")).toBeNull();
  expect(screen.getByText("没有找到匹配的名称")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "清空搜索" }));
  fireEvent.click(screen.getByRole("button", { name: "查看 本地检索" }));
  expect(await screen.findByText("查找资料")).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox", { name: "启用 find" }));
  await waitFor(() => expect(review).toHaveBeenCalledWith("one", "find", {
    enabled: true, expectedDescriptionHash: tool.descriptionHash, expectedSchemaHash: tool.schemaHash,
  }, expect.any(AbortSignal)));
  expect(probe).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "返回列表" }));
  expect(screen.getByRole("button", { name: "添加外部工具" })).toBeTruthy();
});
test("keyboard tabs load and search skills locally without changing total counts", async () => {
  setup(); render(<PluginCenterView />);
  await screen.findByText("本地检索");
  fireEvent.keyDown(screen.getByRole("tab", { name: "外部工具" }), { key: "ArrowRight" });
  expect(await screen.findByText("合同审查")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "技能" })).toBe(document.activeElement);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "案例" } });
  expect(screen.queryByText("合同审查")).toBeNull();
  expect(screen.getByText("本机 2 个技能，1 个启用")).toBeTruthy();
  expect(screen.getByText(/缺少说明/)).toBeTruthy();
  expect(api.listSkills).toHaveBeenCalledTimes(1);
});
test("saved test refreshes status and tools; delete stays behind confirmation", async () => {
  setup();
  const probe = vi.spyOn(api, "refreshMcpServerTools").mockResolvedValue([tool]);
  const remove = vi.spyOn(api, "deleteMcpServer").mockResolvedValue({ deleted: true, selectedThreadCount: 2 });
  render(<PluginCenterView />); await screen.findByText("本地检索");
  fireEvent.click(screen.getByRole("button", { name: "查看 本地检索" }));
  await screen.findByText("查找资料");
  fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
  await waitFor(() => expect(probe).toHaveBeenCalledTimes(1));
  expect(await screen.findByText("连接成功，有 1 项工具更新需要确认。请在下方选择要启用的工具。")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "删除 本地检索" }));
  expect(remove).not.toHaveBeenCalled();
  const dialog = screen.getByRole("dialog");
  expect(dialog.textContent).toContain("2 个对话");
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.queryByText("本地检索")).toBeNull();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "添加外部工具" }));
});
test("Yuan Dian endpoints form one searchable built-in card", async () => {
  setup(YUANDIAN_SERVICES.map((s, i) => server({ id: `yd-${i}`, displayName: s.id, transport: "http", command: null, url: s.url, secretHeaderKeys: ["Authorization"] })));
  render(<PluginCenterView />);
  await screen.findByText("元典智库");
  await waitFor(() => expect(screen.getByRole("button", { name: "更新元典智库密钥" })).toBeTruthy());
  expect(screen.getAllByText("内置")).toHaveLength(1);
  expect(screen.queryByText("自定义")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "更新元典智库密钥" }));
  const dialog=screen.getByRole("dialog", {name:"元典智库"});
  expect(within(dialog).getByLabelText("元典 API Key")).toBeTruthy();
  expect(within(dialog).getByRole("button", {name:"确认更新"})).toBeTruthy();
  expect(screen.queryByRole("heading", {name:"工具清单"})).toBeNull();
  fireEvent.click(within(dialog).getByRole("button", {name:"取消"}));
  expect(screen.queryByRole("dialog", {name:"元典智库"})).toBeNull();
  expect(screen.getByRole("button", {name:"更新元典智库密钥"})).toBeTruthy();
});

test('connected Yuan Dian updates its key from the popup without opening the tool list', async()=>{
  const records=YUANDIAN_SERVICES.map((s,i)=>server({id:`yd-${i}`,displayName:s.id,transport:'http',command:null,url:s.url,secretHeaderKeys:['Authorization']}));
  setup(records);
  vi.mocked(api.listMcpServerTools).mockResolvedValue([]);
  const probe=vi.spyOn(api,'testSavedMcpServer').mockResolvedValue({testReceipt:'receipt',tools:[],riskCodes:[]});
  const update=vi.spyOn(api,'updateMcpServer').mockImplementation(async id=>records.find(item=>item.id===id)!);
  render(<PluginCenterView/>);
  fireEvent.click(await screen.findByRole('button',{name:'更新元典智库密钥'}));
  const dialog=screen.getByRole('dialog',{name:'元典智库'});
  fireEvent.change(within(dialog).getByLabelText('元典 API Key'),{target:{value:'synthetic-key'}});
  fireEvent.click(within(dialog).getByRole('button',{name:'确认更新'}));
  await waitFor(()=>expect(update).toHaveBeenCalledTimes(records.length));
  expect(probe).toHaveBeenCalledTimes(records.length);
  await waitFor(()=>expect(screen.queryByRole('dialog',{name:'元典智库'})).toBeNull());
  expect(screen.queryByRole('heading',{name:'工具清单'})).toBeNull();
});

test("unconfigured Yuan Dian opens the same key popup", async () => {
  setup([]);
  render(<PluginCenterView />);
  fireEvent.click(await screen.findByRole("button", { name: "添加元典智库" }));
  const dialog = screen.getByRole("dialog", { name: "元典智库" });
  expect(within(dialog).getByLabelText("元典 API Key")).toBeTruthy();
  expect(within(dialog).getByRole("button", { name: "确认添加" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "工具清单" })).toBeNull();
});


test("slow catalog prefetch cannot overwrite a just-reviewed tool", async () => {
  setup();
  let finish!: (value: McpToolRecord[]) => void;
  vi.mocked(api.listMcpServerTools).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue([tool]);
  vi.spyOn(api, "reviewMcpServerTool").mockResolvedValue({ ...tool, enabled: true, reviewState: "known" });
  render(<PluginCenterView />); await screen.findByText("本地检索");
  fireEvent.click(screen.getByRole("button", { name: "查看 本地检索" }));
  const toggle = await screen.findByRole("checkbox", { name: "启用 find" });
  fireEvent.click(toggle);
  await waitFor(() => expect((toggle as HTMLInputElement).checked).toBe(true));
  finish([tool]);
  await waitFor(() => expect(screen.queryByText("工具数量待读取")).toBeNull());
  expect((toggle as HTMLInputElement).checked).toBe(true);
});

test("refresh supersedes an unfinished detail read and releases the loading state", async () => {
  setup();
  vi.mocked(api.listMcpServerTools).mockResolvedValueOnce([tool]).mockImplementationOnce(() => new Promise(() => {}))
    .mockResolvedValue([tool]);
  vi.spyOn(api, "refreshMcpServerTools").mockResolvedValue([tool]);
  render(<PluginCenterView />); await screen.findByText("本地检索");
  fireEvent.click(screen.getByRole("button", { name: "查看 本地检索" }));
  expect(screen.getByText("正在读取工具…")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
  expect(await screen.findByText("连接成功，有 1 项工具更新需要确认。请在下方选择要启用的工具。")).toBeTruthy();
  expect(screen.queryByText("正在读取工具…")).toBeNull();
  expect(screen.getByRole("checkbox", { name: "启用 find" })).toBeTruthy();
});

test("adding reuses parse-test-save and never authorizes a conversation", async () => {
  setup([]);
  const draft = { ...server(), risks: [] };
  vi.spyOn(api, "parseMcpImport").mockResolvedValue({ drafts: [draft] });
  vi.spyOn(api, "testMcpServerDraft").mockResolvedValue({ testReceipt: "receipt", riskCodes: [], tools: [tool] });
  const save = vi.spyOn(api, "createMcpServer").mockResolvedValue(server());
  const activate = vi.spyOn(api, "activateMcpView");
  render(<PluginCenterView />);
  fireEvent.click(await screen.findByRole("button", { name: "添加外部工具" }));
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), { target: { value: '{"mcpServers":{"本地检索":{"command":"node","args":[]}}}' } });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  await screen.findByText("找到 1 个工具");
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save.mock.calls[0][0].testReceipt).toBe("receipt");
  expect(activate).not.toHaveBeenCalled();
  expect(await screen.findByRole("button", { name: "查看 本地检索" })).toBeTruthy();
});


test("failed initial load is not shown as an empty catalog and can retry", async () => {
  setup();
  vi.mocked(api.listMcpServers).mockRejectedValueOnce(new Error("failure"));
  render(<PluginCenterView />);
  await screen.findByRole("alert");
  expect(screen.queryByText("已添加 0 个外部工具服务")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "刷新列表" }));
  expect(await screen.findByText("本地检索")).toBeTruthy();
});

test("error retry cannot abort the test's pending status refresh", async () => {
  setup();
  let finish!: (value: McpServerRecord[]) => void;
  vi.spyOn(api, "refreshMcpServerTools").mockRejectedValue(new Error("failure"));
  render(<PluginCenterView />); await screen.findByText("本地检索");
  fireEvent.click(screen.getByRole("button", { name: "查看 本地检索" }));
  await screen.findByText("查找资料");
  vi.mocked(api.listMcpServers).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "刷新列表" }).hasAttribute("disabled")).toBe(true);
  finish([server()]);
  await waitFor(() => expect(screen.getByRole("button", { name: "测试连接" }).hasAttribute("disabled")).toBe(false));
});
