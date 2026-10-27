import { getVersion } from "@tauri-apps/api/app";
import { relaunch } from "@tauri-apps/plugin-process";
import { check } from "@tauri-apps/plugin-updater";
import { afterEach, describe, expect, test, vi } from "vitest";

import { tauriUpdaterClient } from "../src/updater/tauriUpdaterClient";

vi.mock("@tauri-apps/api/app", () => ({
  getVersion: vi.fn(async () => "0.3.0"),
}));
vi.mock("@tauri-apps/plugin-process", () => ({
  relaunch: vi.fn(async () => undefined),
}));
vi.mock("@tauri-apps/plugin-updater", () => ({
  check: vi.fn(),
}));

type NativeDownloadEvent =
  | { event: "Started"; data: { contentLength?: number } }
  | { event: "Progress"; data: { chunkLength: number } }
  | { event: "Finished"; data?: undefined };

interface NativeUpdateFixtureOptions {
  date?: string;
  body?: string;
  events?: NativeDownloadEvent[];
}

function createNativeUpdate({
  date = "2026-08-12T00:00:00Z",
  body = "修复更新流程",
  events = [],
}: NativeUpdateFixtureOptions = {}) {
  return {
    rid: 1,
    available: true,
    currentVersion: "0.3.0",
    version: "0.3.1",
    date,
    body,
    rawJson: {},
    close: vi.fn(async () => undefined),
    download: vi.fn(async () => undefined),
    install: vi.fn(async () => undefined),
    downloadAndInstall: vi.fn(
      async (onEvent?: (event: NativeDownloadEvent) => void) => {
        for (const event of events) {
          onEvent?.(event);
        }
      },
    ),
  };
}

const tauriGlobal = globalThis as typeof globalThis & {
  __TAURI_INTERNALS__?: object;
};

afterEach(() => {
  delete tauriGlobal.__TAURI_INTERNALS__;
  vi.clearAllMocks();
});

describe("tauriUpdaterClient", () => {
  test("stays inert outside the Tauri runtime", async () => {
    expect(tauriUpdaterClient.supported).toBe(false);
    await expect(tauriUpdaterClient.getCurrentVersion()).resolves.toBe("开发模式");
    await expect(tauriUpdaterClient.check()).resolves.toBeNull();
    await expect(tauriUpdaterClient.relaunch()).resolves.toBeUndefined();

    expect(getVersion).not.toHaveBeenCalled();
    expect(check).not.toHaveBeenCalled();
    expect(relaunch).not.toHaveBeenCalled();
  });

  test("checks with a 15 second timeout and maps update metadata", async () => {
    tauriGlobal.__TAURI_INTERNALS__ = {};
    vi.mocked(check).mockResolvedValue(createNativeUpdate() as never);

    expect(tauriUpdaterClient.supported).toBe(true);
    await expect(tauriUpdaterClient.getCurrentVersion()).resolves.toBe("0.3.0");
    const update = await tauriUpdaterClient.check();

    expect(check).toHaveBeenCalledWith({ timeout: 15_000 });
    expect(update).toMatchObject({
      version: "0.3.1",
      currentVersion: "0.3.0",
      date: "2026-08-12T00:00:00Z",
      body: "修复更新流程",
    });
  });

  test("accumulates progress chunks before reporting installation", async () => {
    tauriGlobal.__TAURI_INTERNALS__ = {};
    vi.mocked(check).mockResolvedValue(
      createNativeUpdate({
        events: [
          { event: "Started", data: { contentLength: 10 } },
          { event: "Progress", data: { chunkLength: 4 } },
          { event: "Progress", data: { chunkLength: 6 } },
          { event: "Finished" },
        ],
      }) as never,
    );

    const update = await tauriUpdaterClient.check();
    const progress: unknown[] = [];
    await update?.install((event) => progress.push(event));

    expect(progress).toEqual([
      { phase: "downloading", downloadedBytes: 0, totalBytes: 10 },
      { phase: "downloading", downloadedBytes: 4, totalBytes: 10 },
      { phase: "downloading", downloadedBytes: 10, totalBytes: 10 },
      { phase: "installing", downloadedBytes: 10, totalBytes: 10 },
    ]);
  });

  test("keeps an unknown total size as null", async () => {
    tauriGlobal.__TAURI_INTERNALS__ = {};
    vi.mocked(check).mockResolvedValue(
      createNativeUpdate({
        events: [
          { event: "Started", data: {} },
          { event: "Progress", data: { chunkLength: 5 } },
        ],
      }) as never,
    );

    const update = await tauriUpdaterClient.check();
    const progress: unknown[] = [];
    await update?.install((event) => progress.push(event));

    expect(progress).toEqual([
      { phase: "downloading", downloadedBytes: 0, totalBytes: null },
      { phase: "downloading", downloadedBytes: 5, totalBytes: null },
    ]);
  });

  test("delegates relaunch exactly once in Tauri", async () => {
    tauriGlobal.__TAURI_INTERNALS__ = {};

    await tauriUpdaterClient.relaunch();

    expect(relaunch).toHaveBeenCalledTimes(1);
  });
});


test("bounds the download request separately from the short check and exposes native disposal", async () => {
  tauriGlobal.__TAURI_INTERNALS__ = {};
  const native = createNativeUpdate();
  vi.mocked(check).mockResolvedValue(native as never);
  const update = await tauriUpdaterClient.check();
  await update?.install(() => {});
  expect(native.downloadAndInstall).toHaveBeenCalledWith(expect.any(Function), { timeout: 1_800_000 });
  await update?.close?.();
  expect(native.close).toHaveBeenCalledTimes(1);
});

test("reports a missing release manifest without claiming the installed version is latest", async () => {
  tauriGlobal.__TAURI_INTERNALS__ = {};
  vi.mocked(check).mockRejectedValue("Could not fetch a valid release JSON from the remote");
  await expect(tauriUpdaterClient.check()).rejects.toThrow("更新服务暂未提供可用的更新信息");
});
