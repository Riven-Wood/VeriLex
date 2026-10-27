export const PDF_MIN_ZOOM = 50;
export const PDF_MAX_ZOOM = 300;
export const PDF_FIT_WIDTH_ZOOM = 100;
export const PDF_FIXED_ZOOM_STEPS = [75, 100, 125, 150, 200] as const;

const PDF_ZOOM_LADDER = [
  PDF_MIN_ZOOM,
  ...PDF_FIXED_ZOOM_STEPS,
  PDF_MAX_ZOOM,
] as const;

export type PdfZoomDirection = "in" | "out";
export interface PdfZoomAnchor {
  clientX: number;
  clientY: number;
  xRatio: number;
  yRatio: number;
}
export interface PdfPageRect {
  left: number;
  top: number;
  width: number;
  height: number;
}
export interface PdfScrollPosition {
  left: number;
  top: number;
}

export function clampPdfZoom(value: number): number {
  return Math.min(PDF_MAX_ZOOM, Math.max(PDF_MIN_ZOOM, value));
}

export function nextFixedPdfZoom(
  current: number,
  direction: PdfZoomDirection,
): number {
  const bounded = clampPdfZoom(current);
  if (direction === "in") {
    return PDF_ZOOM_LADDER.find((value) => value > bounded) ?? PDF_MAX_ZOOM;
  }
  return (
    [...PDF_ZOOM_LADDER].reverse().find((value) => value < bounded) ??
    PDF_MIN_ZOOM
  );
}

export function zoomFromWheel(current: number, deltaY: number): number {
  if (!Number.isFinite(deltaY)) return current;
  const rawFactor = Math.exp(-deltaY * 0.002);
  if (rawFactor >= 1.15) return clampPdfZoom((current * 115) / 100);
  if (rawFactor <= 1 / 1.15) return clampPdfZoom((current * 100) / 115);
  return clampPdfZoom(current * rawFactor);
}

export function anchoredPdfScroll(
  anchor: PdfZoomAnchor,
  pageRect: PdfPageRect,
  currentScroll: PdfScrollPosition,
): PdfScrollPosition {
  return {
    left: Math.max(
      0,
      currentScroll.left + pageRect.left + pageRect.width * anchor.xRatio -
        anchor.clientX,
    ),
    top: Math.max(
      0,
      currentScroll.top + pageRect.top + pageRect.height * anchor.yRatio -
        anchor.clientY,
    ),
  };
}
