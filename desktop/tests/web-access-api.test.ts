import { afterEach, expect, test, vi } from "vitest";

import { RuntimeApiError } from "../src/runtime/api";
import {
  getWebAccess,
  putWebAccess,
} from "../src/runtime/webAccessClient";

afterEach(() => {
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
});

test("GET reads the thread web-access record without caching it", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    enabled: true,
    granted: false,
    enabledAt: "2026-08-27T00:00:00Z",
    grantedAt: null,
  }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(getWebAccess("thread / 1")).resolves.toEqual({
    enabled: true,
    granted: false,
    enabledAt: "2026-08-27T00:00:00Z",
    grantedAt: null,
  });
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:9999/v1/threads/thread%20%2F%201/web-access",
    expect.objectContaining({
      cache: "no-store",
      headers: expect.any(Headers),
    }),
  );
  const init = fetchMock.mock.calls[0][1] as RequestInit;
  expect(new Headers(init.headers).get("X-VeriLex-Token")).toBe("test-token");
});

test("PUT persists only the requested enabled flag without caching it", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    enabled: false,
    granted: false,
    enabledAt: null,
    grantedAt: null,
  }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(putWebAccess("thread-1", false)).resolves.toMatchObject({
    enabled: false,
    granted: false,
  });
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:9999/v1/threads/thread-1/web-access",
    expect.objectContaining({
      method: "PUT",
      cache: "no-store",
      body: JSON.stringify({ enabled: false }),
    }),
  );
});

test("web-access failures keep the Runtime stable error code", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    error: {
      code: "thread_turn_active",
      message: "thread has an active turn",
      retryable: false,
    },
  }), { status: 409 })));

  const error = await putWebAccess("thread-1", false).catch((caught) => caught);

  expect(error).toBeInstanceOf(RuntimeApiError);
  expect(error).toMatchObject({
    code: "thread_turn_active",
    retryable: false,
    status: 409,
  });
});
