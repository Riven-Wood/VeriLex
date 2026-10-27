const GENERIC_MCP_ERROR = "连接没有成功，请检查配置后重试。";

const MCP_ERROR_COPY: Readonly<Record<string, string>> = {
  mcp_config_invalid: "无法识别这份配置，请修改后重试。",
  mcp_config_blocked: "配置包含不安全内容，已停止处理。",
  mcp_secret_in_arguments: "命令参数中可能包含秘密，请移除后改用秘密环境变量保存。",
  mcp_url_credentials: "网络地址中包含账号或密码，请从地址中移除。",
  mcp_secret_in_url: "网络地址中可能包含令牌等秘密，请移除后改用秘密请求头保存。",
  mcp_config_review_required: "配置发生了变化，请先查看后再继续。",
  mcp_name_conflict: "这个名称已经被使用，请换一个名称。",
  mcp_revision_conflict: "配置已在别处更新，请刷新后重试。",
  mcp_test_receipt_invalid: "配置已发生变化，请重新测试后再保存。",
  mcp_test_receipt_expired: "测试结果已过期，请重新测试后再保存。",
  mcp_keychain_unavailable: "本地密钥配置暂时不可用，请稍后重试。",
  mcp_credentials_missing: "需要补充密钥后再试。",
  mcp_connect_timeout: "这个工具服务连接超时。请检查网络或本地服务，在能力中心重新测试后再使用。",
  mcp_connection_failed: "无法连接这个工具服务。请检查网络或本地服务，在能力中心重新测试；其他本地功能仍可使用。",
  mcp_schema_unsupported: "这个服务器提供的工具格式暂不受支持。",
  mcp_arguments_invalid: "工具参数不符合要求，本次没有执行。",
  mcp_tool_changed: "这个服务器改变了工具，请先查看后再继续。",
  mcp_tool_disabled: "这个工具当前没有启用。",
  mcp_not_selected: "当前对话尚未选择这个服务器。",
  mcp_binary_output_omitted: "工具返回了当前版本无法显示的二进制内容。",
  mcp_output_too_large: "工具返回的内容过大，本次没有载入。",
  mcp_result_unknown: "服务中断，操作可能已经完成，请先检查结果再决定是否重试。",
  mcp_tool_error: "插件返回执行失败，本次未取得可用结果。请查看回答中的说明后再决定是否重试。",
  mcp_transaction_failed: "更改没有保存成功，请重试。",
  mcp_tool_catalog_invalid: "服务器返回的工具列表无法使用。",
  mcp_connection_retiring: "旧连接正在停止，请稍后重试。",
};

/** Return user-facing copy from a closed allowlist; raw Runtime text is never shown. */
export function mcpErrorCopy(code: unknown): string {
  return typeof code === "string"
    ? MCP_ERROR_COPY[code] ?? GENERIC_MCP_ERROR
    : GENERIC_MCP_ERROR;
}
