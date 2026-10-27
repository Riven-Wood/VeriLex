import { afterEach, expect, test } from "vitest";

import { toolStepLine } from "../src/timeline/toolPresentation";
import type { ToolItem } from "../src/timeline/items";

afterEach(() => {});

function item(overrides: Partial<ToolItem>): ToolItem {
  return {
    kind: "tool",
    id: "t",
    name: "read_file",
    status: "succeeded",
    riskLevel: "low",
    argumentsSummary: { path: "合同/审查报告.docx" },
    ...overrides,
  };
}

test("read step reads as a completed plain line", () => {
  const line = toolStepLine(item({}));
  expect(line).toContain("已读取");
  expect(line).toContain("审查报告.docx");
  expect(line).not.toContain("成功");
});

test("run_command step echoes a truncated command", () => {
  const line = toolStepLine(
    item({
      name: "run_command",
      riskLevel: "high",
      argumentsSummary: { command: "git status --short --branch && git log -3" },
    }),
  );
  expect(line).toContain("已运行");
  expect(line).toContain("git status");
  expect(line.length).toBeLessThanOrEqual(60);
});

test("document_create never leaks filenames into the step line", () => {
  const line = toolStepLine(
    item({
      name: "document_create",
      approvalBehavior: "additive_artifact",
      argumentsSummary: { output_name_sha256: "289eb145", intent_quote: "生成 Word" },
    }),
  );
  expect(line).toContain("Word");
  expect(line).not.toContain("289eb145");
});

test("list directory and skill reads read naturally", () => {
  expect(toolStepLine(item({ name: "list_directory", argumentsSummary: { path: "" } }))).toContain(
    "已列出",
  );
  expect(
    toolStepLine(item({ name: "skill_read", argumentsSummary: { name: "合同审查" } })),
  ).toContain("合同审查");
});

test("running steps read as in-progress", () => {
  const line = toolStepLine(item({ status: "running" }));
  expect(line).toContain("正在");
});
