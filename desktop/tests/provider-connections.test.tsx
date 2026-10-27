import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { SettingsView } from "../src/views/SettingsView";
import { App } from "../src/app/App";
import { RuntimeApiError } from "../src/runtime/api";
import type { HealthResponse } from "../src/runtime/contracts";
import { runProviderStatusRefresh } from "../src/components/ProviderConnections";

afterEach(cleanup);

const zeroConfigured = { models: 0, mcp_servers: 0, skills: 0, folders: 0 };
const zeroHealth: HealthResponse = {
  ok: true,
  service: "verilex-runtime",
  version: "0.3.0",
  storage: "local",
  cloud_backend: false,
  configured: zeroConfigured,
};

const savedOllama = {
  id: "provider-1",
  name: "Local Ollama",
  kind: "ollama" as const,
  base_url: "http://127.0.0.1:11434",
  default_model: "qwen2.5:7b",
  auth_mode: "none" as const,
  has_secret: false,
  enabled: true,
  created_at: "2026-08-09T12:00:00Z",
  updated_at: "2026-08-09T12:00:00Z",
};

function providerApi(overrides = {}) {
  return {
    listProviders: vi.fn().mockResolvedValue([savedOllama]),
    testProviderConnection: vi.fn(),
    createProvider: vi.fn(),
    testSavedProvider: vi.fn(),
    discoverProviderModels: vi.fn(),
    listModelContextWindows: vi.fn().mockResolvedValue([]),
    refreshModelContextWindows: vi.fn().mockResolvedValue([]),
    setModelContextWindowOverride: vi.fn(),
    resetModelContextWindowOverride: vi.fn(),
    deleteProvider: vi.fn(),
    ...overrides,
  };
}

