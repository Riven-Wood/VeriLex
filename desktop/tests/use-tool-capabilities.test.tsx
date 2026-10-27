import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { useToolCapabilities } from "../src/hooks/useToolCapabilities";
import type { ToolCapabilityRecord } from "../src/runtime/contracts";

afterEach(cleanup);

const records: ToolCapabilityRecord[] = [
  {
    name: "read_file",
    description: "Read a UTF-8 text file inside the authorized workspace.",
    source: "builtin",
    source_name: null,
    category: "files",
    scopes: ["read"],
    risk_level: "low",
    default_approval: "automatic",
    approval_behavior: "risk_default",
    requires_workspace: true,
    availability: "available",
    availability_reason: null,
  },
];

test("loads the tool catalog from loading to ready", async () => {
  const loader = vi.fn().mockResolvedValue(records);

  const { result } = renderHook(() => useToolCapabilities({ loader }));

  expect(result.current).toMatchObject({
    status: "loading",
    tools: [],
    error: "",
  });
  await waitFor(() => expect(result.current.status).toBe("ready"));
  expect(result.current.tools).toEqual(records);
  expect(result.current.error).toBe("");
});

test("replaces loader details with a safe catalog error", async () => {
  const loader = vi.fn().mockRejectedValue(
    new Error("private endpoint http://127.0.0.1:8766 failed"),
  );

  const { result } = renderHook(() => useToolCapabilities({ loader }));

  await waitFor(() => expect(result.current.status).toBe("error"));
  expect(result.current.tools).toEqual([]);
  expect(result.current.error).toBe("无法读取工具目录。");
  expect(result.current.error).not.toContain("127.0.0.1");
});

test("retry clears the old error and performs a fresh load", async () => {
  let resolveRetry: ((value: ToolCapabilityRecord[]) => void) | undefined;
  const loader = vi.fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockImplementationOnce(
      () => new Promise<ToolCapabilityRecord[]>((resolve) => {
        resolveRetry = resolve;
      }),
    );
  const { result } = renderHook(() => useToolCapabilities({ loader }));
  await waitFor(() => expect(result.current.status).toBe("error"));

  act(() => result.current.retry());

  await waitFor(() => expect(result.current.status).toBe("loading"));
  expect(result.current.error).toBe("");
  expect(loader).toHaveBeenCalledTimes(2);

  await act(async () => {
    resolveRetry?.(records);
    await Promise.resolve();
  });
  await waitFor(() => expect(result.current.status).toBe("ready"));
  expect(result.current.tools).toEqual(records);
});

test("unmount aborts the request and ignores its later result", async () => {
  let requestSignal: AbortSignal | undefined;
  let resolveRequest: ((value: ToolCapabilityRecord[]) => void) | undefined;
  const loader = (signal?: AbortSignal) => {
    requestSignal = signal;
    return new Promise<ToolCapabilityRecord[]>((resolve) => {
      resolveRequest = resolve;
    });
  };
  const { result, unmount } = renderHook(() => useToolCapabilities({ loader }));
  expect(result.current.status).toBe("loading");
  expect(requestSignal?.aborted).toBe(false);

  unmount();
  expect(requestSignal?.aborted).toBe(true);

  await act(async () => {
    resolveRequest?.(records);
    await Promise.resolve();
  });
  expect(result.current.status).toBe("loading");
  expect(result.current.tools).toEqual([]);
});
