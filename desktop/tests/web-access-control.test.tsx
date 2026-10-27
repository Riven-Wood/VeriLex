import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { WebAccessOffer } from "../src/components/timeline/WebAccessOffer";
import { App } from "../src/app/App";
import { useWebAccess } from "../src/hooks/useWebAccess";
import type { HealthResponse, ThreadRecord } from "../src/runtime/contracts";
import type { ThreadApi } from "../src/runtime/threadClient";
import type { WebAccessApi, WebAccessRecord } from "../src/runtime/webAccessClient";
import { buildTimelineHistory } from "../src/timeline/history";
import { applyTimelineEvent, bufferDeltas, initialTimeline } from "../src/timeline/reducer";

const OFF: WebAccessRecord = {
  enabled: false,
  granted: false,
  enabledAt: null,
  grantedAt: null,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
  WebSocketStub.instances = [];
  WebSocketStub.onSend = null;
});

class WebSocketStub {
  static OPEN = 1;
  static instances: WebSocketStub[] = [];
  static onSend: ((data: string) => void) | null = null;
  readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sentMessages: string[] = [];

  constructor() {
    WebSocketStub.instances.push(this);
  }

  send(data: string) {
    this.sentMessages.push(data);
    WebSocketStub.onSend?.(data);
  }

  close() {
    this.readyState = 3;
  }
}

const HEALTH: HealthResponse = {
  ok: true,
  service: "verilex-runtime",
  version: "0.3.0",
  storage: "local",
  cloud_backend: false,
  configured: { models: 1, mcp_servers: 0, skills: 0, folders: 0 },
};

const DRAFT_THREAD: ThreadRecord = {
  id: "draft-1",
  title: "新会话",
  provider_id: "provider-1",
  model: "model-1",
  workspace_id: null,
  status: "idle",
  created_at: "2026-08-27T00:00:00Z",
  updated_at: "2026-08-27T00:00:00Z",
  archived_at: null,
  deleted_at: null,
  is_draft: true,
  reasoning_effort: "auto",
};

const EXISTING_THREAD: ThreadRecord = {
  ...DRAFT_THREAD,
  id: "thread-b",
  title: "B 会话",
  is_draft: false,
};

function turnStarts() {
  return WebSocketStub.instances.flatMap((socket) => socket.sentMessages)
    .filter((raw) => (JSON.parse(raw) as { type?: string }).type === "turn.start");
}

function appThreadApi(createThread: ThreadApi["createThread"]): ThreadApi {
  return {
    createThread,
    getThread: vi.fn(),
    listThreads: vi.fn().mockResolvedValue([]),
    updateThreadContext: vi.fn(),
    discardDraftThread: vi.fn().mockResolvedValue(undefined),
    renameThread: vi.fn(),
    archiveThread: vi.fn(),
    unarchiveThread: vi.fn(),
    trashThread: vi.fn(),
    restoreThread: vi.fn(),
    deleteThread: vi.fn(),
  };
}

function setupAppFetch() {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("WebSocket", WebSocketStub);
  vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/v1/providers")) {
      return Promise.resolve(new Response(JSON.stringify([{
        id: "provider-1",
        name: "本地模型",
        kind: "openai_compatible",
        base_url: "http://127.0.0.1:1234/v1",
        default_model: "model-1",
        auth_mode: "none",
        has_secret: false,
        enabled: true,
        created_at: "2026-08-27T00:00:00Z",
        updated_at: "2026-08-27T00:00:00Z",
      }]), { status: 200 }));
    }
    return Promise.resolve(new Response("[]", { status: 200 }));
  }));
}

test("a late GET from the previous thread cannot replace the selected thread state", async () => {
  const first = deferred<WebAccessRecord>();
  const api: WebAccessApi = {
    get: vi.fn((threadId) => threadId === "thread-1"
      ? first.promise
      : Promise.resolve(OFF)),
    put: vi.fn(),
  };
  const { result, rerender } = renderHook(
    ({ threadId }) => useWebAccess({ threadId, isRunning: false, api }),
    { initialProps: { threadId: "thread-1" as string | null } },
  );

  rerender({ threadId: "thread-2" });
  await waitFor(() => expect(result.current.state.loading).toBe(false));
  expect(result.current.state).toMatchObject({ enabled: false, threadId: "thread-2" });

  await act(async () => {
    first.resolve({
      enabled: true,
      granted: true,
      enabledAt: "first",
      grantedAt: "first",
    });
    await first.promise;
  });

  expect(result.current.state).toMatchObject({ enabled: false, threadId: "thread-2" });
});

