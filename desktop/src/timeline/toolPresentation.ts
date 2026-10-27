import type { ToolItem } from "./items";

const WEB_TOOLS = new Set(["web_search", "web_read"]);

export interface WebSearchCandidateSummary {
  title: string;
  url: string;
  site: string;
  snippet: string;
}

interface WebSearchResultSummary extends Record<string, unknown> {
  kind: "web_search";
  resultCount: number;
  results: WebSearchCandidateSummary[];
}

interface WebReadResultSummary extends Record<string, unknown> {
  kind: "web_read";
  title: string;
  site: string;
  characterCount: number;
  truncated: boolean;
}

type WebResultSummary = WebSearchResultSummary | WebReadResultSummary;

function bounded(value: unknown, fallback: string, limit = 200): string {
  if (typeof value !== "string" || !value.trim()) return fallback;
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
}

function publicHost(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.hostname
      : null;
  } catch {
    return null;
  }
}

function safeCandidateUrl(value: unknown): { url: string; site: string } | null {
  if (typeof value !== "string" || value.length > 2048) return null;
  try {
    const parsed = new URL(value);
    if (
      (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password
    ) return null;
    return { url: parsed.toString(), site: parsed.hostname };
  } catch {
    return null;
  }
}

function plain(value: unknown, maximum: number, allowEmpty = false): string | null {
  if (typeof value !== "string" || value.length > maximum) return null;
  if (!allowEmpty && !value.trim()) return null;
  if (Array.from(value).some((character) => {
    const code = character.charCodeAt(0);
    return code < 32 || code === 127;
  })) return null;
  return value;
}

function normalizedWebResultSummary(value: unknown): WebResultSummary | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (record.kind === "web_search") {
    if (!Array.isArray(record.results) || record.results.length > 10) return null;
    const results: WebSearchCandidateSummary[] = [];
    for (const candidateValue of record.results) {
      if (
        typeof candidateValue !== "object" ||
        candidateValue === null ||
        Array.isArray(candidateValue)
      ) continue;
      const candidate = candidateValue as Record<string, unknown>;
      const title = plain(candidate.title, 512);
      const snippet = plain(candidate.snippet, 2_000, true);
      const target = safeCandidateUrl(candidate.url);
      if (!title || snippet === null || !target) continue;
      results.push({ title, url: target.url, site: target.site, snippet });
    }
    return { kind: "web_search", resultCount: results.length, results };
  }
  if (record.kind !== "web_read") return null;
  const title = plain(record.title, 512);
  const site = plain(record.site, 255);
  const characterCount = record.characterCount;
  if (
    !title ||
    !site ||
    typeof characterCount !== "number" ||
    !Number.isInteger(characterCount) ||
    characterCount < 1 ||
    characterCount > 20_000 ||
    typeof record.truncated !== "boolean"
  ) return null;
  return {
    kind: "web_read",
    title,
    site,
    characterCount,
    truncated: record.truncated,
  };
}

export function webResultSummaryFromOutput(
  name: string,
  content: string,
): Record<string, unknown> | undefined {
  if (name !== "web_search" && name !== "web_read") return undefined;
  try {
    const payload: unknown = JSON.parse(content);
    if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
      return undefined;
    }
    const value = payload as Record<string, unknown>;
    const candidate = name === "web_search"
      ? {
          kind: "web_search",
          resultCount: value.result_count,
          results: value.results,
        }
      : {
          kind: "web_read",
          title: value.title,
          site: value.site,
          characterCount: value.character_count,
          truncated: value.truncated,
        };
    return normalizedWebResultSummary(candidate) ?? undefined;
  } catch {
    return undefined;
  }
}

function resultSummary(item: ToolItem): WebResultSummary | null {
  const structured = normalizedWebResultSummary(item.resultSummary);
  if (structured) return structured;
  if (!item.resultPreview || item.resultTruncated) return null;
  return normalizedWebResultSummary(
    webResultSummaryFromOutput(item.name, item.resultPreview),
  );
}

export function webSearchCandidates(
  item: ToolItem,
): WebSearchCandidateSummary[] {
  const result = resultSummary(item);
  return result?.kind === "web_search" ? result.results : [];
}

export function isWebTool(item: ToolItem): boolean {
  return WEB_TOOLS.has(item.name);
}

