import { afterEach, expect, test, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";

import {
  fetchAttachmentContent,
  getWorkspaceFilePreview,
  listThreadArtifacts,
  openWorkspacePath,
} from "../src/runtime/api";
import { useObjectUrl } from "../src/hooks/useObjectUrl";

afterEach(() => {
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
  cleanup();
});

test("workspace file preview sends the path encoded with the launch token", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:43210";
  globalThis.__VERILEX_API_TOKEN__ = "launch-token";
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        kind: "text",
        name: "draft.md",
        path: "draft.md",
        size_bytes: 6,
        modified_at: "2026-08-26T00:00:00Z",
        truncated: false,
        content: "# hi",
      }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(
    getWorkspaceFilePreview("ws 1", "notes/draft one.md"),
  ).resolves.toMatchObject({ kind: "text", name: "draft.md" });
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:43210/v1/workspaces/ws%201/file-preview?path=notes%2Fdraft%20one.md",
    expect.objectContaining({ headers: expect.any(Headers) }),
  );
  const headers = fetchMock.mock.calls[0][1].headers as Headers;
  expect(headers.get("X-VeriLex-Token")).toBe("launch-token");
});

test("thread artifacts unwraps the artifacts array", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:43210";
  globalThis.__VERILEX_API_TOKEN__ = "launch-token";
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        artifacts: [
          {
            kind: "legacy_workspace",
            path: "a.txt",
            name: "a.txt",
            last_operation: "write_file",
            last_operated_at: "2026-08-26T00:00:05Z",
          },
        ],
      }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(listThreadArtifacts("thread-1")).resolves.toEqual([
    {
      kind: "legacy_workspace",
      path: "a.txt",
      name: "a.txt",
      last_operation: "write_file",
      last_operated_at: "2026-08-26T00:00:05Z",
    },
  ]);
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:43210/v1/threads/thread-1/artifacts",
    expect.anything(),
  );
});

test("open path posts mode and path as JSON", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:43210";
  globalThis.__VERILEX_API_TOKEN__ = "launch-token";
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ path: "memo.txt", mode: "reveal" }), {
      status: 200,
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(
    openWorkspacePath("ws-1", { path: "memo.txt", mode: "reveal" }),
  ).resolves.toEqual({ path: "memo.txt", mode: "reveal" });
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:43210/v1/workspaces/ws-1/open-path",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ path: "memo.txt", mode: "reveal" }),
    }),
  );
});

test("attachment content fetch returns a blob with the token in a header", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:43210";
  globalThis.__VERILEX_API_TOKEN__ = "launch-token";
  const payload = new Blob([new Uint8Array([1, 2, 3])], {
    type: "image/png",
  });
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(payload, {
      status: 200,
      headers: { "Content-Type": "image/png" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  const blob = await fetchAttachmentContent("thread-1", "att-1");
  expect(blob.type).toBe("image/png");
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:43210/v1/threads/thread-1/attachments/att-1/content",
    expect.objectContaining({ headers: expect.any(Headers) }),
  );
  const headers = fetchMock.mock.calls[0][1].headers as Headers;
  expect(headers.get("X-VeriLex-Token")).toBe("launch-token");
  const calledUrl = fetchMock.mock.calls[0][0] as string;
  expect(calledUrl).not.toContain("launch-token");
});

test("attachment content errors map to RuntimeApiError", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:43210";
  globalThis.__VERILEX_API_TOKEN__ = "launch-token";
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "attachment_not_found",
            message: "Attachment was not found.",
            retryable: false,
          },
        }),
        { status: 404 },
      ),
    ),
  );

  const error = await fetchAttachmentContent("thread-1", "att-1").catch(
    (cause) => cause,
  );
  expect(error).toMatchObject({
    code: "attachment_not_found",
    status: 404,
  });
});

test("useObjectUrl revokes the previous URL when the blob is replaced", async () => {
  const revoke = vi.fn();
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = vi.fn(() => "blob:preview-1");
  URL.revokeObjectURL = revoke;
  try {
    const first = new Blob(["a"]);
    const second = new Blob(["b"]);
    const { result, rerender } = renderHook(
      ({ blob }: { blob: Blob | null }) => useObjectUrl(blob),
      { initialProps: { blob: first } },
    );
    expect(result.current).toBe("blob:preview-1");

    URL.createObjectURL = vi.fn(() => "blob:preview-2");
    rerender({ blob: second });
    expect(result.current).toBe("blob:preview-2");
    expect(revoke).toHaveBeenCalledWith("blob:preview-1");

    rerender({ blob: null });
    expect(result.current).toBeNull();
    expect(revoke).toHaveBeenCalledWith("blob:preview-2");
  } finally {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  }
});