// The App loads the thread sidebar on startup; without network stubs these
// tests inject an empty thread API so the fetch never leaves jsdom.
function emptyThreadApi() {
  const emptyList = vi.fn().mockResolvedValue([]);
  return {
    listThreads: emptyList,
    renameThread: vi.fn(),
    archiveThread: vi.fn(),
    unarchiveThread: vi.fn(),
    trashThread: vi.fn(),
    restoreThread: vi.fn(),
    deleteThread: vi.fn().mockResolvedValue(undefined),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

test.each(["resolve", "reject"] as const)(
  "keeps the global provider refresh but skips local %s state after unmount",
  async (outcome) => {
    const pending = deferred<void>();
    const lifecycle = { current: true };
    const localEvents: string[] = [];
    let refreshStarted = false;
    const running = runProviderStatusRefresh(
      () => {
        refreshStarted = true;
        return pending.promise;
      },
      lifecycle,
      {
        success: () => localEvents.push("success"),
        failure: () => localEvents.push("failure"),
        settled: () => localEvents.push("settled"),
      },
    );

    lifecycle.current = false;
    if (outcome === "resolve") pending.resolve();
    else pending.reject(new Error("health unavailable"));
    await running;

    expect(refreshStarted).toBe(true);
    expect(localEvents).toEqual([]);
  },
);

test.each(["resolve", "reject"] as const)(
  "finishes a real %s provider refresh after the settings view unmounts",
  async (outcome) => {
    const pending = deferred<void>();
    const onProvidersChanged = vi.fn(() => pending.promise);
    const view = render(
      <SettingsView
        selectedCategory="models"
        configured={zeroConfigured}
        reduceMotion={false}
        onReduceMotionChange={() => undefined}
        providerApi={providerApi()}
        runtimeStatusError="Runtime health is stale"
        onProvidersChanged={onProvidersChanged}
      />,
    );

    await screen.findByText("Local Ollama");
    fireEvent.click(screen.getByRole("button", { name: "重新同步连接状态" }));
    await waitFor(() => expect(onProvidersChanged).toHaveBeenCalledTimes(1));
    view.unmount();

    await act(async () => {
      if (outcome === "resolve") pending.resolve();
      else pending.reject(new Error("health unavailable"));
      await pending.promise.catch(() => undefined);
      await Promise.resolve();
    });

    expect(onProvidersChanged).toHaveBeenCalledTimes(1);
  },
);

test("shows real providers and opens a local-only connection wizard", async () => {
  render(
    <SettingsView
        selectedCategory="models"
      configured={zeroConfigured}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={providerApi()}
      onProvidersChanged={async () => undefined}
    />,
  );

  expect(await screen.findByText("Local Ollama")).toBeTruthy();
  expect(screen.getByText("已启用")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));

  expect(screen.getByRole("dialog", { name: "添加模型连接" })).toBeTruthy();
  expect(screen.getByText("仅保存在本机")).toBeTruthy();
  const connectionName = screen.getByLabelText("连接名称");
  const baseUrl = screen.getByLabelText("Base URL");
  const defaultModel = screen.getByLabelText("默认模型");
  const apiKey = screen.getByLabelText("API Key");
  expect(connectionName.getAttribute("name")).toBe("provider_name");
  expect(connectionName.hasAttribute("required")).toBe(true);
  expect(baseUrl.getAttribute("name")).toBe("provider_base_url");
  expect(baseUrl.getAttribute("type")).toBe("url");
  expect(baseUrl.getAttribute("autocomplete")).toBe("url");
  expect(baseUrl.getAttribute("aria-describedby")).toBe("provider-base-url-help");
  expect(defaultModel.getAttribute("name")).toBe("provider_default_model");
  expect(defaultModel.getAttribute("aria-describedby")).toBe("provider-default-model-help");
  expect(apiKey.getAttribute("name")).toBe("provider_api_key");
  expect(apiKey.getAttribute("autocomplete")).toBe("new-password");
  expect(apiKey.hasAttribute("required")).toBe(true);
  expect(apiKey.closest("form")).not.toBeNull();
});

test("keeps a failed provider list distinct from a real empty state and retries it", async () => {
  const listProviders = vi.fn()
    .mockRejectedValueOnce(new Error("Runtime unavailable"))
    .mockResolvedValueOnce([]);

  render(
    <SettingsView
        selectedCategory="models"
      configured={zeroConfigured}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={providerApi({ listProviders })}
      onProvidersChanged={async () => undefined}
    />,
  );

  expect((await screen.findByRole("alert")).textContent).toContain("Runtime unavailable");
  expect(screen.queryByText("未连接任何模型提供商")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "重新读取模型连接" }));

  expect(await screen.findByText("未连接任何模型提供商")).toBeTruthy();
  expect(listProviders).toHaveBeenCalledTimes(2);
});

test("reloads the authoritative provider list after saving without losing existing connections", async () => {
  const pendingList = deferred<typeof savedOllama[]>();
  const existingProvider = {
    ...savedOllama,
    id: "provider-existing",
    name: "Existing Gateway",
  };
  const api = providerApi({
    listProviders: vi.fn()
      .mockReturnValueOnce(pendingList.promise)
      .mockResolvedValueOnce([existingProvider, savedOllama]),
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["qwen2.5:7b"],
      selected_model: "qwen2.5:7b",
    }),
    createProvider: vi.fn().mockResolvedValue(savedOllama),
  });

  render(
    <SettingsView
        selectedCategory="models"
      configured={zeroConfigured}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={api}
      onProvidersChanged={async () => undefined}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("提供商类型"), { target: { value: "ollama" } });
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Local Ollama" } });
  fireEvent.click(screen.getByRole("button", { name: "发现并测试 Ollama" }));
  await screen.findByText("连接测试通过，发现 1 个模型");
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));

  expect(await screen.findByText("Local Ollama")).toBeTruthy();
  expect(await screen.findByText("Existing Gateway")).toBeTruthy();
  expect(screen.queryByText("已配置 2 个模型提供商")).toBeNull();
  await act(async () => {
    pendingList.resolve([]);
    await pendingList.promise;
  });

  expect(screen.getByText("Local Ollama")).toBeTruthy();
  expect(screen.getByText("Existing Gateway")).toBeTruthy();
  expect(screen.queryByText("未连接任何模型提供商")).toBeNull();
});

