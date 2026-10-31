import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { MarkdownBody } from "../src/components/timeline/MarkdownBody";
import { ReasoningDisclosure } from "../src/components/timeline/ReasoningDisclosure";
import { TimelineNotice } from "../src/components/timeline/TimelineNotice";
import { ToolCallCard } from "../src/components/timeline/ToolCallCard";
import { AgentWorkspaceView } from "../src/views/AgentWorkspaceView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
});

test("markdown body renders headings lists and code without raw HTML", () => {
  const { container } = render(
    <MarkdownBody
      text={"# 标题\n\n- 项目一\n- 项目二\n\n`code`\n\n<script>alert(1)</script>"}
    />,
  );
  expect(container.querySelector("h1")?.textContent).toBe("标题");
  expect(container.querySelectorAll("li").length).toBe(2);
  expect(container.querySelector("code")?.textContent).toBe("code");
  // Raw HTML must be escaped into visible text, never executed.
  expect(container.querySelector("script")).toBeNull();
  expect(container.textContent).toContain("<script>alert(1)</script>");
});

test("markdown links open in the system browser", () => {
  const { container } = render(
    <MarkdownBody text={"[文档](https://example.com/doc)"} />,
  );
  const link = container.querySelector("a");
  expect(link?.getAttribute("href")).toBe("https://example.com/doc");
  expect(link?.getAttribute("target")).toBe("_blank");
  expect(link?.getAttribute("rel")).toBe("noreferrer noopener");
});

test("reasoning disclosure toggles with aria-expanded", async () => {
  render(
    <ReasoningDisclosure
      segment={{ type: "reasoning", text: "推理内容", isStreaming: false }}
    />,
  );
  const button = screen.getByRole("button", { name: /已深度思考/ });
  expect(button.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("推理内容")).toBeNull();

  await act(async () => {
    button.click();
  });
  expect(button.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("推理内容")).toBeDefined();
});

test("reasoning disclosure streams a live headline", () => {
  render(
    <ReasoningDisclosure
      segment={{ type: "reasoning", text: "正在推理", isStreaming: true }}
    />,
  );
  expect(screen.getByRole("button", { name: /正在深度思考/ })).toBeDefined();
});

test("reasoning stays open while streaming and collapses once it ends", async () => {
  const view = render(
    <ReasoningDisclosure
      segment={{ type: "reasoning", text: "推理一", isStreaming: true }}
    />,
  );
  // While thinking, the process is visible live.
  expect(view.getByText("推理一")).toBeDefined();
  const button = view.getByRole("button", { name: /正在深度思考/ });
  expect(button.getAttribute("aria-expanded")).toBe("true");

  // The answer finished streaming: collapse back to the headline row.
  view.rerender(
    <ReasoningDisclosure
      segment={{ type: "reasoning", text: "推理一推理二", isStreaming: false }}
    />,
  );
  const doneButton = view.getByRole("button", { name: /已深度思考/ });
  expect(doneButton.getAttribute("aria-expanded")).toBe("false");
  expect(view.queryByText(/推理二/)).toBeNull();

  // The user can still reopen the finished reasoning.
  await act(async () => {
    doneButton.click();
  });
  expect(doneButton.getAttribute("aria-expanded")).toBe("true");
  expect(view.getByText(/推理一推理二/)).toBeDefined();
});

test("tool card shows status label, duration and result preview", async () => {
  const { container } = render(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "tc-1",
        name: "read_file",
        argumentsSummary: { path: "a.txt" },
        status: "succeeded",
        durationMs: 42,
        resultPreview: "hello content",
        resultTruncated: false,
      }}
    />,
  );
  expect(screen.getByText("执行 read_file")).toBeDefined();
  expect(screen.getByText("成功")).toBeDefined();
  expect(screen.getByText("42 ms")).toBeDefined();
  // Finished cards stay one compact row: the technical argument line and the
  // result body only appear when the user asks for them.
  expect(screen.queryByText(/path: a\.txt/)).toBeNull();
  // …and they render as a quiet borderless row rather than a boxed card.
  expect(container.querySelector(".tool-card.tool-quiet-row")).not.toBeNull();

  const toggle = screen.getByRole("button", { name: "查看结果" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  await act(async () => {
    toggle.click();
  });
  expect(screen.getByText("hello content")).toBeDefined();
});

