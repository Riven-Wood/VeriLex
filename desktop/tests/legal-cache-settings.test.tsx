import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { LegalCacheSettings } from "../src/components/LegalCacheSettings";

const GET_STATS = vi.fn();
const CLEAR = vi.fn();

vi.mock("../src/runtime/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/runtime/api")>();
  return {
    ...original,
    getLegalCacheStats: (...args: unknown[]) => GET_STATS(...args),
    clearLegalCache: (...args: unknown[]) => CLEAR(...args),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("空缓存显示 0 条且无清除按钮", async () => {
  GET_STATS.mockResolvedValue({ count: 0, oldestFetchedAt: null });

  render(<LegalCacheSettings />);

  await waitFor(() => {
    expect(screen.getByText(/0 条/)).toBeTruthy();
  });
  expect(screen.queryByRole("button", { name: "清除法条缓存" })).toBeNull();
});

test("有缓存显示条数与最早读取时间，两步确认后清除并刷新", async () => {
  GET_STATS.mockResolvedValue({
    count: 3,
    oldestFetchedAt: "2026-09-04T01:00:00Z",
  });
  CLEAR.mockResolvedValue(undefined);
  GET_STATS.mockResolvedValueOnce({ count: 3, oldestFetchedAt: "2026-09-04T01:00:00Z" })
    .mockResolvedValue({ count: 0, oldestFetchedAt: null });

  render(<LegalCacheSettings />);

  await waitFor(() => {
    expect(screen.getByText(/3 条/)).toBeTruthy();
  });
  expect(screen.getByText(/2026-09-04/)).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "清除法条缓存" }));

  expect(screen.getByText(/清除后不可恢复/)).toBeTruthy();
  expect(CLEAR).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "确认清除" }));

  await waitFor(() => {
    expect(CLEAR).toHaveBeenCalledTimes(1);
  });
  await waitFor(() => {
    expect(screen.getByText(/0 条/)).toBeTruthy();
  });
});

test("确认前可取消，不触发清除", async () => {
  GET_STATS.mockResolvedValue({ count: 2, oldestFetchedAt: "2026-09-04T01:00:00Z" });

  render(<LegalCacheSettings />);

  await waitFor(() => {
    expect(screen.getByRole("button", { name: "清除法条缓存" })).toBeTruthy();
  });
  fireEvent.click(screen.getByRole("button", { name: "清除法条缓存" }));
  fireEvent.click(screen.getByRole("button", { name: "取消" }));

  expect(CLEAR).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "清除法条缓存" })).toBeTruthy();
});

test("统计加载失败就地提示且可重试", async () => {
  GET_STATS.mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ count: 1, oldestFetchedAt: null });

  render(<LegalCacheSettings />);

  await waitFor(() => {
    expect(screen.getByText(/暂时无法读取/)).toBeTruthy();
  });

  fireEvent.click(screen.getByRole("button", { name: "重试" }));

  await waitFor(() => {
    expect(screen.getByText(/1 条/)).toBeTruthy();
  });
});
