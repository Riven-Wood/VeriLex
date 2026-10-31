import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { CaseSkillSuggestions } from "../src/components/CaseSkillSuggestions";
const skills = vi.fn();
vi.mock("../src/runtime/api", () => ({ listSkills: (...args: unknown[]) => skills(...args) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
test("valid case skill can be chosen locally even globally disabled", async () => {
  skills.mockResolvedValue([{ name: "类案检索", category: "case-retrieval", description: "整理相似案例", valid: true, enabled: false }, { name: "会计", description: "制作账目", valid: true }, { name: "损坏", valid: false }]);
  const change = vi.fn(); render(<CaseSkillSuggestions onChange={change} />);
  fireEvent.click(await screen.findByRole("combobox", { name: "检索技能" }));
  fireEvent.click(await screen.findByRole("option", { name: /类案检索/ })); expect(change).toHaveBeenCalledWith(["类案检索"]);
  expect(screen.queryByText("会计")).toBeNull();
  expect(screen.queryByText("损坏")).toBeNull();
});
test("no skills retains the picker and an empty state", async () => {
  skills.mockResolvedValue([]); render(<CaseSkillSuggestions />);
  fireEvent.click(screen.getByRole("combobox", { name: "检索技能" }));
  expect(await screen.findByText("暂无可用的检索技能")).toBeTruthy();
});
