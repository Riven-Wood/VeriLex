Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, writable: true, value() { this.setAttribute('open', ''); } });
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { SkillsManager } from "../src/components/SkillsManager";
import * as runtimeApi from "../src/runtime/api";
import { RuntimeApiError } from "../src/runtime/api";
import type { SkillSummary } from "../src/runtime/contracts";
import { skillCategory, managedSkillCategory } from "../src/components/skillCategories";

beforeEach(() => { vi.spyOn(runtimeApi, "listSkillCategories").mockResolvedValue([]); });

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("category and name filters intersect without changing global counts or management targets", async () => {
  const records = [
    skill({ name: "合同条款", category: "contract-review" }),
    skill({ name: "合同履约", category: "contract-review", enabled: false }),
    skill({ name: "法规查询", category: "legal-retrieval" }),
    skill({ name: "旧技能", description: "提供协助", category: "unknown-category" }),
  ];
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue(records);
  const toggle = vi.spyOn(runtimeApi, "setSkillEnabled").mockResolvedValue(undefined);
  const { rerender } = render(<SkillsManager />);
  fireEvent.click(await screen.findByRole("button", { name: "合同审查与起草（2）" }));
  expect(screen.queryByText("法规查询")).toBeNull();
  expect(screen.getAllByRole("switch")).toHaveLength(2);
  expect(screen.getByText("本机 4 个技能，3 个启用")).toBeTruthy();
  rerender(<SkillsManager query="履约" />);
  expect(screen.queryByText("合同条款")).toBeNull();
  fireEvent.click(screen.getByRole("switch", { name: "启用技能 合同履约" }));
  await waitFor(() => expect(toggle).toHaveBeenCalledWith("合同履约", true));
  rerender(<SkillsManager query="法规" />);
  expect(screen.getByText("没有找到匹配的名称")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "全部（4）" }));
  expect(screen.getByText("法规查询")).toBeTruthy();
  rerender(<SkillsManager />);
  fireEvent.click(screen.getByRole("button", { name: "未分类（1）" }));
  expect(screen.getByText("旧技能")).toBeTruthy();
});

test("infers categories from names and descriptions while respecting explicit classifications", async () => {
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue([
    skill({name: "审查买卖合同", category: undefined}),
    skill({name: "deadline-planner", description: "提醒期限", category: undefined}),
    skill({name: "资料处理", description: "处理个人信息和隐私", category: undefined}),
    skill({name: "合同", category: "employment"}),
    skill({name: "通用助手", description: "帮助用户", category: undefined}),
  ]);
  render(<SkillsManager />);
  expect(await screen.findByRole("button", {name: "合同审查与起草（1）"})).toBeTruthy();
  expect(screen.getByRole("button", {name: "案件管理与期限（1）"})).toBeTruthy();
  expect(screen.getByRole("button", {name: "合规监管与隐私（1）"})).toBeTruthy();
  expect(screen.getByRole("button", {name: "劳动用工（1）"})).toBeTruthy();
  expect(screen.getByRole("button", {name: "未分类（1）"})).toBeTruthy();
});

test("manual organization does not hide a skill from its existing legal use", () => {
  const item = skill({category: "legal-retrieval", categoryOverride: "custom:abc"});
  expect(managedSkillCategory(item)).toBe("custom:abc");
  expect(skillCategory(item)).toBe("legal-retrieval");
});

