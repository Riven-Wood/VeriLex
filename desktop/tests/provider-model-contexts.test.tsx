import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ProviderModelContexts } from "../src/components/ProviderModelContexts";

afterEach(cleanup);

const provider = {
  id: "p1", name: "Gateway", kind: "openai_compatible" as const,
  base_url: "https://api.test/v1", default_model: "provider-model",
  auth_mode: "none" as const, has_secret: false, enabled: true,
  created_at: "now", updated_at: "now",
};

const rows = [
  {
    provider_id: "p1", model: "manual-model", context_window_tokens: 64_000,
    context_window_source: "user_declared" as const, automatic_tokens: 128_000,
    automatic_source: "provider" as const, manual_tokens: 64_000,
    is_available: true, updated_at: "now",
  },
  {
    provider_id: "p1", model: "provider-model", context_window_tokens: 128_000,
    context_window_source: "provider" as const, automatic_tokens: 128_000,
    automatic_source: "provider" as const, manual_tokens: null,
    is_available: true, updated_at: "now",
  },
  {
    provider_id: "p1", model: "curated-model", context_window_tokens: 200_000,
    context_window_source: "curated" as const, automatic_tokens: 200_000,
    automatic_source: "curated" as const, manual_tokens: null,
    is_available: true, updated_at: "now",
  },
  {
    provider_id: "p1", model: "unknown-model", context_window_tokens: null,
    context_window_source: "unknown" as const, automatic_tokens: null,
    automatic_source: "unknown" as const, manual_tokens: null,
    is_available: false, updated_at: "now",
  },
];

function api(overrides = {}) {
  return {
    listProviders: vi.fn(), testProviderConnection: vi.fn(), createProvider: vi.fn(),
    testSavedProvider: vi.fn(), discoverProviderModels: vi.fn(), deleteProvider: vi.fn(),
    listModelContextWindows: vi.fn().mockResolvedValue(rows),
    refreshModelContextWindows: vi.fn().mockResolvedValue(rows),
    setModelContextWindowOverride: vi.fn(), resetModelContextWindowOverride: vi.fn(),
    ...overrides,
  };
}

test("loads cached rows and labels automatic, curated, manual, unknown and unavailable states", async () => {
  render(<ProviderModelContexts api={api()} provider={provider} onChanged={() => undefined} />);

  expect(await screen.findByRole("region", { name: "Gateway 的模型上下文上限" })).toBeTruthy();
  expect(await screen.findByText("手动 64K")).toBeTruthy();
  expect(screen.getByText("128K · Provider 自动")).toBeTruthy();
  expect(screen.getByText("200K · VeriLex 资料")).toBeTruthy();
  expect(screen.getByText("未知 · 当前不可用")).toBeTruthy();
});

test("saves one model's K value and resets to the latest automatic value", async () => {
  const updated = { ...rows[1], context_window_tokens: 64_000,
    context_window_source: "user_declared" as const, manual_tokens: 64_000 };
  const reset = { ...updated, context_window_tokens: 200_000,
    context_window_source: "provider" as const, automatic_tokens: 200_000,
    manual_tokens: null };
  const client = api({
    setModelContextWindowOverride: vi.fn().mockResolvedValue(updated),
    resetModelContextWindowOverride: vi.fn().mockResolvedValue(reset),
  });
  const onChanged = vi.fn();
  render(<ProviderModelContexts api={client} provider={provider} onChanged={onChanged} />);
  await screen.findByText("provider-model");

  fireEvent.change(screen.getByLabelText("设置 provider-model 上下文上限"), {
    target: { value: "64K" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存 provider-model 上下文上限" }));
  await waitFor(() => expect(client.setModelContextWindowOverride)
    .toHaveBeenCalledWith("p1", "provider-model", 64_000));
  expect(await screen.findAllByText("手动 64K")).toHaveLength(2);

  fireEvent.click(screen.getByRole("button", { name: "恢复 provider-model 自动上限" }));
  expect(await screen.findByText("200K · Provider 自动")).toBeTruthy();
  expect(onChanged).toHaveBeenCalledTimes(2);
});

test("refresh keeps old rows on failure and offers retry", async () => {
  const client = api({ refreshModelContextWindows: vi.fn().mockRejectedValue(new Error("offline")) });
  render(<ProviderModelContexts api={client} provider={provider} onChanged={() => undefined} />);
  await screen.findByText("provider-model");

  fireEvent.click(screen.getByRole("button", { name: "刷新 Gateway 的自动资料" }));

  expect(await screen.findByText("自动资料刷新失败，请重试。")).toBeTruthy();
  expect(screen.getByText("provider-model")).toBeTruthy();
  expect(screen.getByRole("button", { name: "重试刷新 Gateway 的自动资料" })).toBeTruthy();
});

test("renders long model lists inside the bounded container", async () => {
  const many = Array.from({ length: 40 }, (_, index) => ({
    ...rows[1], model: `model-${index}`,
  }));
  const client = api({ listModelContextWindows: vi.fn().mockResolvedValue(many) });
  const { container } = render(
    <ProviderModelContexts api={client} provider={provider} onChanged={() => undefined} />,
  );
  await screen.findByText("model-39");
  expect(container.querySelector(".provider-context-list")?.getAttribute("role")).toBe("list");
});
