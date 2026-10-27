import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { StrictMode } from "react";

import type { HealthResponse } from "../src/runtime/contracts";

vi.mock("../src/runtime/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/runtime/api")>();
  return { ...actual, getHealth: vi.fn() };
});

import { App } from "../src/app/App";
import { getHealth } from "../src/runtime/api";

const zeroHealth: HealthResponse = {
  ok: true,
  service: "verilex-runtime",
  version: "0.3.0",
  storage: "local",
  cloud_backend: false,
  configured: { models: 0, mcp_servers: 0, skills: 0, folders: 0 },
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.resetAllMocks();
});

test("reports an unavailable local runtime without inventing an offline demo", async () => {
  vi.useFakeTimers();
  vi.mocked(getHealth).mockRejectedValue(new Error("本地 Runtime 连接失败"));

  render(<App />);

  expect(screen.getByText("正在启动本地 Runtime")).toBeTruthy();
  await act(async () => {
    await vi.runAllTimersAsync();
  });
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toContain("本地 Runtime 未能启动");
  expect(alert.textContent).toContain("本地 Runtime 连接失败");
  expect(screen.queryByText("尚未连接模型")).toBeNull();
});

test("lets the user restart the startup probe after a cold-start failure", async () => {
  vi.useFakeTimers();
  vi.mocked(getHealth).mockRejectedValue(new Error("本地 Runtime 连接失败"));

  render(<App />);

  await act(async () => {
    await vi.runAllTimersAsync();
  });
  const retryButton = screen.getByRole("button", { name: "重新检查本机后台状态" });

  vi.mocked(getHealth).mockResolvedValue(zeroHealth);
  await act(async () => {
    fireEvent.click(retryButton);
    await vi.runAllTimersAsync();
  });

  expect(screen.getByRole("heading", { name: "开启今天的法律工作" })).toBeTruthy();
});

test("transitions from startup to the honest zero-configuration state", async () => {
  vi.mocked(getHealth).mockResolvedValueOnce(zeroHealth);

  render(<App />);

  expect(screen.getByText("正在启动本地 Runtime")).toBeTruthy();
  expect(await screen.findByRole("heading", { name: "开启今天的法律工作" })).toBeTruthy();
  // Counts moved to their own settings pages; the sidebar shows none.
  expect(screen.queryByText(/^MCP \d+$/)).toBeNull();
  expect(screen.queryByText(/^Skill \d+$/)).toBeNull();
  expect(screen.queryByText(/模型已连接|已连接模型/)).toBeNull();
});

test("retries while the supervised runtime is still starting", async () => {
  vi.useFakeTimers();
  vi.mocked(getHealth)
    .mockRejectedValueOnce(new Error("Runtime 尚未监听"))
    .mockResolvedValueOnce(zeroHealth);

  render(<App />);

  expect(screen.getByText("正在启动本地 Runtime")).toBeTruthy();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(250);
  });
  expect(
    screen.getByRole("heading", { name: "开启今天的法律工作" }),
  ).toBeTruthy();
  expect(getHealth).toHaveBeenCalledTimes(2);
});

test("times out a hung health attempt and retries", async () => {
  vi.useFakeTimers();
  vi.mocked(getHealth).mockImplementation(
    (signal) =>
      new Promise((_resolve, reject) => {
        signal?.addEventListener(
          "abort",
          () => reject(signal.reason ?? new DOMException("aborted", "AbortError")),
          { once: true },
        );
      }),
  );

  render(<App />);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(2_750);
  });
  expect(getHealth).toHaveBeenCalledTimes(2);
  expect(vi.mocked(getHealth).mock.calls[0][0]).toBeInstanceOf(AbortSignal);
});

test("starts a fresh startup probe after StrictMode aborts the first attempt", async () => {
  vi.mocked(getHealth)
    .mockImplementationOnce(
      () =>
        // The first attempt is aborted mid-flight by StrictMode's simulated
        // unmount and its rejection may settle only after the remount.
        new Promise(() => {}),
    )
    .mockResolvedValue(zeroHealth);

  render(
    <StrictMode>
      <App />
    </StrictMode>,
  );

  expect(await screen.findByRole("heading", { name: "开启今天的法律工作" })).toBeTruthy();
  expect(getHealth).toHaveBeenCalledTimes(2);
});
