import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AppToolbar } from "../src/components/AppToolbar";

afterEach(cleanup);

function renderToolbar(overrides = {}) {
  const props = {
    sidebarCollapsed: false,
    onToggleSidebar: vi.fn(),
    canGoBack: false,
    onBack: vi.fn(),
    canGoForward: false,
    onForward: vi.fn(),
    rightRail: { open: false, onToggle: vi.fn() },
    ...overrides,
  };
  render(<AppToolbar {...props} />);
  return props;
}

test("toolbar toggles sidebar, back/forward, and the right rail", () => {
  const props = renderToolbar({
    canGoBack: true,
    canGoForward: true,
  });

  fireEvent.click(screen.getByRole("button", { name: "收起侧栏" }));
  expect(props.onToggleSidebar).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: "后退" }));
  expect(props.onBack).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: "前进" }));
  expect(props.onForward).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: "打开右侧栏" }));
  expect(props.rightRail?.onToggle).toHaveBeenCalledTimes(1);
});

test("back and forward are disabled at the ends of history", () => {
  renderToolbar();

  expect(screen.getByRole("button", { name: "后退" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "前进" }).hasAttribute("disabled")).toBe(true);

  cleanup();
  renderToolbar({ canGoBack: true });

  expect(screen.getByRole("button", { name: "后退" }).hasAttribute("disabled")).toBe(false);
  expect(screen.getByRole("button", { name: "前进" }).hasAttribute("disabled")).toBe(true);
});


test("left controls show back, forward, then sidebar", () => {
  renderToolbar();
  const labels=screen.getAllByRole('button').map(button=>button.getAttribute('aria-label'));
  expect(labels.slice(0,3)).toEqual(['后退','前进','收起侧栏']);
});
