import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type {
  AvailableUpdate,
  UpdateProgress,
  UpdaterClient,
} from "../src/updater/contracts";
import { useUpdateController } from "../src/updater/useUpdateController";

afterEach(cleanup);

interface FakeUpdaterOptions {
  update: AvailableUpdate | null;
  supported?: boolean;
  progress?: UpdateProgress[];
  checkError?: Error;
  installError?: Error;
  installErrors?: Array<Error | null>;
  relaunchError?: Error;
  deferredInstall?: boolean;
}

class FakeUpdaterClient implements UpdaterClient {
  readonly supported: boolean;
  checkCalls = 0;
  installCalls = 0;
  relaunchCalls = 0;
  calls: string[] = [];
  reportProgress: ((progress: UpdateProgress) => void) | null = null;
  completeInstall: (() => void) | null = null;

  constructor(private readonly options: FakeUpdaterOptions) {
    this.supported = options.supported ?? true;
  }

  async getCurrentVersion(): Promise<string> {
    this.calls.push("getCurrentVersion");
    return "0.3.0";
  }

  async check(): Promise<AvailableUpdate | null> {
    this.checkCalls += 1;
    this.calls.push("check");
    if (this.options.checkError) {
      throw this.options.checkError;
    }
    if (this.options.update === null) {
      return null;
    }

    return {
      ...this.options.update,
      install: async (onProgress) => {
        this.installCalls += 1;
        if (this.options.deferredInstall) {
          this.reportProgress = onProgress;
          await new Promise<void>((resolve) => {
            this.completeInstall = resolve;
          });
        }
        for (const progress of this.options.progress ?? []) {
          onProgress(progress);
        }
        const attemptError = this.options.installErrors?.shift();
        if (attemptError) {
          throw attemptError;
        }
        if (this.options.installError) {
          throw this.options.installError;
        }
      },
    };
  }

  async relaunch(): Promise<void> {
    this.relaunchCalls += 1;
    if (this.options.relaunchError) {
      throw this.options.relaunchError;
    }
  }
}

function createFakeUpdaterClient(options: FakeUpdaterOptions): FakeUpdaterClient {
  return new FakeUpdaterClient(options);
}

const sampleUpdate: AvailableUpdate = {
  version: "0.3.1",
  currentVersion: "0.3.0",
  date: "2026-08-11T00:00:00Z",
  body: "修复更新流程",
  async install(_onProgress: (progress: UpdateProgress) => void): Promise<void> {},
};

test("checks once on startup and stays quiet when current", async () => {
  const client = createFakeUpdaterClient({ update: null });
  const { result, rerender } = renderHook(() => useUpdateController(client), {
    reactStrictMode: true,
  });

  await waitFor(() => expect(result.current.state.phase).toBe("up-to-date"));
  rerender();

  expect(client.checkCalls).toBe(1);
  expect(client.calls).toEqual(["getCurrentVersion", "check"]);
  expect(result.current.state.update).toBeNull();
  expect(result.current.state.error).toBe("");
});

test("keeps unsupported clients idle without checking", async () => {
  const client = createFakeUpdaterClient({ update: null, supported: false });
  const { result } = renderHook(() => useUpdateController(client));

  await act(async () => {
    await Promise.resolve();
  });

  expect(result.current.state.phase).toBe("idle");
  expect(client.calls).toEqual([]);
});

test("keeps the current version when the update check fails", async () => {
  const client = createFakeUpdaterClient({
    update: null,
    checkError: new Error("private endpoint details"),
  });
  const { result } = renderHook(() => useUpdateController(client));

  await waitFor(() => expect(result.current.state.phase).toBe("failed"));

  expect(client.calls).toEqual(["getCurrentVersion", "check"]);
  expect(result.current.state.currentVersion).toBe("0.3.0");
  expect(result.current.state.error).toBe("无法连接更新服务，请稍后重试");
});

test("keeps available metadata after the automatic check", async () => {
  const client = createFakeUpdaterClient({ update: sampleUpdate });
  const { result } = renderHook(() => useUpdateController(client));

  await waitFor(() => expect(result.current.state.phase).toBe("available"));

  expect(result.current.state.update).toEqual({
    version: "0.3.1",
    currentVersion: "0.3.0",
    date: "2026-08-11T00:00:00Z",
    body: "修复更新流程",
  });
  expect("install" in (result.current.state.update ?? {})).toBe(false);
});

