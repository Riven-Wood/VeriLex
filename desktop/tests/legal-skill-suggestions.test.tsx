import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { LegalSkillSuggestions } from "../src/components/LegalSkillSuggestions";
import type { SkillSummary } from "../src/runtime/contracts";

function skill(overrides: Partial<SkillSummary> = {}): SkillSummary {
  return {
    category: "legal-retrieval",
    name: "法律检索方法",
    description: "按争议焦点拆解检索关键词",
    instructions: "...",
    enabled: true,
    valid: true,
    error: null,
    path: "/skills/law",
    ...overrides,
  };
}

const LIST_SKILLS = vi.fn();
const SET_ENABLED = vi.fn().mockResolvedValue(undefined);

vi.mock("../src/runtime/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/runtime/api")>();
  return {
    ...original,
    listSkills: (...args: unknown[]) => LIST_SKILLS(...args),
    setSkillEnabled: (...args: unknown[]) => SET_ENABLED(...args),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("选择全局停用技能只改变本次选择", async () => {
  LIST_SKILLS.mockResolvedValue([skill({ enabled: false })]);
  const onChange = vi.fn();
  render(<LegalSkillSuggestions value={[]} onChange={onChange} />);
  fireEvent.click(screen.getByRole("combobox", { name: "检索技能" }));
  const checkbox = await screen.findByRole("option");
  fireEvent.click(checkbox);
  expect(onChange).toHaveBeenCalledWith(["法律检索方法"]);
  expect(SET_ENABLED).not.toHaveBeenCalled();
});

test("没有技能时保留选择入口和真实空态", async () => {
  LIST_SKILLS.mockResolvedValue([]);
  render(<LegalSkillSuggestions />);
  fireEvent.click(screen.getByRole("combobox", { name: "检索技能" }));
  expect(await screen.findByText("暂无可用的检索技能")).toBeTruthy();
});

test("按分类选择技能而非依赖名称", async () => {
  LIST_SKILLS.mockResolvedValue([skill({ name: "文件整理", description: "整理文件" }), skill({ name: "损坏", valid: false }), skill({ name: "其他", category: "case-retrieval" })]);
  const onChange = vi.fn(); render(<LegalSkillSuggestions value={[]} onChange={onChange} />);
  fireEvent.click(screen.getByRole("combobox", { name: "检索技能" }));
  fireEvent.click(await screen.findByRole("option", { name: /文件整理/ }));
  expect(onChange).toHaveBeenCalledWith(["文件整理"]);
  expect(screen.queryByText("损坏")).toBeNull();
  expect(screen.queryByText("其他")).toBeNull();
});