test("renames and deletes custom categories after confirmation", async () => {
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue([skill()]);
  let categories = [{id: "custom:abc", name: "合同"}];
  vi.mocked(runtimeApi.listSkillCategories).mockImplementation(async () => categories);
  const rename = vi.spyOn(runtimeApi, "renameSkillCategory").mockImplementation(async (_id, name) => { categories = [{id:"custom:abc", name}]; });
  const remove = vi.spyOn(runtimeApi, "deleteSkillCategory").mockImplementation(async () => { categories = []; });
  render(<SkillsManager />);
  fireEvent.click(await screen.findByRole("button", {name:"管理分类"}));
  fireEvent.click(screen.getByRole("button", {name:"重命名分类 合同"}));
  fireEvent.change(screen.getByLabelText("修改分类 合同 的名称"), {target:{value:"重点合同"}});
  fireEvent.click(screen.getByRole("button", {name:"保存"}));
  await waitFor(() => expect(rename).toHaveBeenCalledWith("custom:abc", "重点合同"));
  fireEvent.click(await screen.findByRole("button", {name:"删除分类 重点合同"}));
  expect(remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", {name:"确认删除分类 重点合同"}));
  await waitFor(() => expect(remove).toHaveBeenCalledWith("custom:abc"));
  await screen.findByText("还没有自定义分类");
});

test("creates a category and assigns a skill, then allows returning to automatic classification", async () => {
  const records = [skill({name: "合同审查", category: "contract-review"})];
  vi.spyOn(runtimeApi, "listSkills").mockImplementation(async () => records);
  let categories: {id:string; name:string}[] = [];
  vi.mocked(runtimeApi.listSkillCategories).mockImplementation(async () => categories);
  const create = vi.spyOn(runtimeApi, "createSkillCategory").mockImplementation(async name => {
    categories = [{id: "custom:abc", name}]; return categories[0];
  });
  const assign = vi.spyOn(runtimeApi, "setSkillCategory").mockImplementation(async (_name, category) => {
    records[0] = {...records[0], categoryOverride: category};
  });
  render(<SkillsManager />);
  fireEvent.click(await screen.findByRole("button", {name: "管理分类"}));
  fireEvent.change(screen.getByLabelText("新分类名称"), {target: {value: "我的办案"}});
  fireEvent.click(screen.getByRole("button", {name: "新建分类"}));
  await waitFor(() => expect(create).toHaveBeenCalledWith("我的办案"));
  await screen.findByText("0 个技能");
  fireEvent.click(screen.getByRole("button", {name: "关闭"}));
  const select = screen.getByRole("combobox", {name: "选择技能 合同审查 的分类"});
  fireEvent.change(select, {target: {value: "custom:abc"}});
  await waitFor(() => expect(assign).toHaveBeenCalledWith("合同审查", "custom:abc"));
  await screen.findByRole("button", {name: "我的办案（1）"});
  fireEvent.change(select, {target: {value: "auto"}});
  await waitFor(() => expect(assign).toHaveBeenCalledWith("合同审查", null));
  await screen.findByRole("button", {name: "合同审查与起草（1）"});
});

test("deleting the final skill in a category preserves the filter and allows returning to all", async () => {
  const other = skill({ name: "法规查询", category: "legal-retrieval" });
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValueOnce([skill({ category: "contract-review" }), other]).mockResolvedValue([other]);
  vi.spyOn(runtimeApi, "deleteSkill").mockResolvedValue({ trashedPath: "trash" });
  render(<SkillsManager />);
  fireEvent.click(await screen.findByRole("button", { name: "合同审查与起草（1）" }));
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await screen.findByText("此分类暂无技能");
  expect(screen.getByRole("button", { name: "合同审查与起草（0）" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "全部（1）" }));
  expect(screen.getByText("法规查询")).toBeTruthy();
});

function skill(overrides: Partial<SkillSummary> = {}): SkillSummary {
  return {
    name: "contract-review",
    description: "审查合同",
    instructions: "---\nname: contract-review\n---\n\n# 审查流程\n1. 通读合同原文",
    enabled: true,
    valid: true,
    error: null,
    path: "/data/skills/contract-review/SKILL.md",
    ...overrides,
  };
}

function failure(message: string, code: string) {
  return new RuntimeApiError(message, { code, retryable: false, status: 500 });
}

test("renders skill rows with counts and switch roles", async () => {
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue([
    skill(),
    skill({ name: "legal-research", description: "法律检索", enabled: false }),
  ]);

  render(<SkillsManager />);

  await screen.findByText("contract-review");
  expect(screen.getByText("本机 2 个技能，1 个启用")).toBeTruthy();
  expect(screen.getByText("想新建技能？在对话里说『帮我创建一个技能：…』")).toBeTruthy();
  const switches = screen.getAllByRole("switch");
  expect(switches).toHaveLength(2);
  const on = screen.getByRole("switch", { name: "启用技能 contract-review" });
  const off = screen.getByRole("switch", { name: "启用技能 legal-research" });
  expect(on.getAttribute("aria-checked")).toBe("true");
  expect(off.getAttribute("aria-checked")).toBe("false");
});

test("disabled skill name is visually muted", async () => {
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue([
    skill({ name: "legal-research", description: "法律检索", enabled: false }),
  ]);

  render(<SkillsManager />);

  const name = await screen.findByText("legal-research");
  expect(name.className).toContain("skills-manager-muted");
});

test.each(["close", "escape"])("view restores an unfocused mouse trigger after %s", async (method) => {
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue([skill()]);

  render(<SkillsManager />);
  await screen.findByText("contract-review");
  expect(screen.queryByRole("heading", {name: "审查流程"})).toBeNull();

  const trigger = screen.getByRole("button", { name: "查看" });
  expect(document.activeElement).not.toBe(trigger);
  fireEvent.click(trigger);
  expect(screen.getByRole("dialog", {name:"contract-review"})).toBeTruthy();
  expect(screen.getByRole("heading", {name: "审查流程"})).toBeTruthy();

  if (method === "escape") fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
  else fireEvent.click(screen.getByRole("button", { name: "关闭" }));
  expect(screen.queryByRole("heading", {name: "审查流程"})).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

test("switch calls setSkillEnabled and refreshes", async () => {
  const list = vi
    .spyOn(runtimeApi, "listSkills")
    .mockResolvedValue([skill()]);
  const toggle = vi
    .spyOn(runtimeApi, "setSkillEnabled")
    .mockResolvedValue(undefined);

  render(<SkillsManager />);
  fireEvent.click(await screen.findByRole("switch", { name: "启用技能 contract-review" }));

  await waitFor(() => expect(toggle).toHaveBeenCalledWith("contract-review", false));
  await waitFor(() => expect(list.mock.calls.length).toBeGreaterThanOrEqual(2));
});

test("toggle failure surfaces an alert", async () => {
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue([skill()]);
  vi.spyOn(runtimeApi, "setSkillEnabled").mockRejectedValue(
    failure("写入失败，请重试", "skill_write_failed"),
  );

  render(<SkillsManager />);
  fireEvent.click(await screen.findByRole("switch", { name: "启用技能 contract-review" }));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("写入失败，请重试");
});

test("delete requires a second confirming click and disarms on blur", async () => {
  const list = vi
    .spyOn(runtimeApi, "listSkills")
    .mockResolvedValueOnce([skill()])
    .mockResolvedValue([]);
  const remove = vi
    .spyOn(runtimeApi, "deleteSkill")
    .mockResolvedValue({ trashedPath: "contract-review-20260830T000000Z" });

  render(<SkillsManager />);
  const deleteButton = await screen.findByRole("button", { name: "删除" });

  fireEvent.click(deleteButton);
  const confirm = screen.getByRole("button", { name: "确认删除" });
  expect(confirm.className).toContain("skills-manager-danger");
  expect(remove).not.toHaveBeenCalled();

  fireEvent.blur(confirm);
  expect(screen.getByRole("button", { name: "删除" })).toBeTruthy();
  expect(remove).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

  await waitFor(() =>
    expect(remove).toHaveBeenCalledWith("contract-review"),
  );
  await waitFor(() =>
    expect(screen.queryByText("contract-review")).toBeNull(),
  );
  expect(list.mock.calls.length).toBeGreaterThanOrEqual(2);
});

test("delete failure keeps the row and surfaces an alert", async () => {
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue([skill()]);
  vi.spyOn(runtimeApi, "deleteSkill").mockRejectedValue(
    failure("写入失败，请重试", "skill_write_failed"),
  );

  render(<SkillsManager />);
  fireEvent.click(await screen.findByRole("button", { name: "删除" }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("写入失败");
  expect(screen.getByText("contract-review")).toBeTruthy();
});

test("invalid skill row shows the parse reason and delete only", async () => {
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue([
    skill({
      name: "broken",
      description: "",
      instructions: "",
      valid: false,
      error: "SKILL.md 缺少名称或描述",
    }),
  ]);

  render(<SkillsManager />);

  expect(await screen.findByText("broken")).toBeTruthy();
  expect(screen.getByText("SKILL.md 缺少名称或描述")).toBeTruthy();
  expect(screen.getByText("无法解析")).toBeTruthy();
  expect(screen.queryAllByRole("switch")).toHaveLength(0);
  expect(screen.getByRole("button", { name: "删除" })).toBeTruthy();
});

test("empty state guides creation through conversation", async () => {
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue([]);

  render(<SkillsManager />);

  await screen.findByText("本机 0 个技能，0 个启用");
  expect(screen.getByText("想新建技能？在对话里说『帮我创建一个技能：…』")).toBeTruthy();
});

test("reveal button calls revealSkill", async () => {
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue([skill()]);
  const reveal = vi
    .spyOn(runtimeApi, "revealSkill")
    .mockResolvedValue({ path: "contract-review", mode: "reveal" });

  render(<SkillsManager />);
  fireEvent.click(await screen.findByRole("button", { name: "在访达中显示" }));

  await waitFor(() =>
    expect(reveal).toHaveBeenCalledWith("contract-review"),
  );
});

test("reveal failure surfaces an alert", async () => {
  vi.spyOn(runtimeApi, "listSkills").mockResolvedValue([skill()]);
  vi.spyOn(runtimeApi, "revealSkill").mockRejectedValue(
    failure("打开失败", "open_failed"),
  );

  render(<SkillsManager />);
  fireEvent.click(await screen.findByRole("button", { name: "在访达中显示" }));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("打开失败");
});

test("load failure shows an alert instead of an empty list", async () => {
  vi.spyOn(runtimeApi, "listSkills").mockRejectedValue(
    failure("VeriLex Runtime 请求失败（500）", "runtime_request_failed"),
  );

  render(<SkillsManager />);

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("请求失败");
});
