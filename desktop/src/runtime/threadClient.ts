import {
  archiveThread,
  createThread,
  deleteThread,
  discardDraftThread,
  getThread,
  listThreads,
  renameThread,
  restoreThread,
  trashThread,
  unarchiveThread,
  updateThreadContext,
} from "./api";
import type {
  ThreadContextUpdateRequest,
  ThreadCreateRequest,
  ThreadRecord,
  ThreadScope,
} from "./contracts";

export interface ThreadApi {
  createThread: (request: ThreadCreateRequest, signal?: AbortSignal) => Promise<ThreadRecord>;
  getThread: (threadId: string, signal?: AbortSignal) => Promise<ThreadRecord>;
  listThreads: (
    scope?: ThreadScope,
    signal?: AbortSignal,
    scene?: "general" | "legal-retrieval" | "case-retrieval" | "litigation-work" | "contract-review" | "scheduled" | "all",
  ) => Promise<ThreadRecord[]>;
  updateThreadContext: (
    threadId: string,
    request: ThreadContextUpdateRequest,
    signal?: AbortSignal,
  ) => Promise<ThreadRecord>;
  discardDraftThread: (threadId: string, signal?: AbortSignal) => Promise<void>;
  renameThread: (threadId: string, title: string, signal?: AbortSignal) => Promise<ThreadRecord>;
  archiveThread: (threadId: string, signal?: AbortSignal) => Promise<ThreadRecord>;
  unarchiveThread: (threadId: string, signal?: AbortSignal) => Promise<ThreadRecord>;
  trashThread: (threadId: string, signal?: AbortSignal) => Promise<ThreadRecord>;
  restoreThread: (threadId: string, signal?: AbortSignal) => Promise<ThreadRecord>;
  deleteThread: (threadId: string, signal?: AbortSignal) => Promise<void>;
}

export const runtimeThreadApi: ThreadApi = {
  createThread,
  getThread,
  listThreads,
  updateThreadContext,
  discardDraftThread,
  renameThread,
  archiveThread,
  unarchiveThread,
  trashThread,
  restoreThread,
  deleteThread,
};
