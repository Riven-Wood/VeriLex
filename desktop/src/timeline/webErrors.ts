const WEB_ERROR_LABELS: Readonly<Record<string, string>> = {
  web_access_disabled: "本任务尚未打开联网，请先开启联网后再试。",
  web_access_not_granted: "本任务首次联网未获允许，未发送网页请求。",
  web_search_unavailable: "免费搜索服务暂时不可用，请稍后再试。",
  web_search_rate_limited: "搜索过于频繁，请稍后再试。",
  web_url_invalid: "网址格式不受支持，请检查后再试。",
  web_url_blocked: "网址指向本机、局域网或其他禁止地址，已停止读取。",
  web_redirect_blocked: "网页跳转到了禁止地址，已停止读取。",
  web_fetch_timeout: "网页读取超时，请稍后再试。",
  web_fetch_too_large: "网页超过 2 MiB，本轮未读取。",
  web_content_unsupported: "本轮只读取普通网页和纯文本，不读取 PDF、下载或其他二进制内容。",
  web_fetch_failed: "网页暂时无法读取，请稍后再试。",
  web_turn_limit_reached: "本轮读取的网页数量或正文总量已达上限。",
  web_citation_invalid: "网页引用未通过核验，未将其列为来源。",
};

export const GENERIC_WEB_ERROR = "网页操作未能完成，请稍后重试。";

export function webErrorMessage(code: unknown): string | null {
  return typeof code === "string" ? WEB_ERROR_LABELS[code] ?? null : null;
}

export function safeWebErrorMessage(code: unknown): string | null {
  const known = webErrorMessage(code);
  if (known) return known;
  return typeof code === "string" && code.startsWith("web_")
    ? GENERIC_WEB_ERROR
    : null;
}

export function webErrorMessageFromPreview(preview: unknown): string | null {
  if (typeof preview !== "string") return null;
  const match = /^([a-z][a-z0-9_]*):/.exec(preview);
  return match ? webErrorMessage(match[1]) : null;
}
