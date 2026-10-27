import { useCallback, useEffect, useRef, useState } from "react";

import {
  deleteAttachmentUpload,
  uploadAttachment,
} from "../runtime/api";
import type { AttachmentUploadRecord } from "../runtime/contracts";

export const MAX_COMPOSER_FILES = 10;
export const MAX_COMPOSER_BYTES = 30 * 1024 * 1024;

const COMPOSER_TEXT_EXTENSIONS = [
  "txt", "md", "markdown", "csv", "tsv", "json", "yaml", "yml", "toml",
  "xml", "html", "htm", "css", "sql", "py", "js", "jsx", "ts", "tsx",
  "java", "kt", "go", "rs", "c", "h", "cpp", "hpp", "cs", "rb", "php",
  "swift", "sh", "zsh", "fish", "ps1", "log",
] as const;

const COMPOSER_SPECIAL_TEXT_NAMES = new Set([
  "README",
  "LICENSE",
  "Makefile",
  "Dockerfile",
  ".gitignore",
  ".editorconfig",
]);

const COMPOSER_IMAGE_MEDIA_TYPES: Record<string, string> = {
  jpeg: "image/jpeg",
  jpg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
};

const COMPOSER_TEXT_EXTENSION_SET = new Set<string>(COMPOSER_TEXT_EXTENSIONS);
const DOCX_MEDIA_TYPE =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export const COMPOSER_FILE_ACCEPT = [
  ...COMPOSER_TEXT_EXTENSIONS.map((extension) => `.${extension}`),
  ".pdf",
  ".docx",
  ".doc",
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
].join(",");

export function composerMediaTypeForName(name: string): string | null {
  if (COMPOSER_SPECIAL_TEXT_NAMES.has(name)) return "text/plain";
  const extension = name.split(".").pop()?.toLocaleLowerCase() ?? "";
  if (COMPOSER_TEXT_EXTENSION_SET.has(extension)) return "text/plain";
  if (extension === "pdf") return "application/pdf";
  if (extension === "doc") return "application/msword";
  if (extension === "docx") return DOCX_MEDIA_TYPE;
  return COMPOSER_IMAGE_MEDIA_TYPES[extension] ?? null;
}

export type ComposerAttachmentStatus =
  | "pending"
  | "succeeded"
  | "failed";

export interface ComposerAttachment {
  key: string;
  file: File;
  name: string;
  mediaType: string;
  size: number;
  status: ComposerAttachmentStatus;
  uploadId: string | null;
  threadId: string | null;
  error: string;
}

interface AttachmentApi {
  uploadAttachment: (
    threadId: string,
    file: File,
    signal?: AbortSignal,
  ) => Promise<AttachmentUploadRecord>;
  deleteAttachmentUpload: (
    threadId: string,
    uploadId: string,
    signal?: AbortSignal,
  ) => Promise<void>;
}

interface UseComposerAttachmentsOptions {
  threadId: string | null;
  enabled: boolean;
  ensureDraftThread: () => Promise<string | null>;
  api?: AttachmentApi;
}

const DEFAULT_API: AttachmentApi = {
  uploadAttachment,
  deleteAttachmentUpload,
};

const fileContents = new WeakMap<File, Promise<Uint8Array>>();
const fileKeys = new WeakMap<File, string>();
let nextFileKey = 0;
function fileKey(file: File): string {
  let key = fileKeys.get(file);
  if (!key) {
    key = `attachment-${++nextFileKey}`;
    fileKeys.set(file, key);
  }
  return key;
}
function fileBytes(file: File): Promise<Uint8Array> {
  let pending = fileContents.get(file);
  if (!pending) {
    pending = new Promise<Uint8Array>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
      reader.onerror = () => reject(new Error(`${file.name} 无法读取，请重新选择。`));
      reader.onabort = () => reject(new Error(`${file.name} 的读取已取消。`));
      reader.readAsArrayBuffer(file);
    });
    fileContents.set(file, pending);
    void pending.catch(() => fileContents.delete(file));
  }
  return pending;
}

function supported(file: File): boolean {
  return composerMediaTypeForName(file.name) !== null;
}

