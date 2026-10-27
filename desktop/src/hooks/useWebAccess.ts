import { useCallback, useEffect, useRef, useState } from "react";

import {
  runtimeWebAccessApi,
  type WebAccessApi,
  type WebAccessRecord,
} from "../runtime/webAccessClient";

export interface WebAccessState extends WebAccessRecord {
  threadId: string | null;
  loading: boolean;
  updating: boolean;
  error: string | null;
}

export interface WebAccessHandoff {
  threadId: string;
  operationId: number;
  record: WebAccessRecord;
}

interface UseWebAccessOptions {
  threadId: string | null;
  isRunning: boolean;
  api?: WebAccessApi;
}

const CLOSED_RECORD: WebAccessRecord = {
  enabled: false,
  granted: false,
  enabledAt: null,
  grantedAt: null,
};

function initialState(threadId: string | null, loading: boolean): WebAccessState {
  return {
    threadId,
    ...CLOSED_RECORD,
    loading,
    updating: false,
    error: null,
  };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "无法更新联网状态，请重试。";
}

export function useWebAccess({
  threadId,
  isRunning,
  api = runtimeWebAccessApi,
}: UseWebAccessOptions) {
  const [storedState, setStoredState] = useState<WebAccessState>(() =>
    initialState(threadId, threadId !== null),
  );
  const generationRef = useRef(0);
  const requestControllerRef = useRef<AbortController | null>(null);
  const currentThreadRef = useRef(threadId);
  currentThreadRef.current = threadId;

  const cancelRequest = useCallback(() => {
    ++generationRef.current;
    requestControllerRef.current?.abort();
    requestControllerRef.current = null;
  }, []);

  const load = useCallback((id: string, preserve: WebAccessRecord) => {
    cancelRequest();
    const controller = new AbortController();
    requestControllerRef.current = controller;
    const generation = ++generationRef.current;
    setStoredState({
      threadId: id,
      ...preserve,
      loading: true,
      updating: false,
      error: null,
    });
    void api.get(id, controller.signal).then((record) => {
      if (
        generationRef.current !== generation ||
        currentThreadRef.current !== id
      ) return;
      setStoredState({
        threadId: id,
        ...record,
        loading: false,
        updating: false,
        error: null,
      });
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      if (
        generationRef.current !== generation ||
        currentThreadRef.current !== id
      ) return;
      setStoredState({
        threadId: id,
        ...preserve,
        loading: false,
        updating: false,
        error: errorMessage(error),
      });
    }).finally(() => {
      if (requestControllerRef.current === controller) {
        requestControllerRef.current = null;
      }
    });
  }, [api, cancelRequest]);

  useEffect(() => cancelRequest, [cancelRequest]);

  useEffect(() => {
    if (threadId === null) {
      cancelRequest();
      setStoredState(initialState(null, false));
      return;
    }
    load(threadId, CLOSED_RECORD);
  }, [cancelRequest, load, threadId]);

  const state = storedState.threadId === threadId
    ? storedState
    : initialState(threadId, threadId !== null);

  const toggle = useCallback(async (enabled: boolean): Promise<boolean> => {
    const id = currentThreadRef.current;
    if (!id || isRunning) return false;
    cancelRequest();
    const controller = new AbortController();
    requestControllerRef.current = controller;
    const generation = ++generationRef.current;
    const prior = storedState.threadId === id ? storedState : initialState(id, true);
    setStoredState({ ...prior, loading: false, updating: true, error: null });
    try {
      const record = await api.put(id, enabled, controller.signal);
      if (
        generationRef.current !== generation ||
        currentThreadRef.current !== id
      ) return false;
      setStoredState({
        threadId: id,
        ...record,
        loading: false,
        updating: false,
        error: null,
      });
      return true;
    } catch (error) {
      if (controller.signal.aborted) return false;
      if (
        generationRef.current === generation &&
        currentThreadRef.current === id
      ) {
        setStoredState({
          ...prior,
          loading: false,
          updating: false,
          error: errorMessage(error),
        });
      }
      return false;
    } finally {
      if (requestControllerRef.current === controller) {
        requestControllerRef.current = null;
      }
    }
  }, [api, cancelRequest, isRunning, storedState]);

  const refresh = useCallback(() => {
    const id = currentThreadRef.current;
    if (!id) return;
    const current = storedState.threadId === id ? storedState : initialState(id, true);
    load(id, current);
  }, [load, storedState]);

  const disableAfterDenied = useCallback(() => {
    const id = currentThreadRef.current;
    if (!id) return;
    const denied = initialState(id, false);
    setStoredState(denied);
    load(id, denied);
  }, [load]);

  const acceptRecord = useCallback((id: string, record: WebAccessRecord) => {
    if (currentThreadRef.current !== id) return;
    cancelRequest();
    setStoredState({
      threadId: id,
      ...record,
      loading: false,
      updating: false,
      error: null,
    });
  }, [cancelRequest]);

  return { state, toggle, refresh, disableAfterDenied, acceptRecord };
}
