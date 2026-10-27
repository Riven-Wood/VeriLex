import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { App } from "../src/app/App";
import * as runtimeApi from "../src/runtime/api";
import type { HealthResponse, WorkspaceRecord } from "../src/runtime/contracts";
import type { AvailableUpdate, UpdaterClient } from "../src/updater/contracts";

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(runtimeApi, "getPreferences").mockResolvedValue({background_task_max_minutes:30});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.querySelector("style[data-settings-test]")?.remove();
});

async function loadSettingsStyles() {
  const stylesheet = document.createElement("style");
  stylesheet.dataset.settingsTest = "true";
  stylesheet.textContent = await readFile(
    resolve(process.cwd(), "src/styles/views.css"),
    "utf8",
  );
  document.head.append(stylesheet);
}

function findStyleRule(
  selector: string,
  media = "",
): CSSStyleDeclaration | null {
  const sheet = document.querySelector<HTMLStyleElement>("style[data-settings-test]")?.sheet;
  if (!sheet) return null;

  const visit = (rules: CSSRuleList, activeMedia: string): CSSStyleDeclaration | null => {
    for (const rule of Array.from(rules)) {
      const styleRule = rule as CSSStyleRule;
      if (styleRule.selectorText === selector && activeMedia === media) {
        return styleRule.style;
      }
      const group = rule as CSSMediaRule;
      if (group.cssRules) {
        const nestedMedia = group.conditionText ?? group.media?.mediaText ?? activeMedia;
        const match = visit(group.cssRules, nestedMedia);
        if (match) return match;
      }
    }
    return null;
  };

  return visit(sheet.cssRules, "");
}

function findStyleRules(selector: string): CSSStyleDeclaration[] {
  const sheet = document.querySelector<HTMLStyleElement>("style[data-settings-test]")?.sheet;
  if (!sheet) return [];

  const matches: CSSStyleDeclaration[] = [];
  const visit = (rules: CSSRuleList) => {
    for (const rule of Array.from(rules)) {
      const styleRule = rule as CSSStyleRule;
      if (styleRule.selectorText?.split(",").map((part) => part.trim()).includes(selector)) {
        matches.push(styleRule.style);
      }
      const group = rule as CSSGroupingRule;
      if (group.cssRules) visit(group.cssRules);
    }
  };

  visit(sheet.cssRules);
  return matches;
}

const zeroHealth: HealthResponse = {
  ok: true,
  service: "verilex-runtime",
  version: "0.3.0",
  storage: "local",
  cloud_backend: false,
  configured: { models: 0, mcp_servers: 0, skills: 0, folders: 0 },
};

const configuredWorkspaces: WorkspaceRecord[] = Array.from({ length: 3 }, (_, index) => ({
  id: `workspace-${index + 1}`,
  root_path: `/Cases/${index + 1}`,
  display_name: `工作区 ${index + 1}`,
  allow_read: true,
  allow_write: index === 0,
  created_at: "2026-08-25T00:00:00Z",
  last_used_at: "2026-08-25T00:00:00Z",
}));

const availableUpdate: AvailableUpdate = {
  version: "0.3.1",
  currentVersion: "0.3.0",
  date: "2026-08-11T00:00:00Z",
  body: "修复更新流程",
  async install() {},
};

function updaterClient(
  check: () => Promise<AvailableUpdate | null>,
): UpdaterClient & { checkCalls: number } {
  return {
    supported: true,
    checkCalls: 0,
    async getCurrentVersion() {
      return "0.3.0";
    },
    async check() {
      this.checkCalls += 1;
      return check();
    },
    async relaunch() {},
  };
}

test("shows actionable preferences without placeholder controls", () => {
  render(<App initialHealth={zeroHealth} />);
  fireEvent.click(screen.getByRole("button", {name:"设置"}));
  expect(screen.getByRole("heading", {name:"通用"})).toBeTruthy();
  expect(screen.getByRole("combobox", {name:"发送消息"})).toBeTruthy();
  expect(screen.queryByText("当前语言：简体中文")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name:"外观"}));
  expect(screen.getByRole("switch", {name:"减少动态效果"})).toBeTruthy();
  expect(screen.queryByRole("heading", {name:"通知"})).toBeNull();
});