export function webToolArguments(item: ToolItem): string | null {
  if (item.name === "web_search") {
    return `搜索词：${bounded(item.externalValue ?? item.argumentsSummary?.query, "未提供")}`;
  }
  if (item.name !== "web_read") return null;
  const value = item.externalValue ?? item.argumentsSummary?.url;
  if (typeof value !== "string") return "网址：未提供";
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "网址：格式无效";
    }
    parsed.search = "";
    parsed.hash = "";
    parsed.username = "";
    parsed.password = "";
    return `网址：${bounded(parsed.toString(), "格式无效", 220)}`;
  } catch {
    return "网址：格式无效";
  }
}

export function webToolSummary(item: ToolItem): string | null {
  if (item.name !== "web_read" || item.status !== "succeeded") return null;
  const result = resultSummary(item);
  if (result?.kind !== "web_read") return null;
  const site = result.site;
  const count = `${result.characterCount.toLocaleString("zh-CN")} 个字符`;
  const truncated = result.truncated ? "内容已截断" : null;
  return [site, count, truncated].filter(Boolean).join(" · ") || null;
}

export function webApprovalImpact(item: ToolItem): string | null {
  if (item.approvalBehavior !== "thread_first_use" || !isWebTool(item)) {
    return null;
  }
  const recipient = bounded(
    item.externalRecipient ?? (
      item.name === "web_search"
        ? "DuckDuckGo"
        : publicHost(item.externalValue ?? item.argumentsSummary?.url) ?? "目标网站"
    ),
    "外部服务",
  );
  if (item.name === "web_search") {
    return `搜索词将发送到外部服务 ${recipient}；候选结果会交给当前模型继续处理。系统网络代理可能参与转发。`;
  }
  return `${recipient} 会收到普通网页请求；提取出的网页文字会交给当前模型继续处理。系统网络代理可能参与转发。`;
}

export function toolArgumentBasename(value: unknown): string {
  if (typeof value !== "string") return "文件";
  return value.split(/[\\/]/).filter(Boolean).at(-1) ?? "文件";
}

export function toolPresentation(item: ToolItem): { title: string; impact: string } {
  const fileName = toolArgumentBasename(item.argumentsSummary?.path);
  if (item.name === "ppt_plugin_create" || item.name === "ppt_plugin_replace") {
    return { title: item.name === "ppt_plugin_create" ? "生成 PPT 演示文稿" : "修改 PPT 副本", impact: "在工作文件夹交付新 PPTX，原文件保留；请用演示软件核对排版。" };
  }
  if (item.name === "ppt_plugin_read") return { title: "读取 PPT 文字与备注", impact: "只读取工作文件夹内的 PPTX；不还原图片、动画或版式。" };
  const sourceName = toolArgumentBasename(item.argumentsSummary?.source);
  if (item.name === "litigation_work_save") {
    return {title: "保存工作台成果", impact: "保存后更新中央工作页，并保留旧版本。案卷原件保持不变。"};
  }
  if (item.name === "litigation_work_read") {
    return {title: "读取工作台成果", impact: "读取当前讨论对象和已保存成果"};
  }
  if (
    item.approvalBehavior === "thread_first_use" &&
    isWebTool(item) &&
    item.status === "awaiting_approval"
  ) {
    return {
      title: "允许此任务联网？",
      impact: webApprovalImpact(item) ?? "联网前需要你的允许",
    };
  }
  if (item.name === "web_search") {
    const result = resultSummary(item);
    const count = result?.kind === "web_search" ? result.resultCount : null;
    if (item.status === "succeeded") {
      return {
        title: typeof count === "number" && Number.isInteger(count) && count >= 0
          ? `找到 ${count} 条候选`
          : "已找到网页候选",
        impact: "搜索结果尚未读取正文，不能作为引用来源",
      };
    }
    return {
      title: `正在搜索：${bounded(item.argumentsSummary?.query, "网页资料")}`,
      impact: "搜索结果尚未读取正文，不能作为引用来源",
    };
  }
  if (item.name === "web_read") {
    const result = resultSummary(item);
    const title = bounded(
      result?.kind === "web_read" ? result.title : undefined,
      publicHost(item.argumentsSummary?.url) ?? "网页",
    );
    return {
      title: item.status === "succeeded" ? `已读取 ${title}` : `正在读取 ${title}`,
      impact: "网页文字会作为不可信外部资料交给当前模型",
    };
  }
  if (item.name === "law_search" || item.name === "law_read") {
    return {
      title: item.name === "law_search" ? "检索国家法律法规数据库" : "读取国家法律法规数据库条文",
      impact: "检索词或法规编号将发送到国家法律法规数据库，返回资料交给当前模型处理；允许后，本次检索中该工具的后续调用无需重复确认。",
    };
  }
  if (item.name === "write_file") {
    return { title: `创建文件 ${fileName}`, impact: "会修改本机工作区文件" };
  }
  if (item.name === "apply_file_patch") {
    return { title: `修改文件 ${fileName}`, impact: "会修改本机工作区文件" };
  }
  if (item.name === "run_command") {
    return { title: "运行命令", impact: "命令拥有当前系统用户权限" };
  }
  if (item.name === "run_background_command") {
    return {
      title: "后台运行命令",
      impact: "命令拥有当前系统用户权限，将在后台运行，可随时停止",
    };
  }
  if (item.name === "background_task_output") {
    return { title: "查看后台命令进度", impact: "只读取后台命令的输出" };
  }
  if (item.name === "background_task_stop") {
    return { title: "停止后台命令", impact: "会终止这条后台命令" };
  }
  if (item.name === "copy_path") {
    return {
      title: `复制文件 ${sourceName}`,
      impact: "会在工作区内复制出一份新文件，不修改原文件",
    };
  }
  if (item.name === "move_path") {
    return {
      title: `移动/重命名 ${sourceName}`,
      impact: "会移动或重命名工作区内的文件",
    };
  }
  if (item.name === "delete_path") {
    return {
      title: `删除文件 ${fileName}`,
      impact: "将移到废纸篓，可从废纸篓恢复",
    };
  }
  return { title: `执行 ${item.name}`, impact: "此操作需要你的允许" };
}