test("installs directly, reports progress, and relaunches", async () => {
  const client = createFakeUpdaterClient({
    update: sampleUpdate,
    deferredInstall: true,
  });
  const { result } = renderHook(() => useUpdateController(client));
  await waitFor(() => expect(result.current.state.phase).toBe("available"));

  let installPromise: Promise<void> | null = null;
  await act(async () => {
    installPromise = result.current.installUpdate();
    await Promise.resolve();
  });
  await waitFor(() => expect(client.reportProgress).not.toBeNull());

  await act(async () => {
    client.reportProgress?.({
      phase: "downloading",
      downloadedBytes: 4,
      totalBytes: 10,
    });
    await Promise.resolve();
  });

  expect(result.current.state.phase).toBe("downloading");
  expect(result.current.state.progress).toEqual({
    phase: "downloading",
    downloadedBytes: 4,
    totalBytes: 10,
  });

  await act(async () => {
    client.reportProgress?.({
      phase: "installing",
      downloadedBytes: 10,
      totalBytes: 10,
    });
    await Promise.resolve();
  });

  expect(result.current.state.phase).toBe("installing");
  expect(result.current.state.progress).toEqual({
    phase: "installing",
    downloadedBytes: 10,
    totalBytes: 10,
  });

  await act(async () => {
    client.completeInstall?.();
    await installPromise;
  });
  expect(client.installCalls).toBe(1);
  expect(client.relaunchCalls).toBe(1);
  expect(result.current.state.phase).toBe("ready-to-relaunch");
});

test("preserves update metadata when install fails so retry remains possible", async () => {
  const client = createFakeUpdaterClient({
    update: sampleUpdate,
    installError: new Error("disk"),
  });
  const { result } = renderHook(() => useUpdateController(client));
  await waitFor(() => expect(result.current.state.phase).toBe("available"));

  await act(() => result.current.installUpdate());

  expect(result.current.state.phase).toBe("failed");
  expect(result.current.state.update?.version).toBe("0.3.1");
  expect(result.current.state.error).toBe("更新下载或安装失败，请重试");
});

test("ignores overlapping manual checks", async () => {
  const client = createFakeUpdaterClient({ update: null });
  const { result } = renderHook(() => useUpdateController(client));
  await waitFor(() => expect(result.current.state.phase).toBe("up-to-date"));
  const callsBeforeManualCheck = client.checkCalls;

  await act(async () => {
    await Promise.all([
      result.current.checkForUpdates(),
      result.current.checkForUpdates(),
    ]);
  });

  expect(client.checkCalls - callsBeforeManualCheck).toBe(1);
  expect(result.current.state.phase).toBe("up-to-date");
});

test("ignores overlapping install requests", async () => {
  const client = createFakeUpdaterClient({ update: sampleUpdate });
  const { result } = renderHook(() => useUpdateController(client));
  await waitFor(() => expect(result.current.state.phase).toBe("available"));

  await act(async () => {
    await Promise.all([
      result.current.installUpdate(),
      result.current.installUpdate(),
    ]);
  });

  expect(client.installCalls).toBe(1);
  expect(client.relaunchCalls).toBe(1);
});

test("asks for a manual restart when relaunch fails", async () => {
  const client = createFakeUpdaterClient({
    update: sampleUpdate,
    relaunchError: new Error("secret path must not leak"),
  });
  const { result } = renderHook(() => useUpdateController(client));
  await waitFor(() => expect(result.current.state.phase).toBe("available"));

  await act(() => result.current.installUpdate());

  expect(result.current.state.phase).toBe("ready-to-relaunch");
  expect(result.current.state.relaunchRequired).toBe(true);
  expect(result.current.state.error).toBe("更新已安装，请重新启动 VeriLex");
});

test("does not download an installed update again when relaunch fails", async () => {
  const client = createFakeUpdaterClient({
    update: sampleUpdate,
    relaunchError: new Error("restart unavailable"),
  });
  const { result } = renderHook(() => useUpdateController(client));
  await waitFor(() => expect(result.current.state.phase).toBe("available"));

  await act(() => result.current.installUpdate());
  await act(() => result.current.installUpdate());

  expect(client.installCalls).toBe(1);

  await act(() => result.current.checkForUpdates());
  await act(() => result.current.installUpdate());

  expect(client.checkCalls).toBe(1);
  expect(client.installCalls).toBe(1);
  expect(result.current.state.phase).toBe("ready-to-relaunch");
});