test("awaiting approval uses the rounded approval surface", () => {
  const { container } = render(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "tc-2",
        name: "run_command",
        argumentsSummary: { command: "ls" },
        status: "awaiting_approval",
      }}
    />,
  );
  expect(container.querySelector(".tool-card.tool-quiet-row")).toBeNull();
  expect(
    container.querySelector(".approval-card"),
  ).not.toBeNull();
});

test.each(["running", "failed", "denied", "cancelled"] as const)(
  "tool card hides the arguments row while %s",
  (status) => {
    render(
      <ToolCallCard
        item={{
          kind: "tool",
          id: `tc-${status}`,
          name: "run_command",
          argumentsSummary: { command: "textutil -convert txt" },
          status,
        }}
      />,
    );
    expect(screen.queryByText(/textutil/)).toBeNull();
  },
);

test.each([
  ["write_file", "创建文件 合同.md"],
  ["apply_file_patch", "修改文件 合同.md"],
  ["run_command", "运行命令"],
  ["future_tool", "执行 future_tool"],
])("tool card presents %s in plain language without exposing parent paths", (name, title) => {
  render(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "t",
        name,
        status: "awaiting_approval",
        argumentsSummary: { path: "/secret/contracts/合同.md", content: "有界摘要" },
      }}
    />,
  );
  expect(screen.getByText(title)).toBeDefined();
  expect(screen.queryByText(/secret\/contracts/)).toBeNull();
  expect(screen.getByText(/path: 合同.md/)).toBeDefined();
});

test("approval reason stays optional, bounded, and is passed to allow or deny", () => {
  const onResolve = vi.fn();
  const item = {
    kind: "tool" as const,
    id: "tc-1",
    turnId: "turn-1",
    approvalId: "ap-1",
    name: "write_file",
    status: "awaiting_approval" as const,
    riskLevel: "high" as const,
    argumentsSummary: { path: "/secret/contracts/合同.md", content: "有界摘要" },
  };
  const { rerender } = render(<ToolCallCard item={item} onResolve={onResolve} />);

  const reasonToggle = screen.getByRole("button", { name: "添加说明（可选）" });
  expect(reasonToggle.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByLabelText("审批说明（可选）")).toBeNull();
  fireEvent.click(reasonToggle);

  const textarea = screen.getByLabelText("审批说明（可选）") as HTMLTextAreaElement;
  expect(textarea.maxLength).toBe(500);
  fireEvent.change(textarea, { target: { value: "已核对" } });
  expect(screen.getByText("还可输入 497 字")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "允许一次" }));
  expect(onResolve).toHaveBeenLastCalledWith("allowed", "已核对");

  const tooLong = "字".repeat(501);
  fireEvent.change(textarea, { target: { value: tooLong } });
  expect(textarea.value).toHaveLength(500);
  expect(screen.getByText("还可输入 0 字")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
  expect(onResolve).toHaveBeenLastCalledWith("denied", "字".repeat(500));
  expect(screen.queryByText(/secret\/contracts/)).toBeNull();

  rerender(
    <ToolCallCard item={item} submission={{ status: "submitting" }} onResolve={onResolve} />,
  );
  expect(screen.getByRole("status").textContent).toContain("正在提交决定");
  expect(screen.getByRole("button", { name: "允许一次" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "拒绝" }).hasAttribute("disabled")).toBe(true);
  expect((screen.getByLabelText("审批说明（可选）") as HTMLTextAreaElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "允许一次" }));
  expect(onResolve).toHaveBeenCalledTimes(2);
});

