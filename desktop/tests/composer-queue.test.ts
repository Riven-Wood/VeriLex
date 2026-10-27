import { expect, test } from "vitest";

import {
  cancelQueue,
  editQueue,
  emptyComposerQueue,
  nextQueueAction,
  queueSubmission,
} from "../src/composer/queue";

const first = {
  text: "第一条",
  attachmentUploadIds: ["upl-1"],
  attachmentNames: ["一.pdf"],
};
const second = {
  text: "更新后的内容",
  attachmentUploadIds: [],
  attachmentNames: [],
};

test("one queue entry can be replaced, edited, and cancelled", () => {
  const queued = queueSubmission(emptyComposerQueue(), first, "after_current", {
    threadId: "thread-1",
    terminalToken: 3,
    historyToken: 1,
  });
  const replaced = queueSubmission(queued.state, second, "after_current", {
    threadId: "thread-1",
    terminalToken: 3,
    historyToken: 1,
  });

  expect(queued.state.submission).toEqual(first);
  expect(replaced.replacedSubmission).toEqual(first);
  expect(replaced.state.submission).toEqual(second);
  expect(editQueue(replaced.state)).toMatchObject({ editing: true });
  expect(cancelQueue(editQueue(replaced.state))).toEqual(emptyComposerQueue());
});

test("queued work sends once only after a terminal token and a ready connection", () => {
  const queued = queueSubmission(
    emptyComposerQueue(),
    first,
    "after_stop",
    { threadId: "thread-1", terminalToken: 7, historyToken: 2 },
  ).state;

  expect(nextQueueAction({
    queue: queued,
    runningTurnId: "turn-1",
    connectionReady: true,
    terminalToken: 7,
    currentThreadId: "thread-1",
    historyLoadedThreadId: "thread-1",
    historyToken: 2,
  })).toBeNull();
  expect(nextQueueAction({
    queue: queued,
    runningTurnId: null,
    connectionReady: false,
    terminalToken: 8,
    currentThreadId: "thread-1",
    historyLoadedThreadId: "thread-1",
    historyToken: 2,
  })).toBeNull();
  expect(nextQueueAction({
    queue: queued,
    runningTurnId: null,
    connectionReady: true,
    terminalToken: 8,
    currentThreadId: "thread-1",
    historyLoadedThreadId: "thread-1",
    historyToken: 2,
  })).toEqual({ type: "send", submission: first });
  expect(nextQueueAction({
    queue: queued,
    runningTurnId: null,
    connectionReady: true,
    terminalToken: 7,
    currentThreadId: "thread-1",
    historyLoadedThreadId: "thread-1",
    historyToken: 2,
  })).toBeNull();
});

test("queued work never sends in another thread and resumes after returning", () => {
  const queued = queueSubmission(
    emptyComposerQueue(),
    first,
    "after_current",
    { threadId: "thread-1", terminalToken: 4, historyToken: 10 },
  ).state;

  expect(nextQueueAction({
    queue: queued,
    runningTurnId: null,
    connectionReady: true,
    terminalToken: 5,
    currentThreadId: "thread-2",
    historyLoadedThreadId: "thread-2",
    historyToken: 11,
  })).toBeNull();
  expect(nextQueueAction({
    queue: queued,
    runningTurnId: null,
    connectionReady: true,
    terminalToken: 4,
    currentThreadId: "thread-1",
    historyLoadedThreadId: "thread-1",
    historyToken: 12,
  })).toEqual({ type: "send", submission: first });
});
