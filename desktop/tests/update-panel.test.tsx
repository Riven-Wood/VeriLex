import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "../src/app/App";
import { Sidebar } from "../src/components/Sidebar";
import { UpdatePanel } from "../src/components/UpdatePanel";
import type {
  AvailableUpdate,
  UpdateController,
  UpdateState,
  UpdaterClient,
} from "../src/updater/contracts";
import type { HealthResponse } from "../src/runtime/contracts";

afterEach(() => {
  cleanup();
  document.querySelector("style[data-update-panel-test]")?.remove();
});

const zeroHealth: HealthResponse = {
  ok: true,
  service: "verilex-runtime",
  version: "0.3.0",
  storage: "local",
  cloud_backend: false,
  configured: { models: 0, mcp_servers: 0, skills: 0, folders: 0 },
};

const availableUpdate: AvailableUpdate = {
  version: "0.3.1",
  currentVersion: "0.3.0",
  date: "2026-08-11T00:00:00Z",
  body: "修复更新流程",
  async install() {},
};

const availableClient: UpdaterClient = {
  supported: true,
  async getCurrentVersion() {
    return "0.3.0";
  },
  async check() {
    return availableUpdate;
  },
  async relaunch() {},
};

function controllerFor(overrides: Partial<UpdateState>): UpdateController {
  return {
    state: {
      supported: true,
      phase: "available",
      currentVersion: "0.3.0",
      update: {
        version: "0.3.1",
        currentVersion: "0.3.0",
        date: "2026-08-11T00:00:00Z",
        body: "修复更新流程",
      },
      progress: null,
      error: "",
      relaunchRequired: false,
      ...overrides,
    },
    checkForUpdates: vi.fn(async () => undefined),
    installUpdate: vi.fn(async () => undefined),
  };
}

test("renders no update entry without available metadata", () => {
  render(
    <UpdatePanel
      controller={controllerFor({ phase: "up-to-date", update: null })}
    />,
  );

  expect(screen.queryByRole("button", { name: /发现新版本/ })).toBeNull();
});

test("places the update entry before Settings when an update exists", async () => {
  render(<App initialHealth={zeroHealth} updaterClient={availableClient} />);

  const updateButton = await screen.findByRole("button", {
    name: "发现新版本 0.3.1",
  });
  const settingsButton = screen.getByRole("button", { name: "设置" });

  expect(
    updateButton.compareDocumentPosition(settingsButton)
      & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

test("keeps the anchored panel visible while internal regions own overflow", async () => {
  const shellCss = await readFile(
    resolve(process.cwd(), "src/styles/shell.css"),
    "utf8",
  );
  const stylesheet = document.createElement("style");
  stylesheet.dataset.updatePanelTest = "true";
  stylesheet.textContent = shellCss;
  document.head.append(stylesheet);
  render(
    <Sidebar
      activeView="agent"
      configured={zeroHealth.configured}
      threads={[]}
      activeThreadId={null}
      updates={controllerFor({})}
      onNavigate={() => undefined}
      onNewThread={() => undefined}
      onSelectThread={() => undefined}
    />,
  );
  const trigger = screen.getByRole("button", { name: "发现新版本 0.3.1" });
  fireEvent.click(trigger);

  const sidebar = screen.getByRole("complementary", { name: "主导航" });
  const panel = screen.getByRole("region", { name: "软件更新" });
  const context = screen.getByRole("region", { name: "会话" });

  expect(getComputedStyle(sidebar).overflow).toBe("visible");
  expect(getComputedStyle(context).overflow).toBe("auto");
  expect(getComputedStyle(panel).position).toBe("absolute");
  expect(getComputedStyle(panel).overflow).toBe("auto");
  expect(getComputedStyle(panel).maxHeight).not.toBe("none");
});

test("exposes expanded state and a named update region", () => {
  render(<UpdatePanel controller={controllerFor({})} />);
  const trigger = screen.getByRole("button", { name: "发现新版本 0.3.1" });

  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("region", { name: "软件更新" })).toBeNull();

  fireEvent.click(trigger);

  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByRole("region", { name: "软件更新" })).toBeTruthy();
});

test("shows the version transition, date, notes control, and install action", () => {
  render(<UpdatePanel controller={controllerFor({})} />);

  fireEvent.click(screen.getByRole("button", { name: "发现新版本 0.3.1" }));

  expect(screen.getByText("0.3.0 → 0.3.1")).toBeTruthy();
  expect(screen.getByText("2026年8月11日")).toBeTruthy();
  expect(screen.getByRole("button", { name: "查看更新说明" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "立即更新" })).toBeTruthy();
});

test("installs directly without requiring the notes view", () => {
  const controller = controllerFor({});
  render(<UpdatePanel controller={controller} />);
  fireEvent.click(screen.getByRole("button", { name: "发现新版本 0.3.1" }));

  fireEvent.click(screen.getByRole("button", { name: "立即更新" }));

  expect(controller.installUpdate).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "返回" })).toBeNull();
});

