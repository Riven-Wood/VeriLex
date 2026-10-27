import type { ReasoningEffort } from "../runtime/contracts";

export interface ComposerContext {
  providerId: string;
  model: string;
  workspaceId: string | null;
  reasoningEffort: ReasoningEffort;
}

export interface ModelPreference {
  providerId: string;
  model: string;
}

export interface ComposerState {
  mode: "ambient" | "new_task" | "formal";
  hiddenThreadId: string | null;
  context: ComposerContext;
  attachmentIds: string[];
  queued: { text: string } | null;
}

export function initialComposerState(context: ComposerContext): ComposerState {
  return {
    mode: "ambient",
    hiddenThreadId: null,
    context,
    attachmentIds: [],
    queued: null,
  };
}

export function beginNewTask(
  _current: ComposerState,
  context: ComposerContext,
): ComposerState {
  return { ...initialComposerState(context), mode: "new_task" };
}

export function bindHiddenDraft(
  current: ComposerState,
  threadId: string,
): ComposerState {
  return { ...current, hiddenThreadId: threadId };
}

export function materializeComposerTask(current: ComposerState): ComposerState {
  return {
    ...current,
    mode: "formal",
    hiddenThreadId: null,
    attachmentIds: [],
    queued: null,
  };
}

export function leaveComposerTask(
  current: ComposerState,
  nextContext: ComposerContext,
): { state: ComposerState; discardDraftId: string | null } {
  return {
    state: initialComposerState(nextContext),
    discardDraftId: current.hiddenThreadId,
  };
}