test("a failed approval submission shows its error and can be retried", () => {
  const onResolve = vi.fn();
  render(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "tc-1",
        turnId: "turn-1",
        approvalId: "ap-1",
        name: "run_command",
        status: "awaiting_approval",
      }}
      submission={{ status: "failed", error: "提交失败，请重试" }}
      onResolve={onResolve}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain("提交失败，请重试");
  const deny = screen.getByRole("button", { name: "拒绝" });
  expect(deny.hasAttribute("disabled")).toBe(false);
  fireEvent.click(deny);
  expect(onResolve).toHaveBeenCalledWith("denied", "");
});

test("a non-retryable approval failure keeps controls locked", () => {
  render(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "tc-1",
        turnId: "turn-1",
        approvalId: "ap-1",
        name: "run_command",
        status: "awaiting_approval",
      }}
      submission={{
        status: "failed",
        code: "approval_not_pending",
        retryable: false,
      }}
      onResolve={() => undefined}
    />,
  );
  const allow = screen.getByRole("button", { name: "允许一次" }) as HTMLButtonElement;
  expect(allow.disabled).toBe(true);
});

test("unknown approval failures only suggest retrying when Runtime allows it", () => {
  const item = {
    kind: "tool" as const,
    id: "tc-1",
    turnId: "turn-1",
    approvalId: "ap-1",
    name: "run_command",
    status: "awaiting_approval" as const,
  };
  const { rerender } = render(
    <ToolCallCard
      item={item}
      submission={{ status: "failed", code: "future_error", retryable: false }}
      onResolve={() => undefined}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain(
    "审批提交失败，请等待页面同步最新状态。",
  );
  expect(screen.getByRole("alert").textContent).not.toContain("重试");

  rerender(
    <ToolCallCard
      item={item}
      submission={{ status: "failed", code: "future_error", retryable: true }}
      onResolve={() => undefined}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain(
    "审批提交失败，请稍后重试。",
  );
});

test.each([
  [{ turnId: undefined, id: "tc", approvalId: "ap" }, "missing turn id"],
  [{ turnId: "turn", id: "", approvalId: "ap" }, "missing tool call id"],
  [{ turnId: "turn", id: "tc", approvalId: undefined }, "missing approval id"],
])("approval controls fail closed for $1", (ids) => {
  render(
    <ToolCallCard
      item={{
        kind: "tool",
        name: "write_file",
        status: "awaiting_approval",
        ...ids,
      }}
      onResolve={() => undefined}
    />,
  );
  expect(screen.queryByRole("button", { name: "允许一次" })).toBeNull();
  expect(screen.queryByRole("button", { name: "拒绝" })).toBeNull();
});

test("tool card renders persisted approval terminal wording", () => {
  const { rerender } = render(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "t",
        turnId: "turn",
        approvalId: "ap",
        name: "write_file",
        status: "running",
        approvalDecision: "allowed",
      }}
    />,
  );
  expect(screen.getByText("已允许，正在执行")).toBeDefined();

  rerender(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "t",
        turnId: "turn",
        approvalId: "ap",
        name: "write_file",
        status: "running",
        approvalDecision: "denied",
      }}
    />,
  );
  expect(screen.getByText("已拒绝")).toBeDefined();

  rerender(
    <ToolCallCard
      item={{ kind: "tool", id: "t", name: "write_file", status: "cancelled" }}
    />,
  );
  expect(screen.getByText("已取消")).toBeDefined();
});

test("timeline notices render each variant with the right role", () => {
  const { rerender } = render(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "n1",
        notice: "attachment_degraded",
        total: 3,
        truncated: true,
        attachmentNames: ["a.png", "b.png"],
      }}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain("已省略 3 张历史图片");
  expect(screen.getByRole("status").textContent).toContain("a.png");

  rerender(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "n-effort",
        notice: "reasoning_effort_degraded",
        reasoningEffortFrom: "high",
        reasoningEffortTo: "auto",
      }}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain("已自动改为自动推理");

  rerender(
    <TimelineNotice item={{ kind: "notice", id: "n2", notice: "turn_interrupted" }} />,
  );
  expect(screen.getByRole("status").textContent).toContain("被中断");

  rerender(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "n3",
        notice: "turn_failed",
        errorCode: "provider_auth_failed",
      }}
    />,
  );
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toContain("模型连接认证失败");
});