test("switching threads synchronously exposes a closed state until that thread GET finishes", async () => {
  const second = deferred<WebAccessRecord>();
  const api: WebAccessApi = {
    get: vi.fn((threadId) => threadId === "thread-1"
      ? Promise.resolve({ ...OFF, enabled: true, enabledAt: "first" })
      : second.promise),
    put: vi.fn(),
  };
  const { result, rerender } = renderHook(
    ({ threadId }) => useWebAccess({ threadId, isRunning: false, api }),
    { initialProps: { threadId: "thread-1" as string | null } },
  );
  await waitFor(() => expect(result.current.state.enabled).toBe(true));

  rerender({ threadId: "thread-2" });

  expect(result.current.state).toMatchObject({
    threadId: "thread-2",
    enabled: false,
    granted: false,
    loading: true,
  });
});

test("failed PUT preserves the last Runtime record and can be retried", async () => {
  const put = vi.fn()
    .mockRejectedValueOnce(new Error("暂时失败"))
    .mockResolvedValueOnce({ ...OFF, enabled: true, enabledAt: "now" });
  const api: WebAccessApi = { get: vi.fn().mockResolvedValue(OFF), put };
  const { result } = renderHook(() =>
    useWebAccess({ threadId: "thread-1", isRunning: false, api }),
  );
  await waitFor(() => expect(result.current.state.loading).toBe(false));

  await act(async () => {
    expect(await result.current.toggle(true)).toBe(false);
  });
  expect(result.current.state).toMatchObject({
    enabled: false,
    error: "暂时失败",
  });

  await act(async () => {
    expect(await result.current.toggle(true)).toBe(true);
  });
  expect(result.current.state).toMatchObject({ enabled: true, error: null });
  expect(put).toHaveBeenCalledTimes(2);
});

test("running turns cannot change web access", async () => {
  const api: WebAccessApi = {
    get: vi.fn().mockResolvedValue({ ...OFF, enabled: true, enabledAt: "now" }),
    put: vi.fn(),
  };
  const { result } = renderHook(() =>
    useWebAccess({ threadId: "thread-1", isRunning: true, api }),
  );
  await waitFor(() => expect(result.current.state.loading).toBe(false));

  await act(async () => {
    expect(await result.current.toggle(false)).toBe(false);
  });

  expect(api.put).not.toHaveBeenCalled();
  expect(result.current.state.enabled).toBe(true);
});

test("unmounting aborts a pending web-access GET", async () => {
  const request = deferred<WebAccessRecord>();
  let signal: AbortSignal | undefined;
  const api: WebAccessApi = {
    get: vi.fn((_threadId, requestSignal) => {
      signal = requestSignal;
      return request.promise;
    }),
    put: vi.fn(),
  };
  const { unmount } = renderHook(() =>
    useWebAccess({ threadId: "thread-1", isRunning: false, api }),
  );
  await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));

  unmount();

  expect(signal?.aborted).toBe(true);
});

test("unmounting aborts a pending web-access PUT", async () => {
  const request = deferred<WebAccessRecord>();
  let signal: AbortSignal | undefined;
  const api: WebAccessApi = {
    get: vi.fn().mockResolvedValue(OFF),
    put: vi.fn((_threadId, _enabled, requestSignal) => {
      signal = requestSignal;
      return request.promise;
    }),
  };
  const { result, unmount } = renderHook(() =>
    useWebAccess({ threadId: "thread-1", isRunning: false, api }),
  );
  await waitFor(() => expect(result.current.state.loading).toBe(false));
  let togglePromise!: Promise<boolean>;
  act(() => {
    togglePromise = result.current.toggle(true);
  });
  await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));

  unmount();
  expect(signal?.aborted).toBe(true);
  request.resolve({ ...OFF, enabled: true, enabledAt: "now" });
  await togglePromise;
});