test("closes the connection wizard with Escape", async () => {
  render(
    <SettingsView
        selectedCategory="models"
      configured={zeroConfigured}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={providerApi()}
      onProvidersChanged={async () => undefined}
    />,
  );

  await screen.findByText("Local Ollama");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.keyDown(document, { key: "Escape" });

  expect(screen.queryByRole("dialog", { name: "添加模型连接" })).toBeNull();
});

test("tests, discovers, and saves a provider without retaining its API key", async () => {
  localStorage.clear();
  sessionStorage.clear();
  const marker = "VERILEX_BROWSER_STORAGE_SENTINEL";
  const savedProvider = {
    id: "provider-2",
    name: "Firm Gateway",
    kind: "openai_compatible" as const,
    base_url: "https://models.example.test/v1",
    default_model: "legal-model",
    auth_mode: "api_key" as const,
    has_secret: true,
    enabled: true,
    created_at: "2026-08-09T13:00:00Z",
    updated_at: "2026-08-09T13:00:00Z",
  };
  const api = providerApi({
    listProviders: vi.fn().mockResolvedValue([]),
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["legal-model", "review-model"],
      selected_model: "legal-model",
    }),
    createProvider: vi.fn().mockResolvedValue(savedProvider),
  });

  render(
    <SettingsView
        selectedCategory="models"
      configured={zeroConfigured}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={api}
      onProvidersChanged={async () => undefined}
    />,
  );

  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Firm Gateway" } });
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://models.example.test/v1" },
  });
  fireEvent.change(screen.getByLabelText("默认模型"), { target: { value: "legal-model" } });
  fireEvent.change(screen.getByLabelText("API Key"), { target: { value: marker } });

  expect(screen.getByRole("button", { name: "保存连接" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "测试并发现模型" }));

  expect(await screen.findByText("连接测试通过，发现 2 个模型")).toBeTruthy();
  expect(screen.getByRole("option", { name: "review-model" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("默认模型"), { target: { value: "review-model" } });
  expect(screen.getByRole("option", { name: "review-model" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "保存连接" }).hasAttribute("disabled")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));

  expect(await screen.findByText("Firm Gateway")).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "添加模型连接" })).toBeNull();
  expect([...Array(localStorage.length)].map((_, index) => localStorage.getItem(localStorage.key(index)!))).not.toContain(marker);
  expect([...Array(sessionStorage.length)].map((_, index) => sessionStorage.getItem(sessionStorage.key(index)!))).not.toContain(marker);

  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  expect((screen.getByLabelText("API Key") as HTMLInputElement).value).toBe("");
  expect(api.createProvider).toHaveBeenCalledWith(
    expect.objectContaining({ default_model: "review-model" }),
  );
});

test("tests a saved provider and opens its saved per-model context manager", async () => {
  const api = providerApi({
    testSavedProvider: vi.fn().mockResolvedValue({
      models: ["qwen2.5:7b"],
      selected_model: "qwen2.5:7b",
    }),
    listModelContextWindows: vi.fn().mockResolvedValue([
      {
        provider_id: "provider-1", model: "qwen2.5:7b",
        context_window_tokens: 128000, context_window_source: "provider",
        automatic_tokens: 128000, automatic_source: "provider",
        manual_tokens: null, is_available: true, updated_at: "now",
      },
      {
        provider_id: "provider-1", model: "deepseek-r1:8b",
        context_window_tokens: null, context_window_source: "unknown",
        automatic_tokens: null, automatic_source: "unknown",
        manual_tokens: null, is_available: true, updated_at: "now",
      },
    ]),
  });

  render(
    <SettingsView
        selectedCategory="models"
      configured={{ ...zeroConfigured, models: 1 }}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={api}
      onProvidersChanged={async () => undefined}
    />,
  );

  await screen.findByText("Local Ollama");
  fireEvent.click(screen.getByRole("button", { name: "测试 Local Ollama" }));
  expect(await screen.findByText("连接可用，当前模型 qwen2.5:7b")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "发现 Local Ollama 的模型" }));
  expect(await screen.findByText("deepseek-r1:8b")).toBeTruthy();
  expect(await screen.findByRole("region", { name: "Local Ollama 的模型上下文上限" })).toBeTruthy();
});