test("timeline notices translate the document tool error codes", () => {
  const { rerender } = render(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "n-ws",
        notice: "turn_failed",
        errorCode: "document_workspace_required",
      }}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain(
    "请先为这个任务选择工作区，再生成 Word 文件",
  );

  rerender(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "n-notreq",
        notice: "turn_failed",
        errorCode: "document_write_not_requested",
      }}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain(
    "当前消息没有明确要求生成 Word，未创建文件",
  );
});

test("timeline notices expose only the recovery actions that match the failure", () => {
  const onRestoreText = vi.fn();
  const onOpenSettings = vi.fn();
  const onReselectAttachment = vi.fn();
  const callbacks = { onRestoreText, onOpenSettings, onReselectAttachment };
  const { rerender } = render(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "settings",
        notice: "turn_failed",
        errorCode: "provider_auth_failed",
        recoveryKind: "open_settings",
      }}
      {...callbacks}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "打开模型设置" }));
  expect(onOpenSettings).toHaveBeenCalledTimes(1);
  expect(onRestoreText).not.toHaveBeenCalled();
  expect(onReselectAttachment).not.toHaveBeenCalled();

  rerender(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "restore",
        notice: "turn_failed",
        errorCode: "provider_request_failed",
        recoveryKind: "restore_text",
        sourceText: "请继续检查合同",
      }}
      {...callbacks}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "恢复原指令" }));
  expect(onRestoreText).toHaveBeenCalledTimes(1);

  rerender(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "attachment",
        notice: "turn_failed",
        errorCode: "attachment_blob_missing",
        recoveryKind: "reselect_attachment",
        sourceText: "请检查附件",
      }}
      {...callbacks}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "重新选择附件" }));
  fireEvent.click(screen.getByRole("button", { name: "恢复文字" }));
  expect(onReselectAttachment).toHaveBeenCalledTimes(1);
  expect(onRestoreText).toHaveBeenCalledTimes(2);

  rerender(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "restart",
        notice: "turn_failed",
        errorCode: "internal_error",
        recoveryKind: "restart_app",
      }}
      {...callbacks}
    />,
  );
  expect(screen.getByText("请退出并重新打开应用后再检查此任务。")).toBeDefined();
  expect(screen.getByRole("alert").textContent).not.toContain("重试");
  expect(screen.queryByRole("button")).toBeNull();
});

test("timeline notice warns before a user repeats work after a side effect", () => {
  render(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "side-effect",
        notice: "turn_failed",
        errorCode: "provider_request_failed",
        recoveryKind: "restore_text",
        sourceText: "继续",
        hasPriorSideEffect: true,
      }}
      onRestoreText={() => undefined}
    />,
  );

  expect(screen.getByText("部分操作可能已经完成，请检查后再发送。")).toBeDefined();
});

