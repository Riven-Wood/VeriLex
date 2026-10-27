import { useCallback, useEffect, useRef, useState } from "react";

import { getBackgroundTaskOutput, stopBackgroundTask } from "../runtime/api";
import type { BackgroundTaskRecord } from "../runtime/contracts";

/** Desktop-side retention for accumulated live output (matches the 64 KiB
 * single-read cap with a small tolerance for the omission marker). */
const MAX_ACCUMULATED_OUTPUT_CHARS = 64 * 1024 + 1024;

const POLL_INTERVAL_MS = 1000;

export interface BackgroundTaskLiveState {
  outputText: string;
  status: string | null;
  error: string | null;
}

interface LiveEntry extends BackgroundTaskLiveState {
  cursor: number;
}

function initialEntry(): LiveEntry {
  return { outputText: "", status: null, error: null, cursor: 0 };
}

function clampOutput(text: string): string {
  if (text.length <= MAX_ACCUMULATED_OUTPUT_CHARS) return text;
  return text.slice(text.length - MAX_ACCUMULATED_OUTPUT_CHARS);
}

/**
 * Poll incremental output for the running background tasks of a thread.
 *
 * Polling only happens while `enabled` is true (the task card is visible)
 * and the task list still contains a `running` entry for the task id; a
 * terminal status from the Runtime stops that task's polling for good.
 */
export function useBackgroundTasks(
  threadId: string,
  tasks: BackgroundTaskRecord[],
  enabled: boolean,
) {
  const [states, setStates] = useState<Record<string, BackgroundTaskLiveState>>({});
  const entries = useRef<Record<string, LiveEntry>>({});
  const stopped = useRef<Set<string>>(new Set());

  const runningIds = tasks
    .filter((task) => task.status === "running")
    .map((task) => task.id);
  const runningKey = runningIds.join(",");

  useEffect(() => {
    if (!enabled || runningIds.length === 0) return undefined;

    const ids = runningKey.split(",").filter(Boolean);

    const poll = async () => {
      await Promise.all(
        ids.map(async (taskId) => {
          if (stopped.current.has(taskId)) return;
          const entry = entries.current[taskId] ?? initialEntry();
          try {
            const response = await getBackgroundTaskOutput(
              threadId,
              taskId,
              entry.cursor,
            );
            const next: LiveEntry = {
              cursor: response.cursor,
              outputText: clampOutput(entry.outputText + response.text),
              status: response.status,
              error: null,
            };
            entries.current[taskId] = next;
            if (response.status !== "running") {
              stopped.current.add(taskId);
            }
          } catch {
            entries.current[taskId] = {
              ...entry,
              error: "后台命令状态暂时无法获取，稍后会自动重试",
            };
          }
        }),
      );
      setStates(
        ids.reduce<Record<string, BackgroundTaskLiveState>>((acc, taskId) => {
          const entry = entries.current[taskId] ?? initialEntry();
          acc[taskId] = {
            outputText: entry.outputText,
            status: entry.status,
            error: entry.error,
          };
          return acc;
        }, {}),
      );
    };

    const timer = window.setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, runningKey, enabled]);

  const stopTask = useCallback(
    async (taskId: string) => {
      await stopBackgroundTask(threadId, taskId);
    },
    [threadId],
  );

  return { states, stopTask };
}
