import { createContext, useContext, useMemo, useRef, type ReactNode } from 'react';
import type { ContractEditorTurnContext } from '../../runtime/contractEditorClient';

interface Binding { capture: () => ContractEditorTurnContext; sent: () => void; refresh?: () => void }
interface EditorContext {
  capture: (threadId: string) => ContractEditorTurnContext | null;
  sent: (threadId: string) => void;
  refresh: (threadId: string) => void;
  register: (threadId: string, binding: Binding) => () => void;
}
const Context = createContext<EditorContext>({ capture: () => null, sent: () => {}, refresh: () => {}, register: () => () => {} });
export const useContractEditorContext = () => useContext(Context);

export function ContractEditorProvider({ children }: { children: ReactNode }) {
  const bindings = useRef(new Map<string, Binding>());
  const captured = useRef(new Map<string, Binding>());
  const value = useMemo<EditorContext>(() => ({
    capture: threadId => {
      const binding = bindings.current.get(threadId);
      if (!binding) return null;
      const snapshot = binding.capture();
      captured.current.set(threadId, binding);
      return snapshot;
    },
    sent: threadId => {
      const binding = captured.current.get(threadId);
      if (binding && binding === bindings.current.get(threadId)) binding.sent();
      captured.current.delete(threadId);
    },
    refresh: threadId => bindings.current.get(threadId)?.refresh?.(),
    register: (threadId, binding) => {
      bindings.current.set(threadId, binding);
      return () => { if (bindings.current.get(threadId) === binding) bindings.current.delete(threadId); };
    },
  }), []);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