export async function selectComposerFiles(
  existing: readonly File[],
  incoming: readonly File[],
): Promise<{ accepted: File[]; errors: string[] }> {
  const accepted: File[] = [];
  const errors: string[] = [];
  const contents = await Promise.all(existing.map(fileBytes));
  let count = existing.length;
  let bytes = existing.reduce((total, file) => total + file.size, 0);

  for (const file of incoming) {
    if (!supported(file)) {
      errors.push(`${file.name} 的格式不支持。`);
      continue;
    }
    if (count >= MAX_COMPOSER_FILES) {
      errors.push(`每次最多添加 ${MAX_COMPOSER_FILES} 个附件。`);
      continue;
    }
    if (bytes + file.size > MAX_COMPOSER_BYTES) {
      errors.push("附件总大小不能超过 30 MiB。");
      continue;
    }
    const content = await fileBytes(file);
    if (contents.some(previous => previous.length === content.length && previous.every((byte, index) => byte === content[index]))) {
      errors.push(`${file.name} 已经添加。`);
      continue;
    }
    contents.push(content);
    count += 1;
    bytes += file.size;
    accepted.push(file);
  }
  return { accepted, errors };
}

export function useComposerAttachments({
  threadId,
  enabled,
  ensureDraftThread,
  api = DEFAULT_API,
}: UseComposerAttachmentsOptions) {
  const [attachments, setAttachmentsState] = useState<ComposerAttachment[]>([]);
  const [error, setError] = useState("");
  const [selecting, setSelecting] = useState(0);
  const attachmentsRef = useRef<ComposerAttachment[]>([]);
  const controllersRef = useRef(new Map<string, AbortController>());
  const selectionQueue = useRef<Promise<void>>(Promise.resolve());
  const selectionGeneration = useRef(0);

  const setAttachments = useCallback((
    update: (current: ComposerAttachment[]) => ComposerAttachment[],
  ) => {
    const next = update(attachmentsRef.current);
    attachmentsRef.current = next;
    setAttachmentsState(next);
  }, []);

  const uploadOne = useCallback(async (
    attachment: ComposerAttachment,
    targetThreadId: string,
  ) => {
    const controller = new AbortController();
    controllersRef.current.set(attachment.key, controller);
    setAttachments((current) =>
      current.map((item) =>
        item.key === attachment.key
          ? { ...item, status: "pending", error: "", threadId: targetThreadId }
          : item,
      ),
    );
    try {
      const record = await api.uploadAttachment(
        targetThreadId,
        attachment.file,
        controller.signal,
      );
      setAttachments((current) =>
        current.map((item) =>
          item.key === attachment.key
            ? {
                ...item,
                status: "succeeded",
                uploadId: record.id,
                threadId: targetThreadId,
                error: "",
              }
            : item,
        ),
      );
    } catch (uploadError) {
      if (controller.signal.aborted) return;
      setAttachments((current) =>
        current.map((item) =>
          item.key === attachment.key
            ? {
                ...item,
                status: "failed",
                error: uploadError instanceof Error
                  ? uploadError.message
                  : "附件上传失败",
              }
            : item,
        ),
      );
    } finally {
      controllersRef.current.delete(attachment.key);
    }
  }, [api, setAttachments]);

  const addFiles = useCallback((files: readonly File[]): Promise<void> => {
    const generation = selectionGeneration.current;
    // Join the current draft/MCP selection before reading file contents. A
    // concurrent send must share the same result, including a failed selection.
    const target = enabled && files.some(supported)
      ? Promise.resolve(threadId ?? ensureDraftThread()).then(
          id => ({ id, error: null as unknown }),
          error => ({ id: null, error }),
        )
      : null;
    setSelecting(count => count + 1);
    const run = selectionQueue.current.then(async () => {
      if (generation !== selectionGeneration.current) return;
      if (!enabled) {
        setError("请先连接模型，再添加附件或选择工作区。");
        return;
      }
      const selection = await selectComposerFiles(
        attachmentsRef.current.map((item) => item.file),
        files,
      );
      if (generation !== selectionGeneration.current) return;
      setError(selection.errors.join(" "));
      if (selection.accepted.length === 0) return;

      const additions = selection.accepted.map<ComposerAttachment>((file) => ({
        key: fileKey(file),
        file,
        name: file.name,
        mediaType: file.type,
        size: file.size,
        status: "pending",
        uploadId: null,
        threadId: threadId,
        error: "",
      }));
      if (generation !== selectionGeneration.current) return;
      setAttachments((current) => [...current, ...additions]);

      let targetThreadId = threadId;
      try {
        const resolved = await target;
        if (resolved?.error) throw resolved.error;
        targetThreadId = targetThreadId ?? resolved?.id ?? null;
      } catch (ensureError) {
        setError(
          ensureError instanceof Error ? ensureError.message : "无法创建附件任务",
        );
      }
      if (!targetThreadId) {
        setAttachments((current) =>
          current.map((item) =>
            additions.some((addition) => addition.key === item.key)
              ? { ...item, status: "failed", error: "无法创建附件任务" }
              : item,
          ),
        );
        return;
      }
      if (generation !== selectionGeneration.current) return;
      await Promise.all(additions.map((item) => uploadOne(item, targetThreadId)));
    }).catch(error => {
      if (generation === selectionGeneration.current) setError(error instanceof Error ? error.message : "附件读取失败，请重试。");
    }).finally(() => {
      if (generation === selectionGeneration.current) setSelecting(count => count - 1);
    });
    selectionQueue.current = run;
    return run;
  }, [enabled, ensureDraftThread, setAttachments, threadId, uploadOne]);

  const retry = useCallback(async (key: string) => {
    const attachment = attachmentsRef.current.find((item) => item.key === key);
    if (!attachment) return;
    let targetThreadId = attachment.threadId ?? threadId;
    targetThreadId = targetThreadId ?? await ensureDraftThread();
    if (!targetThreadId) return;
    await uploadOne(attachment, targetThreadId);
  }, [ensureDraftThread, threadId, uploadOne]);

  const remove = useCallback(async (key: string) => {
    const attachment = attachmentsRef.current.find((item) => item.key === key);
    if (!attachment) return;
    controllersRef.current.get(key)?.abort();
    if (attachment.uploadId && attachment.threadId) {
      const controller = new AbortController();
      await api.deleteAttachmentUpload(
        attachment.threadId,
        attachment.uploadId,
        controller.signal,
      );
    }
    setAttachments((current) => current.filter((item) => item.key !== key));
  }, [api, setAttachments]);

  const clear = useCallback(() => {
    selectionGeneration.current += 1;
    setSelecting(0);
    for (const controller of controllersRef.current.values()) controller.abort();
    controllersRef.current.clear();
    attachmentsRef.current = [];
    setAttachmentsState([]);
    setError("");
  }, []);

  const consumeSubmitted = useCallback((uploadIds: readonly string[]) => {
    if (uploadIds.length === 0) return;
    const consumed = new Set(uploadIds);
    setAttachments((current) =>
      current.filter((item) =>
        item.uploadId === null || !consumed.has(item.uploadId),
      ),
    );
  }, [setAttachments]);

  const detach = useCallback((): ComposerAttachment[] => {
    selectionGeneration.current += 1;
    setSelecting(0);
    const detached = attachmentsRef.current;
    attachmentsRef.current = [];
    setAttachmentsState([]);
    setError("");
    return detached;
  }, []);

  const restore = useCallback((items: readonly ComposerAttachment[]) => {
    attachmentsRef.current = [...items];
    setAttachmentsState([...items]);
    setError("");
  }, []);

  const discard = useCallback(async (items: readonly ComposerAttachment[]) => {
    await Promise.all(items.map(async (item) => {
      if (!item.uploadId || !item.threadId) return;
      const controller = new AbortController();
      try {
        await api.deleteAttachmentUpload(
          item.threadId,
          item.uploadId,
          controller.signal,
        );
      } catch {
        // The Runtime expires unconsumed uploads and hidden drafts; cancellation
        // remains locally successful even if an already-gone upload returns 404.
      }
    }));
  }, [api]);

  useEffect(() => () => {
    selectionGeneration.current += 1;
    for (const controller of controllersRef.current.values()) controller.abort();
  }, []);

  return {
    attachments,
    error,
    addFiles,
    retry,
    remove,
    clear,
    consumeSubmitted,
    detach,
    restore,
    discard,
    ready: attachments.filter((item) => item.status === "succeeded"),
    hasPending: selecting > 0 || attachments.some((item) => item.status === "pending"),
    hasImages: attachments.some((item) => item.mediaType.startsWith("image/")),
  };
}
