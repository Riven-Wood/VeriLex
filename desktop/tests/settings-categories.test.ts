import { expect, test } from "vitest";

import {
  SETTINGS_CATEGORIES,
  filterSettingsCategories,
} from "../src/settings/categories";

test("keeps the setting categories in their product order", () => {
  expect(SETTINGS_CATEGORIES.map((item) => item.id)).toEqual([
    "general", "appearance", "models", "tools", "workspaces", "help", "diagnostics",
  ]);
});

test.each([
  ["终端", ["tools"]],
  ["provider", ["models"]],
  ["诊断", ["diagnostics"]],
  ["  MCP  ", ["tools"]],
] as const)("finds categories with user-facing alias %s", (query, expected) => {
  expect(filterSettingsCategories(query).map((item) => item.id)).toEqual(expected);
});

test("returns every category for an empty normalized query", () => {
  expect(filterSettingsCategories("  ").map((item) => item.id)).toEqual([
    "general", "appearance", "models", "tools", "workspaces", "help", "diagnostics",
  ]);
});
