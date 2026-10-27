import { expect, test } from "vitest";

import {
  anchoredPdfScroll,
  clampPdfZoom,
  nextFixedPdfZoom,
  zoomFromWheel,
} from "../src/components/preview/pdfZoom";

test("clamps PDF zoom to the 50 through 300 percent range", () => {
  expect(clampPdfZoom(20)).toBe(50);
  expect(clampPdfZoom(137)).toBe(137);
  expect(clampPdfZoom(420)).toBe(300);
});

test("moves continuous values to the adjacent toolbar stop", () => {
  expect(nextFixedPdfZoom(137, "out")).toBe(125);
  expect(nextFixedPdfZoom(137, "in")).toBe(150);
  expect(nextFixedPdfZoom(75, "out")).toBe(50);
  expect(nextFixedPdfZoom(200, "in")).toBe(300);
  expect(nextFixedPdfZoom(50, "out")).toBe(50);
  expect(nextFixedPdfZoom(300, "in")).toBe(300);
});

test("maps wheel deltas smoothly without changing more than 15 percent", () => {
  expect(zoomFromWheel(100, -500)).toBe(115);
  expect(zoomFromWheel(100, 500)).toBeCloseTo(100 / 1.15);
  expect(zoomFromWheel(137, Number.NaN)).toBe(137);
});

test("restores a page-relative anchor after layout changes", () => {
  expect(
    anchoredPdfScroll(
      { clientX: 200, clientY: 250, xRatio: 0.25, yRatio: 0.5 },
      { left: 100, top: 100, width: 800, height: 1200 },
      { left: 0, top: 0 },
    ),
  ).toEqual({ left: 100, top: 450 });
});
