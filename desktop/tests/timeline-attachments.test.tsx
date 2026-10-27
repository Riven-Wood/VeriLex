import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/react";
import { render } from "@testing-library/react";

import { TimelineAttachments } from "../src/components/timeline/TimelineAttachments";
import { appendUserMessage, initialTimeline } from "../src/timeline/reducer";
import { buildTimelineHistory } from "../src/timeline/history";
import type { TimelineAttachment } from "../src/timeline/items";

afterEach(() => cleanup());

const IMAGE_ATTACHMENT: TimelineAttachment = {
  id: "att-1",
  name: "合同截图.png",
  mediaType: "image/png",
  sizeBytes: 2048,
};

const PDF_ATTACHMENT: TimelineAttachment = {
  id: "att-2",
  name: "合同.pdf",
  mediaType: "application/pdf",
  sizeBytes: 3 * 1024 * 1024,
};

function makeLoad(
  blobs: Record<string, Blob> = {},
): (attachment: { id: string }) => Promise<Blob> {
  return (attachment) =>
    attachment.id in blobs
      ? Promise.resolve(blobs[attachment.id])
      : Promise.reject(new Error("missing"));
}

test("image attachments render fetched thumbnails", async () => {
  const blob = new Blob([new Uint8Array([1])], { type: "image/png" });
  const createObjectURL = vi.fn(() => "blob:thumb-1");
  const revokeObjectURL = vi.fn();
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = createObjectURL;
  URL.revokeObjectURL = revokeObjectURL;
  try {
    const { container } = render(
      <TimelineAttachments
        attachments={[IMAGE_ATTACHMENT]}
        loadContent={makeLoad({ "att-1": blob })}
        onOpen={() => {}}
      />,
    );
    await waitFor(() => {
      expect(container.querySelector(".timeline-attachment-thumb")).not.toBeNull();
    });
    const image = container.querySelector(
      ".timeline-attachment-thumb",
    ) as HTMLImageElement;
    expect(image.src).toBe("blob:thumb-1");
  } finally {
    cleanup();
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  }
});

test("image thumbnail failure falls back to an icon without blocking clicks", async () => {
  const onOpen = vi.fn();
  const { container } = render(
    <TimelineAttachments
      attachments={[IMAGE_ATTACHMENT]}
      loadContent={makeLoad()}
      onOpen={onOpen}
    />,
  );
  await waitFor(() =>
    expect(container.querySelector(".timeline-attachment-thumb")).toBeNull(),
  );
  const button = container.querySelector(
    ".timeline-attachment-image",
  ) as HTMLButtonElement;
  expect(button).not.toBeNull();
  fireEvent.click(button);
  expect(onOpen).toHaveBeenCalledWith(IMAGE_ATTACHMENT);
});

test("non-image attachments render file cards with size", () => {
  const { container } = render(
    <TimelineAttachments
      attachments={[PDF_ATTACHMENT]}
      loadContent={makeLoad()}
      onOpen={() => {}}
    />,
  );
  const card = container.querySelector(
    ".timeline-attachment-file",
  ) as HTMLButtonElement;
  expect(card).not.toBeNull();
  expect(card.textContent).toContain("合同.pdf");
  expect(card.textContent).toContain("3.0 MB");
  expect(card.getAttribute("aria-label")).toBe("预览附件 合同.pdf");
});

test("appendUserMessage stores rich attachment metadata", () => {
  const state = appendUserMessage(
    initialTimeline(),
    "请看这份合同",
    "user-1",
    ["合同.pdf"],
    [PDF_ATTACHMENT],
  );
  const item = state.items[0];
  if (item.kind !== "user") throw new Error("expected user item");
  expect(item.attachments).toEqual([PDF_ATTACHMENT]);
  expect(item.attachmentNames).toBeUndefined();
});

test("appendUserMessage keeps name fallback when metadata is absent", () => {
  const state = appendUserMessage(
    initialTimeline(),
    "旧风格消息",
    "user-1",
    ["合同.pdf"],
  );
  const item = state.items[0];
  if (item.kind !== "user") throw new Error("expected user item");
  expect(item.attachmentNames).toEqual(["合同.pdf"]);
  expect(item.attachments).toBeUndefined();
});

function minimalHistorySources(overrides: Record<string, unknown> = {}) {
  return {
    messages: [
      {
        id: "m-1",
        thread_id: "t-1",
        turn_id: "turn-1",
        role: "user",
        content: { version: 1, text: "看图", attachmentIds: ["att-1"] },
        created_at: "2026-08-26T00:00:00Z",
      },
    ],
    turns: [
      {
        id: "turn-1",
        thread_id: "t-1",
        status: "completed",
        created_at: "2026-08-26T00:00:00Z",
      },
    ],
    events: [],
    ...overrides,
  };
}

test("history prefers attachment metadata and falls back to names", () => {
  const withRecords = buildTimelineHistory(
    minimalHistorySources({
      attachmentRecordsById: { "att-1": IMAGE_ATTACHMENT },
      attachmentNamesById: { "att-1": "合同截图.png" },
    }),
  );
  const rich = withRecords.state.items[0];
  if (rich.kind !== "user") throw new Error("expected user item");
  expect(rich.attachments).toEqual([IMAGE_ATTACHMENT]);
  expect(rich.attachmentNames).toBeUndefined();

  const namesOnly = buildTimelineHistory(
    minimalHistorySources({
      attachmentNamesById: { "att-1": "合同截图.png" },
    }),
  );
  const legacy = namesOnly.state.items[0];
  if (legacy.kind !== "user") throw new Error("expected user item");
  expect(legacy.attachmentNames).toEqual(["合同截图.png"]);
  expect(legacy.attachments).toBeUndefined();
});
