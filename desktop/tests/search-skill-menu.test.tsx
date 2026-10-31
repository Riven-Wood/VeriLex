import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SkillMenu } from "../src/components/SkillMenu";

afterEach(cleanup);
const entries = Array.from({ length: 11 }, (_, i) => ({ name: `检索技能${i}`, description: `技能${i}的完整介绍` }));
function Picker({ initial = [] }: { initial?: string[] }) {
  const [value, setValue] = useState(initial);
  return <SkillMenu label="检索技能" entries={entries} value={value} multiple onChange={setValue} />;
}

test("preview stays separate from multiple selection and toggling keeps the menu open", () => {
  render(<Picker />);
  const trigger = screen.getByRole("combobox");
  fireEvent.click(trigger);
  const first = screen.getByRole("option", { name: entries[0].name });
  const second = screen.getByRole("option", { name: entries[1].name });
  fireEvent.mouseEnter(second);
  expect(screen.getByRole("tooltip").textContent).toContain(entries[1].description);
  expect(second.getAttribute("aria-selected")).toBe("false");
  fireEvent.click(first);
  fireEvent.click(second);
  expect(trigger.textContent).toBe("检索技能0、检索技能1");
  expect(trigger.getAttribute("title")).toBe("检索技能0、检索技能1");
  expect(first.getAttribute("aria-selected")).toBe("true");
  expect(second.getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("listbox").getAttribute("aria-multiselectable")).toBe("true");
  fireEvent.keyDown(trigger, { key: "Home" });
  fireEvent.keyDown(trigger, { key: " " });
  expect(first.getAttribute("aria-selected")).toBe("false");
  expect(trigger.textContent).toBe("检索技能1");
  fireEvent.keyDown(trigger, { key: "Escape" });
  expect(screen.queryByRole("listbox")).toBeNull();
});

test("ten-skill limit blocks addition while allowing preview and removal", () => {
  render(<Picker initial={entries.slice(0, 10).map(entry => entry.name)} />);
  const trigger = screen.getByRole("combobox");
  fireEvent.click(trigger);
  const extra = screen.getByRole("option", { name: entries[10].name });
  expect(extra.getAttribute("aria-disabled")).toBe("true");
  fireEvent.mouseEnter(extra);
  expect(screen.getByRole("tooltip").textContent).toContain(entries[10].description);
  fireEvent.click(extra);
  fireEvent.keyDown(trigger, { key: "End" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  expect(trigger.textContent).toBe(entries.slice(0, 10).map(entry => entry.name).join("、"));
  expect(extra.getAttribute("aria-selected")).toBe("false");
  fireEvent.click(screen.getByRole("option", { name: entries[0].name }));
  expect(extra.hasAttribute("aria-disabled")).toBe(false);
  fireEvent.click(extra);
  expect(extra.getAttribute("aria-selected")).toBe("true");
});


test('input-area skill menu opens above its trigger and closes when locked', () => {
  const props = {label: '检索技能', entries, value: [], onChange: () => undefined, multiple: true};
  const {rerender} = render(<SkillMenu {...props} placement="above" />);
  const trigger = screen.getByRole('combobox');
  vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({top: 600, bottom: 634, left: 200, right: 350, width: 150, height: 34, x: 200, y: 600, toJSON() {}});
  fireEvent.click(trigger);
  const panel = screen.getByRole('listbox').parentElement!;
  expect(panel.style.top).toBe('594px');
  expect(panel.style.transform).toBe('translateY(-100%)');
  expect(panel.style.height).toBe('440px');
  fireEvent.mouseEnter(screen.getByRole('option', { name: entries[1].name }));
  expect(panel.style.height).toBe('440px');
  rerender(<SkillMenu {...props} placement="above" disabled />);
  expect(screen.queryByRole('listbox')).toBeNull();
});
