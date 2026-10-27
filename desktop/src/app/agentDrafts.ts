import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";
const prefix = "verilex:agent-draft:v1:";
const pending = new Map<string, string>();
export function readDraft(key: string): string {
  if (pending.has(key)) return pending.get(key)!;
  try { return localStorage.getItem(prefix + key) ?? ""; } catch { return ""; }
}
export function flushDraft(key: string): boolean {
  if (!pending.has(key)) return true;
  try {
    const value = pending.get(key)!;
    if (value) localStorage.setItem(prefix + key, value); else localStorage.removeItem(prefix + key);
    pending.delete(key);
    return true;
  } catch { window.dispatchEvent(new Event("verilex-draft-storage-error")); return false; }
}
export function writeDraft(key: string, text: string): void {
  pending.set(key, text);
  // Native Cmd-Q may destroy the WebView without pagehide. Persist before
  // returning from the input handler so the last edit has no debounce gap.
  flushDraft(key);
}
export function clearDraft(key: string) { writeDraft(key, ""); flushDraft(key); }
export function moveDraft(from: string, to: string) {
  writeDraft(to, readDraft(from)); flushDraft(to); clearDraft(from);
}
export function useAgentDraft(key: string): [string, (value: SetStateAction<string>) => void, boolean] {
  const [storageFailed, setStorageFailed] = useState(false);
  useEffect(() => {
    const failed = () => setStorageFailed(true);
    window.addEventListener("verilex-draft-storage-error", failed);
    return () => window.removeEventListener("verilex-draft-storage-error", failed);
  }, []);
  const [state, setState] = useState(() => ({key, value: readDraft(key)}));
  const current = useRef(state);
  if (current.current.key !== key) current.current = {key, value: readDraft(key)};
  else if (state.key === key) current.current = state;
  const set = useCallback((update: SetStateAction<string>) => {
    const value = typeof update === "function" ? update(current.current.value) : update;
    current.current = {key, value};
    writeDraft(key, value);
    setState({key, value});
  }, [key]);
  useEffect(() => {
    const flush = () => flushDraft(key);
    window.addEventListener("pagehide", flush);
    window.addEventListener("blur", flush);
    return () => { flush(); window.removeEventListener("pagehide", flush); window.removeEventListener("blur", flush); };
  }, [key]);
  return [current.current.value, set, storageFailed];
}
