import type { TimelineItem } from "./items";
import { visibleAssistantText } from "./assistantText";
const statuses: Record<string, string> = {succeeded: "成功", failed: "失败", denied: "已拒绝", cancelled: "已取消", running: "进行中", awaiting_approval: "等待批准"};
export function exportFilename(title: string, date = new Date()): string {
  return `VeriLex-${title.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").slice(0, 80) || "对话"}-${date.toISOString().slice(0, 10)}.md`;
}
export function exportMarkdown(title: string, createdAt: string, items: TimelineItem[], running = false, date = new Date()): string {
  const lines = [`# ${title.replace(/[\r\n]/g, " ")}`, `创建时间：${createdAt}`, `导出时间：${date.toISOString()}`, "工具调用为简记，完整记录见应用内时间线。", ...(running ? ["本会话仍在进行中，以下为导出时的内容。"] : [])];
  for (const item of items) {
    if (item.kind === "user") {
      lines.push("## 你", item.text);
      const names = item.attachments?.map(a => a.name) ?? item.attachmentNames;
      if (names?.length) lines.push(`附件（未嵌入文件）：${names.join("、")}`);
    } else if (item.kind === "assistant") {
      lines.push("## VeriLex", visibleAssistantText(item.segments.filter(s => s.type === "text").map(s => s.text).join(""), item.isStreaming));
    } else if (item.kind === "tool") {
      lines.push(`工具：${item.name} — ${statuses[item.status] ?? item.status}`);
    } else if (item.notice === "turn_failed" || item.notice === "turn_interrupted" || item.notice === "turn_cancelled") {
      lines.push(`本轮${item.notice === "turn_failed" ? "失败" : "已中断或取消"}。`);
    }
  }
  return lines.join("\n\n") + "\n";
}
