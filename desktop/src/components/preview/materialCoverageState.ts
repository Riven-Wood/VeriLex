import {useSyncExternalStore} from 'react';

export interface Coverage {status: string; gaps: string[]; notice: string}
const values = new Map<string, Coverage>();
const listeners = new Set<() => void>();
const key = (workspaceId: string, path: string) => JSON.stringify([workspaceId, path]);
export function publishCoverage(workspaceId: string, path: string, value: Coverage) {
  values.set(key(workspaceId, path), value);
  // Bound this optional UI cache. Inspection is always rerun when a file opens.
  if (values.size > 500) values.delete(values.keys().next().value!);
  listeners.forEach(listener => listener());
}
export function useMaterialCoverage(workspaceId: string, path: string) {
  return useSyncExternalStore(listener => {listeners.add(listener); return () => {listeners.delete(listener);};},
    () => values.get(key(workspaceId, path)));
}
