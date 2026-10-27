import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import {
  composerMediaTypeForName,
  selectComposerFiles,
  useComposerAttachments,
} from "../src/hooks/useComposerAttachments";

const uploadRecord = (id: string, file: File) => ({
  id,
  thread_id: "draft-1",
  display_name: file.name,
  media_type: file.type,
  size_bytes: file.size,
  sha256: id.padEnd(64, "0"),
  created_at: "2026-08-19T00:00:00Z",
  expires_at: "2026-08-20T00:00:00Z",
});

test("file selection enforces type, duplicate, count and total-size limits", async () => {
  const existing = [new File(["a"], "same.txt", { type: "text/plain", lastModified: 1 })];
  const duplicate = new File(["a"], "same.txt", { type: "text/plain", lastModified: 1 });
  const unsupported = new File(["x"], "archive.zip", { type: "application/zip" });
  const accepted = new File(["ok"], "evidence.pdf", { type: "application/pdf" });

  const selection = await selectComposerFiles(existing, [duplicate, unsupported, accepted]);

  expect(selection.accepted).toEqual([accepted]);
  expect(selection.errors.join(" ")).toContain("same.txt");
  expect(selection.errors.join(" ")).toContain("archive.zip");

  const ten = Array.from({ length: 10 }, (_, index) =>
    new File([`${index}`], `${index}.txt`, { type: "text/plain", lastModified: index }),
  );
  expect((await selectComposerFiles(ten, [accepted])).accepted).toHaveLength(0);

  const huge = new File([new Uint8Array(30 * 1024 * 1024)], "huge.pdf", {
    type: "application/pdf",
  });
  expect((await selectComposerFiles(existing, [huge])).errors.join(" ")).toContain("30 MiB");
});

test("file selection accepts every runtime-supported text extension without MIME help", async () => {
  const source = new File(["print()"], "example.py", { type: "" });
  const config = new File(["name = 'demo'"], "settings.toml", {
    type: "application/toml",
  });

  expect(await selectComposerFiles([], [source, config])).toEqual({
    accepted: [source, config],
    errors: [],
  });
});

test("file selection accepts doc and docx with canonical MIME", async () => {
  const standard = new File(["docx"], "contract.docx", { type: "" });
  const legacy = new File(["doc"], "contract.doc", {type: ""});
  expect(composerMediaTypeForName(legacy.name)).toBe("application/msword");
  const macro = new File(["docm"], "contract.docm", { type: "" });
  const template = new File(["dotx"], "contract.dotx", { type: "" });

  expect(composerMediaTypeForName(standard.name)).toBe(
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  );
  expect(await selectComposerFiles([], [standard, legacy, macro, template])).toEqual({
    accepted: [standard, legacy],
    errors: [
      "contract.docm 的格式不支持。",
      "contract.dotx 的格式不支持。",
    ],
  });
});

test("attachment upload can fail, retry, and delete a successful upload", async () => {
  const file = new File(["memo"], "memo.txt", { type: "text/plain", lastModified: 2 });
  const ensureDraftThread = vi.fn().mockResolvedValue("draft-1");
  const upload = vi.fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(uploadRecord("upl-1", file));
  const remove = vi.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() =>
    useComposerAttachments({
      threadId: null,
      enabled: true,
      ensureDraftThread,
      api: { uploadAttachment: upload, deleteAttachmentUpload: remove },
    }),
  );

  await act(async () => {
    await result.current.addFiles([file]);
  });
  expect(result.current.attachments[0]).toMatchObject({ status: "failed" });
  expect(ensureDraftThread).toHaveBeenCalledTimes(1);

  await act(async () => {
    await result.current.retry(result.current.attachments[0].key);
  });
  await waitFor(() => {
    expect(result.current.attachments[0]).toMatchObject({
      status: "succeeded",
      uploadId: "upl-1",
    });
  });

  await act(async () => {
    await result.current.remove(result.current.attachments[0].key);
  });
  expect(remove).toHaveBeenCalledWith("draft-1", "upl-1", expect.any(AbortSignal));
  expect(result.current.attachments).toHaveLength(0);
});

test("disabled attachment input asks for a model instead of a new-task click", async () => {
  const ensureDraftThread = vi.fn().mockResolvedValue(null);
  const { result } = renderHook(() =>
    useComposerAttachments({
      threadId: null,
      enabled: false,
      ensureDraftThread,
      api: {
        uploadAttachment: vi.fn(),
        deleteAttachmentUpload: vi.fn(),
      },
    }),
  );

  await act(async () => {
    await result.current.addFiles([
      new File(["memo"], "memo.txt", { type: "text/plain" }),
    ]);
  });

  expect(result.current.error).toBe("请先连接模型，再添加附件或选择工作区。");
  expect(ensureDraftThread).not.toHaveBeenCalled();
});


test("same metadata does not hide different content, while renamed identical content is deduplicated", async () => {
  const original = new File(["abc"], "same.txt", { lastModified: 1 });
  const different = new File(["xyz"], "same.txt", { lastModified: 1 });
  const renamed = new File(["abc"], "renamed.txt", { lastModified: 2 });
  expect(await selectComposerFiles([original], [different, renamed])).toEqual({
    accepted: [different], errors: ["renamed.txt 已经添加。"],
  });
});

test("concurrent selections serialize content checks and keep distinct attachment identities", async () => {
  const upload = vi.fn(async (_: string, file: File) => uploadRecord(`upl-${upload.mock.calls.length}`, file));
  const { result } = renderHook(() => useComposerAttachments({ threadId: "draft-1", enabled: true,
    ensureDraftThread: vi.fn(), api: { uploadAttachment: upload, deleteAttachmentUpload: vi.fn() } }));
  const first = new File(["one"], "same.txt", { lastModified: 1 });
  const second = new File(["two"], "same.txt", { lastModified: 1 });
  await act(async () => { await Promise.all([result.current.addFiles([first]), result.current.addFiles([second, new File(["one"], "copy.txt")])]); });
  expect(upload).toHaveBeenCalledTimes(2);
  expect(new Set(result.current.attachments.map(item => item.key)).size).toBe(2);
  expect(result.current.error).toContain("copy.txt 已经添加");
  expect(result.current.hasPending).toBe(false);
});
