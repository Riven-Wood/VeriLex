import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ToolCatalog, ToolCatalogView } from "../src/components/ToolCatalog";
import type { ToolCapabilityRecord } from "../src/runtime/contracts";
import {
  toolDisplayName,
  toolScopeLabels,
  toolSourceLabel,
} from "../src/settings/toolCopy";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.querySelector("style[data-tool-catalog-test]")?.remove();
});

async function loadToolCatalogStyles() {
  const stylesheet = document.createElement("style");
  stylesheet.dataset.toolCatalogTest = "true";
  stylesheet.textContent = await readFile(
    resolve(process.cwd(), "src/styles/views.css"),
    "utf8",
  );
  document.head.append(stylesheet);
}

function findStyleRule(selector: string): CSSStyleDeclaration | null {
  const rules = document.querySelector<HTMLStyleElement>("style[data-tool-catalog-test]")
    ?.sheet?.cssRules;
  if (!rules) return null;

  const visit = (nestedRules: CSSRuleList): CSSStyleDeclaration | null => {
    for (const rule of Array.from(nestedRules)) {
      const styleRule = rule as CSSStyleRule;
      if (styleRule.selectorText === selector) return styleRule.style;
      const group = rule as CSSGroupingRule;
      if (group.cssRules) {
        const match = visit(group.cssRules);
        if (match) return match;
      }
    }
    return null;
  };

  return visit(rules);
}

function tool(
  overrides: Partial<ToolCapabilityRecord>,
): ToolCapabilityRecord {
  return {
    name: "read_file",
    description: "受控地检查案卷目录中的 UTF-8 文本。",
    source: "builtin",
    source_name: null,
    category: "files",
    scopes: ["read"],
    risk_level: "low",
    default_approval: "automatic",
    approval_behavior: "risk_default",
    requires_workspace: true,
    availability: "available",
    availability_reason: null,
    ...overrides,
  };
}

const tools: ToolCapabilityRecord[] = [
  tool({}),
  tool({
    name: "write_file",
    description: "在授权工作区中创建文本文件。",
    scopes: ["write"],
    risk_level: "high",
    default_approval: "required",
  }),
  tool({
    name: "run_command",
    description: "在受控终端中运行命令。",
    category: "terminal",
    scopes: ["execute"],
    risk_level: "high",
    default_approval: "required",
  }),
  tool({
    name: "skill_read",
    description: "读取已安装 Skill 的说明。",
    source: "skill",
    source_name: "legal-research",
    category: "skills",
    scopes: ["read"],
    requires_workspace: false,
  }),
  tool({
    name: "mcp__remote__lookup",
    description: `${"M".repeat(200)}PRIVATE_SUFFIX`,
    source: "mcp",
    source_name: "law",
    category: "external",
    scopes: ["network", "external_service"],
    risk_level: "high",
    default_approval: "required",
    requires_workspace: false,
  }),
];

function readyState(records: ToolCapabilityRecord[] = tools) {
  return {
    status: "ready" as const,
    tools: records,
    error: "",
    retry: vi.fn(),
  };
}

test("uses exact labels for every built-in tool and preserves unknown names", () => {
  expect([
    "list_directory",
    "search_files",
    "read_file",
    "write_file",
    "apply_file_patch",
    "run_command",
    "skill_read",
    "document_create",
  ].map(toolDisplayName)).toEqual([
    "查看目录",
    "搜索文件",
    "读取文本文件",
    "创建文本文件",
    "修改文本文件",
    "运行终端命令",
    "读取技能说明",
    "生成 Word 文档",
  ]);
  expect(toolDisplayName("mcp__law__lookup")).toBe("mcp__law__lookup");
});

test("labels a tool's real source and scopes without widening them", () => {
  expect(toolSourceLabel({ source: "mcp", source_name: "law" })).toBe("MCP · law");
  expect(toolSourceLabel({ source: "skill", source_name: "legal-research" })).toBe(
    "Skill · legal-research",
  );
  expect(toolSourceLabel({ source: "builtin", source_name: null })).toBe("内置");
  expect(toolScopeLabels(["write", "execute"])).toEqual(["写入文件", "执行命令"]);
});

test("shows a truthful loading state", () => {
  render(
    <ToolCatalogView
      query=""
      state={{ status: "loading", tools: [], error: "", retry: vi.fn() }}
    />,
  );

  expect(screen.getByRole("status").textContent).toBe("正在读取工具目录…");
});

