import { memo } from "react";
import { mcpErrorCopy } from "../../mcp/errorCopy";

import type { NoticeItem } from "../../timeline/items";
import { safeWebErrorMessage } from "../../timeline/webErrors";

const TURN_ERROR_LABELS: Record<string, string> = {
  invalid_provider_url: "模型服务地址无效，请检查连接设置。",
  insecure_remote_url: "远程模型服务地址必须使用 HTTPS。",
  secret_store_unavailable: "本地密钥配置暂时无法读取，请退出并重新打开应用。",
  secret_store_write_failed: "本地密钥配置无法保存或删除，请检查磁盘空间后重试。",
  provider_unreachable: "无法连接当前模型服务。请检查网络或本机模型是否运行，再测试连接；本地历史和文件仍可使用。",
  provider_stream_interrupted: "与模型服务的连接中断了，回复可能不完整。请检查已有成果，恢复连接后再发送。",
  provider_timeout: "模型服务响应超时。请检查该服务后再发送；这不代表所有服务都已断网。",
  provider_auth_failed: "模型连接认证失败，请检查密钥或重新测试连接。",
  provider_request_failed: "模型请求失败，请稍后重试。",
  provider_model_invalid: "模型名无效，请在设置中检查模型配置。",
  provider_overloaded: "模型服务繁忙，请稍后重试。",
  provider_rate_limited: "模型服务请求过于频繁，请稍后重试。",
  provider_model_not_found: "找不到当前配置的模型，请检查模型设置。",
  provider_context_exceeded: "当前内容超出模型容量，请减少本轮材料或更换容量更大的模型。",
  provider_protocol_error: "模型服务返回了不兼容的响应，请检查服务兼容性。",
  provider_response_too_large: "本次生成内容过长，已停止。请缩小本次工作范围，分几次完成；已有工作成果仍保留。",
  provider_vision_unsupported: "当前模型不支持图片附件，请更换模型或重新选择附件。",
  provider_has_no_models: "模型服务没有可用模型，请检查模型设置。",
  provider_not_found: "当前模型连接已不存在，请重新选择模型。",
  provider_in_use: "当前模型连接正在使用中，暂时无法更改。",
  provider_busy: "模型服务操作繁忙，请稍后重试。",
  provider_disabled: "当前模型连接已停用，请在设置中重新启用或选择其他模型。",
  knowledge_answer_unverified: "回答未通过已读资料核对，未展示草稿。已生成的原文卡仍可查看；可缩小问题范围后重试。",
  turn_limit_exceeded: "本次任务超出长度或时间限制，已停止。",
  approval_wait_expired: "等待操作确认已超时，待确认的操作没有执行。请重新发起；此前已完成的操作仍保留。",
  turn_conflict: "会话正在运行另一个任务，请稍候。",
  attachment_type_unsupported: "附件格式不支持，请重新选择文件。",
  attachment_upload_unavailable: "附件不可用或已过期，请重新上传。",
  attachment_too_large: "附件大小超出限制，请选择较小的文件。",
  attachment_count_exceeded: "附件数量超出限制（最多 10 个）。",
  attachment_total_size_exceeded: "附件总大小超出限制（30 MiB）。",
  attachment_invalid_utf8: "文本附件不是有效的 UTF-8 内容，请重新保存后选择。",
  attachment_pdf_invalid: "PDF 附件无法读取，请检查文件后重新选择。",
  attachment_pdf_encrypted: "暂不支持加密 PDF 附件，请先解除密码保护。",
  attachment_pdf_no_text: "PDF 附件没有可提取的文字，请选择其他文件。",
  attachment_pdf_page_limit_exceeded: "PDF 附件页数超出限制，请拆分后重新选择。",
  attachment_text_budget_exceeded: "附件文本总量超出上下文限制。",
  attachment_media_type_mismatch: "附件类型与内容不一致，已被拒绝。",
  attachment_blob_missing: "附件本地内容缺失，请重新上传。",
  model_vision_unsupported: "当前模型不支持图片附件，请更换模型或重新选择附件。",
  document_workspace_required: "请先为这个任务选择工作区，再生成 Word 文件。",
  document_write_not_requested: "当前消息没有明确要求生成 Word，未创建文件。",
  internal_error: "本地服务需要恢复。",
};

const REASONING_EFFORT_LABELS: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
  auto: "自动",
};

function failedText(errorCode: string | undefined): string {
  if (!errorCode) return "对话失败。";
  if (errorCode.startsWith("mcp_")) return mcpErrorCopy(errorCode);
  return safeWebErrorMessage(errorCode) ?? TURN_ERROR_LABELS[errorCode] ?? `对话失败：${errorCode}`;
}

