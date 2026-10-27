import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { LegalChannelPicker } from "../src/components/LegalChannelPicker";
import type { LegalChannel, LegalMenu } from "../src/components/LegalChannelPicker";
import type { McpServerRecord } from "../src/runtime/contracts";

vi.mock("../src/runtime/knowledgeClient", () => ({ knowledgeApi: { list: async () => [{ id: "library-1", name: "法律资料", status: "ready" }] } }));

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

const OPEN_MCP_SETTINGS = vi.fn();

function renderPicker(
  servers: McpServerRecord[],
  value: LegalChannel,
  onChange: (value: LegalChannel) => void = vi.fn(),
) {
  function ControlledPicker() {
    const [menu, setMenu] = useState<LegalMenu>(null);
    const [selected, onChangeKnowledge] = useState<string[]>([]);
    return (
      <LegalChannelPicker
        knowledgeSelection={{ selected, onChange: onChangeKnowledge, disabled: false }}
        mcpServers={servers}
        value={value}
        onChange={onChange}
        onOpenMcpSettings={OPEN_MCP_SETTINGS}
        menu={menu}
        onMenuChange={setMenu}
      />
    );
  }
  return render(<ControlledPicker />);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("无已接入 MCP 时插件与联网都可见且可切换，联网为当前渠道", () => {
  renderPicker([], { kind: "online" });

  expect(
    screen.getByRole("button", { name: "插件" }).getAttribute("aria-pressed"),
  ).toBe("false");
  expect(
    screen.getByRole("button", { name: "知识库" }).getAttribute("aria-pressed"),
  ).toBe("false");
  expect(
    screen.getByRole("button", { name: "联网" }).getAttribute("aria-pressed"),
  ).toBe("true");
});

test("插件菜单列出已接入 MCP，选择一项后生效", () => {
  const onChange = vi.fn();
  renderPicker(
    [server("yuandian", "元典智库"), server("custom", "自建服务")],
    { kind: "plugin", serverId: null },
    onChange,
  );

  fireEvent.click(screen.getByRole("button", { name: "插件" }));

  expect(screen.getByRole("menuitemradio", { name: "元典智库" })).toBeTruthy();
  expect(screen.getByRole("menuitemradio", { name: "自建服务" })).toBeTruthy();

  fireEvent.click(screen.getByRole("menuitemradio", { name: "元典智库" }));

  expect(onChange).toHaveBeenCalledWith({ kind: "plugin", serverId: "yuandian" });
});

test("插件菜单无服务时呈现唯一引导动作，通向既有插件面板", () => {
  renderPicker([], { kind: "plugin", serverId: null });

  fireEvent.click(screen.getByRole("button", { name: "插件" }));

  expect(
    screen.getByText("还没有接入任何插件，接入后可直接检索商业法律数据库。"),
  ).toBeTruthy();
  const guide = screen.getByRole("menuitem", { name: "添加插件" });
  expect(guide).toBeTruthy();

  fireEvent.click(guide);

  expect(OPEN_MCP_SETTINGS).toHaveBeenCalledTimes(1);
});

test("知识库菜单读取现有资料库并支持显式选择", async () => {
  const onChange = vi.fn();
  renderPicker([], { kind: "online" }, onChange);
  fireEvent.click(screen.getByRole("button", { name: "知识库" }));
  const checkbox = await screen.findByRole("checkbox", { name: "法律资料" });
  fireEvent.click(checkbox);
  expect((checkbox as HTMLInputElement).checked).toBe(true);
  expect(onChange).toHaveBeenCalledWith({ kind: "knowledge" });
});

test("联网渠道直接可选且不弹菜单", () => {
  const onChange = vi.fn();
  renderPicker([server("yuandian", "元典智库")], {
    kind: "plugin",
    serverId: "yuandian",
  }, onChange);

  fireEvent.click(screen.getByRole("button", { name: "联网" }));

  expect(onChange).toHaveBeenCalledWith({ kind: "online" });
  expect(screen.queryByTestId("legal-plugin-menu")).toBeNull();
});

test("已选插件的渠道按钮显示所选服务名", () => {
  renderPicker([server("yuandian", "元典智库")], {
    kind: "plugin",
    serverId: "yuandian",
  });

  const pluginButton = screen.getByRole("button", { name: /插件/ });
  expect(pluginButton.textContent).toContain("元典智库");
});