test("a denied first-use approval closes immediately and refreshes Runtime truth", async () => {
  const refreshed = deferred<WebAccessRecord>();
  const get = vi.fn()
    .mockResolvedValueOnce({ ...OFF, enabled: true, enabledAt: "now" })
    .mockImplementationOnce(() => refreshed.promise);
  const api: WebAccessApi = { get, put: vi.fn() };
  const { result } = renderHook(() =>
    useWebAccess({ threadId: "thread-1", isRunning: false, api }),
  );
  await waitFor(() => expect(result.current.state.enabled).toBe(true));

  act(() => result.current.disableAfterDenied());

  expect(result.current.state.enabled).toBe(false);
  expect(get).toHaveBeenCalledTimes(2);
  await act(async () => {
    refreshed.resolve(OFF);
    await refreshed.promise;
  });
  expect(result.current.state.enabled).toBe(false);
});

test("the non-modal offer enables access before sending the visible follow-up", async () => {
  const order: string[] = [];
  const onEnable = vi.fn(async () => {
    order.push("enable");
    return true;
  });
  const onSend = vi.fn(() => order.push("send"));
  render(<WebAccessOffer disabled={false} onEnable={onEnable} onSend={onSend} />);

  fireEvent.click(screen.getByRole("button", { name: "联网补充" }));

  await waitFor(() => expect(onSend).toHaveBeenCalledWith("请联网补充以上问题"));
  expect(order).toEqual(["enable", "send"]);
});

test("the non-modal offer does not send when enabling fails", async () => {
  const onSend = vi.fn();
  render(
    <WebAccessOffer
      disabled={false}
      onEnable={vi.fn().mockResolvedValue(false)}
      onSend={onSend}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "联网补充" }));

  await waitFor(() => expect(
    screen.getByRole("button", { name: "联网补充" }).hasAttribute("disabled"),
  ).toBe(false));
  expect(onSend).not.toHaveBeenCalled();
});

test("an unmounted offer cannot send after its enable promise resolves", async () => {
  const request = deferred<boolean>();
  const onSend = vi.fn();
  const { unmount } = render(
    <WebAccessOffer
      disabled={false}
      onEnable={() => request.promise}
      onSend={onSend}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "联网补充" }));

  unmount();
  await act(async () => {
    request.resolve(true);
    await request.promise;
  });

  expect(onSend).not.toHaveBeenCalled();
});

test("the offer still sends after the StrictMode effect probe", async () => {
  const onSend = vi.fn();
  render(
    <StrictMode>
      <WebAccessOffer
        disabled={false}
        onEnable={vi.fn().mockResolvedValue(true)}
        onSend={onSend}
      />
    </StrictMode>,
  );

  fireEvent.click(screen.getByRole("button", { name: "联网补充" }));

  await waitFor(() => expect(onSend).toHaveBeenCalledWith("请联网补充以上问题"));
});

test("a blank draft toggle creates no thread and PUT completes before the first send", async () => {
  setupAppFetch();
  const order: string[] = [];
  WebSocketStub.onSend = (raw) => {
    if ((JSON.parse(raw) as { type?: string }).type === "turn.start") {
      order.push("send");
    }
  };
  const createThread = vi.fn(async () => DRAFT_THREAD);
  const webApi: WebAccessApi = {
    get: vi.fn().mockResolvedValue(OFF),
    put: vi.fn(async () => {
      order.push("put");
      return { ...OFF, enabled: true, enabledAt: "now" };
    }),
  };
  render(
    <App
      initialHealth={HEALTH}
      threadApi={appThreadApi(createThread)}
      webAccessApi={webApi}
    />,
  );
  await screen.findByRole("button", { name: "选择模型，当前 model-1" });
  WebSocketStub.instances[0].onopen?.();

  fireEvent.click(screen.getByRole("button", { name: "联网已关闭" }));
  expect(createThread).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "查询最新规则" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  await waitFor(() => expect(WebSocketStub.instances[0].sentMessages.some((raw) =>
    (JSON.parse(raw) as { type?: string }).type === "turn.start",
  )).toBe(true));
  expect(webApi.put).toHaveBeenCalledWith("draft-1", true);
  expect(order).toEqual(["put", "send"]);
  expect(webApi.get).toHaveBeenCalledTimes(1);
});