test("shows release notes as text and returns to the summary", () => {
  const unsafeNotes = "修复更新流程\n<img src=x onerror=alert(1)>";
  render(
    <UpdatePanel
      controller={controllerFor({
        update: {
          version: "0.3.1",
          currentVersion: "0.3.0",
          date: "2026-08-11T00:00:00Z",
          body: unsafeNotes,
        },
      })}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "发现新版本 0.3.1" }));

  fireEvent.click(screen.getByRole("button", { name: "查看更新说明" }));

  expect(document.querySelector(".update-notes")?.textContent).toBe(unsafeNotes);
  expect(document.querySelector("img")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "返回" }));
  expect(screen.getByRole("button", { name: "立即更新" })).toBeTruthy();
});

test("closes and returns to the summary when update identity changes", () => {
  const { rerender } = render(<UpdatePanel controller={controllerFor({})} />);
  fireEvent.click(screen.getByRole("button", { name: "发现新版本 0.3.1" }));
  fireEvent.click(screen.getByRole("button", { name: "查看更新说明" }));
  expect(screen.getByRole("button", { name: "返回" })).toBeTruthy();

  rerender(
    <UpdatePanel
      controller={controllerFor({
        update: {
          version: "0.4.0",
          currentVersion: "0.3.1",
          date: "2026-09-01T00:00:00Z",
          body: "新的更新说明",
        },
      })}
    />,
  );

  expect(screen.queryByRole("region", { name: "软件更新" })).toBeNull();
  const nextTrigger = screen.getByRole("button", { name: "发现新版本 0.4.0" });
  expect(nextTrigger.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(nextTrigger);
  expect(screen.getByText("0.3.1 → 0.4.0")).toBeTruthy();
  expect(screen.getByRole("button", { name: "查看更新说明" })).toBeTruthy();
});

test("keeps the notes view open for progress changes to the same update", () => {
  const { rerender } = render(<UpdatePanel controller={controllerFor({})} />);
  fireEvent.click(screen.getByRole("button", { name: "发现新版本 0.3.1" }));
  fireEvent.click(screen.getByRole("button", { name: "查看更新说明" }));

  rerender(
    <UpdatePanel
      controller={controllerFor({
        phase: "downloading",
        progress: {
          phase: "downloading",
          downloadedBytes: 4,
          totalBytes: 10,
        },
      })}
    />,
  );

  expect(screen.getByRole("region", { name: "软件更新" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "返回" })).toBeTruthy();
});

test("closes with Escape while leaving the update trigger visible", () => {
  render(<UpdatePanel controller={controllerFor({})} />);
  const trigger = screen.getByRole("button", { name: "发现新版本 0.3.1" });
  fireEvent.click(trigger);

  fireEvent.keyDown(document, { key: "Escape" });

  expect(screen.queryByRole("region", { name: "软件更新" })).toBeNull();
  expect(trigger).toBeTruthy();
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(trigger);
});

test("closes with the accessible close button", () => {
  render(<UpdatePanel controller={controllerFor({})} />);
  const trigger = screen.getByRole("button", { name: "发现新版本 0.3.1" });
  fireEvent.click(trigger);

  fireEvent.click(screen.getByRole("button", { name: "关闭软件更新" }));

  expect(screen.queryByRole("region", { name: "软件更新" })).toBeNull();
  expect(trigger).toBeTruthy();
  expect(document.activeElement).toBe(trigger);
});

test("shows byte progress and disables duplicate install while downloading", () => {
  const controller = controllerFor({
    phase: "downloading",
    progress: {
      phase: "downloading",
      downloadedBytes: 4,
      totalBytes: 10,
    },
  });
  render(<UpdatePanel controller={controller} />);
  fireEvent.click(screen.getByRole("button", { name: "发现新版本 0.3.1" }));

  expect(screen.getByText("已下载 4 B / 10 B")).toBeTruthy();
  expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("40");
  expect(screen.getByRole("button", { name: "正在下载" }).hasAttribute("disabled"))
    .toBe(true);
});

test("shows a sanitized install failure and retries", () => {
  const controller = controllerFor({
    phase: "failed",
    error: "更新下载或安装失败，请重试",
  });
  render(<UpdatePanel controller={controller} />);
  fireEvent.click(screen.getByRole("button", { name: "发现新版本 0.3.1" }));

  expect(screen.getByRole("alert").textContent).toBe("更新下载或安装失败，请重试");
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(controller.installUpdate).toHaveBeenCalledTimes(1);
});

test("shows the manual restart instruction after relaunch failure", () => {
  render(
    <UpdatePanel
      controller={controllerFor({
        phase: "ready-to-relaunch",
        error: "更新已安装，请手动重启 VeriLex",
        relaunchRequired: true,
      })}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "更新已安装 0.3.1" }));

  expect(screen.getByRole("alert").textContent).toBe(
    "更新已安装，请手动重启 VeriLex",
  );
  expect(screen.getByRole("button", { name: "重新启动" })).toBeTruthy();
});
