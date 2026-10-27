import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { SettingsView } from "../src/views/SettingsView";
import type { HealthResponse } from "../src/runtime/contracts";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
});

const immediateDiscovery = { providerAutoDiscoverDelayMs: 0 } as const;

const zeroConfigured = { models: 0, mcp_servers: 0, skills: 0, folders: 0 };
const zeroHealth: HealthResponse = {
  ok: true,
  service: "verilex-runtime",
  version: "0.3.0",
  storage: "local",
  cloud_backend: false,
  configured: zeroConfigured,
};

function providerApi(overrides = {}) {
  return {
    listProviders: vi.fn().mockResolvedValue([]),
    testProviderConnection: vi.fn(),
    createProvider: vi.fn(),
    testSavedProvider: vi.fn(),
    discoverProviderModels: vi.fn(),
    listModelContextWindows: vi.fn(),
    refreshModelContextWindows: vi.fn(),
    setModelContextWindowOverride: vi.fn(),
    resetModelContextWindowOverride: vi.fn(),
    deleteProvider: vi.fn(),
    ...overrides,
  };
}

test("auto-discovers models after base URL and API key are filled", async () => {
  const api = providerApi({
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["deepseek-v4-flash", "deepseek-v4-pro"],
      selected_model: "deepseek-v4-flash",
    }),
  });

  render(
    <SettingsView
        selectedCategory="models"
      {...immediateDiscovery}
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

  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "DeepSeek" } });
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://api.deepseek.com" },
  });
  fireEvent.change(screen.getByLabelText("API Key"), {
    target: { value: "sk-test-key" },
  });

  expect(await screen.findByText(/连接测试通过，发现 2 个模型/)).toBeTruthy();
  expect(screen.getByRole("option", { name: "deepseek-v4-flash" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "deepseek-v4-pro" })).toBeTruthy();
});

test("shows discovering placeholder before models are found", async () => {
  let resolveTest!: (value: { models: string[]; selected_model: string }) => void;
  const api = providerApi({
    testProviderConnection: vi.fn().mockReturnValue(
      new Promise((resolve) => {
        resolveTest = resolve;
      }),
    ),
  });

  render(
    <SettingsView
        selectedCategory="models"
      {...immediateDiscovery}
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

  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "DeepSeek" } });
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://api.deepseek.com" },
  });
  fireEvent.change(screen.getByLabelText("API Key"), {
    target: { value: "sk-test-key" },
  });

  await waitFor(() => {
    expect(screen.getByLabelText("默认模型").getAttribute("placeholder")).toBe("正在发现可用模型…");
  });

  act(() => {
    resolveTest({ models: ["model-a"], selected_model: "model-a" });
  });

  expect(await screen.findByText(/连接测试通过/)).toBeTruthy();
});

test("does not auto-discover before API key is entered", async () => {
  const api = providerApi({
    testProviderConnection: vi.fn(),
  });

  render(
    <SettingsView
        selectedCategory="models"
      {...immediateDiscovery}
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

  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "DeepSeek" } });
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://api.deepseek.com" },
  });

  expect(api.testProviderConnection).not.toHaveBeenCalled();
  expect(screen.getByLabelText("默认模型").getAttribute("placeholder")).toBe(
    "填写 Base URL 后自动发现模型",
  );
});

test("auto-discover works with no-auth providers", async () => {
  const api = providerApi({
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["qwen2.5:7b"],
      selected_model: "qwen2.5:7b",
    }),
  });

  render(
    <SettingsView
        selectedCategory="models"
      {...immediateDiscovery}
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

  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Ollama Local" } });
  fireEvent.change(screen.getByLabelText("认证方式"), { target: { value: "none" } });
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "http://127.0.0.1:11434" },
  });

  expect(await screen.findByText(/连接测试通过，发现 1 个模型/)).toBeTruthy();
});

test("auto-discover retries after connection fields change", async () => {
  const api = providerApi({
    testProviderConnection: vi.fn()
      .mockResolvedValueOnce({ models: ["old-model"], selected_model: "old-model" })
      .mockResolvedValueOnce({ models: ["new-model"], selected_model: "new-model" }),
  });

  render(
    <SettingsView
        selectedCategory="models"
      {...immediateDiscovery}
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

  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Test" } });
  fireEvent.change(screen.getByLabelText("认证方式"), { target: { value: "none" } });
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://api.first.test" },
  });

  expect(await screen.findByText(/连接测试通过，发现 1 个模型/)).toBeTruthy();
  expect(screen.getByRole("option", { name: "old-model" })).toBeTruthy();

  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://api.second.test" },
  });

  expect(await screen.findByText(/连接测试通过，发现 1 个模型/)).toBeTruthy();
  expect(screen.getByRole("option", { name: "new-model" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "old-model" })).toBeNull();
});

