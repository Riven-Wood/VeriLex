import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ComposerContextMenus } from "../src/components/ComposerContextMenus";
import { RuntimeApiError } from "../src/runtime/api";
import type { ProviderRecord } from "../src/runtime/contracts";

afterEach(cleanup);

const provider: ProviderRecord = {
  id: "provider-1",
  name: "Local Model",
  kind: "ollama",
  base_url: "http://127.0.0.1:11434",
  default_model: "model-a",
  auth_mode: "none",
  has_secret: false,
  enabled: true,
  created_at: "2026-08-19T00:00:00Z",
  updated_at: "2026-08-19T00:00:00Z",
};

const secondProvider: ProviderRecord = {
  ...provider,
  id: "provider-2",
  name: "Second Model",
  default_model: "model-b",
};

test("model menu selects an unknown vision model without a declaration step", async () => {
  const onSelectModel = vi.fn();
  const api = {
    discoverProviderModels: vi.fn().mockResolvedValue({
      models: ["model-a", "vision-b"],
      selected_model: "model-a",
    }),
    listModelCapabilities: vi.fn().mockResolvedValue([]),
  };
  render(
    <ComposerContextMenus
      kind="model"
      providers={[provider]}
      context={{ providerId: "provider-1", model: "model-a", workspaceId: null }}
      onSelectModel={onSelectModel}
      onSelectWorkspace={() => {}}
      onClose={() => {}}
      api={api}
    />,
  );

  fireEvent.click(await screen.findByRole("button", { name: "vision-b" }));
  expect(onSelectModel).toHaveBeenCalledWith("provider-1", "vision-b", undefined);
  expect(screen.queryByRole("button", { name: "确认此模型支持图片" })).toBeNull();
});

test("model menu keeps the thread's exact current model when discovery omits it", async () => {
  render(
    <ComposerContextMenus
      kind="model"
      providers={[provider]}
      context={{
        providerId: "provider-1",
        model: "pinned-model:2026-08-19",
        workspaceId: null,
      }}
      onSelectModel={() => {}}
      onSelectWorkspace={() => {}}
      onClose={() => {}}
      api={{
        discoverProviderModels: vi.fn().mockResolvedValue({
          models: ["model-a"],
          selected_model: "model-a",
        }),
        listModelCapabilities: vi.fn().mockResolvedValue([]),
      }}
    />,
  );

  expect(await screen.findByRole("button", { name: "pinned-model:2026-08-19" }))
    .not.toBeNull();
  expect(screen.getByRole("button", { name: "model-a" })).not.toBeNull();
});

test("model menu ignores a stale response from the previously selected provider", async () => {
  let resolveFirst: ((value: { models: string[]; selected_model: string }) => void) | null = null;
  const firstResponse = new Promise<{ models: string[]; selected_model: string }>((resolve) => {
    resolveFirst = resolve;
  });
  const discoverProviderModels = vi.fn().mockImplementation((providerId: string) =>
    providerId === "provider-1"
      ? firstResponse
      : Promise.resolve({ models: ["model-b-live"], selected_model: "model-b-live" }),
  );
  render(
    <ComposerContextMenus
      kind="model"
      providers={[provider, secondProvider]}
      context={{ providerId: "provider-1", model: "model-a", workspaceId: null }}
      onSelectModel={() => {}}
      onSelectWorkspace={() => {}}
      onClose={() => {}}
      api={{
        discoverProviderModels,
        listModelCapabilities: vi.fn().mockResolvedValue([]),
      }}
    />,
  );

  await waitFor(() => expect(discoverProviderModels).toHaveBeenCalledWith("provider-1"));
  fireEvent.change(screen.getByRole("combobox", { name: "模型提供方" }), {
    target: { value: "provider-2" },
  });
  expect(await screen.findByRole("button", { name: "model-b-live" })).not.toBeNull();

  await act(async () => {
    resolveFirst?.({ models: ["stale-model-a"], selected_model: "stale-model-a" });
    await Promise.resolve();
  });
  expect(screen.queryByRole("button", { name: "stale-model-a" })).toBeNull();
  expect(screen.getByRole("button", { name: "model-b-live" })).not.toBeNull();
});