test("refreshes global runtime truth after a provider is saved", async () => {
  const api = providerApi({
    listProviders: vi.fn().mockResolvedValue([]),
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["qwen2.5:7b"],
      selected_model: "qwen2.5:7b",
    }),
    createProvider: vi.fn().mockResolvedValue(savedOllama),
  });
  const refreshHealth = vi.fn().mockResolvedValue({
    ...zeroHealth,
    configured: { ...zeroConfigured, models: 1 },
  });

  render(
    <App
      initialHealth={zeroHealth}
      providerApi={api}
      threadApi={emptyThreadApi()}
      refreshHealth={refreshHealth}
    />,
  );

  expect(
    screen.getByRole("heading", { name: "开启今天的法律工作" }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "模型", exact: true }));
  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("提供商类型"), { target: { value: "ollama" } });
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Local Ollama" } });
  expect(screen.getByRole("button", { name: "发现并测试 Ollama" }).hasAttribute("disabled")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "发现并测试 Ollama" }));
  await screen.findByText("连接测试通过，发现 1 个模型");
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
  // The provider dialog closes itself on a successful save, and the save
  // triggers the global health refresh (the sidebar/banner no longer show
  // connection state by design).
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "添加模型连接" })).toBeNull(),
  );
  await waitFor(() => expect(refreshHealth).toHaveBeenCalled());
});

test("marks global Runtime status offline when post-save health refresh fails and recovers", async () => {
  // Stateful listing: App fetches providers on mount and on every health
  // change, so call-order-sensitive Once mocks would be brittle.
  let providerSaved = false;
  const api = providerApi({
    listProviders: vi.fn().mockImplementation(async () => (providerSaved ? [savedOllama] : [])),
    createProvider: vi.fn().mockImplementation(async () => {
      providerSaved = true;
      return savedOllama;
    }),
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["qwen2.5:7b"],
      selected_model: "qwen2.5:7b",
    }),
  });
  const refreshHealth = vi.fn()
    .mockRejectedValueOnce(new Error("health unavailable"))
    .mockResolvedValueOnce({
      ...zeroHealth,
      configured: { ...zeroConfigured, models: 1 },
    });

  render(
    <App
      initialHealth={zeroHealth}
      providerApi={api}
      threadApi={emptyThreadApi()}
      refreshHealth={refreshHealth}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "模型", exact: true }));
  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("提供商类型"), { target: { value: "ollama" } });
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Local Ollama" } });
  fireEvent.click(screen.getByRole("button", { name: "发现并测试 Ollama" }));
  await screen.findByText("连接测试通过，发现 1 个模型");
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));

  expect(await screen.findByText(/全局连接状态尚未同步/)).toBeTruthy();
  expect(screen.getByText("本机后台暂时无法连接")).toBeTruthy();
  expect(screen.queryByText("本地 Runtime 已连接")).toBeNull();
  expect(screen.getByText("Local Ollama")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "重新检查本机后台状态" }));

  // Recovery clears the offline banner; there is no "已连接" banner any more
  // by design (status left the ambient chrome).
  await waitFor(() => expect(screen.queryByText("本机后台暂时无法连接")).toBeNull());

  fireEvent.click(screen.getByRole("button", { name: /返回应用/ }));
  expect(
    await screen.findByRole("heading", { name: "开启今天的法律工作" }),
  ).toBeTruthy();
});