interface TimelineNoticeProps {
  item: NoticeItem;
  onRestoreText?: () => void;
  onOpenSettings?: () => void;
  onOpenMcpSettings?: () => void;
  onReselectAttachment?: () => void;
}

export const TimelineNotice = memo(function TimelineNotice({
  item,
  onRestoreText,
  onOpenSettings,
  onOpenMcpSettings,
  onReselectAttachment,
}: TimelineNoticeProps) {
  if (item.notice === "attachment_degraded") {
    const names = item.attachmentNames ?? [];
    const suffix =
      names.length > 0 && (item.truncated || names.length < (item.total ?? names.length))
        ? `等 ${item.total ?? names.length} 个文件`
        : "";
    const list = names.length > 0 ? `（${names.join("、")}${suffix ? "，" : ""}${suffix}）` : "";
    return (
      <div className="timeline-notice notice-degraded" role="status">
        已省略 {item.total ?? names.length} 张历史图片：当前模型不支持视觉输入{list}
      </div>
    );
  }
  if (item.notice === "turn_cancelled") return <div className="timeline-notice" role="status">本轮已停止，已生成的内容保留在这里。</div>;
  if (item.notice === "turn_retry") return <div className="timeline-notice" role="status">模型服务暂时不可用，正在自动重试（第 {item.attempt}/2 次）</div>;
  if (item.notice === "turn_compacted") return <div className="timeline-notice" role="status">已压缩早期对话内容{item.degraded ? "（摘要失败，已保留早期问题清单）" : ""}<div>发送给 AI 的早期内容已缩短，完整原文仍保留在本机。</div></div>;
  if (item.notice === "reasoning_effort_degraded") {
    const from = item.reasoningEffortFrom
      ? REASONING_EFFORT_LABELS[item.reasoningEffortFrom] ?? item.reasoningEffortFrom
      : "所选";
    return (
      <div className="timeline-notice notice-degraded" role="status">
        当前模型不支持{from}强度，已自动改为自动推理并继续回复。
      </div>
    );
  }
  if (item.notice === "turn_interrupted") {
    return (
      <div className="timeline-notice notice-interrupted" role="status">
        <div>会话在此处被中断（应用退出或重启）。可重新发送消息继续。</div>
        {item.recoveryKind === "restore_text" && onRestoreText && (
          <div className="notice-actions">
            <button type="button" onClick={onRestoreText}>恢复原指令</button>
          </div>
        )}
      </div>
    );
  }

  const mcpFailed = item.errorCode?.startsWith("mcp_");
  const connectionFailed = ["provider_unreachable", "provider_timeout", "provider_stream_interrupted"].includes(item.errorCode ?? "");
  const recoveryAction = item.recoveryKind === "open_settings" && onOpenSettings
    ? <button type="button" onClick={onOpenSettings}>打开模型设置</button>
    : item.recoveryKind === "restore_text" && onRestoreText
      ? <button type="button" onClick={onRestoreText}>恢复原指令</button>
      : item.recoveryKind === "reselect_attachment" && onReselectAttachment
        ? <button type="button" onClick={onReselectAttachment}>重新选择附件</button>
        : null;

  return (
    <div className="timeline-notice notice-failed" role="alert">
      <div>{failedText(item.errorCode)}</div>
      {(item.errorCode === "mcp_connection_failed" || item.errorCode === "mcp_connect_timeout") && (
        <div className="notice-guidance">如果这轮不需要该工具，可在对话的插件列表中取消选择，再发送原指令。</div>
      )}
      {item.recoveryKind === "restart_app" && (
        <div className="notice-guidance">请退出并重新打开应用后再检查此任务。</div>
      )}
      {item.hasPriorSideEffect && (
        <div className="notice-side-effect-warning">
          部分操作可能已经完成，请检查后再发送。
        </div>
      )}
      {(recoveryAction || (mcpFailed && onOpenMcpSettings) || (connectionFailed && onOpenSettings) || (
        item.recoveryKind === "reselect_attachment" &&
        item.sourceText &&
        onRestoreText
      )) && (
        <div className="notice-actions">
          {recoveryAction}
          {mcpFailed && onOpenMcpSettings && (
            <button type="button" onClick={onOpenMcpSettings}>检查外部工具</button>
          )}
          {connectionFailed && item.recoveryKind !== "open_settings" && onOpenSettings && (
            <button type="button" onClick={onOpenSettings}>检查模型连接</button>
          )}
          {item.recoveryKind === "reselect_attachment" && item.sourceText && onRestoreText && (
            <button type="button" onClick={onRestoreText}>恢复文字</button>
          )}
        </div>
      )}
    </div>
  );
});
