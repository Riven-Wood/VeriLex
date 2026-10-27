import { useCallback, useEffect, useRef, useState } from "react";

import { getThreadContextUsage } from "../runtime/api";
import type { ContextUsageRecord } from "../runtime/contracts";

export function useContextUsage(
  threadId: string | null,
  refreshToken: number | string,
  enabled = true,
) {
  const [usage, setUsage] = useState<ContextUsageRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [manualToken, setManualToken] = useState(0);
  const requestGeneration = useRef(0);

  const refresh = useCallback(() => setManualToken((value) => value + 1), []);

  useEffect(() => {
    const generation = ++requestGeneration.current;
    setUsage(null);
    setError("");
    if (!enabled || !threadId) {
      setLoading(false);
      return undefined;
    }

    const controller = new AbortController();
    setLoading(true);
    getThreadContextUsage(threadId, controller.signal)
      .then((record) => {
        if (requestGeneration.current !== generation) return;
        setUsage(record);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted || requestGeneration.current !== generation) return;
        setError(caught instanceof Error ? caught.message : "无法读取上下文用量");
        setLoading(false);
      });

    return () => controller.abort();
  }, [enabled, manualToken, refreshToken, threadId]);

  return { usage, loading, error, refresh };
}