export function approvalErrorMessage(
  code: string | undefined,
  retryable?: boolean,
): string {
  if (code === "approval_not_pending") {
    return "这项操作已不再等待审批，请等待页面同步最新状态。";
  }
  if (code === "internal_error") {
    return "本地服务未能完成审批，请退出并重新打开应用后再检查。";
  }
  if (code === "invalid_turn_request") {
    return "审批请求已失效，请等待页面同步最新状态。";
  }
  if (retryable === false) {
    return "审批提交失败，请等待页面同步最新状态。";
  }
  return "审批提交失败，请稍后重试。";
}

function baseName(value: unknown): string {
  const text = typeof value === "string" ? value : "";
  if (!text) return "";
  const parts = text.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? text;
}

function commandExcerpt(value: unknown, limit = 44): string {
  const text = typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

/**
 * Codex-style single quiet line for a tool step: a plain past-tense verb
 * plus its target ("已读取 合同.doc", "已运行 git status…"). Document names
 * for additive artifacts stay private — the summary only carries a hash,
 * so that line stays generic on purpose.
 */
export function toolStepLine(item: ToolItem): string {
  const running = item.status === "running";
  const path = baseName(item.argumentsSummary?.path);
  const source = baseName(item.argumentsSummary?.source);
  const prefix = running ? "正在" : "已";
  switch (item.name) {
    case "read_file":
      return `${prefix}读取 ${path}`;
    case "list_directory":
      return path ? `${prefix}列出 ${path} 的内容` : `${prefix}列出工作区文件`;
    case "search_files":
      return `${prefix}检索 ${
        commandExcerpt(item.argumentsSummary?.query, 20) || "工作区"
      }`;
    case "knowledge_find":
      return "定位原文关键词";
    case "knowledge_follow_link":
      return `${prefix}定位资料中的链接`;
    case "knowledge_search":
      return `${prefix}查找知识库资料`;
    case "knowledge_read":
      return `${prefix}读取知识库原文`;
    case "knowledge_quote":
      return `${prefix}生成原文引用`;
    case "skill_read":
      return `${prefix}读取技能 ${baseName(item.argumentsSummary?.name) || ""}`.trimEnd();
    case "document_create":
      return `${prefix}生成 Word 文档`;
    case "litigation_work_read":
      return `${prefix}读取工作台成果`;
    case "litigation_work_save":
      return `${prefix}保存工作台成果`;
    case "write_file":
      return `${prefix}创建 ${path}`;
    case "apply_file_patch":
      return `${prefix}修改 ${path}`;
    case "run_command":
      return `${prefix}运行 ${commandExcerpt(item.argumentsSummary?.command)}`;
    case "run_background_command":
      return `${prefix}转入后台运行 ${commandExcerpt(item.argumentsSummary?.command)}`;
    case "background_task_output":
      return `${prefix}查看后台命令进度`;
    case "background_task_stop":
      return `${prefix}停止后台命令`;
    case "copy_path":
      return `${prefix}复制 ${source}`;
    case "move_path":
      return `${prefix}移动 ${source}`;
    case "delete_path":
      return `${prefix}删除 ${path}`;
    case "web_search":
      return `${prefix}搜索网页`;
    case "web_read":
      return `${prefix}读取网页`;
    default:
      return `${prefix}执行 ${item.name}`;
  }
}
