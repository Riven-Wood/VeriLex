import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { TimelineNotice } from "../src/components/timeline/TimelineNotice";
import { ToolCallCard } from "../src/components/timeline/ToolCallCard";
import type { AgentEvent } from "../src/runtime/contracts";
import { buildTimelineHistory } from "../src/timeline/history";
import { applyTimelineEvent, initialTimeline } from "../src/timeline/reducer";

afterEach(cleanup);

const WEB_ERRORS = [
  ["web_access_disabled", "本任务尚未打开联网"],
  ["web_access_not_granted", "首次联网未获允许"],
  ["web_search_unavailable", "免费搜索服务暂时不可用"],
  ["web_search_rate_limited", "搜索过于频繁"],
  ["web_url_invalid", "网址格式不受支持"],
  ["web_url_blocked", "网址指向本机、局域网或其他禁止地址"],
  ["web_redirect_blocked", "网页跳转到了禁止地址"],
  ["web_fetch_timeout", "网页读取超时"],
  ["web_fetch_too_large", "网页超过 2 MiB"],
  ["web_content_unsupported", "本轮只读取普通网页和纯文本"],
  ["web_fetch_failed", "网页暂时无法读取"],
  ["web_turn_limit_reached", "本轮读取的网页数量或正文总量已达上限"],
  ["web_citation_invalid", "网页引用未通过核验"],
] as const;

test.each(WEB_ERRORS)(
  "failed web tool maps %s to stable Chinese without exposing Runtime detail",
  (code, expected) => {
    render(
      <ToolCallCard
        item={{
          kind: "tool",
          id: "web-tool",
          name: "web_read",
          status: "failed",
          resultPreview: `${code}: secret stack at http://127.0.0.1 token=private`,
        }}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(expected);
    expect(alert.textContent).not.toContain(code);
    expect(alert.textContent).not.toContain("127.0.0.1");
    expect(alert.textContent).not.toContain("private");
  },
);

test.each(WEB_ERRORS)("turn failure uses the same web error wording for %s", (code, expected) => {
  render(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "notice",
        notice: "turn_failed",
        errorCode: code,
      }}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain(expected);
});

test("an unknown web turn error fails closed without exposing its internal code", () => {
  render(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "notice",
        notice: "turn_failed",
        errorCode: "web_future_internal_detail",
      }}
    />,
  );

  expect(screen.getByRole("alert").textContent).toBe("网页操作未能完成，请稍后重试。");
  expect(screen.getByRole("alert").textContent).not.toContain("web_future_internal_detail");
});

test.each([
  "prefix web_fetch_timeout: secret stack",
  "web_fetch_timeout secret stack",
  "future_web_error: secret stack",
])("failed web cards fail closed for non-whitelisted preview %s", (preview) => {
  render(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "web-tool",
        name: "web_read",
        status: "failed",
        resultPreview: preview,
      }}
    />,
  );

  const alert = screen.getByRole("alert");
  expect(alert.textContent).toBe("网页操作未能完成，请稍后重试。");
  expect(alert.textContent).not.toContain("secret stack");
  expect(alert.textContent).not.toContain("web_fetch_timeout");
});

test("live failed web result renders the safe mapped error", () => {
  let state = initialTimeline();
  const events: AgentEvent[] = [
    {
      type: "tool_call_started",
      turnId: "turn-live",
      sequence: 1,
      payload: { toolCallId: "call-live", name: "web_read" },
    },
    {
      type: "tool_call_completed",
      turnId: "turn-live",
      sequence: 2,
      payload: {
        toolCallId: "call-live",
        status: "failed",
        resultPreview: "web_url_blocked: secret stack",
      },
    },
  ];
  for (const event of events) state = applyTimelineEvent(state, event);
  const item = state.items.find((candidate) => candidate.kind === "tool");
  if (!item || item.kind !== "tool") throw new Error("missing tool item");

  render(<ToolCallCard item={item} />);
  expect(screen.getByRole("alert").textContent).toContain("网址指向本机、局域网或其他禁止地址");
  expect(screen.getByRole("alert").textContent).not.toContain("secret stack");
});

test("history rebuild renders the same safe error without replaying any request", () => {
  const history = buildTimelineHistory({
    turns: [{ id: "turn-history", status: "completed", error_code: null }],
    events: [
      {
        id: "event-start",
        turn_id: "turn-history",
        sequence: 1,
        type: "tool_call_started",
        payload: { toolCallId: "call-history", name: "web_read" },
        created_at: "2026-08-27T00:00:00Z",
      },
      {
        id: "event-complete",
        turn_id: "turn-history",
        sequence: 2,
        type: "tool_call_completed",
        payload: {
          toolCallId: "call-history",
          status: "failed",
          resultPreview: "web_fetch_timeout: stack with private-token",
        },
        created_at: "2026-08-27T00:00:01Z",
      },
    ],
    messages: [],
  });
  const item = history.state.items.find((candidate) => candidate.kind === "tool");
  if (!item || item.kind !== "tool") throw new Error("missing tool item");

  render(<ToolCallCard item={item} />);
  expect(screen.getByRole("alert").textContent).toContain("网页读取超时");
  expect(screen.getByRole("alert").textContent).not.toContain("private-token");
});
