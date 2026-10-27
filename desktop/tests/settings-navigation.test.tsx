import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { useState } from "react";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { SettingsNavigation } from "../src/components/SettingsNavigation";
import type { SettingsCategoryId } from "../src/settings/categories";

afterEach(() => {
  cleanup();
  document.querySelector("style[data-settings-navigation-test]")?.remove();
});

async function loadSettingsStyles() {
  const stylesheet = document.createElement("style");
  stylesheet.dataset.settingsNavigationTest = "true";
  stylesheet.textContent = await readFile(
    resolve(process.cwd(), "src/styles/views.css"),
    "utf8",
  );
  document.head.append(stylesheet);
}

function findStyleRule(
  selector: string,
  media = "",
): CSSStyleDeclaration | null {
  const sheet = document.querySelector<HTMLStyleElement>(
    "style[data-settings-navigation-test]",
  )?.sheet;
  if (!sheet) return null;

  const visit = (rules: CSSRuleList, activeMedia: string): CSSStyleDeclaration | null => {
    for (const rule of Array.from(rules)) {
      const styleRule = rule as CSSStyleRule;
      if (styleRule.selectorText === selector && activeMedia === media) {
        return styleRule.style;
      }
      const group = rule as CSSMediaRule;
      if (group.cssRules) {
        const nestedMedia = group.conditionText ?? group.media?.mediaText ?? activeMedia;
        const match = visit(group.cssRules, nestedMedia);
        if (match) return match;
      }
    }
    return null;
  };

  return visit(sheet.cssRules, "");
}

function NavigationHarness() {
  const [selected, setSelected] = useState<SettingsCategoryId>("models");
  const [query, setQuery] = useState("");

  return (
    <SettingsNavigation
      selected={selected}
      query={query}
      onQueryChange={setQuery}
      onSelect={setSelected}
    />
  );
}

test("identifies the category rail and updates the current page on click", () => {
  render(<NavigationHarness />);

  expect(screen.getByRole("navigation", { name: "设置分类" })).toBeTruthy();
  expect(screen.getByRole("button", { name: /模型/ }).getAttribute("aria-current")).toBe("page");

  fireEvent.click(screen.getByRole("button", { name: /通用/ }));

  expect(screen.getByRole("button", { name: /通用/ }).getAttribute("aria-current")).toBe("page");
  expect(screen.getByRole("button", { name: /模型/ }).hasAttribute("aria-current")).toBe(false);
});

test("filters categories, explains an empty result, and clears search with Escape", () => {
  render(<NavigationHarness />);
  const search = screen.getByRole("searchbox", { name: "搜索设置" });

  fireEvent.change(search, { target: { value: "MCP" } });
  expect(screen.getByRole("button", { name: /技能与外部工具/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /通用/ })).toBeNull();

  fireEvent.change(search, { target: { value: "完全不存在" } });
  const emptyStatus = screen.getByRole("status");
  expect(emptyStatus.textContent).toBe("没有匹配的设置");
  expect(emptyStatus.getAttribute("aria-live")).toBe("polite");

  fireEvent.keyDown(search, { key: "Escape" });
  expect((screen.getByRole("searchbox", { name: "搜索设置" }) as HTMLInputElement).value).toBe("");
  expect(screen.getAllByRole("button")).toHaveLength(7);
});

test("moves focus with arrow keys without selecting until activation", () => {
  render(<NavigationHarness />);
  const models = screen.getByRole("button", { name: /模型/ });
  const agent = screen.getByRole("button", { name: /^助手与权限$/ });

  models.focus();
  fireEvent.keyDown(models, { key: "ArrowDown" });
  expect(document.activeElement).toBe(agent);
  expect(models.getAttribute("aria-current")).toBe("page");
  expect(agent.hasAttribute("aria-current")).toBe(false);

  fireEvent.keyDown(agent, { key: "Enter" });
  expect(agent.getAttribute("aria-current")).toBe("page");

  fireEvent.keyDown(agent, { key: "ArrowLeft" });
  expect(document.activeElement).toBe(models);
  expect(agent.getAttribute("aria-current")).toBe("page");

  fireEvent.keyDown(models, { key: " " });
  expect(models.getAttribute("aria-current")).toBe("page");
});

test("defines narrow horizontal navigation, tool wrapping and visible keyboard focus", async () => {
  await loadSettingsStyles();

  const narrowShell = findStyleRule(".settings-shell", "(max-width: 980px)");
  const narrowNavigation = findStyleRule(".settings-navigation", "(max-width: 980px)");
  const narrowCategories = findStyleRule(".settings-category-list", "(max-width: 980px)");
  const narrowSearch = findStyleRule(".settings-search", "(max-width: 980px)");
  expect(narrowShell?.getPropertyValue("grid-template-columns")).toBe("minmax(0, 1fr)");
  expect(narrowNavigation?.position).toBe("static");
  expect(narrowCategories?.display).toBe("flex");
  expect(narrowCategories?.getPropertyValue("overflow-x")).toBe("auto");
  expect(narrowSearch?.width).toBe("100%");

  expect(findStyleRule(".tool-catalog-title", "(max-width: 680px)")?.getPropertyValue("flex-wrap")).toBe("wrap");
  expect(findStyleRule(".tool-catalog-badges", "(max-width: 680px)")?.getPropertyValue("flex-wrap")).toBe("wrap");
  expect(findStyleRule(".tool-catalog-status", "(max-width: 680px)")?.getPropertyValue("flex-wrap")).toBe("wrap");

  expect(findStyleRule(".settings-search input:focus-visible")?.getPropertyValue("outline")).toContain("solid");
  expect(findStyleRule(".settings-category-list button:focus-visible")?.getPropertyValue("outline")).toContain("solid");
});

test("keeps the MCP editor usable at narrow 200 percent layouts and reduced motion", async () => {
  await loadSettingsStyles();

  const narrowEditor = findStyleRule(".mcp-editor-grid", "(max-width: 680px)");
  const narrowActions = findStyleRule(".mcp-editor-actions", "(max-width: 680px)");
  const reducedSpinner = findStyleRule(
    ".mcp-editor .attachment-spinner",
    "(prefers-reduced-motion: reduce)",
  );

  expect(narrowEditor?.getPropertyValue("grid-template-columns")).toBe("minmax(0, 1fr)");
  expect(narrowActions?.getPropertyValue("flex-wrap")).toBe("wrap");
  expect(reducedSpinner?.animation).toBe("none");
});
