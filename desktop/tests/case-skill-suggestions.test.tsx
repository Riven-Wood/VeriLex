import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { CaseSkillSuggestions } from "../src/components/CaseSkillSuggestions";
const skills = vi.fn();
vi.mock("../src/runtime/api", () => ({ listSkills: (...args: unknown[]) => skills(...args) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
test("valid case skill can be chosen locally even globally disabled", async () => {
  skills.mockResolvedValue([{ name: "类案检索", description: "整理相似案例", valid: true, enabled: false }, { name: "会计", description: "制作账目", valid: true }, { name: "损坏", valid: false }]);
  const change = vi.fn(); render(<CaseSkillSuggestions onChange={change} />);
  fireEvent.click(await screen.findByRole("combobox", { name: "检索技能" }));
  fireEvent.click(await screen.findByRole("option", { name: /类案检索/ })); expect(change).toHaveBeenCalledWith(["类案检索"]);
  expect(screen.getByText("会计")).toBeTruthy();
  expect(screen.queryByText("损坏")).toBeNull();
});
test("no skills leaves no empty tutorial", async () => {
  skills.mockResolvedValue([]); const { container } = render(<CaseSkillSuggestions />);
  await vi.waitFor(() => expect(skills).toHaveBeenCalledOnce()); expect(container.innerHTML).toBe("");
});