test("keeps health recovery available after Settings unmounts during a post-save refresh", async () => {
  const pendingHealth = deferred<HealthResponse>();
  const api = providerApi({
    listProviders: vi.fn().mockResolvedValue([]),
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["qwen2.5:7b"],
      selected_model: "qwen2.5:7b",
    }),
    createProvider: vi.fn().mockResolvedValue(savedOllama),
  });
  const refreshHealth = vi.fn()
    .mockReturnValueOnce(pendingHealth.promise)
    .mockRejectedValueOnce(new Error("health still unavailable"))
    .mockResolvedValueOnce({
      ...zeroHealth,
      configured: { ...zeroConfigured, models: 1 },
    });

  render(
    <App
      initialHealth={zeroHealth}
      providerApi={api}
      threadApi={emptyThreadApi()}
      refreshHealth={refreshHealth}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "模型", exact: true }));
  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("提供商类型"), { target: { value: "ollama" } });
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Local Ollama" } });
  fireEvent.click(screen.getByRole("button", { name: "发现并测试 Ollama" }));
  await screen.findByText("连接测试通过，发现 1 个模型");
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "添加模型连接" })).toBeNull();
  });

  fireEvent.click(screen.getByRole("button", { name: /返回应用/ }));
  await act(async () => {
    pendingHealth.reject(new Error("health unavailable"));
    await pendingHealth.promise.catch(() => undefined);
  });

  expect(screen.getByText("本机后台暂时无法连接")).toBeTruthy();
  const retryButton = screen.getByRole("button", { name: "重新检查本机后台状态" });
  fireEvent.click(retryButton);

  await waitFor(() => {
    expect(refreshHealth).toHaveBeenCalledTimes(2);
    expect(retryButton.hasAttribute("disabled")).toBe(false);
  });
  expect(screen.getByText("本机后台暂时无法连接")).toBeTruthy();

  fireEvent.click(retryButton);

  await waitFor(() => {
    expect(refreshHealth).toHaveBeenCalledTimes(3);
    expect(screen.queryByText("本机后台暂时无法连接")).toBeNull();
  });
});

test("retries the global health refresh after a provider was saved", async () => {
  const api = providerApi({
    listProviders: vi.fn().mockResolvedValue([]),
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["legal-model"],
      selected_model: "legal-model",
    }),
    createProvider: vi.fn().mockResolvedValue({
      ...savedOllama,
      name: "Firm Gateway",
      kind: "openai_compatible",
      base_url: "https://models.example.test/v1",
      default_model: "legal-model",
      auth_mode: "none",
    }),
  });
  const pendingRetry = deferred<void>();
  const onProvidersChanged = vi.fn()
    .mockRejectedValueOnce(new Error("health unavailable"))
    .mockReturnValueOnce(pendingRetry.promise);

  render(
    <SettingsView
        selectedCategory="models"
      configured={zeroConfigured}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={api}
      onProvidersChanged={onProvidersChanged}
    />,
  );

  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Firm Gateway" } });
  fireEvent.change(screen.getByLabelText("认证方式"), { target: { value: "none" } });
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://models.example.test/v1" },
  });
  fireEvent.change(screen.getByLabelText("默认模型"), { target: { value: "legal-model" } });
  fireEvent.click(screen.getByRole("button", { name: "测试并发现模型" }));
  await screen.findByText("连接测试通过，发现 1 个模型");
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));

  expect(await screen.findByText(/全局连接状态尚未同步/)).toBeTruthy();
  const retryButton = screen.getByRole("button", { name: "重新同步连接状态" });
  fireEvent.click(retryButton);

  expect(retryButton.hasAttribute("disabled")).toBe(true);
  fireEvent.click(retryButton);
  expect(onProvidersChanged).toHaveBeenCalledTimes(2);

  await act(async () => {
    pendingRetry.resolve();
    await pendingRetry.promise;
  });

  expect(await screen.findByText("连接状态已同步")).toBeTruthy();
  expect(onProvidersChanged).toHaveBeenCalledTimes(2);
});

