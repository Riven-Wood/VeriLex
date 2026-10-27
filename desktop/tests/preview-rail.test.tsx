import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/react";
import { render } from "@testing-library/react";

import { PreviewRail } from "../src/components/PreviewRail";
import type { PreviewTarget } from "../src/components/PreviewRail";
import { ToolCallCard } from "../src/components/timeline/ToolCallCard";
import { RuntimeApiError } from "../src/runtime/api";
import type { TimelineAttachment, ToolItem } from "../src/timeline/items";

const {
  getWorkspaceFilePreview: previewMock,
  openWorkspacePath: openMock,
  listThreadArtifacts: artifactsMock,
  fetchAttachmentContent: contentMock,
} = vi.hoisted(() => ({
  getWorkspaceFilePreview: vi.fn(),
  openWorkspacePath: vi.fn(),
  listThreadArtifacts: vi.fn(),
  fetchAttachmentContent: vi.fn(),
}));

vi.mock("../src/runtime/api", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../src/runtime/api")>();
  return {
    ...original,
    getWorkspaceFilePreview: previewMock,
    openWorkspacePath: openMock,
    listThreadArtifacts: artifactsMock,
    fetchAttachmentContent: contentMock,
  };
});

vi.mock("../src/components/preview/PdfViewer", () => ({
  PdfViewer: ({ source, page }: { source: string | Blob; page?: number }) => (
    <div data-testid="pdf-viewer-source" data-page={page}>
      {source instanceof Blob ? "blob" : "string"}
    </div>
  ),
}));
vi.mock("../src/components/preview/DocxViewer", () => ({
  DocxViewer: () => <div data-testid="docx-viewer">Word 正文只读预览</div>,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const WORKSPACE_TARGET: PreviewTarget = {
  source: "workspace",
  workspaceId: "ws-1",
  path: "drafts/memo.txt",
};

function makeRail(target: PreviewTarget | null, overrides: Record<string, unknown> = {}) {
  const onClose = vi.fn();
  const onOpenWorkspaceFile = vi.fn();
  const onOpenList = vi.fn();
  const utils = render(
    <PreviewRail
      open={true}
      target={target}
      threadId="thread-1"
      workspaceId="ws-1"
      onClose={onClose}
      onOpenWorkspaceFile={onOpenWorkspaceFile}
      onOpenList={onOpenList}
      {...overrides}
    />,
  );
  return { ...utils, onClose, onOpenWorkspaceFile, onOpenList };
}

test("renders nothing when closed", () => {
  const { container } = makeRail(WORKSPACE_TARGET, { open: false });
  expect(container.querySelector(".preview-rail")).toBeNull();
});

test("workspace PDF citation forwards its physical page", async () => {
  previewMock.mockResolvedValue({kind:'pdf',name:'case.pdf',path:'case.pdf',size_bytes:100,data_url:'data:application/pdf;base64,JVBERg=='});
  const view = makeRail({source:'workspace',workspaceId:'ws-1',path:'case.pdf',page:45});
  expect((await view.findByTestId('pdf-viewer-source')).dataset.page).toBe('45');
});

test("validated workspace Word opens the embedded read-only renderer", async () => {
  previewMock.mockResolvedValue({kind:'docx',name:'draft.docx',path:'draft.docx',size_bytes:39000,data_url:'data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,UEs='});
  const view = makeRail({source:'workspace',workspaceId:'ws-1',path:'draft.docx'});
  expect(await view.findByTestId('docx-viewer')).toBeTruthy();
  expect(view.queryByText('暂不支持在应用内预览此格式。')).toBeNull();
});

test("unsupported preview explains the available action without calling its size an app name", async () => {
  previewMock.mockResolvedValue({kind:'unsupported',name:'draft.bin',path:'draft.bin',size_bytes:39000});
  const view = makeRail(WORKSPACE_TARGET);
  expect(await view.findByText('点击上方“用默认应用打开”查看文件。')).toBeTruthy();
});

test("text preview renders line numbers and truncation notice", async () => {
  previewMock.mockResolvedValue({
    kind: "text",
    name: "memo.txt",
    path: "drafts/memo.txt",
    size_bytes: 30,
    modified_at: "2026-08-26T00:00:00Z",
    truncated: true,
    content: "第一行\n第二行",
  });
  const { container } = makeRail(WORKSPACE_TARGET);
  await waitFor(() =>
    expect(container.querySelectorAll(".preview-code-line")).toHaveLength(2),
  );
  expect(container.querySelector(".preview-line-number")?.textContent).toBe("1");
  expect(container.querySelector(".preview-truncated")).not.toBeNull();
  expect(previewMock).toHaveBeenCalledWith("ws-1", "drafts/memo.txt");
});

test("markdown preview toggles between rendered and source", async () => {
  previewMock.mockResolvedValue({
    kind: "text",
    name: "readme.md",
    path: "readme.md",
    size_bytes: 12,
    modified_at: "2026-08-26T00:00:00Z",
    truncated: false,
    content: "# 标题",
  });
  const { container } = makeRail({
    source: "workspace",
    workspaceId: "ws-1",
    path: "readme.md",
  });
  await waitFor(() =>
    expect(container.querySelector(".preview-markdown")).not.toBeNull(),
  );
  fireEvent.click(container.querySelector(".preview-text-toggle button")!);
  await waitFor(() =>
    expect(container.querySelector(".preview-code")).not.toBeNull(),
  );
});

test("unsupported preview states the limit and offers system open", async () => {
  openMock.mockResolvedValue({ path: "memo.docx", mode: "open" });
  previewMock.mockResolvedValue({
    kind: "unsupported",
    name: "memo.docx",
    path: "memo.docx",
    size_bytes: 1024,
    modified_at: "2026-08-26T00:00:00Z",
    truncated: false,
  });
  const { container } = makeRail({
    source: "workspace",
    workspaceId: "ws-1",
    path: "memo.docx",
  });
  await waitFor(() =>
    expect(container.querySelector(".preview-unsupported")).not.toBeNull(),
  );
  const buttons = container.querySelectorAll(
    ".preview-system-actions button",
  );
  expect(buttons).toHaveLength(2);
  expect(buttons[0].textContent).toContain("用默认应用打开");
  expect(buttons[1].textContent).toContain("在访达中显示");
  fireEvent.click(buttons[0]);
  await waitFor(() => expect(openMock).toHaveBeenCalled());
  expect(openMock).toHaveBeenCalledWith("ws-1", {
    path: "memo.docx",
    mode: "open",
  });
});

test("missing workspace file renders the gone state without system buttons", async () => {
  previewMock.mockRejectedValue(
    new RuntimeApiError("文件不存在", {
      code: "file_not_found",
      retryable: false,
      status: 404,
    }),
  );
  const { container } = makeRail(WORKSPACE_TARGET);
  await waitFor(() =>
    expect(container.querySelector(".preview-missing")).not.toBeNull(),
  );
  expect(container.querySelector(".preview-system-actions")).toBeNull();
});

test("escape key closes the rail", async () => {
  previewMock.mockResolvedValue({
    kind: "text",
    name: "memo.txt",
    path: "drafts/memo.txt",
    size_bytes: 3,
    modified_at: "2026-08-26T00:00:00Z",
    truncated: false,
    content: "abc",
  });
  const { onClose } = makeRail(WORKSPACE_TARGET);
  await waitFor(() =>
    expect(document.querySelector(".preview-rail")).not.toBeNull(),
  );
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});

test("artifacts list renders entries and opens files on click", async () => {
  artifactsMock.mockResolvedValue([
    {
      kind: "legacy_workspace",
      path: "out/report.md",
      name: "report.md",
      last_operation: "write_file",
      last_operated_at: "2026-08-26T00:00:05Z",
    },
  ]);
  const { container, onOpenWorkspaceFile } = makeRail({
    source: "artifacts-list",
  });
  await waitFor(() =>
    expect(
      container.querySelectorAll(".preview-artifact-list li"),
    ).toHaveLength(1),
  );
  expect(container.querySelector(".preview-artifact-meta")?.textContent)
    .toContain("写入");
  fireEvent.click(container.querySelector(".preview-artifact-list button")!);
  expect(onOpenWorkspaceFile).toHaveBeenCalledWith("ws-1", "out/report.md");
});

test("empty artifacts list explains itself", async () => {
  artifactsMock.mockResolvedValue([]);
  const { container } = makeRail({ source: "artifacts-list" });
  await waitFor(() =>
    expect(container.querySelector(".preview-empty")).not.toBeNull(),
  );
  expect(container.textContent).toContain("本次对话还没有生成或修改过文件");
});

test("image attachment preview uses a fetched blob url", async () => {
  const createObjectURL = vi.fn(() => "blob:attachment-1");
  const revokeObjectURL = vi.fn();
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = createObjectURL;
  URL.revokeObjectURL = revokeObjectURL;
  contentMock.mockResolvedValue(
    new Blob([new Uint8Array([1])], { type: "image/png" }),
  );
  try {
    const attachment: TimelineAttachment = {
      id: "att-1",
      name: "截图.png",
      mediaType: "image/png",
      sizeBytes: 1024,
    };
    const { container } = makeRail({
      source: "attachment",
      threadId: "thread-1",
      attachment,
    });
    await waitFor(() =>
      expect(container.querySelector(".preview-image-canvas img")).not.toBeNull(),
    );
    const image = container.querySelector(
      ".preview-image-canvas img",
    ) as HTMLImageElement;
    expect(image.src).toBe("blob:attachment-1");
    expect(image.alt).toBe("截图.png");
    cleanup();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:attachment-1");
  } finally {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  }
});

test("PDF attachment preview passes the fetched Blob straight to the viewer", async () => {
  const createObjectURL = vi.fn(() => "blob:must-not-be-created");
  const originalCreate = URL.createObjectURL;
  URL.createObjectURL = createObjectURL;
  contentMock.mockResolvedValue(
    new Blob([new Uint8Array([37, 80, 68, 70])], {
      type: "application/pdf",
    }),
  );
  try {
    const attachment: TimelineAttachment = {
      id: "att-pdf",
      name: "合同.pdf",
      mediaType: "application/pdf",
      sizeBytes: 2048,
    };
    const { getByTestId } = makeRail({
      source: "attachment",
      threadId: "thread-1",
      attachment,
    });

    await waitFor(() =>
      expect(getByTestId("pdf-viewer-source").textContent).toBe("blob"),
    );
    expect(createObjectURL).not.toHaveBeenCalled();
  } finally {
    URL.createObjectURL = originalCreate;
  }
});

const SUCCEEDED_TOOL: ToolItem = {
  kind: "tool",
  id: "tc-1",
  turnId: "turn-1",
  name: "write_file",
  status: "succeeded",
  argumentsSummary: { path: "spin.py", content: "…" },
};

test("tool card shows the artifact preview entry only when succeeded", () => {
  const onPreviewPath = vi.fn();
  const { container, rerender } = render(
    <ToolCallCard item={SUCCEEDED_TOOL} previewPath="spin.py" onPreviewPath={onPreviewPath} />,
  );
  const button = container.querySelector(
    ".tool-preview-entry button",
  ) as HTMLButtonElement;
  expect(button).not.toBeNull();
  expect(button.textContent).toContain("预览产物");
  fireEvent.click(button);
  expect(onPreviewPath).toHaveBeenCalledWith("spin.py");

  rerender(
    <ToolCallCard
      item={{ ...SUCCEEDED_TOOL, status: "failed" }}
      previewPath="spin.py"
      onPreviewPath={onPreviewPath}
    />,
  );
  expect(
    container.querySelector(".tool-preview-entry button"),
  ).toBeNull();
});


test("Escape in the command dialog does not close the underlying rail", () => {
  artifactsMock.mockResolvedValue([]);
  const {onClose} = makeRail({source: "artifacts-list"});
  const dialog=document.createElement('dialog');dialog.open=true;document.body.append(dialog);
  fireEvent.keyDown(dialog,{key:'Escape'});expect(onClose).not.toHaveBeenCalled();
  dialog.remove();fireEvent.keyDown(window,{key:'Escape'});expect(onClose).toHaveBeenCalledOnce();
});
