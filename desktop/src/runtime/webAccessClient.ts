import { runtimeRequest } from "./api";
import type { WebAccessRecord } from "./contracts";

export type { WebAccessRecord } from "./contracts";

export interface WebAccessApi {
  get: (threadId: string, signal?: AbortSignal) => Promise<WebAccessRecord>;
  put: (
    threadId: string,
    enabled: boolean,
    signal?: AbortSignal,
  ) => Promise<WebAccessRecord>;
}

export function getWebAccess(
  threadId: string,
  signal?: AbortSignal,
): Promise<WebAccessRecord> {
  return runtimeRequest<WebAccessRecord>(
    `/v1/threads/${encodeURIComponent(threadId)}/web-access`,
    { cache: "no-store", signal },
  );
}

export function putWebAccess(
  threadId: string,
  enabled: boolean,
  signal?: AbortSignal,
): Promise<WebAccessRecord> {
  return runtimeRequest<WebAccessRecord>(
    `/v1/threads/${encodeURIComponent(threadId)}/web-access`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
      cache: "no-store",
      signal,
    },
  );
}

export const runtimeWebAccessApi: WebAccessApi = {
  get: getWebAccess,
  put: putWebAccess,
};
