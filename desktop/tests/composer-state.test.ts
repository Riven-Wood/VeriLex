import { expect, test } from "vitest";

import {
  beginNewTask,
  bindHiddenDraft,
  initialComposerState,
  leaveComposerTask,
  materializeComposerTask,
} from "../src/composer/state";
import {
  chooseComposerContext,
  parseStoredModelPreference,
  serializeModelPreference,
} from "../src/composer/context";
import type { ProviderRecord } from "../src/runtime/contracts";

const providers: ProviderRecord[] = [
  {
    id: "provider-1",
    name: "First",
    kind: "openai_compatible",
    base_url: "https://first.example.test/v1",
    default_model: "model-a",
    auth_mode: "none",
    has_secret: false,
    enabled: true,
    created_at: "2026-08-19T00:00:00Z",
    updated_at: "2026-08-19T00:00:00Z",
  },
  {
    id: "provider-2",
    name: "Second",
    kind: "ollama",
    base_url: "http://127.0.0.1:11434",
    default_model: "model-b",
    auth_mode: "none",
    has_secret: false,
    enabled: true,
    created_at: "2026-08-19T00:00:00Z",
    updated_at: "2026-08-19T00:00:00Z",
  },
];

test("new task stays client-only until a hidden runtime draft is needed", () => {
  const context = { providerId: "provider-1", model: "model-a", workspaceId: null, reasoningEffort: "auto" as const };
  const ambient = initialComposerState(context);
  const clientDraft = beginNewTask(ambient, context);
  const hiddenDraft = bindHiddenDraft(clientDraft, "draft-1");
  const formal = materializeComposerTask(hiddenDraft);

  expect(ambient.mode).toBe("ambient");
  expect(clientDraft).toMatchObject({ mode: "new_task", hiddenThreadId: null });
  expect(hiddenDraft).toMatchObject({ mode: "new_task", hiddenThreadId: "draft-1" });
  expect(formal).toMatchObject({ mode: "formal", hiddenThreadId: null });
});

test("leaving a hidden draft requests one discard and clears workspace attachments and queue", () => {
  const context = { providerId: "provider-1", model: "model-a", workspaceId: null, reasoningEffort: "auto" as const };
  const hidden = {
    ...bindHiddenDraft(beginNewTask(initialComposerState(context), context), "draft-1"),
    context: { ...context, workspaceId: "workspace-1" },
    attachmentIds: ["upl-1"],
    queued: { text: "later" },
  };

  const transition = leaveComposerTask(hidden, context);

  expect(transition.discardDraftId).toBe("draft-1");
  expect(transition.state).toEqual(initialComposerState(context));
  expect(leaveComposerTask(transition.state, context).discardDraftId).toBeNull();
});

test("new task inherits only the last still-usable provider and model", () => {
  const stored = serializeModelPreference({ providerId: "provider-2", model: "custom-b" });

  expect(chooseComposerContext(providers, parseStoredModelPreference(stored))).toEqual({
    providerId: "provider-2",
    model: "custom-b",
    workspaceId: null,
    reasoningEffort: "auto",
  });
  expect(
    chooseComposerContext(providers, { providerId: "missing", model: "stale" }),
  ).toEqual({ providerId: "provider-1", model: "model-a", workspaceId: null, reasoningEffort: "auto" });
  expect(chooseComposerContext([], parseStoredModelPreference(stored))).toBeNull();
});

test("invalid stored model preference is ignored without throwing", () => {
  expect(parseStoredModelPreference("not-json")).toBeNull();
  expect(parseStoredModelPreference('{"providerId":"provider-1"}')).toBeNull();
  expect(parseStoredModelPreference(null)).toBeNull();
});
