import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";

import { PreviewRail } from "../src/components/PreviewRail";

const {
  getWorkspaceFilePreview: previewMock,
  openWorkspacePath: openMock,
  listThreadArtifacts: artifactsMock,
} = vi.hoisted(() => ({
  getWorkspaceFilePreview: vi.fn(),
  openWorkspacePath: vi.fn(),
  listThreadArtifacts: vi.fn(),
}));

vi.mock("../src/runtime/api", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../src/runtime/api")>();
  return {
    ...original,
    getWorkspaceFilePreview: previewMock,
    openWorkspacePath: openMock,
    listThreadArtifacts: artifactsMock,
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const OPINION_ARTIFACT = {
  kind: "artifact" as const,
  id: "artifact-opinion",
  name: "意见.docx",
  published_workspace_id: "artifact-ws",
  published_relative_path: "意见.docx",
  operation: "document_create",
  size_bytes: 4096,
  structure_status: "passed" as const,
  render_status: "not_required" as const,
  vision_status: "not_run" as const,
  created_at: "2026-09-01T00:00:00Z",
};

function makeRail(target: Record<string, unknown>) {
  const onOpenWorkspaceFile = vi.fn();
  const view = render(
    <PreviewRail
      open={true}
      target={target}
      threadId="thread-1"
      workspaceId="current-ws"
      onClose={vi.fn()}
      onOpenWorkspaceFile={onOpenWorkspaceFile}
      onOpenList={vi.fn()}
    />,
  );
  return { ...view, onOpenWorkspaceFile };
}

test("published docx opens through its own workspace from the session list", async () => {
  artifactsMock.mockResolvedValue([OPINION_ARTIFACT]);
  const { container, onOpenWorkspaceFile } = makeRail({
    source: "artifacts-list",
  });

  await waitFor(() =>
    expect(container.querySelector(".preview-artifact-list button")).not.toBeNull(),
  );
  expect(container.textContent).toContain("生成");
  expect(container.textContent).toContain("文件结构已检查");
  fireEvent.click(container.querySelector(".preview-artifact-list button")!);

  expect(onOpenWorkspaceFile).toHaveBeenCalledWith("artifact-ws", "意见.docx");
  expect(onOpenWorkspaceFile).not.toHaveBeenCalledWith(
    "current-ws",
    expect.anything(),
  );
});

test("docx preview states the in-app limit and offers system open and reveal", async () => {
  previewMock.mockResolvedValue({
    kind: "unsupported",
    name: "意见.docx",
    path: "意见.docx",
    size_bytes: 4096,
    modified_at: "2026-09-01T00:00:00Z",
    truncated: false,
  });
  openMock.mockResolvedValue({ path: "意见.docx", mode: "open" });
  const { container } = makeRail({
    source: "workspace",
    workspaceId: "artifact-ws",
    path: "意见.docx",
  });

  await waitFor(() =>
    expect(container.querySelector(".preview-unsupported")).not.toBeNull(),
  );
  expect(container.textContent).toContain("暂不支持在应用内预览此格式");
  const buttons = container.querySelectorAll(
    ".preview-system-actions button",
  );
  expect(buttons).toHaveLength(2);
  expect(buttons[0].textContent).toContain("用默认应用打开");
  expect(buttons[1].textContent).toContain("在访达中显示");

  fireEvent.click(buttons[0]);
  await waitFor(() => expect(openMock).toHaveBeenCalled());
  expect(openMock).toHaveBeenCalledWith("artifact-ws", {
    path: "意见.docx",
    mode: "open",
  });
});