test("turns structured provider failures into actionable recovery guidance", async () => {
  const api = providerApi({
    listProviders: vi.fn().mockResolvedValue([]),
    testProviderConnection: vi.fn().mockRejectedValue(
      new RuntimeApiError(
        "Provider rejected the configured credentials.",
        { code: "provider_auth_failed", retryable: false, status: 401 },
      ),
    ),
  });

  render(
    <SettingsView
        selectedCategory="models"
      configured={zeroConfigured}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={api}
      onProvidersChanged={async () => undefined}
    />,
  );

  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Firm Gateway" } });
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://models.example.test/v1" },
  });
  fireEvent.change(screen.getByLabelText("默认模型"), { target: { value: "legal-model" } });
  fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "transient-key" } });
  fireEvent.click(screen.getByRole("button", { name: "测试并发现模型" }));

  expect(await screen.findByText(/请检查 API Key/)).toBeTruthy();
});

test("ignores a stale connection-test response after the draft changes", async () => {
  const pendingTest = deferred<{ models: string[]; selected_model: string }>();
  const api = providerApi({
    listProviders: vi.fn().mockResolvedValue([]),
    testProviderConnection: vi.fn().mockReturnValue(pendingTest.promise),
  });

  render(
    <SettingsView
        selectedCategory="models"
      configured={zeroConfigured}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={api}
      onProvidersChanged={async () => undefined}
    />,
  );

  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Firm Gateway" } });
  fireEvent.change(screen.getByLabelText("认证方式"), { target: { value: "none" } });
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://models.example.test/v1" },
  });
  fireEvent.change(screen.getByLabelText("默认模型"), { target: { value: "old-model" } });
  fireEvent.click(screen.getByRole("button", { name: "测试并发现模型" }));

  fireEvent.change(screen.getByLabelText("默认模型"), { target: { value: "new-model" } });
  await act(async () => {
    pendingTest.resolve({ models: ["old-model"], selected_model: "old-model" });
    await pendingTest.promise;
  });

  expect((screen.getByLabelText("默认模型") as HTMLInputElement).value).toBe("new-model");
  expect(screen.queryByText(/连接测试通过/)).toBeNull();
});

test("does not dismiss the wizard while a provider save is in flight", async () => {
  const pendingSave = deferred<typeof savedOllama>();
  const api = providerApi({
    listProviders: vi.fn().mockResolvedValue([]),
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["qwen2.5:7b"],
      selected_model: "qwen2.5:7b",
    }),
    createProvider: vi.fn().mockReturnValue(pendingSave.promise),
  });

  render(
    <SettingsView
        selectedCategory="models"
      configured={zeroConfigured}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={api}
      onProvidersChanged={async () => undefined}
    />,
  );

  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("提供商类型"), { target: { value: "ollama" } });
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Local Ollama" } });
  fireEvent.change(screen.getByLabelText("默认模型"), { target: { value: "qwen2.5:7b" } });
  fireEvent.click(screen.getByRole("button", { name: "发现并测试 Ollama" }));
  await screen.findByText("连接测试通过，发现 1 个模型");
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));

  expect(screen.getByRole("button", { name: "关闭连接向导" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "取消" }).hasAttribute("disabled")).toBe(true);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.getByRole("dialog", { name: "添加模型连接" })).toBeTruthy();

  await act(async () => {
    pendingSave.resolve(savedOllama);
    await pendingSave.promise;
  });
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "添加模型连接" })).toBeNull();
  });
});

test("keeps keyboard focus inside the provider dialog", async () => {
  render(
    <SettingsView
        selectedCategory="models"
      configured={zeroConfigured}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={providerApi()}
      onProvidersChanged={async () => undefined}
    />,
  );

  await screen.findByText("Local Ollama");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  const closeButton = screen.getByRole("button", { name: "关闭连接向导" });
  const cancelButton = screen.getByRole("button", { name: "取消" });

  cancelButton.focus();
  fireEvent.keyDown(document, { key: "Tab" });
  expect(document.activeElement).toBe(closeButton);

  closeButton.focus();
  fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
  expect(document.activeElement).toBe(cancelButton);
});

