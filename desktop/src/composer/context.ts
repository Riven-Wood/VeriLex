import type { ProviderRecord } from "../runtime/contracts";

import type { ComposerContext, ModelPreference } from "./state";

export function chooseComposerContext(
  providers: readonly ProviderRecord[],
  preference: ModelPreference | null,
): ComposerContext | null {
  const enabled = providers.filter((provider) => provider.enabled);
  if (enabled.length === 0) return null;
  const preferred = preference
    ? enabled.find((provider) => provider.id === preference.providerId)
    : undefined;
  if (preferred && preference) {
    return {
      providerId: preferred.id,
      model: preference.model,
      workspaceId: null,
      reasoningEffort: "auto",
    };
  }
  return {
    providerId: enabled[0].id,
    model: enabled[0].default_model,
    workspaceId: null,
    reasoningEffort: "auto",
  };
}

export function parseStoredModelPreference(
  serialized: string | null,
): ModelPreference | null {
  if (!serialized) return null;
  try {
    const parsed = JSON.parse(serialized) as Record<string, unknown>;
    if (
      typeof parsed.providerId !== "string" ||
      !parsed.providerId ||
      typeof parsed.model !== "string" ||
      !parsed.model
    ) {
      return null;
    }
    return { providerId: parsed.providerId, model: parsed.model };
  } catch {
    return null;
  }
}

export function serializeModelPreference(preference: ModelPreference): string {
  return JSON.stringify(preference);
}
