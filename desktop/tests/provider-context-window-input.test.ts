import { expect, test } from "vitest";

import {
  formatContextWindowTokens,
  parseContextWindowInput,
} from "../src/providers/contextWindowInput";

test("parses full integers and friendly K/M suffixes", () => {
  expect(parseContextWindowInput("128K")).toEqual({ tokens: 128_000 });
  expect(parseContextWindowInput(" 1m ")).toEqual({ tokens: 1_000_000 });
  expect(parseContextWindowInput("32768")).toEqual({ tokens: 32_768 });
});

test("rejects non-integers, decorated numbers, and values outside the runtime bound", () => {
  expect(parseContextWindowInput("0")).toEqual({
    error: "上下文上限必须在 1 到 1000 万 tokens 之间",
  });
  expect(parseContextWindowInput("128.5K")).toEqual({
    error: "请输入整数，例如 128K、1M 或 32768",
  });
  for (const value of ["-1", "+128K", "1e6", "128,000", "12 800", "10000001"] ) {
    expect(parseContextWindowInput(value)).toHaveProperty("error");
  }
});

test("formats exact millions and thousands without inventing rounded values", () => {
  expect(formatContextWindowTokens(1_000_000)).toBe("1M");
  expect(formatContextWindowTokens(128_000)).toBe("128K");
  expect(formatContextWindowTokens(32_768)).toBe("32,768");
});
