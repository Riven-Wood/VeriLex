import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { LegalSearchView } from "../src/views/LegalSearchView";
import type { McpServerRecord } from "../src/runtime/contracts";

const OPEN_LAW_DATABASE = vi.fn();
const START_SEARCH = vi.fn();
const SELECT_HISTORY = vi.fn();
const DELETE_HISTORY = vi.fn();
const OPEN_MCP = vi.fn();

vi.mock("../src/platform/lawDatabase", () => ({
  openLawDatabase: (...args: unknown[]) => OPEN_LAW_DATABASE(...args),
}));

function server(id: string, displayName: string): McpServerRecord {
  return {
    id,
    displayName,
    transport: "stdio",
    command: "/usr/bin/echo",
    args: [],
    cwd: null,
    url: null,
    env: {},
    headers: {},
    secretEnvKeys: [],
    secretHeaderKeys: [],
    connectTimeoutMs: 10_000,
  } as unknown as McpServerRecord;
}

const NO_SERVERS: McpServerRecord[] = [];

function renderView(servers: McpServerRecord[] = NO_SERVERS, history = []) {
  return render(
    <LegalSearchView
      mcpServers={servers}
      history={history}
      starting={false}
      onStartSearch={START_SEARCH}
      onSelectHistory={SELECT_HISTORY}
      onDeleteHistory={DELETE_HISTORY}
      onOpenMcpSettings={OPEN_MCP}
    />,
  );
}

const INPUT = "输入法律问题，例如：劳动合同解除的赔偿标准";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("页面呈现标题、官方数据库按钮与三渠道", () => {
  renderView();

  expect(screen.getByRole("heading", { name: "法律检索" })).toBeTruthy();
  expect(
    screen.getByRole("button", { name: /国家法律法规数据库/ }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "联网" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "插件" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "知识库" })).toBeTruthy();
});

test("官方数据库按钮打开内置浏览器窗口", () => {
  OPEN_LAW_DATABASE.mockResolvedValue(undefined);
  renderView();

  fireEvent.click(screen.getByRole("button", { name: /国家法律法规数据库/ }));

  expect(OPEN_LAW_DATABASE).toHaveBeenCalledTimes(1);
});

test("联网渠道检索把问题交给上层，回车等同点击", () => {
  renderView();
  const input = screen.getByPlaceholderText(INPUT);
  fireEvent.change(input, { target: { value: "民法典第二百条" } });
  fireEvent.submit(input.closest("form")!);

  expect(START_SEARCH).toHaveBeenCalledWith("民法典第二百条", null, [], []);
});

test("插件渠道选中服务后检索携带服务 ID", () => {
  renderView([server("yuandian", "元典智库")]);
  fireEvent.click(screen.getByRole("button", { name: "插件" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: "元典智库" }));
  fireEvent.change(screen.getByPlaceholderText(INPUT), {
    target: { value: "劳动合同解除赔偿" },
  });
  fireEvent.click(screen.getByRole("button", { name: "检索" }));

  expect(START_SEARCH).toHaveBeenCalledWith("劳动合同解除赔偿", "yuandian", [], []);
});

test("插件渠道未选服务时先打开插件菜单", () => {
  renderView([server("yuandian", "元典智库")]);
  fireEvent.change(screen.getByPlaceholderText(INPUT), {
    target: { value: "民法典" },
  });
  fireEvent.click(screen.getByRole("button", { name: "检索" }));

  expect(START_SEARCH).not.toHaveBeenCalled();
  expect(screen.getByTestId("legal-plugin-menu")).toBeTruthy();
});

test("检索记录只显示查询词，点击回到该检索、×删除", () => {
  renderView(NO_SERVERS, [
    { id: "t1", keyword: "民法典第二百条" },
    { id: "t2", keyword: "竞业限制" },
  ]);

  expect(screen.getByText("民法典第二百条")).toBeTruthy();
  expect(screen.getByText("竞业限制")).toBeTruthy();

  fireEvent.click(screen.getByText("民法典第二百条"));
  expect(SELECT_HISTORY).toHaveBeenCalledWith("t1");

  fireEvent.click(
    screen.getByRole("button", { name: "删除检索记录 民法典第二百条" }),
  );
  expect(DELETE_HISTORY).toHaveBeenCalledWith("t1");
});

test("创建中禁用检索按钮", () => {
  render(
    <LegalSearchView
      mcpServers={NO_SERVERS}
      history={[]}
      starting={true}
      onStartSearch={START_SEARCH}
      onSelectHistory={SELECT_HISTORY}
      onDeleteHistory={DELETE_HISTORY}
      onOpenMcpSettings={OPEN_MCP}
    />,
  );

  expect(screen.getByRole("button", { name: "创建中…" }).disabled).toBe(true);
});