test("workspace menu creates a read-write workspace from the chosen folder", async () => {
  const onSelectWorkspace = vi.fn();
  const api = {
    listWorkspaces: vi.fn().mockResolvedValue([]),
    createWorkspace: vi.fn().mockResolvedValue({
      id: "workspace-1",
      root_path: "/Users/test/Contracts",
      display_name: "Contracts",
      allow_read: true,
      allow_write: true,
      created_at: "2026-08-19T00:00:00Z",
      last_used_at: "2026-08-19T00:00:00Z",
    }),
  };
  render(
    <ComposerContextMenus
      kind="workspace"
      providers={[provider]}
      context={{ providerId: "provider-1", model: "model-a", workspaceId: null }}
      onSelectModel={() => {}}
      onSelectWorkspace={onSelectWorkspace}
      onClose={() => {}}
      api={api}
      pickDirectory={vi.fn().mockResolvedValue("/Users/test/Contracts")}
    />,
  );

  fireEvent.click(await screen.findByRole("button", { name: "选择新文件夹" }));
  await waitFor(() => {
    expect(api.createWorkspace).toHaveBeenCalledWith({
      root_path: "/Users/test/Contracts",
      display_name: "Contracts",
      allow_read: true,
      allow_write: true,
    });
  });
  expect(onSelectWorkspace).toHaveBeenCalledWith(
    expect.objectContaining({ id: "workspace-1", allow_write: true }),
  );
});

test("workspace duplicate race refreshes and selects the existing record", async () => {
  const onSelectWorkspace = vi.fn();
  const existingWorkspace = {
    id: "workspace-existing",
    root_path: "/Users/test/Contracts",
    display_name: "Contracts",
    allow_read: true,
    allow_write: true,
    created_at: "2026-08-19T00:00:00Z",
    last_used_at: "2026-08-19T00:00:00Z",
  };
  const api = {
    listWorkspaces: vi.fn()
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([existingWorkspace]),
    createWorkspace: vi.fn().mockRejectedValue(new RuntimeApiError(
      "Workspace root path already exists",
      { code: "workspace_root_path_exists", retryable: false, status: 409 },
    )),
  };
  render(
    <ComposerContextMenus
      kind="workspace"
      providers={[provider]}
      context={{ providerId: "provider-1", model: "model-a", workspaceId: null }}
      onSelectModel={() => {}}
      onSelectWorkspace={onSelectWorkspace}
      onClose={() => {}}
      api={api}
      pickDirectory={vi.fn().mockResolvedValue("/Users/test/Contracts")}
    />,
  );

  fireEvent.click(await screen.findByRole("button", { name: "选择新文件夹" }));
  await waitFor(() => {
    expect(api.listWorkspaces).toHaveBeenCalledTimes(2);
    expect(onSelectWorkspace).toHaveBeenCalledWith(existingWorkspace);
  });
});

test("menu dismisses on pointerdown outside and Escape", async () => {
  const onClose = vi.fn();
  render(
    <ComposerContextMenus
      kind="workspace"
      providers={[provider]}
      context={{ providerId: "provider-1", model: "model-a", workspaceId: null }}
      onSelectModel={() => {}}
      onSelectWorkspace={() => {}}
      onClose={onClose}
      api={{ listWorkspaces: vi.fn().mockResolvedValue([]) }}
    />,
  );

  await screen.findByRole("dialog", { name: "选择工作区" });

  // Clicking inside the popover keeps it open.
  fireEvent.pointerDown(screen.getByRole("dialog", { name: "选择工作区" }));
  expect(onClose).not.toHaveBeenCalled();

  // Clicking blank space anywhere else dismisses it.
  fireEvent.pointerDown(document.body);
  expect(onClose).toHaveBeenCalledTimes(1);

  // Escape dismisses too.
  fireEvent.keyDown(document.body, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(2);
});

test("a deleted provider does not strand the model picker", async () => {
  const onSelectModel = vi.fn();
  const discoverProviderModels = vi.fn().mockResolvedValue({ models: ["model-a"], selected_model: "model-a" });
  render(<ComposerContextMenus kind="model" providers={[provider]}
    context={{ providerId: "deleted-provider", model: "old-model", workspaceId: null, reasoningEffort: "auto" }}
    onSelectModel={onSelectModel} onSelectWorkspace={() => {}} onClose={() => {}}
    api={{ discoverProviderModels, listModelCapabilities: vi.fn().mockResolvedValue([]) }} />);
  fireEvent.click(await screen.findByRole("button", { name: "model-a" }));
  expect(discoverProviderModels).toHaveBeenCalledWith("provider-1");
  expect(onSelectModel).toHaveBeenCalledWith("provider-1", "model-a", undefined);
});
