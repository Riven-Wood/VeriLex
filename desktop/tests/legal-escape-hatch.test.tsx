import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { LegalEscapeHatch } from "../src/components/LegalEscapeHatch";

const OPEN_PAGE = vi.fn();

vi.mock("../src/runtime/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/runtime/api")>();
  return {
    ...original,
    openLegalOfficialPage: (...args: unknown[]) => OPEN_PAGE(...args),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("失败卡下方提供用系统浏览器打开官方页面按钮并调用固定端点", async () => {
  OPEN_PAGE.mockResolvedValue(undefined);
  render(<LegalEscapeHatch />);

  const button = screen.getByRole("button", { name: "用系统浏览器打开官方页面" });
  fireEvent.click(button);

  await waitFor(() => {
    expect(OPEN_PAGE).toHaveBeenCalledTimes(1);
  });
  expect(screen.queryByRole("alert")).toBeNull();
});

test("打开失败时就地提示，不弹系统外错误细节", async () => {
  OPEN_PAGE.mockRejectedValue(new Error("boom"));
  render(<LegalEscapeHatch />);

  fireEvent.click(screen.getByRole("button", { name: "用系统浏览器打开官方页面" }));

  await waitFor(() => {
    expect(screen.getByRole("alert")).toBeTruthy();
  });
  expect(screen.getByRole("alert").textContent).toContain("没有打开");
});
