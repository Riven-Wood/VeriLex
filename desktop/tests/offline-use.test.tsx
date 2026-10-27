import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RuntimeBanner } from "../src/components/RuntimeBanner";
import { TimelineNotice } from "../src/components/timeline/TimelineNotice";
import { ToolCallCard } from "../src/components/timeline/ToolCallCard";
import { mcpErrorCopy } from "../src/mcp/errorCopy";
import type { NoticeItem } from "../src/timeline/items";
afterEach(cleanup);
it("connection recovery requires explicit user action and retains side-effect warning", () => {
  const restore = vi.fn(), settings = vi.fn();
  render(<TimelineNotice item={{ kind: "notice", id: "failure", turnId: "turn", notice: "turn_failed", errorCode: "provider_unreachable", recoveryKind: "restore_text", sourceText: "保存资料", hasPriorSideEffect: true } as NoticeItem} onRestoreText={restore} onOpenSettings={settings} />);
  expect(screen.getByText(/部分操作可能已经完成/)).toBeTruthy();
  expect(screen.getByText(/本地历史和文件仍可使用/)).toBeTruthy();
  expect(restore).not.toHaveBeenCalled();
  expect(settings).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", {name:"检查模型连接"}));
  expect(settings).toHaveBeenCalledOnce();
  expect(restore).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", {name:"恢复原指令"}));
  expect(restore).toHaveBeenCalledOnce();
});
it("a failed local backend does not claim internet is down", () => {
  const retry = vi.fn();
  const {rerender} = render(<RuntimeBanner state="healthy" onRetry={retry} />);
  expect(screen.queryByRole("status")).toBeNull();
  rerender(<RuntimeBanner state="offline" onRetry={retry} />);
  expect(screen.getByText("本机后台暂时无法连接")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"重新检查本机后台状态"}));
  expect(retry).toHaveBeenCalledOnce();
});
it("MCP errors retain recovery guidance", () => {
  expect(mcpErrorCopy("mcp_result_unknown")).toContain("操作可能已经完成");
  expect(mcpErrorCopy("mcp_connection_failed")).toContain("这个工具服务");
});

it("MCP connection failure offers repair without automatically repeating the turn", () => {
  const open = vi.fn(), restore = vi.fn();
  render(<TimelineNotice item={{kind:"notice",id:"mcp-fail",notice:"turn_failed",errorCode:"mcp_connection_failed",recoveryKind:"restore_text",sourceText:"合成任务"}} onOpenMcpSettings={open} onRestoreText={restore}/>);
  expect(screen.getByText(/无法连接这个工具服务/)).toBeTruthy();
  expect(screen.getByText(/取消选择，再发送原指令/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"检查外部工具"}));
  expect(open).toHaveBeenCalledOnce();
  expect(restore).not.toHaveBeenCalled();
});
it("an uncertain MCP result warns about prior effects instead of claiming nothing ran", () => {
  render(<ToolCallCard item={{kind:"tool",id:"mcp-tool",name:"mcp__fixture__write",status:"failed",resultPreview:"mcp_result_unknown: MCP tool call failed."}}/>);
  expect(screen.getByRole("alert").textContent).toContain("操作可能已经完成");
  expect(screen.queryByRole("button",{name:/重试/})).toBeNull();
});
