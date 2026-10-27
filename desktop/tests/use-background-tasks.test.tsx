import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { useBackgroundTasks } from "../src/hooks/useBackgroundTasks";
import type { BackgroundTaskRecord } from "../src/runtime/contracts";
import * as api from "../src/runtime/api";

vi.mock("../src/runtime/api", () => ({
  getBackgroundTaskOutput: vi.fn(),
  stopBackgroundTask: vi.fn(),
}));

const outputMock = vi.mocked(api.getBackgroundTaskOutput);
const stopMock = vi.mocked(api.stopBackgroundTask);

const runningTask = (id: string): BackgroundTaskRecord => ({
  id,
  toolCallId: `tc-${id}`,
  command: "python3 hold.py",
  description: "hold",
  status: "running",
  exitCode: null,
  omittedBytes: 0,
  startedAt: "2026-08-25T00:00:00Z",
  finishedAt: null,
  createdAt: "2026-08-25T00:00:00Z",
});

const completedTask = (id: string): BackgroundTaskRecord => ({
  ...runningTask(id),
  status: "completed",
  exitCode: 0,
  finishedAt: "2026-08-25T00:00:05Z",
});

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

test("polls running tasks every second and advances the cursor", async () => {
  outputMock
    .mockResolvedValueOnce({ cursor: 4, text: "tick", omittedBytes: 0, status: "running" })
    .mockResolvedValue({ cursor: 9, text: "-tock", omittedBytes: 0, status: "running" });

  const { result } = renderHook(() =>
    useBackgroundTasks("t1", [runningTask("bt-1")], true),
  );

  expect(outputMock).not.toHaveBeenCalled();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(outputMock).toHaveBeenCalledWith("t1", "bt-1", 0);
  expect(result.current.states["bt-1"].outputText).toBe("tick");

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(outputMock).toHaveBeenLastCalledWith("t1", "bt-1", 4);
  expect(result.current.states["bt-1"].outputText).toBe("tick-tock");
});

test("stops polling once the task reports a terminal status", async () => {
  outputMock.mockResolvedValue({
    cursor: 4,
    text: "done",
    omittedBytes: 0,
    status: "completed",
  });

  renderHook(() => useBackgroundTasks("t1", [runningTask("bt-1")], true));

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(outputMock).toHaveBeenCalledTimes(1);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(outputMock).toHaveBeenCalledTimes(1);
});

test("does not poll terminal tasks or when disabled", async () => {
  const { rerender } = renderHook(
    ({ enabled, task }: { enabled: boolean; task: BackgroundTaskRecord }) =>
      useBackgroundTasks("t1", [task], enabled),
    { initialProps: { enabled: true, task: completedTask("bt-1") } },
  );

  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500);
  });
  expect(outputMock).not.toHaveBeenCalled();

  rerender({ enabled: false, task: runningTask("bt-2") });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500);
  });
  expect(outputMock).not.toHaveBeenCalled();
});

test("keeps accumulated output bounded to roughly 64 KiB", async () => {
  const bigChunk = "x".repeat(40 * 1024);
  outputMock.mockResolvedValue({
    cursor: bigChunk.length,
    text: bigChunk,
    omittedBytes: 0,
    status: "running",
  });

  const { result } = renderHook(() =>
    useBackgroundTasks("t1", [runningTask("bt-1")], true),
  );

  for (let round = 0; round < 6; round += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
  }

  expect(result.current.states["bt-1"].outputText.length).toBeLessThanOrEqual(
    64 * 1024 + 1024,
  );
});

test("unmount clears the polling timer", async () => {
  outputMock.mockResolvedValue({
    cursor: 1,
    text: "x",
    omittedBytes: 0,
    status: "running",
  });

  const { unmount } = renderHook(() =>
    useBackgroundTasks("t1", [runningTask("bt-1")], true),
  );

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(outputMock).toHaveBeenCalledTimes(1);
  unmount();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(outputMock).toHaveBeenCalledTimes(1);
});

test("request failures surface a safe error without crashing", async () => {
  outputMock.mockRejectedValue(new Error("boom"));

  const { result } = renderHook(() =>
    useBackgroundTasks("t1", [runningTask("bt-1")], true),
  );

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });

  expect(result.current.states["bt-1"].error).toContain("后台命令状态暂时无法获取");
  expect(result.current.states["bt-1"].outputText).toBe("");
});

test("stopTask calls the stop endpoint", async () => {
  stopMock.mockResolvedValue({ taskId: "bt-1", status: "stopped" });

  const { result } = renderHook(() =>
    useBackgroundTasks("t1", [runningTask("bt-1")], true),
  );

  await act(async () => {
    await result.current.stopTask("bt-1");
  });

  expect(stopMock).toHaveBeenCalledWith("t1", "bt-1");
});