test("shows actual authorizations and cancels reads on navigation", async () => {
  let workspaceSignal: AbortSignal | undefined;
  const request=vi.spyOn(runtimeApi,"listWorkspaces").mockResolvedValueOnce([]).mockImplementation(signal=>{
    workspaceSignal=signal;return Promise.resolve(configuredWorkspaces);
  });
  render(<App initialHealth={zeroHealth}/>);
  await waitFor(()=>expect(request).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", {name:"设置"}));
  fireEvent.click(screen.getByRole("button", {name:"助手与权限"}));
  expect(await screen.findByText("/Cases/1")).toBeTruthy();
  expect(screen.getByRole("combobox", {name:"工作区 1的访问权限"})).toBeTruthy();
  expect(workspaceSignal?.aborted).toBe(false);
  fireEvent.click(screen.getByRole("button", {name:"通用"}));
  expect(workspaceSignal?.aborted).toBe(true);
});

test("does not expose private workspace request errors", async () => {
  const request=vi.spyOn(runtimeApi,"listWorkspaces").mockResolvedValueOnce([]).mockRejectedValue(new Error("http://127.0.0.1 sk-workspace-secret"));
  render(<App initialHealth={zeroHealth}/>);
  await waitFor(()=>expect(request).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", {name:"设置"}));
  fireEvent.click(screen.getByRole("button", {name:"助手与权限"}));
  expect(await screen.findByText(/无法读取授权文件夹/)).toBeTruthy();
  expect(document.body.textContent).not.toContain("sk-workspace-secret");
});

test("uses a settings rail and one real content column on wide windows", async () => {
  await loadSettingsStyles();
  render(<App initialHealth={zeroHealth} />);

  fireEvent.click(screen.getByRole("button", { name: "设置" }));

  const rail = document.querySelector<HTMLElement>(".settings-rail");
  const navigation = document.querySelector<HTMLElement>(".settings-navigation");
  const content = document.querySelector<HTMLElement>(".settings-content");
  expect(rail).toBeTruthy();
  expect(navigation).toBeTruthy();
  expect(content).toBeTruthy();

  const legacySettingsLayout = findStyleRules(".settings-layout");
  const legacyProviderLayout = findStyleRules(".provider-settings-card");
  expect(legacySettingsLayout.some((rule) => rule.getPropertyValue("grid-template-columns"))).toBe(false);
  expect(legacyProviderLayout.some((rule) => rule.getPropertyValue("grid-row"))).toBe(false);

  // The settings rail replaces the app sidebar in the app-frame's first column.
  const frame = document.querySelector<HTMLElement>(".app-frame");
  expect(frame?.getAttribute("data-view")).toBe("settings");
  // The navigation is no longer a sticky element inside a nested two-column shell.
  expect(getComputedStyle(navigation!).position).not.toBe("sticky");
  expect(getComputedStyle(content!).display).toBe("grid");
});

test("uses one capability management entry without a duplicate tool catalog", () => {
  render(<App initialHealth={zeroHealth}/>);
  fireEvent.click(screen.getByRole("button", {name:"设置"}));
  fireEvent.click(screen.getByRole("button", {name:"助手与权限"}));
  expect(screen.getByRole("button", {name:"打开能力中心"})).toBeTruthy();
  expect(screen.queryByText("这里显示当前真正可用的工具")).toBeNull();
});

test("passes the existing Runtime version into diagnostics without another request", () => {
  const healthRequest = vi.spyOn(runtimeApi, "getHealth")
    .mockRejectedValue(new Error("Settings must not request health again"));
  render(<App initialHealth={{ ...zeroHealth, version: "0.3.7-test" }} />);

  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "关于" }));

  expect(screen.getByText("后台版本 0.3.7-test")).toBeTruthy();
  expect(screen.getByText("PolyForm Noncommercial 1.0.0（仅限非商业用途）")).toBeTruthy();
  expect(healthRequest).not.toHaveBeenCalled();
});

test("exposes current navigation and reduced-motion state", () => {
  render(<App initialHealth={zeroHealth} />);

  // The agent workspace is the default view; the sidebar's session actions
  // no longer carry page-level aria-current.
  expect(screen.getByRole("heading", { name: "开启今天的法律工作" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "新建会话" })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "外观" }));
  fireEvent.click(screen.getByRole("switch", { name: "减少动态效果" }));

  expect(document.querySelector(".app-frame")?.getAttribute("data-reduce-motion")).toBe("true");
});

test("shows the current version and up-to-date result in software update settings", async () => {
  const client = updaterClient(async () => null);
  render(<App initialHealth={zeroHealth} updaterClient={client} />);

  await waitFor(() => expect(client.checkCalls).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "关于" }));

  expect(screen.getByRole("heading", { name: "软件更新" })).toBeTruthy();
  expect(screen.getByText("当前版本 0.3.0")).toBeTruthy();
  expect(screen.getByText("当前已是最新版本")).toBeTruthy();
  expect(screen.getByRole("button", { name: "检查更新" })).toBeTruthy();
});

