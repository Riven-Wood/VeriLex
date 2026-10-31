import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { CaseSearchView } from "../src/views/CaseSearchView";
import type { McpServerRecord } from "../src/runtime/contracts";
const settings = vi.fn(), open = vi.fn();
vi.mock("../src/platform/caseDatabase", () => ({ openCaseDatabase: (...args: unknown[]) => open(...args) }));
vi.mock("../src/runtime/knowledgeClient", () => ({ knowledgeApi: { list: async () => [{ id: "library-1", name: "案例资料", status: "ready" }] } }));
vi.mock("../src/runtime/api", () => ({ listSkills: async () => [] }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const server = { id: "example", displayName: "示例插件" } as McpServerRecord;
function view(servers: McpServerRecord[] = []) { return render(<CaseSearchView mcpServers={servers} history={[]} starting={false}
  onSettingsChange={settings} onNewSearch={vi.fn()} onSelectHistory={vi.fn()} onDeleteHistory={vi.fn()} onOpenMcpSettings={vi.fn()} />); }
test("选择知识库传递渠道，顶部没有输入框", async () => {
  view(); expect(screen.queryByRole("textbox")).toBeNull();
  expect(settings).toHaveBeenLastCalledWith(expect.objectContaining({kind:"knowledge",valid:false}));
  fireEvent.click(screen.getByRole("button", { name: "知识库" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "案例资料" }));
  expect(settings).toHaveBeenLastCalledWith(expect.objectContaining({kind:"knowledge",knowledgeIds:["library-1"],valid:true}));
});
test("插件必须选中并在列表中存在", () => {
  const {rerender} = view([server]);
  fireEvent.click(screen.getByRole("button", { name: "插件" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: "示例插件" }));
  expect(settings).toHaveBeenLastCalledWith(expect.objectContaining({kind:"plugin",serverId:"example",valid:true}));
  rerender(<CaseSearchView mcpServers={[]} history={[]} starting={false} onSettingsChange={settings} onNewSearch={vi.fn()}
    onSelectHistory={vi.fn()} onDeleteHistory={vi.fn()} onOpenMcpSettings={vi.fn()} />);
  expect(settings).toHaveBeenLastCalledWith(expect.objectContaining({kind:"plugin",valid:false}));
});
test("官方案例网站只在点击后打开", () => {
  open.mockResolvedValue(undefined); view(); expect(open).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "用系统浏览器打开人民法院案例库" })); expect(open).toHaveBeenCalledWith("library");
  fireEvent.click(screen.getByRole("button", { name: "用系统浏览器打开中国裁判文书网" })); expect(open).toHaveBeenCalledWith("judgments");
});
test("检索页面不重复显示历史或新建对话", () => {
  render(<CaseSearchView mcpServers={[]} history={[{id:"c1",keyword:"示例案例"}]} locked starting={false} onSettingsChange={settings} onNewSearch={vi.fn()} onSelectHistory={vi.fn()} onDeleteHistory={vi.fn()} onOpenMcpSettings={vi.fn()} />);
  expect(screen.queryByText("示例案例")).toBeNull();
  expect(screen.queryByRole("button", {name:"新建对话"})).toBeNull();
});