test("a failed draft PUT sends nothing and preserves the composed text", async () => {
  setupAppFetch();
  const createThread = vi.fn(async () => DRAFT_THREAD);
  const webApi: WebAccessApi = {
    get: vi.fn().mockResolvedValue(OFF),
    put: vi.fn().mockRejectedValue(new Error("无法保存联网状态")),
  };
  render(
    <App
      initialHealth={HEALTH}
      threadApi={appThreadApi(createThread)}
      webAccessApi={webApi}
    />,
  );
  await screen.findByRole("button", { name: "选择模型，当前 model-1" });
  WebSocketStub.instances[0].onopen?.();
  fireEvent.click(screen.getByRole("button", { name: "联网已关闭" }));
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "不要丢失这段正文" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  await waitFor(() => expect(webApi.put).toHaveBeenCalledWith("draft-1", true));
  expect(WebSocketStub.instances[0].sentMessages.every((raw) =>
    (JSON.parse(raw) as { type?: string }).type !== "turn.start",
  )).toBe(true);
  expect((screen.getByRole("textbox", { name: "向智能体提问" }) as HTMLTextAreaElement).value)
    .toBe("不要丢失这段正文");
  expect(webApi.get).toHaveBeenCalledTimes(1);
});

test("live completion attaches an offer only to its assistant item", () => {
  let state = initialTimeline();
  state = bufferDeltas(state, { text: "本地资料不足" });
  state = applyTimelineEvent(state, {
    type: "message_completed",
    turnId: "turn-1",
    sequence: 1,
    payload: { webAccessOffer: true },
  });

  expect(state.items).toHaveLength(1);
  expect(state.items[0]).toMatchObject({
    kind: "assistant",
    webAccessOffer: true,
  });
});

test("history restores a structured offer on the matching assistant only", () => {
  const build = buildTimelineHistory({
    messages: [{
      id: "message-1",
      thread_id: "thread-1",
      turn_id: "turn-1",
      role: "assistant",
      content: [
        { type: "text", text: "本地资料不足" },
        { type: "web_access_offer" },
      ],
      created_at: "2026-08-27T00:00:00Z",
    }],
    turns: [{ id: "turn-1", status: "completed", error_code: null }],
    events: [],
  });

  expect(build.state.items).toHaveLength(1);
  expect(build.state.items[0]).toMatchObject({
    kind: "assistant",
    webAccessOffer: true,
  });
});

test("an offer-only history record cannot attach its offer to the previous assistant", () => {
  const build = buildTimelineHistory({
    messages: [
      {
        id: "message-1",
        thread_id: "thread-1",
        turn_id: "turn-1",
        role: "assistant",
        content: [{ type: "text", text: "上一条回答" }],
        created_at: "2026-08-27T00:00:00Z",
      },
      {
        id: "message-2",
        thread_id: "thread-1",
        turn_id: "turn-2",
        role: "assistant",
        content: [
          { type: "text", text: "" },
          { type: "web_access_offer" },
        ],
        created_at: "2026-08-27T00:01:00Z",
      },
    ],
    turns: [
      { id: "turn-1", status: "completed", error_code: null },
      { id: "turn-2", status: "completed", error_code: null },
    ],
    events: [],
  });

  const assistants = build.state.items.filter((item) => item.kind === "assistant");
  expect(assistants).toHaveLength(2);
  expect(assistants[0]).not.toHaveProperty("webAccessOffer");
  expect(assistants[1]).toMatchObject({ webAccessOffer: true, segments: [] });
});

test("an offer-only live completion cannot mutate a previous assistant", () => {
  let state = bufferDeltas(initialTimeline(), { text: "上一条回答" });
  state = applyTimelineEvent(state, {
    type: "message_completed",
    turnId: "turn-1",
    sequence: 1,
    payload: {},
  });
  state = applyTimelineEvent(state, {
    type: "message_completed",
    turnId: "turn-2",
    sequence: 1,
    payload: { webAccessOffer: true },
  });

  const assistants = state.items.filter((item) => item.kind === "assistant");
  expect(assistants).toHaveLength(2);
  expect(assistants[0]).not.toHaveProperty("webAccessOffer");
  expect(assistants[1]).toMatchObject({ webAccessOffer: true, segments: [] });
});

