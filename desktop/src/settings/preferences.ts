import { useSyncExternalStore } from "react";

export interface UiPreferences {
  defaultWorkspaceId: string;
  theme: "light" | "dark" | "system";
  fontSize: "small" | "default" | "large";
  reduceMotion: boolean;
  sendMode: "enter" | "modifier";
  executionDetail: "summary" | "detailed";
  followUp: "ask" | "queue" | "interrupt";
}
export const UI_PREFERENCES_KEY = "verilex.ui-preferences.v1";
export const DEFAULT_UI_PREFERENCES: UiPreferences = {
  defaultWorkspaceId: "", theme: "light", fontSize: "default", reduceMotion: false, sendMode: "enter",
  executionDetail: "detailed", followUp: "ask",
};
let cachedRaw: string | null | undefined;
let cached = DEFAULT_UI_PREFERENCES;
const subscribers = new Set<() => void>();
export function readUiPreferences(): UiPreferences {
  let raw: string | null = null;
  try { raw = localStorage.getItem(UI_PREFERENCES_KEY); } catch { /* blocked storage keeps safe defaults */ }
  if (raw === cachedRaw) return cached;
  cachedRaw = raw;
  let source: Partial<UiPreferences> = {};
  try { source = JSON.parse(raw ?? "{}") ?? {}; } catch { /* damaged preferences */ }
  cached = {
    defaultWorkspaceId: typeof source.defaultWorkspaceId === "string" ? source.defaultWorkspaceId : "",
    theme: ["light", "dark", "system"].includes(source.theme ?? "") ? source.theme! : "light",
    fontSize: ["small", "default", "large"].includes(source.fontSize ?? "") ? source.fontSize! : "default",
    reduceMotion: source.reduceMotion === true,
    sendMode: source.sendMode === "modifier" ? "modifier" : "enter",
    executionDetail: source.executionDetail === "summary" ? "summary" : "detailed",
    followUp: ["ask", "queue", "interrupt"].includes(source.followUp ?? "") ? source.followUp! : "ask",
  };
  return cached;
}
export function saveUiPreferences(patch: Partial<UiPreferences>): void {
  // Commit only after storage succeeds, so the UI never promises an unsaved choice.
  localStorage.setItem(UI_PREFERENCES_KEY, JSON.stringify({ ...readUiPreferences(), ...patch }));
  subscribers.forEach(listener => listener());
}
function subscribe(listener: () => void) {
  subscribers.add(listener);
  window.addEventListener("storage", listener);
  return () => { subscribers.delete(listener); window.removeEventListener("storage", listener); };
}
export function useUiPreferences() { return useSyncExternalStore(subscribe, readUiPreferences); }
export function isSendKey(event: {key: string; shiftKey: boolean; metaKey: boolean; ctrlKey: boolean; altKey: boolean; isComposing?: boolean; keyCode?: number}, mode: UiPreferences["sendMode"]) {
  if (event.isComposing || event.keyCode === 229 || event.key !== "Enter" || event.shiftKey || event.altKey) return false;
  return mode === "modifier" ? event.metaKey || event.ctrlKey : !event.metaKey && !event.ctrlKey;
}
