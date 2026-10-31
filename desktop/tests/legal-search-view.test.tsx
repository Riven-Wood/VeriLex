import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { LegalSearchView } from "../src/views/LegalSearchView";
import type { McpServerRecord } from "../src/runtime/contracts";
const settings = vi.fn(), open = vi.fn(), selectHistory = vi.fn(), deleteHistory = vi.fn();
vi.mock("../src/platform/lawDatabase", () => ({ openLawDatabase: (...args: unknown[]) => open(...args) }));
vi.mock("../src/runtime/api", () => ({listSkills: async () => []}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const server = { id: "example", displayName: "示例插件" } as McpServerRecord;
function view(servers: McpServerRecord[] = [], history: {id:string;keyword:string}[] = []) {
  return render(<LegalSearchView mcpServers={servers} history={history} starting={false} onSettingsChange={settings} onNewSearch={vi.fn()}
    onSelectHistory={selectHistory} onDeleteHistory={deleteHistory} onOpenMcpSettings={vi.fn()} />);
}
test("默认联网渠道，无顶部输入框", () => {
  view();
  expect(screen.getByRole("heading", {name:"法律检索"})).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(settings).toHaveBeenLastCalledWith(expect.objectContaining({kind:"online",valid:true}));
});
test("插件选择传递检索设置", () => {
  view([server]);
  expect(settings).toHaveBeenLastCalledWith(expect.objectContaining({kind:"plugin",valid:false}));
  fireEvent.click(screen.getByRole("button", {name:"插件"}));
  fireEvent.click(screen.getByRole("menuitemradio", {name:"示例插件"}));
  expect(settings).toHaveBeenLastCalledWith(expect.objectContaining({kind:"plugin",serverId:"example",valid:true}));
});
test("官方库可用，页面不重复显示检索记录或新建对话", () => {
  open.mockResolvedValue(undefined);
  render(<LegalSearchView mcpServers={[]} history={[{id:"t1",keyword:"民法典"}]} locked starting={false} onSettingsChange={settings} onNewSearch={vi.fn()} onSelectHistory={selectHistory} onDeleteHistory={deleteHistory} onOpenMcpSettings={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", {name:/国家法律法规数据库/}));
  expect(open).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("民法典")).toBeNull();
  expect(screen.queryByRole("button", {name:"新建对话"})).toBeNull();
});
