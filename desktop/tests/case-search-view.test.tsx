import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { CaseSearchView } from "../src/views/CaseSearchView";
import type { McpServerRecord } from "../src/runtime/contracts";
const start = vi.fn(), settings = vi.fn(), open = vi.fn();
vi.mock("../src/platform/caseDatabase", () => ({ openCaseDatabase: (...args: unknown[]) => open(...args) }));
vi.mock("../src/runtime/knowledgeClient", () => ({ knowledgeApi: { list: async () => [{ id: "library-1", name: "案例资料", status: "ready" }] } }));
vi.mock("../src/runtime/api", () => ({ listSkills: async () => [] }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const server = { id: "example", displayName: "示例插件" } as McpServerRecord;
function view(servers: McpServerRecord[] = []) { return render(<CaseSearchView mcpServers={servers} history={[]} starting={false}
  onStartSearch={start} onSelectHistory={vi.fn()} onDeleteHistory={vi.fn()} onOpenMcpSettings={settings} />); }
test("without plugin permits searching an explicitly selected knowledge library", async () => {
  view(); expect(screen.getByRole("textbox")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "知识库" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "案例资料" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "本地案例" } });
  fireEvent.submit(screen.getByRole("textbox").closest("form")!);
  expect(start).toHaveBeenCalledWith("本地案例", null, [], ["library-1"]);
});
test("plugin selection and enter submit preserve explicit skills", () => {
  view([server]); fireEvent.click(screen.getByRole("button", { name: "插件" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: "示例插件" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "  示例案件问题  " } });
  fireEvent.submit(screen.getByRole("textbox").closest("form")!);
  expect(start).toHaveBeenCalledWith("示例案件问题", "example", [], []);
});
test("knowledge selection must be explicit before sending", async () => {
  view([server]); fireEvent.click(screen.getByRole("button", { name: "知识库" }));
  await screen.findByRole("checkbox", { name: "案例资料" });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "示例" } });
  fireEvent.click(screen.getByRole("button", { name: "开始调研" })); expect(start).not.toHaveBeenCalled();
  expect(screen.getByRole("menu", { name: "知识库渠道" })).toBeTruthy();
});
test("official destinations are fixed and opened only on clicks", () => {
  open.mockResolvedValue(undefined); view(); expect(open).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "用系统浏览器打开人民法院案例库" })); expect(open).toHaveBeenCalledWith("library");
  fireEvent.click(screen.getByRole("button", { name: "用系统浏览器打开中国裁判文书网" })); expect(open).toHaveBeenCalledWith("judgments");
});

test("all connected endpoints remain selectable and the case endpoint keeps its label", () => {
  const shared = { transport: "http" as const, displayName: "元典" };
  view([{ ...server, ...shared, id: "law", url: "https://open.chineselaw.com/mcp/law/stream" },
    { ...server, ...shared, id: "case", url: "https://open.chineselaw.com/mcp/case/stream" }]);
  fireEvent.click(screen.getByRole("button", { name: "插件" }));
  expect(screen.getAllByRole("menuitemradio")).toHaveLength(2);
  fireEvent.click(screen.getByRole("menuitemradio", { name: "元典 · 含案例检索" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "示例" } });
  fireEvent.click(screen.getByRole("button", { name: "开始调研" })); expect(start).toHaveBeenCalledWith("示例", "case", [], []);
});

test("a removed selected plugin must be chosen again before creating a search", () => {
  const props = { history: [], starting: false, onStartSearch: start,
    onSelectHistory: vi.fn(), onDeleteHistory: vi.fn(), onOpenMcpSettings: settings };
  const { rerender } = render(<CaseSearchView {...props} mcpServers={[server]} />);
  fireEvent.click(screen.getByRole("button", { name: "插件" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: "示例插件" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "保留我的检索问题" } });
  rerender(<CaseSearchView {...props} mcpServers={[{ ...server, id: "replacement", displayName: "新插件" }]} />);
  fireEvent.click(screen.getByRole("button", { name: "开始调研" }));
  expect(start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("menuitemradio", { name: "新插件" }));
  fireEvent.click(screen.getByRole("button", { name: "开始调研" }));
  expect(start).toHaveBeenCalledWith("保留我的检索问题", "replacement", [], []);
});