test("restores focus to the connection button after the wizard closes", async () => {
  render(
    <SettingsView
        selectedCategory="models"
      configured={zeroConfigured}
      reduceMotion={false}
      onReduceMotionChange={() => undefined}
      providerApi={providerApi()}
      onProvidersChanged={async () => undefined}
    />,
  );

  await screen.findByText("Local Ollama");
  const openButton = screen.getByRole("button", { name: "添加模型连接" });
  fireEvent.click(openButton);
  fireEvent.keyDown(document, { key: "Escape" });

  await waitFor(() => expect(document.activeElement).toBe(openButton));
});

test("deletes a saved provider in one click and refreshes global state", async () => {
  const api = providerApi({
    deleteProvider: vi.fn().mockResolvedValue(undefined),
  });
  const refreshHealth = vi.fn().mockResolvedValue(zeroHealth);

  render(
    <App
      initialHealth={{ ...zeroHealth, configured: { ...zeroConfigured, models: 1 } }}
      providerApi={api}
      threadApi={emptyThreadApi()}
      refreshHealth={refreshHealth}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "模型", exact: true }));
  await screen.findByText("Local Ollama");

  fireEvent.click(screen.getByRole("button", { name: "删除 Local Ollama" }));

  expect(await screen.findByText("未连接任何模型提供商")).toBeTruthy();
  expect(screen.queryByText("Local Ollama")).toBeNull();
  expect(api.deleteProvider).toHaveBeenCalledWith("provider-1");
  expect(refreshHealth).toHaveBeenCalled();
});

test("keeps the row and disables actions while a single deletion is pending", async () => {
  const pending = deferred<void>();
  const api = providerApi({ deleteProvider: vi.fn().mockReturnValue(pending.promise) });
  render(
    <App
      initialHealth={{ ...zeroHealth, configured: { ...zeroConfigured, models: 1 } }}
      providerApi={api}
      threadApi={emptyThreadApi()}
      refreshHealth={vi.fn().mockResolvedValue(zeroHealth)}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "模型", exact: true }));
  await screen.findByText("Local Ollama");

  const remove = screen.getByRole("button", { name: "删除 Local Ollama" }) as HTMLButtonElement;
  fireEvent.click(remove);
  fireEvent.click(remove);
  expect(remove.disabled).toBe(true);
  expect(remove.textContent).toContain("正在删除");
  expect((screen.getByRole("button", { name: "测试 Local Ollama" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "发现 Local Ollama 的模型" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("Local Ollama")).toBeTruthy();
  expect(api.deleteProvider).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve());
  expect(screen.queryByText("Local Ollama")).toBeNull();
});

test("keeps the connection when an active turn is still using it", async () => {
  const api = providerApi({
    deleteProvider: vi.fn().mockRejectedValue(
      new RuntimeApiError("Provider is in use.", {
        code: "provider_in_use",
        retryable: false,
        status: 409,
      }),
    ),
  });

  render(
    <App
      initialHealth={{ ...zeroHealth, configured: { ...zeroConfigured, models: 1 } }}
      providerApi={api}
      threadApi={emptyThreadApi()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(screen.getByRole("button", { name: "模型", exact: true }));
  await screen.findByText("Local Ollama");

  fireEvent.click(screen.getByRole("button", { name: "删除 Local Ollama" }));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("请结束当前任务后再删除");
  expect((screen.getByRole("button", { name: "删除 Local Ollama" }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "删除 Local Ollama" }));
  await waitFor(() => expect(api.deleteProvider).toHaveBeenCalledTimes(2));
  expect(screen.getByText("Local Ollama")).toBeTruthy();
  expect(api.deleteProvider).toHaveBeenCalledWith("provider-1");
});
