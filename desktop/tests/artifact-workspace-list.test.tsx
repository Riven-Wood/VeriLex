import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";

import { PreviewRail } from "../src/components/PreviewRail";

const { artifactsMock } = vi.hoisted(() => ({
  artifactsMock: vi.fn(),
}));

vi.mock("../src/runtime/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/runtime/api")>();
  return { ...original, listThreadArtifacts: artifactsMock };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const PUBLISHED_ARTIFACT = {
  kind: "artifact" as const,
  id: "artifact-1",
  name: "report.docx",
  published_workspace_id: "published-ws",
  published_relative_path: "out/report.docx",
  operation: "document_create",
  size_bytes: 2048,
  structure_status: "passed" as const,
  render_status: "not_required" as const,
  vision_status: "not_run" as const,
  created_at: "2026-09-01T00:00:00Z",
};

function makeRail(artifactsRevision = 0) {
  const onOpenWorkspaceFile = vi.fn();
  const props = {
    open: true,
    target: { source: "artifacts-list" as const },
    threadId: "thread-1",
    workspaceId: "current-ws",
    artifactsRevision,
    onClose: vi.fn(),
    onOpenWorkspaceFile,
    onOpenList: vi.fn(),
  };
  const view = render(<PreviewRail {...props} />);
  return { ...view, props, onOpenWorkspaceFile };
}

test("opens a published artifact in its own workspace", async () => {
  artifactsMock.mockResolvedValue([PUBLISHED_ARTIFACT]);
  const { container, onOpenWorkspaceFile } = makeRail();

  await waitFor(() =>
    expect(container.querySelector(".preview-artifact-list button")).not.toBeNull(),
  );
  fireEvent.click(container.querySelector(".preview-artifact-list button")!);

  expect(onOpenWorkspaceFile).toHaveBeenCalledWith(
    "published-ws",
    "out/report.docx",
  );
  expect(container.textContent).toContain("文件结构已检查");
});

test("refreshes the open artifact list only when the revision changes", async () => {
  artifactsMock
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce([PUBLISHED_ARTIFACT]);
  const { container, props, rerender } = makeRail(0);

  await waitFor(() => expect(artifactsMock).toHaveBeenCalledTimes(1));
  rerender(<PreviewRail {...props} workspaceId="another-current-ws" />);
  await Promise.resolve();
  expect(artifactsMock).toHaveBeenCalledTimes(1);

  rerender(<PreviewRail {...props} artifactsRevision={1} />);
  await waitFor(() => expect(artifactsMock).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(container.textContent).toContain("report.docx"));
});