test("an offer-only turn cannot reuse a stale streaming assistant from another turn", () => {
  let state = applyTimelineEvent(initialTimeline(), {
    type: "turn_started",
    turnId: "turn-1",
    sequence: 1,
    payload: {},
  });
  state = bufferDeltas(state, { text: "上一轮尚未收到完成事件" });
  state = applyTimelineEvent(state, {
    type: "turn_started",
    turnId: "turn-2",
    sequence: 1,
    payload: {},
  });
  state = applyTimelineEvent(state, {
    type: "message_completed",
    turnId: "turn-2",
    sequence: 2,
    payload: { webAccessOffer: true },
  });

  const assistants = state.items.filter((item) => item.kind === "assistant");
  expect(assistants).toHaveLength(2);
  expect(assistants[0]).not.toHaveProperty("webAccessOffer");
  expect(assistants[1]).toMatchObject({
    turnId: "turn-2",
    webAccessOffer: true,
    segments: [],
  });
});

test("a successful retry clears the draft PUT error before sending", async () => {
  setupAppFetch();
  const webApi: WebAccessApi = {
    get: vi.fn().mockResolvedValue(OFF),
    put: vi.fn()
      .mockRejectedValueOnce(new Error("无法保存联网状态"))
      .mockResolvedValueOnce({ ...OFF, enabled: true, enabledAt: "now" }),
  };
  render(
    <App
      initialHealth={HEALTH}
      threadApi={appThreadApi(vi.fn(async () => DRAFT_THREAD))}
      webAccessApi={webApi}
    />,
  );
  await screen.findByRole("button", { name: "选择模型，当前 model-1" });
  WebSocketStub.instances[0].onopen?.();
  fireEvent.click(screen.getByRole("button", { name: "联网已关闭" }));
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "重试后发送" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(await screen.findAllByText("无法保存联网状态")).not.toHaveLength(0);

  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  await waitFor(() => expect(webApi.put).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByText("无法保存联网状态")).toBeNull());
});

test("a late draft PUT success cannot send the old submission into a selected thread", async () => {
  setupAppFetch();
  const putRequest = deferred<WebAccessRecord>();
  const threadApi = appThreadApi(vi.fn(async () => DRAFT_THREAD));
  threadApi.listThreads = vi.fn().mockResolvedValue([EXISTING_THREAD]);
  render(
    <App
      initialHealth={HEALTH}
      threadApi={threadApi}
      webAccessApi={{
        get: vi.fn().mockResolvedValue(OFF),
        put: vi.fn(() => putRequest.promise),
      }}
    />,
  );
  await screen.findByText("B 会话");
  const existingThreadButton = document.querySelector<HTMLButtonElement>(
    '[data-thread-id="thread-b"] .thread-select',
  );
  expect(existingThreadButton).not.toBeNull();
  WebSocketStub.instances[0].onopen?.();
  fireEvent.click(screen.getByRole("button", { name: "联网已关闭" }));
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "A 的正文" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(threadApi.createThread).toHaveBeenCalledTimes(1));
  fireEvent.click(existingThreadButton!);
  await waitFor(() => expect(
    existingThreadButton?.getAttribute("aria-current"),
  ).toBe("true"));
  await act(async () => {
    putRequest.resolve({ ...OFF, enabled: true, enabledAt: "now" });
    await putRequest.promise;
  });
  await act(async () => { await Promise.resolve(); });

  expect(turnStarts()).toHaveLength(0);
});