test("clears a prior install error when retry succeeds", async () => {
  const client = createFakeUpdaterClient({
    update: sampleUpdate,
    installErrors: [new Error("disk"), null],
  });
  const { result } = renderHook(() => useUpdateController(client));
  await waitFor(() => expect(result.current.state.phase).toBe("available"));

  await act(() => result.current.installUpdate());
  expect(result.current.state.error).toBe("更新下载或安装失败，请重试");

  await act(() => result.current.installUpdate());

  expect(client.installCalls).toBe(2);
  expect(result.current.state.phase).toBe("ready-to-relaunch");
  expect(result.current.state.error).toBe("");
});

test("ignores install progress delivered after unmount", async () => {
  const client = createFakeUpdaterClient({
    update: sampleUpdate,
    deferredInstall: true,
  });
  const { result, unmount } = renderHook(() => useUpdateController(client));
  await waitFor(() => expect(result.current.state.phase).toBe("available"));

  let installPromise: Promise<void> | null = null;
  await act(async () => {
    installPromise = result.current.installUpdate();
    await Promise.resolve();
  });
  expect(client.reportProgress).not.toBeNull();
  unmount();

  let phaseReads = 0;
  const lateProgress = {
    get phase() {
      phaseReads += 1;
      return "installing" as const;
    },
    downloadedBytes: 10,
    totalBytes: 10,
  };
  client.reportProgress?.(lateProgress);
  expect(phaseReads).toBe(0);

  client.completeInstall?.();
  await installPromise;
});


test("retries a failed restart without checking or downloading again", async () => {
  const client = createFakeUpdaterClient({ update: sampleUpdate });
  const restart = vi.spyOn(client, "relaunch").mockRejectedValueOnce(new Error("restart"));
  const { result } = renderHook(() => useUpdateController(client));
  await waitFor(() => expect(result.current.state.phase).toBe("available"));
  await act(() => result.current.installUpdate());
  expect(result.current.state.relaunchRequired).toBe(true);
  await act(() => result.current.installUpdate());
  expect(restart).toHaveBeenCalledTimes(2);
  expect(client.installCalls).toBe(1);
  expect(client.checkCalls).toBe(1);
  expect(result.current.state.relaunchRequired).toBe(false);
});


test("releases superseded native update handles and the last handle on unmount", async () => {
  const first = { ...sampleUpdate, close: vi.fn(async () => {}) };
  const second = { ...sampleUpdate, close: vi.fn(async () => {}) };
  const client = createFakeUpdaterClient({ update: first });
  vi.spyOn(client, "check").mockResolvedValueOnce(first).mockResolvedValueOnce(second);
  const { result, unmount } = renderHook(() => useUpdateController(client), { reactStrictMode: true });
  await waitFor(() => expect(result.current.state.phase).toBe("available"));
  expect(first.close).not.toHaveBeenCalled();
  await act(() => result.current.checkForUpdates());
  expect(first.close).toHaveBeenCalledTimes(1);
  expect(second.close).not.toHaveBeenCalled();
  unmount();
  await Promise.resolve();
  expect(second.close).toHaveBeenCalledTimes(1);
});

test("closes native metadata delivered after unmount", async () => {
  const update = { ...sampleUpdate, close: vi.fn(async () => {}) };
  let finish!: (update: AvailableUpdate) => void;
  const client = createFakeUpdaterClient({ update: null });
  vi.spyOn(client, "check").mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const { unmount } = renderHook(() => useUpdateController(client));
  await waitFor(() => expect(finish).toBeTruthy());
  unmount();
  await act(async () => finish(update));
  expect(update.close).toHaveBeenCalledTimes(1);
});

test("retains metadata for install retry and disposes it after success", async () => {
  const close = vi.fn(async () => {});
  const client = createFakeUpdaterClient({ update: { ...sampleUpdate, close }, installErrors: [new Error("network"), null] });
  const { result } = renderHook(() => useUpdateController(client));
  await waitFor(() => expect(result.current.state.phase).toBe("available"));
  await act(() => result.current.installUpdate());
  expect(close).not.toHaveBeenCalled();
  await act(() => result.current.installUpdate());
  expect(close).toHaveBeenCalledTimes(1);
});