test("shows the selected model automatic context window and provider source", async () => {
  const api = providerApi({
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["model-a"],
      selected_model: "model-a",
      context_windows: [{ model: "model-a", tokens: 128_000, source: "provider" }],
    }),
  });
  render(<SettingsView selectedCategory="models" {...immediateDiscovery} configured={zeroConfigured} reduceMotion={false}
    onReduceMotionChange={() => undefined} providerApi={api}
    onProvidersChanged={async () => undefined} />);

  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Test" } });
  fireEvent.change(screen.getByLabelText("认证方式"), { target: { value: "none" } });
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://api.test" } });

  expect(await screen.findByText("128K · Provider 自动获取")).toBeTruthy();
  expect((screen.getByLabelText("上下文上限") as HTMLInputElement).value).toBe("128K");
});

test("switching the selected model loads that model's own automatic value", async () => {
  const api = providerApi({
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["model-a", "model-b"], selected_model: "model-a",
      context_windows: [
        { model: "model-a", tokens: 128_000, source: "provider" },
        { model: "model-b", tokens: 32_000, source: "curated" },
      ],
    }),
  });
  render(<SettingsView selectedCategory="models" {...immediateDiscovery} configured={zeroConfigured} reduceMotion={false}
    onReduceMotionChange={() => undefined} providerApi={api}
    onProvidersChanged={async () => undefined} />);
  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Test" } });
  fireEvent.change(screen.getByLabelText("认证方式"), { target: { value: "none" } });
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://api.test" } });
  await screen.findByText("128K · Provider 自动获取");

  fireEvent.change(screen.getByLabelText("默认模型"), { target: { value: "model-b" } });

  expect(screen.getByText("32K · VeriLex 内置资料")).toBeTruthy();
  expect((screen.getByLabelText("上下文上限") as HTMLInputElement).value).toBe("32K");
});

test("unknown model accepts a per-model manual value and sends it only after editing", async () => {
  const saved = {
    id: "p1", name: "Test", kind: "openai_compatible" as const,
    base_url: "https://api.test", default_model: "model-a", auth_mode: "none" as const,
    has_secret: false, enabled: true, created_at: "now", updated_at: "now",
  };
  const api = providerApi({
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["model-a"], selected_model: "model-a",
      context_windows: [{ model: "model-a", tokens: null, source: "unknown" }],
    }),
    createProvider: vi.fn().mockResolvedValue(saved),
  });
  render(<SettingsView selectedCategory="models" {...immediateDiscovery} configured={zeroConfigured} reduceMotion={false}
    onReduceMotionChange={() => undefined} providerApi={api}
    onProvidersChanged={async () => undefined} />);
  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Test" } });
  fireEvent.change(screen.getByLabelText("认证方式"), { target: { value: "none" } });
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://api.test" } });
  expect(await screen.findByText("未知，可手动设置")).toBeTruthy();

  fireEvent.change(screen.getByLabelText("上下文上限"), { target: { value: "64K" } });
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));

  await waitFor(() => expect(api.createProvider).toHaveBeenCalledWith(
    expect.objectContaining({ default_model_context_window_tokens: 64_000 }),
  ));
});

test("invalid manual context blocks save while a higher valid value only warns", async () => {
  const api = providerApi({
    testProviderConnection: vi.fn().mockResolvedValue({
      models: ["model-a"], selected_model: "model-a",
      context_windows: [{ model: "model-a", tokens: 128_000, source: "provider" }],
    }),
  });
  render(<SettingsView selectedCategory="models" {...immediateDiscovery} configured={zeroConfigured} reduceMotion={false}
    onReduceMotionChange={() => undefined} providerApi={api}
    onProvidersChanged={async () => undefined} />);
  await screen.findByText("未连接任何模型提供商");
  fireEvent.click(screen.getByRole("button", { name: "添加模型连接" }));
  fireEvent.click(screen.getByRole("button", { name: /其他连接/ }));
  fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "Test" } });
  fireEvent.change(screen.getByLabelText("认证方式"), { target: { value: "none" } });
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://api.test" } });
  await screen.findByText("128K · Provider 自动获取");

  fireEvent.change(screen.getByLabelText("上下文上限"), { target: { value: "128.5K" } });
  expect(screen.getByText("请输入整数，例如 128K、1M 或 32768")).toBeTruthy();
  expect(screen.getByRole("button", { name: "保存连接" }).hasAttribute("disabled")).toBe(true);

  fireEvent.change(screen.getByLabelText("上下文上限"), { target: { value: "256K" } });
  expect(screen.getByText("手动上限高于 Provider 自动值，请确认模型确实支持。")).toBeTruthy();
  expect(screen.getByRole("button", { name: "保存连接" }).hasAttribute("disabled")).toBe(false);
});
