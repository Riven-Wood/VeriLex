import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ContractSkillMenu } from "../src/components/ContractSkillMenu";

afterEach(cleanup);
const entries = [
  { name: "通用合同审查", description: "审查合同主体、商务条款和法律风险。" },
  { name: "contract-drafting", description: "起草合同并调整条款。\n按交易需求整理。" },
  { name: "无说明技能", description: "" },
];
function mount(disabled = false) {
  const onChange = vi.fn();
  render(<ContractSkillMenu entries={entries} value={entries[0].name} disabled={disabled} onChange={onChange} />);
  const trigger = screen.getByRole("combobox", { name: "审查技能" });
  return { trigger, onChange };
}

test("hover previews the pointed skill without changing the selection; click commits", () => {
  const { trigger, onChange } = mount();
  fireEvent.click(trigger);
  expect(document.activeElement).toBe(trigger);
  const option = screen.getByRole("option", { name: "contract-drafting" });
  fireEvent.mouseEnter(option);
  expect(screen.getByRole("tooltip").textContent).toContain(entries[1].description);
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByRole("option", { name: entries[0].name }).getAttribute("aria-selected")).toBe("true");
  fireEvent.mouseLeave(option);
  expect(screen.getByRole("tooltip").textContent).toContain(entries[1].description);
  fireEvent.click(option);
  expect(onChange).toHaveBeenCalledWith("contract-drafting");
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

test("keyboard browsing previews descriptions and Escape leaves selection untouched", () => {
  const { trigger, onChange } = mount();
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  expect(within(screen.getByRole("tooltip")).getByText("contract-drafting")).toBeTruthy();
  fireEvent.keyDown(trigger, { key: "Escape" });
  expect(screen.queryByRole("tooltip")).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  fireEvent.keyDown(trigger, { key: "End" });
  expect(screen.getByRole("tooltip").textContent).toContain("此技能暂无介绍。");
  fireEvent.keyDown(trigger, { key: "Home" });
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  expect(onChange).toHaveBeenCalledWith("contract-drafting");
});

test("outside click and Tab dismiss the menu without selecting", () => {
  const { trigger, onChange } = mount();
  fireEvent.click(trigger);
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("tooltip")).toBeNull();
  fireEvent.click(trigger);
  fireEvent.keyDown(trigger, { key: "Tab" });
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
});

test("saved review skill remains disabled", () => {
  const { trigger, onChange } = mount(true);
  expect((trigger as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(trigger);
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
});