test.each([
  ["invalid_provider_url", "模型服务地址无效"],
  ["insecure_remote_url", "远程模型服务地址必须使用 HTTPS"],
  ["secret_store_unavailable", "本地密钥配置暂时无法读取"],
  ["secret_store_write_failed", "本地密钥配置无法保存或删除"],
  ["provider_unreachable", "无法连接当前模型服务"],
  ["provider_stream_interrupted", "与模型服务的连接中断了"],
  ["provider_timeout", "模型服务响应超时"],
  ["provider_auth_failed", "模型连接认证失败"],
  ["provider_rate_limited", "模型服务请求过于频繁"],
  ["provider_model_not_found", "找不到当前配置的模型"],
  ["provider_model_invalid", "模型名无效"],
  ["provider_protocol_error", "模型服务返回了不兼容的响应"],
  ["provider_response_too_large", "本次生成内容过长"],
  ["approval_wait_expired", "等待操作确认已超时，待确认的操作没有执行"],
  ["provider_has_no_models", "模型服务没有可用模型"],
  ["provider_not_found", "当前模型连接已不存在"],
  ["provider_in_use", "当前模型连接正在使用中"],
  ["provider_busy", "模型服务操作繁忙"],
  ["attachment_type_unsupported", "附件格式不支持"],
  ["attachment_media_type_mismatch", "附件类型与内容不一致"],
  ["attachment_too_large", "附件大小超出限制"],
  ["attachment_count_exceeded", "附件数量超出限制"],
  ["attachment_total_size_exceeded", "附件总大小超出限制"],
  ["attachment_invalid_utf8", "文本附件不是有效的 UTF-8 内容"],
  ["attachment_pdf_invalid", "PDF 附件无法读取"],
  ["attachment_pdf_encrypted", "暂不支持加密 PDF 附件"],
  ["attachment_pdf_no_text", "PDF 附件没有可提取的文字"],
  ["attachment_pdf_page_limit_exceeded", "PDF 附件页数超出限制"],
  ["attachment_text_budget_exceeded", "附件文本总量超出上下文限制"],
  ["attachment_upload_unavailable", "附件不可用或已过期"],
  ["attachment_blob_missing", "附件本地内容缺失"],
  ["model_vision_unsupported", "当前模型不支持图片附件"],
])("turn error %s has stable everyday Chinese guidance", (errorCode, label) => {
  render(
    <TimelineNotice
      item={{
        kind: "notice",
        id: errorCode,
        notice: "turn_failed",
        errorCode,
        recoveryKind: "none",
      }}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain(label);
  expect(screen.getByRole("alert").textContent).not.toContain(`对话失败：${errorCode}`);
});

test("a genuinely unknown turn error keeps its stable code visible", () => {
  render(
    <TimelineNotice
      item={{
        kind: "notice",
        id: "future",
        notice: "turn_failed",
        errorCode: "future_runtime_error",
        recoveryKind: "none",
      }}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain(
    "对话失败：future_runtime_error",
  );
});

class MockWebSocket {
  static OPEN = 1;
  static instances: MockWebSocket[] = [];
  static last: MockWebSocket | null = null;
  readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sentMessages: string[] = [];
  constructor(public url: string, public subprotocol: string) {
    MockWebSocket.instances.push(this);
    MockWebSocket.last = this;
  }
  send(data: string) {
    this.sentMessages.push(data);
  }
  close() {
    this.readyState = 3;
  }
  receive(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

function receiveViewAwaitingApproval() {
  MockWebSocket.last!.receive({
    type: "event",
    event: {
      type: "turn_started",
      threadId: "thread-1",
      turnId: "turn-1",
      sequence: 1,
      payload: {},
    },
  });
  MockWebSocket.last!.receive({
    type: "event",
    event: {
      type: "tool_call_started",
      threadId: "thread-1",
      turnId: "turn-1",
      sequence: 2,
      payload: {
        toolCallId: "tc-1",
        name: "write_file",
        riskLevel: "high",
        argumentsSummary: { path: "/private/contracts/合同.md" },
      },
    },
  });
  MockWebSocket.last!.receive({
    type: "event",
    event: {
      type: "approval_requested",
      threadId: "thread-1",
      turnId: "turn-1",
      sequence: 3,
      payload: {
        approvalId: "ap-1",
        toolCallId: "tc-1",
        name: "write_file",
        riskLevel: "high",
      },
    },
  });
}

test("a historical approval stays locked until the socket reconnects", async () => {
  setupViewGlobals({
    turns: [{ id: "turn-1", status: "awaiting_approval" }],
    events: [
      { turn_id: "turn-1", sequence: 1, type: "turn_started", payload: {} },
      {
        turn_id: "turn-1",
        sequence: 2,
        type: "tool_call_started",
        payload: {
          toolCallId: "tc-1",
          name: "write_file",
          riskLevel: "high",
          argumentsSummary: { path: "/private/contracts/合同.md" },
        },
      },
      {
        turn_id: "turn-1",
        sequence: 3,
        type: "approval_requested",
        payload: {
          approvalId: "ap-1",
          toolCallId: "tc-1",
          name: "write_file",
          riskLevel: "high",
        },
      },
    ],
  });
  render(<AgentWorkspaceView {...viewProps()} />);

  const allow = await screen.findByRole("button", { name: "允许一次" }) as HTMLButtonElement;
  expect(allow.disabled).toBe(true);
  expect(screen.getByRole("status").textContent).toContain(
    "正在重新连接，连接后可审批",
  );

  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });
  expect(allow.disabled).toBe(false);
  expect(screen.queryByText("正在重新连接，连接后可审批")).toBeNull();
});

test.each([
  [
    "approval_not_pending",
    "Approval is not pending.",
    "这项操作已不再等待审批，请等待页面同步最新状态。",
  ],
  [
    "internal_error",
    "The Runtime could not complete the request.",
    "本地服务未能完成审批，请退出并重新打开应用后再检查。",
  ],
])("workspace maps %s approval failures to stable Chinese guidance", async (
  code,
  runtimeMessage,
  expectedMessage,
) => {
  setupViewGlobals();
  render(<AgentWorkspaceView {...viewProps()} />);
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });
  act(() => receiveViewAwaitingApproval());
  fireEvent.click(await screen.findByRole("button", { name: "允许一次" }));
  act(() => {
    MockWebSocket.last!.receive({
      type: "error",
      requestType: "turn.resolve",
      turnId: "turn-1",
      toolCallId: "tc-1",
      code,
      message: runtimeMessage,
      retryable: false,
    });
  });

  expect(screen.getByRole("alert").textContent).toContain(expectedMessage);
  expect(screen.queryByText(runtimeMessage)).toBeNull();
  expect((screen.getByRole("button", { name: "允许一次" }) as HTMLButtonElement).disabled)
    .toBe(true);
});