test("a late draft PUT failure cannot show its error on a new blank task", async () => {
  setupAppFetch();
  const putRequest = deferred<WebAccessRecord>();
  const webApi: WebAccessApi = {
    get: vi.fn().mockResolvedValue(OFF),
    put: vi.fn(() => putRequest.promise),
  };
  render(
    <App
      initialHealth={HEALTH}
      threadApi={appThreadApi(vi.fn(async () => DRAFT_THREAD))}
      webAccessApi={webApi}
    />,
  );
  await screen.findByRole("button", { name: "选择模型，当前 model-1" });
  WebSocketStub.instances[0].onopen?.();
  fireEvent.click(screen.getByRole("button", { name: "联网已关闭" }));
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "A 的正文" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(webApi.put).toHaveBeenCalledTimes(1));

  fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
  await screen.findByRole("heading", { name: "开启今天的法律工作" });
  await act(async () => {
    putRequest.reject(new Error("A 的联网状态保存失败"));
    await putRequest.promise.catch(() => undefined);
  });
  await act(async () => { await Promise.resolve(); });

  expect(screen.queryAllByText("A 的联网状态保存失败")).toHaveLength(0);
  expect(turnStarts()).toHaveLength(0);
});

test("draft preparation is single-flight across repeated clicks and Enter", async () => {
  setupAppFetch();
  const putRequest = deferred<WebAccessRecord>();
  const put = vi.fn(() => putRequest.promise);
  render(
    <App
      initialHealth={HEALTH}
      threadApi={appThreadApi(vi.fn(async () => DRAFT_THREAD))}
      webAccessApi={{ get: vi.fn().mockResolvedValue(OFF), put }}
    />,
  );
  await screen.findByRole("button", { name: "选择模型，当前 model-1" });
  WebSocketStub.instances[0].onopen?.();
  fireEvent.click(screen.getByRole("button", { name: "联网已关闭" }));
  const textbox = screen.getByRole("textbox", { name: "向智能体提问" });
  fireEvent.change(textbox, { target: { value: "只发送一次" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(put).toHaveBeenCalledTimes(1));

  const send = screen.getByRole("button", { name: "发送" });
  fireEvent.click(send);
  fireEvent.keyDown(textbox, { key: "Enter", shiftKey: false });
  const lockedWhilePreparing = send.hasAttribute("disabled");
  await act(async () => {
    putRequest.resolve({ ...OFF, enabled: true, enabledAt: "now" });
    await putRequest.promise;
  });
  await waitFor(() => expect(turnStarts()).toHaveLength(1));

  expect(lockedWhilePreparing).toBe(true);
  expect(put).toHaveBeenCalledTimes(1);
  expect(turnStarts()).toHaveLength(1);
});

test("a failed hidden-thread creation releases the draft send lock for one retry", async () => {
  setupAppFetch();
  const createThread = vi.fn()
    .mockRejectedValueOnce(new Error("无法创建 A"))
    .mockResolvedValueOnce(DRAFT_THREAD);
  render(
    <App
      initialHealth={HEALTH}
      threadApi={appThreadApi(createThread)}
      webAccessApi={{ get: vi.fn().mockResolvedValue(OFF), put: vi.fn() }}
    />,
  );
  await screen.findByRole("button", { name: "选择模型，当前 model-1" });
  WebSocketStub.instances[0].onopen?.();
  const textbox = screen.getByRole("textbox", { name: "向智能体提问" });
  fireEvent.change(textbox, { target: { value: "创建失败后重试" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await screen.findByText("无法创建 A");

  const send = screen.getByRole("button", { name: "发送" });
  const lockedAfterFailure = send.hasAttribute("disabled");
  fireEvent.click(send);
  await waitFor(() => expect(turnStarts()).toHaveLength(1));

  expect(lockedAfterFailure).toBe(false);
  expect(createThread).toHaveBeenCalledTimes(2);
  expect(turnStarts()).toHaveLength(1);
});

test("the draft handoff stays single-flight and preserves newer edits until connection is ready", async () => {
  setupAppFetch();
  const createThread = vi.fn().mockResolvedValue(DRAFT_THREAD);
  render(
    <App
      initialHealth={HEALTH}
      threadApi={appThreadApi(createThread)}
      webAccessApi={{ get: vi.fn().mockResolvedValue(OFF), put: vi.fn() }}
    />,
  );
  await screen.findByRole("button", { name: "选择模型，当前 model-1" });
  WebSocketStub.instances[0].readyState = 0;
  const textbox = screen.getByRole("textbox", { name: "向智能体提问" });
  fireEvent.change(textbox, { target: { value: "等待发送的正文" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(createThread).toHaveBeenCalledTimes(1));

  fireEvent.change(textbox, { target: { value: "连接前的新编辑" } });
  const send = screen.getByRole("button", { name: "发送" });
  const lockedDuringHandoff = send.hasAttribute("disabled");
  fireEvent.click(send);
  fireEvent.keyDown(textbox, { key: "Enter", shiftKey: false });
  WebSocketStub.instances[0].readyState = WebSocketStub.OPEN;
  act(() => WebSocketStub.instances[0].onopen?.());
  await waitFor(() => expect(turnStarts()).toHaveLength(1));

  expect(lockedDuringHandoff).toBe(true);
  expect(createThread).toHaveBeenCalledTimes(1);
  expect(JSON.parse(turnStarts()[0])).toMatchObject({
    type: "turn.start",
    content: "等待发送的正文",
  });
  act(() => {
    WebSocketStub.instances[0].onmessage?.({
      data: JSON.stringify({ type: "ack", turnId: "turn-1", status: "queued" }),
    });
  });
  await act(async () => { await Promise.resolve(); });
  expect((textbox as HTMLTextAreaElement).value).toBe("连接前的新编辑");
});

test("a consumed PUT handoff cannot revive enabled state after denial and remount", async () => {
  setupAppFetch();
  const webApi: WebAccessApi = {
    get: vi.fn().mockResolvedValue(OFF),
    put: vi.fn().mockResolvedValue({ ...OFF, enabled: true, enabledAt: "now" }),
  };
  const threadApi = appThreadApi(vi.fn().mockResolvedValue(DRAFT_THREAD));
  vi.mocked(threadApi.listThreads)
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce([{ ...DRAFT_THREAD, is_draft: false }])
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce([]);
  render(
    <App
      initialHealth={HEALTH}
      threadApi={threadApi}
      webAccessApi={webApi}
    />,
  );
  await screen.findByRole("button", { name: "选择模型，当前 model-1" });
  WebSocketStub.instances[0].onopen?.();
  fireEvent.click(screen.getByRole("button", { name: "联网已关闭" }));
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "触发首次联网" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(turnStarts()).toHaveLength(1));
  act(() => {
    WebSocketStub.instances[0].onmessage?.({
      data: JSON.stringify({ type: "ack", turnId: "turn-1", status: "queued" }),
    });
  });
  await screen.findByRole("button", { name: "联网已开启，首次使用时确认" });

  const emit = (event: Record<string, unknown>) => {
    WebSocketStub.instances[0].onmessage?.({
      data: JSON.stringify({ type: "event", event }),
    });
  };
  act(() => emit({
    type: "turn_started",
    turnId: "turn-1",
    sequence: 1,
    payload: {},
  }));
  act(() => emit({
    type: "tool_call_started",
    turnId: "turn-1",
    sequence: 2,
    payload: { toolCallId: "web-call-1", name: "web_search" },
  }));
  act(() => emit({
    type: "approval_requested",
    turnId: "turn-1",
    sequence: 3,
    payload: {
      approvalId: "web-approval-1",
      toolCallId: "web-call-1",
      name: "web_search",
      approvalBehavior: "thread_first_use",
    },
  }));
  act(() => emit({
    type: "approval_resolved",
    turnId: "turn-1",
    sequence: 4,
    payload: {
      approvalId: "web-approval-1",
      toolCallId: "web-call-1",
      decision: "denied",
    },
  }));
  act(() => emit({
    type: "turn_completed",
    turnId: "turn-1",
    sequence: 5,
    payload: {},
  }));
  await screen.findByRole("button", { name: "联网已关闭" });
  expect(webApi.get).toHaveBeenCalledTimes(2);

  const socketCountBeforeRemount = WebSocketStub.instances.length;
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(await screen.findByRole("button", { name: "返回应用" }));
  await waitFor(() => {
    expect(WebSocketStub.instances).toHaveLength(socketCountBeforeRemount + 1);
  });
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "联网已关闭" })).toBeTruthy();
  });
});
