import type { ReasoningEffort } from "../runtime/contracts";

export type WorkViewId = "agent" | "settings" | "legal-search" | "case-search" | "litigation" | "knowledge" | "redact" | "contract-review" | "plugins" | "scheduled" | "case-calendar";

export interface ThreadSummary {
  id: string;
  backendId: string | null;
  title: string;
  providerId: string | null;
  model: string;
  workspaceId: string | null;
  createdAt: string;
  updatedAt: string;
  status: string;
  isDraft: boolean;
  reasoningEffort: ReasoningEffort;
  scene: string;
  legalChannel?: "web" | "plugin" | null;
  legalSkills?: string[];
}

/** Sessions created before any user message keep a numbered default title. */
const DEFAULT_TITLE_PATTERN = /^新会话( \d+)?$/;

export function isDefaultThreadTitle(title: string): boolean {
  return DEFAULT_TITLE_PATTERN.test(title.trim());
}

/** First-message auto naming: collapse whitespace, cap at 20 chars. */
export function derivedThreadTitle(
  text: string,
  firstAttachmentName: string = "",
): string {
  const collapsed = (text.trim() || firstAttachmentName.trim()).replace(/\s+/g, " ");
  if (!collapsed) return "";
  return collapsed.length > 20 ? `${collapsed.slice(0, 20)}…` : collapsed;
}

/** Compact sidebar age: 刚刚 / N分钟前 / N小时前 / N天前 / M月D日. */
export function formatRelativeTime(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return "";
  const elapsed = Math.max(0, now - then);
  if (elapsed < 60_000) return "刚刚";
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 60) return `${minutes}分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}天前`;
  const date = new Date(then);
  const sameYear = date.getFullYear() === new Date(now).getFullYear();
  const monthDay = `${date.getMonth() + 1}月${date.getDate()}日`;
  return sameYear ? monthDay : `${date.getFullYear()}年${monthDay}`;
}

export function workViewLabel(id: WorkViewId): string {
  if (id === "case-calendar") return "案件日程";
  if (id === "scheduled") return "定时任务";
  if (id === "plugins") return "能力中心";
  if (id === "redact") return "文件脱敏";
  if (id === "knowledge") return "知识库";
  if (id === "settings") return "设置";
  if (id === "legal-search") return "法律检索";
  if (id === "case-search") return "案例检索";
  if (id === "litigation") return "文书起草";
  if (id === "contract-review") return "合同审查";
  return "VeriLex";
}