test("workspace approval card submits the exact turn and tool call ids", async () => {
  setupViewGlobals();
  render(<AgentWorkspaceView {...viewProps()} />);

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });
  act(() => receiveViewAwaitingApproval());

  fireEvent.click(await screen.findByRole("button", { name: "添加说明（可选）" }));
  fireEvent.change(screen.getByLabelText("审批说明（可选）"), {
    target: { value: "已核对" },
  });
  fireEvent.click(screen.getByRole("button", { name: "允许一次" }));

  expect(JSON.parse(MockWebSocket.last!.sentMessages.at(-1)!)).toEqual({
    type: "turn.resolve",
    turnId: "turn-1",
    toolCallId: "tc-1",
    decision: "allowed",
    reason: "已核对",
  });
  expect(screen.getByRole("status").textContent).toContain("正在提交决定");
});

test.each(["chat", "legal", "litigation"] as const)("%s keeps approval outside the scroll log and restores the draft after resolution", async variant => {
  setupViewGlobals();
  const props = {...viewProps(), variant, draft: "未发送的草稿"};
  const {container} = render(<AgentWorkspaceView {...props} />);
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => { MockWebSocket.last!.onopen?.(); });
  act(() => receiveViewAwaitingApproval());
  const allow = await screen.findByRole("button", {name: "允许一次"});
  expect(allow.closest(".message-list")).toBeNull();
  expect(allow.closest(".approval-dock")).not.toBeNull();
  expect(container.querySelectorAll(".approval-card")).toHaveLength(1);
  expect(container.querySelector(".message-list .approval-card")).toBeNull();
  const draft = container.querySelector(".agent-composer textarea") as HTMLTextAreaElement;
  expect(draft.value).toBe("未发送的草稿");
  expect(draft.closest("[hidden]")).not.toBeNull();
  fireEvent.click(allow);
  // No optimistic dismissal: a persisted decision is required.
  expect(container.querySelector(".approval-dock")).not.toBeNull();
  act(() => MockWebSocket.last!.receive({type: "event", event: {
    type: "approval_resolved", threadId: "thread-1", turnId: "turn-1", sequence: 4,
    payload: {toolCallId: "tc-1", approvalId: "ap-1", decision: "allowed"},
  }}));
  expect(container.querySelector(".approval-dock")).toBeNull();
  expect(draft.value).toBe("未发送的草稿");
  expect(draft.closest("[hidden]")).toBeNull();
});

