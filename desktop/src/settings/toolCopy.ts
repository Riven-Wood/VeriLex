import type {
  ToolCapabilityCategory,
  ToolCapabilityRecord,
  ToolCapabilityScope,
} from "../runtime/contracts";

const BUILTIN_TOOL_NAMES: Readonly<Record<string, string>> = {
  list_directory: "查看目录",
  search_files: "搜索文件",
  read_file: "读取文本文件",
  write_file: "创建文本文件",
  apply_file_patch: "修改文本文件",
  run_command: "运行终端命令",
  "skill_read": "读取技能说明",
  "document_create": "生成 Word 文档",
};

const SCOPE_LABELS: Readonly<Record<ToolCapabilityScope, string>> = {
  read: "读取",
  write: "写入文件",
  execute: "执行命令",
  network: "网络访问",
  external_service: "外部服务",
};

const CATEGORY_LABELS: Readonly<Record<ToolCapabilityCategory, string>> = {
  files: "文件",
  documents: "文档",
  terminal: "终端",
  skills: "Skill",
  external: "MCP 与外部工具",
  other: "其他",
};

export function toolDisplayName(name: string): string {
  return BUILTIN_TOOL_NAMES[name] ?? name;
}

export function toolSourceLabel(
  tool: Pick<ToolCapabilityRecord, "source" | "source_name">,
): string {
  if (tool.source === "builtin") return "内置";
  const source = tool.source === "skill" ? "Skill" : "MCP";
  return tool.source_name ? `${source} · ${tool.source_name}` : source;
}

export function toolScopeLabels(
  scopes: readonly ToolCapabilityScope[],
): string[] {
  return scopes.map((scope) => SCOPE_LABELS[scope]);
}

export function toolCategoryLabel(category: ToolCapabilityCategory): string {
  return CATEGORY_LABELS[category];
}

export function toolApprovalLabel(
  tool: Pick<ToolCapabilityRecord, "approval_behavior" | "risk_level">,
): string {
  if (tool.approval_behavior === "thread_first_use") {
    return "每个任务首次联网确认";
  }
  if (tool.approval_behavior === "additive_artifact") {
    return "当前消息明确要求生成 Word 时直接执行";
  }
  if (tool.approval_behavior === "always_human") {
    return "每次都需要人工确认";
  }
  return tool.risk_level === "high"
    ? "默认需要确认"
    : "默认可直接执行";
}
