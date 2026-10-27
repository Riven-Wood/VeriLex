import { getHealth } from "./api";
import type { HealthResponse } from "./contracts";

const MAX_STARTUP_ATTEMPTS = 12;
const INITIAL_RETRY_DELAY_MS = 250;
const MAX_RETRY_DELAY_MS = 1_000;
const HEALTH_ATTEMPT_TIMEOUT_MS = 2_500;

function pause(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Runtime startup cancelled", "AbortError"));
      return;
    }
    const onAbort = () => {
      globalThis.clearTimeout(timeout);
      reject(new DOMException("Runtime startup cancelled", "AbortError"));
    };
    const timeout = globalThis.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function requestHealth(signal: AbortSignal): Promise<HealthResponse> {
  const controller = new AbortController();
  const onAbort = () => controller.abort(signal.reason);
  if (signal.aborted) {
    controller.abort(signal.reason);
  } else {
    signal.addEventListener("abort", onAbort, { once: true });
  }
  const timeout = globalThis.setTimeout(
    () =>
      controller.abort(
        new DOMException("Runtime 健康检查超时", "TimeoutError"),
      ),
    HEALTH_ATTEMPT_TIMEOUT_MS,
  );
  try {
    return await getHealth(controller.signal);
  } finally {
    globalThis.clearTimeout(timeout);
    signal.removeEventListener("abort", onAbort);
  }
}

export async function waitForRuntime(signal: AbortSignal): Promise<HealthResponse> {
  let lastError: unknown = new Error("本地 Runtime 无法启动");
  for (let attempt = 0; attempt < MAX_STARTUP_ATTEMPTS; attempt += 1) {
    if (signal.aborted) {
      throw new DOMException("Runtime startup cancelled", "AbortError");
    }
    try {
      return await requestHealth(signal);
    } catch (error: unknown) {
      if (signal.aborted) throw error;
      lastError = error;
      if (attempt < MAX_STARTUP_ATTEMPTS - 1) {
        const delay = Math.min(
          INITIAL_RETRY_DELAY_MS * 2 ** attempt,
          MAX_RETRY_DELAY_MS,
        );
        await pause(delay, signal);
      }
    }
  }
  throw lastError;
}