test("read-only history never offers approval controls", async () => {
  setupViewGlobals();
  render(<AgentWorkspaceView {...viewProps()} readOnly />);
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => { MockWebSocket.last!.onopen?.(); });
  act(() => receiveViewAwaitingApproval());
  expect(screen.queryByRole("button", {name: "允许一次"})).toBeNull();
});

interface ViewFetchRoutes {
  messages?: unknown[];
  turns?: unknown[];
  events?: unknown[];
  attachments?: unknown[];
}

function setupViewGlobals(routes: ViewFetchRoutes = {}) {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  globalThis.__VERILEX_API_TOKEN__ = "test-token";
  vi.stubGlobal("WebSocket", MockWebSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.endsWith("/messages")
        ? routes.messages ?? []
        : url.endsWith("/turns")
          ? routes.turns ?? []
          : url.endsWith("/events")
            ? routes.events ?? []
            : url.endsWith("/attachments")
              ? routes.attachments ?? []
              : [];
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
  MockWebSocket.instances = [];
  MockWebSocket.last = null;
}

function viewProps() {
  return {
    hasModel: true,
    thread: {
      id: "local-1",
      backendId: "thread-1",
      title: "会话",
      createdAt: "2026-08-18T00:00:00Z",
      updatedAt: "2026-08-18T00:00:00Z",
      status: "idle",
    },
    workspaceName: "工作区",
    draft: "",
    onDraftChange: () => {},
    pendingAgentMessage: null,
    onPendingAgentMessageSent: () => {},
    onSendWithoutThread: () => {},
    onOpenSettings: () => {},
    onThreadsRefresh: () => {},
    onUserTurnAccepted: () => {},
  };
}

test("workspace view renders markdown, tool cards and the jump button", async () => {
  // jsdom does not implement scrolling primitives.
  Element.prototype.scrollIntoView = () => {};
  setupViewGlobals();
  render(<AgentWorkspaceView {...viewProps()} />);

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });

  act(() => {
    MockWebSocket.last!.receive({
      type: "ack",
      turnId: "turn-1",
      status: "queued",
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_started", turnId: "turn-1", sequence: 1, payload: {} },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "message_delta",
        turnId: "turn-1",
        sequence: 2,
        payload: { kind: "text", delta: "## 结论\n\n- 要点一" },
      },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "tool_call_started",
        turnId: "turn-1",
        sequence: 3,
        payload: { toolCallId: "tc-1", name: "read_file" },
      },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "tool_call_completed",
        turnId: "turn-1",
        sequence: 4,
        payload: {
          toolCallId: "tc-1",
          status: "succeeded",
          durationMs: 5,
          resultPreview: "hello",
          resultTruncated: false,
        },
      },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_completed", turnId: "turn-1", sequence: 5, payload: {} },
    });
  });

  await waitFor(() => {
    expect(screen.getByText("结论")).toBeDefined();
  });
  expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(1);
  // Finished turns fold under the duration header: expand the log to see the
  // quiet step line (Codex-style "已读取 …" row instead of a boxed card).
  const processToggle = screen.getByRole("button", { name: /用时/ });
  await act(async () => {
    processToggle.click();
  });
  expect(screen.getAllByText(/已读取/).length).toBeGreaterThanOrEqual(1);

  // Scrolling away from the bottom pauses following and shows the jump button.
  const list = document.querySelector(".message-list") as HTMLElement;
  Object.defineProperty(list, "scrollHeight", { value: 1000, configurable: true });
  Object.defineProperty(list, "clientHeight", { value: 300, configurable: true });
  fireEvent.scroll(list);
  const jump = screen.getByRole("button", { name: /回到最新/ });
  expect(jump).toBeDefined();

  await act(async () => {
    jump.click();
  });
  expect(list.scrollTop).toBe(1000);
  expect(screen.queryByRole("button", { name: /回到最新/ })).toBeNull();
});