test("groups tools and shows stable names, sources, scopes, risk, workspace and availability", () => {
  render(<ToolCatalogView query="" state={readyState()} />);

  expect(screen.getByRole("heading", { name: "文件" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "终端" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Skill" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "MCP 与外部工具" })).toBeTruthy();

  const readFile = screen.getByRole("article", { name: "读取文本文件（read_file）" });
  expect(within(readFile).getByText("read_file")).toBeTruthy();
  expect(within(readFile).getByText("受控地检查案卷目录中的 UTF-8 文本。")).toBeTruthy();
  expect(within(readFile).getByText("内置")).toBeTruthy();
  expect(within(readFile).getByText("读取")).toBeTruthy();
  expect(within(readFile).getByText("需要工作区")).toBeTruthy();
  expect(within(readFile).getByText("默认可直接执行")).toBeTruthy();
  expect(within(readFile).getByText("可用")).toBeTruthy();

  const command = screen.getByRole("article", { name: "运行终端命令（run_command）" });
  expect(within(command).getByText("执行命令")).toBeTruthy();
  expect(within(command).getByText("默认需要确认")).toBeTruthy();

  const skill = screen.getByRole("article", { name: "读取技能说明（skill_read）" });
  expect(within(skill).getByText("Skill · legal-research")).toBeTruthy();
  expect(within(skill).queryByText("需要工作区")).toBeNull();

  const mcp = screen.getByRole("article", { name: "mcp__remote__lookup（mcp__remote__lookup）" });
  expect(within(mcp).getByText("MCP · law")).toBeTruthy();
  expect(within(mcp).getByText("网络访问")).toBeTruthy();
  expect(within(mcp).getByText("外部服务")).toBeTruthy();
  expect(mcp.textContent).not.toContain("PRIVATE_SUFFIX");

  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.queryByRole("switch")).toBeNull();
});

test("renders the documents group with additive-artifact approval copy", () => {
  render(<ToolCatalogView query="" state={readyState([
    tool({}),
    tool({
      name: "document_create",
      description: "在当前工作区新增一份固定版式 Word 文档。",
      category: "documents",
      scopes: ["write"],
      risk_level: "high",
      default_approval: "automatic",
      approval_behavior: "additive_artifact",
      requires_workspace: true,
    }),
  ])} />);

  expect(screen.getByRole("heading", { name: "文档" })).toBeTruthy();
  const documentCreate = screen.getByRole("article", {
    name: "生成 Word 文档（document_create）",
  });
  expect(within(documentCreate).getByText("写入文件")).toBeTruthy();
  expect(within(documentCreate).getByText("需要工作区")).toBeTruthy();
  expect(
    within(documentCreate).getByText("当前消息明确要求生成 Word 时直接执行"),
  ).toBeTruthy();
  expect(within(documentCreate).queryByText("默认需要确认")).toBeNull();
});

test("prioritizes task-first confirmation for web tools without changing existing risk copy", () => {
  render(<ToolCatalogView query="" state={readyState([
    tool({
      name: "web_search",
      description: "搜索公开网页。",
      category: "external",
      scopes: ["network", "external_service"],
      risk_level: "high",
      default_approval: "required",
      approval_behavior: "thread_first_use",
      requires_workspace: false,
    }),
    tool({
      name: "run_command",
      description: "在受控终端中运行命令。",
      category: "terminal",
      scopes: ["execute"],
      risk_level: "high",
      default_approval: "required",
    }),
  ])} />);

  const webSearch = screen.getByRole("article", {
    name: "web_search（web_search）",
  });
  expect(within(webSearch).getByText("每个任务首次联网确认")).toBeTruthy();
  expect(within(webSearch).getByText("网络访问")).toBeTruthy();
  expect(within(webSearch).getByText("外部服务")).toBeTruthy();
  expect(within(webSearch).queryByText("默认需要确认")).toBeNull();

  const command = screen.getByRole("article", {
    name: "运行终端命令（run_command）",
  });
  expect(within(command).getByText("默认需要确认")).toBeTruthy();
});

test("keeps unbroken descriptions and source names inside narrow tool cards", async () => {
  await loadToolCatalogStyles();
  const description = "D".repeat(220);
  const sourceName = "source".repeat(40);
  render(<ToolCatalogView query="" state={readyState([
    tool({ description, source: "mcp", source_name: sourceName }),
  ])} />);

  const card = screen.getByRole("article", { name: "读取文本文件（read_file）" });
  const descriptionNode = card.querySelector<HTMLElement>(":scope > p");
  const sourceBadge = within(card).getByText(`MCP · ${sourceName}`);
  expect(descriptionNode?.textContent).toBe(`${"D".repeat(179)}…`);
  expect(sourceBadge.parentElement?.classList.contains("tool-catalog-badges")).toBe(true);

  const descriptionStyle = findStyleRule(".tool-catalog-card > p");
  expect(descriptionStyle?.getPropertyValue("min-width")).toBe("0");
  expect(descriptionStyle?.getPropertyValue("max-width")).toBe("100%");
  expect(descriptionStyle?.getPropertyValue("overflow-wrap")).toBe("anywhere");

  const badgeStyle = findStyleRule(".tool-catalog-badges span");
  expect(badgeStyle?.getPropertyValue("min-width")).toBe("0");
  expect(badgeStyle?.getPropertyValue("max-width")).toBe("100%");
  expect(badgeStyle?.getPropertyValue("overflow-wrap")).toBe("anywhere");
});

test("filters by display name, description, source and localized scope", () => {
  const state = readyState();
  const view = render(<ToolCatalogView query="终端" state={state} />);
  expect(screen.getByRole("article", { name: /run_command/ })).toBeTruthy();
  expect(screen.queryByRole("article", { name: /read_file/ })).toBeNull();

  view.rerender(<ToolCatalogView query="案卷" state={state} />);
  expect(screen.getByRole("article", { name: /read_file/ })).toBeTruthy();
  expect(screen.queryByRole("article", { name: /run_command/ })).toBeNull();

  view.rerender(<ToolCatalogView query="law" state={state} />);
  expect(screen.getByRole("article", { name: /mcp__remote__lookup/ })).toBeTruthy();

  view.rerender(<ToolCatalogView query="写入文件" state={state} />);
  expect(screen.getByRole("article", { name: /write_file/ })).toBeTruthy();
  expect(screen.queryByRole("article", { name: /read_file/ })).toBeNull();

  view.rerender(<ToolCatalogView query="PRIVATE_SUFFIX" state={state} />);
  expect(screen.getByRole("status").textContent).toBe("没有匹配的工具");
});

test("filters by the approval wording users see on each tool", () => {
  const state = readyState([
    tool({
      name: "web_search",
      description: "搜索公开网页。",
      category: "external",
      scopes: ["network", "external_service"],
      risk_level: "high",
      default_approval: "required",
      approval_behavior: "thread_first_use",
      requires_workspace: false,
    }),
    tool({
      name: "run_command",
      description: "在受控终端中运行命令。",
      category: "terminal",
      scopes: ["execute"],
      risk_level: "high",
      default_approval: "required",
    }),
  ]);
  const view = render(
    <ToolCatalogView query="首次联网" state={state} />,
  );

  expect(screen.getByRole("article", { name: /web_search/ })).toBeTruthy();
  expect(screen.queryByRole("article", { name: /run_command/ })).toBeNull();

  view.rerender(
    <ToolCatalogView query="默认需要确认" state={state} />,
  );
  expect(screen.getByRole("article", { name: /run_command/ })).toBeTruthy();
  expect(screen.queryByRole("article", { name: /web_search/ })).toBeNull();
});

test("explains an empty tool filter", () => {
  render(<ToolCatalogView query="完全不存在" state={readyState()} />);

  expect(screen.getByRole("status").textContent).toBe("没有匹配的工具");
});

test("shows a safe load failure and retries through the supplied action", () => {
  const retry = vi.fn();
  render(
    <ToolCatalogView
      query=""
      state={{
        status: "error",
        tools: [],
        error: "private endpoint http://127.0.0.1:8766 used token sk-secret-value",
        retry,
      }}
    />,
  );

  const alert = screen.getByRole("alert");
  expect(alert.textContent).toContain("无法读取工具目录。");
  expect(alert.textContent).not.toContain("127.0.0.1");
  expect(alert.textContent).not.toContain("sk-secret-value");
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(retry).toHaveBeenCalledTimes(1);
});

test("keeps a production loader failure from exposing its address or token details", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(
    new Error("private endpoint http://127.0.0.1:8766 failed with token sk-secret-value"),
  );

  render(<ToolCatalog query="" />);

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("无法读取工具目录。");
  expect(alert.textContent).not.toContain("127.0.0.1");
  expect(alert.textContent).not.toContain("sk-secret-value");
});
