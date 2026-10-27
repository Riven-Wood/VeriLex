import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { DeliverablesCard } from "../src/components/timeline/DeliverablesCard";

afterEach(cleanup);

const ARTIFACTS = [
  {
    kind: "artifact" as const,
    id: "artifact-1",
    turn_id: "turn-1",
    name: "意见.docx",
    published_workspace_id: "ws-a",
    published_relative_path: "意见.docx",
    operation: "document_create",
    size_bytes: 4096,
    structure_status: "passed" as const,
    render_status: "not_required" as const,
    vision_status: "not_run" as const,
    created_at: "2026-09-03T00:00:00Z",
  },
  {
    kind: "artifact" as const,
    id: "artifact-2",
    turn_id: "turn-2",
    name: "意见 (2).docx",
    published_workspace_id: "ws-a",
    published_relative_path: "意见 (2).docx",
    operation: "document_create",
    size_bytes: 4200,
    structure_status: "passed" as const,
    render_status: "not_required" as const,
    vision_status: "not_run" as const,
    created_at: "2026-09-03T00:00:01Z",
  },
];

test("lists the newest deliverables and opens one on its own workspace", () => {
  const onOpen = vi.fn();
  const { container } = render(
    <DeliverablesCard artifacts={ARTIFACTS} onOpen={onOpen} />,
  );

  expect(container.querySelector(".deliverables-card")).not.toBeNull();
  const buttons = container.querySelectorAll(".deliverables-card button");
  expect(buttons).toHaveLength(3); // collapse toggle + two files, no view-all under the cap
  fireEvent.click(container.querySelector(".deliverables-file-button")!);
  expect(onOpen).toHaveBeenCalledWith("ws-a", "意见.docx");
});

test("the head row collapses and re-expands the file list", () => {
  const { container } = render(
    <DeliverablesCard artifacts={ARTIFACTS} onOpen={vi.fn()} />,
  );

  const toggle = container.querySelector<HTMLElement>(".deliverables-toggle")!;
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(container.querySelectorAll(".deliverables-file-button")).toHaveLength(2);

  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(container.querySelectorAll(".deliverables-file-button")).toHaveLength(0);

  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(container.querySelectorAll(".deliverables-file-button")).toHaveLength(2);
});

test("shows at most the five newest files with a view-all entry", () => {
  // The API already returns newest first.
  const many = Array.from({ length: 7 }, (_, index) => ({
    ...ARTIFACTS[0],
    id: `artifact-${index}`,
    name: `文件${6 - index}.docx`,
    published_relative_path: `文件${6 - index}.docx`,
  }));
  const { container } = render(
    <DeliverablesCard artifacts={many} onOpen={vi.fn()} onViewAll={vi.fn()} />,
  );

  const files = container.querySelectorAll(".deliverables-file-button");
  expect(files).toHaveLength(5);
  expect(files[0].textContent).toContain("文件6.docx"); // newest first
  expect(container.textContent).toContain("全部 7 个文件");
});

test("renders nothing for the empty or legacy-only list", () => {
  const { container } = render(
    <DeliverablesCard artifacts={[]} onOpen={vi.fn()} />,
  );
  expect(container.querySelector(".deliverables-card")).toBeNull();

  const legacy = [
    {
      kind: "legacy_workspace" as const,
      path: "out/report.md",
      name: "report.md",
      last_operation: "write_file",
      last_operated_at: "2026-09-03T00:00:00Z",
    },
  ];
  const legacyView = render(
    <DeliverablesCard artifacts={legacy} onOpen={vi.fn()} />,
  );
  expect(legacyView.container.querySelector(".deliverables-card")).toBeNull();
});
