import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ApprovalCard } from "../src/components/timeline/ApprovalCard";
import type { ToolItem } from "../src/timeline/items";

afterEach(cleanup);
const item: ToolItem = {kind: "tool", id: "tool", turnId: "turn", approvalId: "approval", name: "run_command", status: "awaiting_approval", argumentsSummary: {command: "printf '演示'"}};

test("Escape only rejects from this card and does not consume typing, composition or repeats", () => {
  const resolve = vi.fn();
  render(<><input aria-label="其他内容" /><ApprovalCard item={item} onResolve={resolve} pendingCount={2} /></>);
  expect(screen.getByText("还有 1 项")).toBeDefined();
  fireEvent.keyDown(screen.getByLabelText("其他内容"), {key: "Escape"});
  fireEvent.keyDown(screen.getByRole("button", {name: "允许一次"}), {key: "Escape", repeat: true});
  fireEvent.keyDown(screen.getByRole("button", {name: "允许一次"}), {key: "Escape", isComposing: true});
  fireEvent.click(screen.getByRole("button", {name: "添加说明（可选）"}));
  fireEvent.keyDown(screen.getByLabelText("审批说明（可选）"), {key: "Escape"});
  expect(resolve).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole("button", {name: "允许一次"}), {key: "Escape"});
  expect(resolve).toHaveBeenCalledTimes(1);
  expect(resolve).toHaveBeenCalledWith("denied", "");
});

test.each([{connectionReady: false}, {submission: {status: "submitting" as const}}, {submission: {status: "failed" as const, retryable: false}}])("locked approvals cannot be resolved by keyboard or click", props => {
  const resolve = vi.fn();
  render(<ApprovalCard item={item} onResolve={resolve} {...props} />);
  fireEvent.keyDown(screen.getByRole("button", {name: "允许一次"}), {key: "Escape"});
  fireEvent.click(screen.getByRole("button", {name: "拒绝"}));
  expect(resolve).not.toHaveBeenCalled();
});
