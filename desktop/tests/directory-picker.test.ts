import { expect, test, vi } from "vitest";

import { pickWorkspaceDirectory } from "../src/platform/directoryPicker";

test("opens one directory and returns the selected path", async () => {
  const open = vi.fn().mockResolvedValue("/Cases/A");

  await expect(pickWorkspaceDirectory(open)).resolves.toBe("/Cases/A");
  expect(open).toHaveBeenCalledWith({ directory: true, multiple: false });
});

test("returns null when the user cancels directory selection", async () => {
  const open = vi.fn().mockResolvedValue(null);

  await expect(pickWorkspaceDirectory(open)).resolves.toBeNull();
});

test("maps malformed or failed dialog results to a safe visible error", async () => {
  const malformed = vi.fn().mockResolvedValue(["/Cases/A"]);
  const failed = vi.fn().mockRejectedValue(new Error("private system detail"));

  await expect(pickWorkspaceDirectory(malformed)).rejects.toThrow(
    "无法选择工作区目录，请重试。",
  );
  await expect(pickWorkspaceDirectory(failed)).rejects.toThrow(
    "无法选择工作区目录，请重试。",
  );
});
