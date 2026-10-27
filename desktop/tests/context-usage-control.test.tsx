import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { ContextUsageControl } from "../src/components/ContextUsageControl";

afterEach(cleanup);

const usage = {
  used_tokens: 7200,
  context_window_tokens: 100000,
  context_window_source: "provider" as const,
  effective_window_tokens: 90000,
  used_percent: 8,
  estimated: true,
  warning: "normal" as const,
  breakdown: {
    messages: 4000,
    system_and_tools: 2000,
    skills: 700,
    attachments: 500,
    other: 0,
  },
  updated_at: "2026-08-24T00:00:00Z",
};

test("opens a local breakdown whose rows preserve the total", () => {
  render(<ContextUsageControl usage={usage} />);
  fireEvent.click(screen.getByRole("button", { name: "上下文量，已用 8%" }));

  expect(screen.getByRole("dialog", { name: "上下文量详情" })).toBeTruthy();
  expect(screen.getByText("约 7,200 / 90,000")).toBeTruthy();
  expect(screen.getByText("模型上限 100K · Provider 自动获取")).toBeTruthy();
  expect(screen.getByText("消息").nextSibling?.textContent).toBe("4,000");
});

test("identifies a manual model limit without confusing it with the safety margin", () => {
  render(<ContextUsageControl usage={{
    ...usage,
    context_window_tokens: 64_000,
    context_window_source: "user_declared",
    effective_window_tokens: 57_600,
  }} />);
  fireEvent.click(screen.getByRole("button", { name: "上下文量，已用 8%" }));
  expect(screen.getByText("模型上限 64K · 用户设置")).toBeTruthy();
  expect(screen.getByText("约 7,200 / 57,600")).toBeTruthy();
});

test("unknown capacity shows no invented percentage", () => {
  render(<ContextUsageControl usage={{ ...usage, context_window_tokens: null, context_window_source: "unknown", effective_window_tokens: null, used_percent: null }} />);
  fireEvent.click(screen.getByRole("button", { name: "上下文量，约 7,200 已用" }));
  expect(screen.getByText("约 7,200 已用")).toBeTruthy();
  expect(screen.getByText("当前模型未提供上下文上限")).toBeTruthy();
});

test("critical usage exposes a text warning in addition to color", () => {
  render(<ContextUsageControl usage={{ ...usage, used_percent: 92, warning: "critical" }} />);
  fireEvent.click(screen.getByRole("button", { name: "上下文量，已用 92%" }));
  expect(screen.getByText("上下文接近上限")).toBeTruthy();
});