test("all assistant text segments hide citation markers until the whole message completes", async () => {
  Element.prototype.scrollIntoView = () => {};
  setupViewGlobals();
  render(<AgentWorkspaceView {...viewProps()} />);

  await waitFor(() => expect(MockWebSocket.last).not.toBeNull());
  await act(async () => {
    MockWebSocket.last!.onopen?.();
  });
  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: { type: "turn_started", turnId: "turn-1", sequence: 1, payload: {} },
    });
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "message_delta",
        turnId: "turn-1",
        sequence: 2,
        payload: { kind: "text", delta: "事实[[cite:source-a]]" },
      },
    });
  });
  await waitFor(() => expect(document.body.textContent).toContain("事实"));
  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "message_delta",
        turnId: "turn-1",
        sequence: 3,
        payload: { kind: "reasoning", delta: "继续核验" },
      },
    });
  });

  await waitFor(() => {
    expect(screen.getByRole("button", { name: /正在深度思考/ })).toBeDefined();
  });
  expect(document.body.textContent).not.toContain("source-a");
  expect(document.body.textContent).not.toContain("引用未验证");
  expect(screen.queryByRole("link", { name: /来源 1/ })).toBeNull();

  act(() => {
    MockWebSocket.last!.receive({
      type: "event",
      event: {
        type: "message_completed",
        turnId: "turn-1",
        sequence: 4,
        payload: {
          sources: [{
            number: 1,
            sourceId: "source-a",
            title: "完成后来源",
            url: "https://example.com/a",
            site: "example.com",
            fetchedAt: "2026-08-27T10:00:00Z",
            truncated: false,
          }],
        },
      },
    });
  });
  expect(document.querySelector("a.citation-link")?.getAttribute("href"))
    .toBe("https://example.com/a");
});

test("workspace history renders a semantic verified-source ledger without public fetches", async () => {
  Element.prototype.scrollIntoView = () => {};
  setupViewGlobals({
    messages: [
      {
        id: "m-1",
        thread_id: "thread-1",
        turn_id: "turn-1",
        role: "assistant",
        content: [
          { type: "text", text: "核验结论[[cite:source-a]]" },
          {
            type: "sources",
            sources: [{
              number: 1,
              sourceId: "source-a",
              title: "一份很长但经过系统核验的网页来源标题",
              url: "https://example.com/a/very/long/path/that/must/wrap",
              site: "example.com",
              fetchedAt: "2026-08-27T10:00:00Z",
              truncated: true,
            }],
          },
          { type: "citation_warning" },
        ],
        created_at: "2026-08-27T10:01:00Z",
      },
    ],
    turns: [{ id: "turn-1", status: "completed", error_code: null }],
  });

  render(
    <div style={{ width: "375px", overflowX: "hidden" }}>
      <AgentWorkspaceView {...viewProps()} />
    </div>,
  );

  const sourceList = await screen.findByRole("list", { name: "已验证来源" });
  expect(sourceList.tagName).toBe("OL");
  const sourceLink = within(sourceList).getByRole("link", {
    name: "来源 1：一份很长但经过系统核验的网页来源标题",
  });
  expect(sourceLink.getAttribute("href")).toBe(
    "https://example.com/a/very/long/path/that/must/wrap",
  );
  expect(screen.getByText("内容已截断")).toBeDefined();
  expect(screen.getByRole("status").textContent).toContain("部分引用未通过验证");
  sourceLink.focus();
  expect(document.activeElement).toBe(sourceLink);

  const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  expect(calls.every(([input]) => String(input).startsWith("http://127.0.0.1:9999/")))
    .toBe(true);
});