test("uses the startup updater client for a second manual check", async () => {
  const client = updaterClient(async () => null);
  render(<App initialHealth={zeroHealth} updaterClient={client} />);

  await waitFor(() => expect(client.checkCalls).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "关于" }));
  fireEvent.click(screen.getByRole("button", { name: "检查更新" }));

  await waitFor(() => expect(client.checkCalls).toBe(2));
  expect(screen.getByText("当前已是最新版本")).toBeTruthy();
});

test("disables the manual check while an update check is pending", async () => {
  const client = updaterClient(
    () => new Promise<AvailableUpdate | null>(() => undefined),
  );
  render(<App initialHealth={zeroHealth} updaterClient={client} />);

  await waitFor(() => expect(client.checkCalls).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "关于" }));

  expect(screen.getByText("当前版本 0.3.0")).toBeTruthy();
  expect(screen.getByText("正在检查更新")).toBeTruthy();
  const checkButton = screen.getByRole("button", { name: "检查更新" });
  expect(checkButton.hasAttribute("disabled")).toBe(true);
  expect(checkButton.getAttribute("data-busy")).toBe("true");
});

test("keeps an automatic check failure quiet until Settings opens", async () => {
  const client: UpdaterClient & { checkCalls: number } = {
    supported: true,
    checkCalls: 0,
    async getCurrentVersion() {
      throw new Error("version unavailable");
    },
    async check() {
      this.checkCalls += 1;
      return null;
    },
    async relaunch() {},
  };
  render(<App initialHealth={zeroHealth} updaterClient={client} />);

  await waitFor(() => expect(screen.queryByText("无法连接更新服务，请稍后重试")).toBeNull());
  expect(screen.queryByText("无法连接更新服务，请稍后重试")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "关于" }));

  expect(screen.getByText("当前版本 未知")).toBeTruthy();
  expect(screen.queryByText("当前版本 开发模式")).toBeNull();
  expect(await screen.findByText("无法连接更新服务，请稍后重试")).toBeTruthy();
  expect(screen.getByRole("button", { name: "重新检查" })).toBeTruthy();
});

test("does not label a supported app as development mode while reading its version", async () => {
  const client: UpdaterClient = {
    supported: true,
    async getCurrentVersion() {
      return new Promise<string>(() => undefined);
    },
    async check() {
      return null;
    },
    async relaunch() {},
  };
  render(<App initialHealth={zeroHealth} updaterClient={client} />);

  await waitFor(() => expect(screen.getByRole("button", { name: "设置" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "关于" }));

  expect(screen.getByText("当前版本 正在读取")).toBeTruthy();
  expect(screen.queryByText("当前版本 开发模式")).toBeNull();
});

test("disables update checks without a busy cursor in unsupported web mode", async () => {
  await loadSettingsStyles();
  const client: UpdaterClient = {
    supported: false,
    async getCurrentVersion() {
      return "开发模式";
    },
    async check() {
      return null;
    },
    async relaunch() {},
  };
  render(<App initialHealth={zeroHealth} updaterClient={client} />);

  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "关于" }));

  expect(screen.getByText("开发模式不检查更新")).toBeTruthy();
  const checkButton = screen.getByRole("button", { name: "检查更新" });
  expect(checkButton.hasAttribute("disabled")).toBe(true);
  expect(checkButton.getAttribute("data-busy")).toBe("false");
  expect(getComputedStyle(checkButton).cursor).toBe("default");
});

test("shows an available version with a direct installation action", async () => {
  const client = updaterClient(async () => availableUpdate);
  render(<App initialHealth={zeroHealth} updaterClient={client} />);

  const updateEntry = await screen.findByRole("button", {
    name: "发现新版本 0.3.1",
  });
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "关于" }));

  expect(screen.getByText("可更新至 0.3.1")).toBeTruthy();
  expect(screen.getByText("更新完成后将自动重启")).toBeTruthy();
  expect(updateEntry).toBeTruthy();
  expect(screen.getByRole("button", { name: "立即更新" })).toBeTruthy();
});


test("installs from Settings without requiring the sidebar", async () => {
  let installs = 0;
  const client = updaterClient(async () => ({
    version: "0.3.1", currentVersion: "0.3.0", date: null, body: "更新说明",
    async install() { installs += 1; },
  }));
  render(<App initialHealth={zeroHealth} updaterClient={client} />);
  await waitFor(() => expect(client.checkCalls).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "关于" }));
  fireEvent.click(await screen.findByRole("button", { name: "立即更新" }));
  await waitFor(() => expect(installs).toBe(1));
  expect(screen.getByText("更新已安装，正在重新启动")).toBeTruthy();
  expect(screen.queryByText("请使用左下角的更新入口完成安装")).toBeNull();
});
