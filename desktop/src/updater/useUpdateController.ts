import { useCallback, useEffect, useRef, useState } from "react";

import { UpdateServiceError } from "./contracts";

import type {
  AvailableUpdate,
  UpdateController,
  UpdateMetadata,
  UpdateState,
  UpdaterClient,
} from "./contracts";

function metadataFrom(update: AvailableUpdate): UpdateMetadata {
  return {
    version: update.version,
    currentVersion: update.currentVersion,
    date: update.date,
    body: update.body,
  };
}

async function closeUpdate(update: AvailableUpdate | null) {
  // Releasing native metadata must not undo an installation or mask its result.
  try { await update?.close?.(); } catch { /* Released on WebView shutdown as well. */ }
}

export function useUpdateController(client: UpdaterClient): UpdateController {
  const [state, setState] = useState<UpdateState>({
    supported: client.supported,
    phase: "idle",
    currentVersion: "",
    update: null,
    progress: null,
    error: "",
    relaunchRequired: false,
  });
  const startedRef = useRef(false);
  const operationRef = useRef(false);
  const mountedRef = useRef(false);
  const availableUpdateRef = useRef<AvailableUpdate | null>(null);
  const installedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // StrictMode immediately remounts effects; defer disposal until it settles.
      queueMicrotask(() => {
        if (!mountedRef.current && !operationRef.current) {
          const update = availableUpdateRef.current;
          availableUpdateRef.current = null;
          void closeUpdate(update);
        }
      });
    };
  }, []);

  const checkForUpdates = useCallback(async () => {
    if (!client.supported || operationRef.current || installedRef.current) {
      return;
    }

    operationRef.current = true;
    if (mountedRef.current) {
      setState((current) => ({
        ...current,
        phase: "checking",
        progress: null,
        error: "",
        relaunchRequired: false,
      }));
    }

    try {
      const currentVersion = await client.getCurrentVersion();
      if (mountedRef.current) {
        setState((current) => ({ ...current, currentVersion }));
      }
      const update = await client.check();
      if (!mountedRef.current) {
        await closeUpdate(update);
        return;
      }
      const previous = availableUpdateRef.current;
      availableUpdateRef.current = update;
      if (previous !== update) await closeUpdate(previous);

      if (mountedRef.current) {
        setState((current) => ({
          ...current,
          phase: update === null ? "up-to-date" : "available",
          currentVersion,
          update: update === null ? null : metadataFrom(update),
          progress: null,
          error: "",
          relaunchRequired: false,
        }));
      }
    } catch (error) {
      if (mountedRef.current) {
        setState((current) => ({
          ...current,
          phase: "failed",
          progress: null,
          error: error instanceof UpdateServiceError ? error.message : "无法连接更新服务，请稍后重试",
          relaunchRequired: false,
        }));
      }
    } finally {
      operationRef.current = false;
      if (!mountedRef.current) {
        const update = availableUpdateRef.current;
        availableUpdateRef.current = null;
        await closeUpdate(update);
      }
    }
  }, [client]);

  useEffect(() => {
    if (startedRef.current || !client.supported) {
      return;
    }
    startedRef.current = true;
    void checkForUpdates();
  }, [checkForUpdates, client.supported]);

  const restart = useCallback(async () => {
    if (mountedRef.current) {
      setState((current) => ({ ...current, phase: "ready-to-relaunch", progress: null,
        error: "", relaunchRequired: false }));
    }
    try {
      await client.relaunch();
    } catch {
      if (mountedRef.current) {
        setState((current) => ({ ...current, phase: "ready-to-relaunch",
          error: "更新已安装，请重新启动 VeriLex", relaunchRequired: true }));
      }
    } finally {
      operationRef.current = false;
    }
  }, [client]);

  const installUpdate = useCallback(async () => {
    const update = availableUpdateRef.current;
    if (operationRef.current) return;
    if (installedRef.current) {
      operationRef.current = true;
      await restart();
      return;
    }
    if (update === null) return;

    operationRef.current = true;
    if (mountedRef.current) {
      setState((current) => ({
        ...current,
        phase: "downloading",
        progress: null,
        error: "",
        relaunchRequired: false,
      }));
    }

    try {
      await update.install((progress) => {
        if (mountedRef.current) {
          setState((current) => ({
            ...current,
            phase: progress.phase,
            progress,
          }));
        }
      });
    } catch {
      if (mountedRef.current) {
        setState((current) => ({
          ...current,
          phase: "failed",
          error: "更新下载或安装失败，请重试",
          relaunchRequired: false,
        }));
      }
      operationRef.current = false;
      if (!mountedRef.current) {
        availableUpdateRef.current = null;
        await closeUpdate(update);
      }
      return;
    }

    installedRef.current = true;
    availableUpdateRef.current = null;
    await closeUpdate(update);
    await restart();
  }, [restart]);

  return { state, checkForUpdates, installUpdate };
}
