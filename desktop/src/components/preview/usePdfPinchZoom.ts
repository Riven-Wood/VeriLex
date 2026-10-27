import {
  type RefObject,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import {
  anchoredPdfScroll,
  clampPdfZoom,
  type PdfZoomAnchor,
  zoomFromWheel,
} from "./pdfZoom";

const GESTURE_WHEEL_SUPPRESSION_MS = 200;
const WHEEL_PINCH_STATE_MS = 120;

interface WebKitGestureEvent extends Event {
  scale: number;
  clientX?: number;
  clientY?: number;
}

interface UsePdfPinchZoomOptions {
  scrollRef: RefObject<HTMLDivElement | null>;
  zoomPercent: number;
  onZoomChange: (nextZoom: number) => void;
  resetKey: Blob | string;
  enabled: boolean;
}

interface PendingAnchor {
  page: HTMLElement;
  anchor: PdfZoomAnchor;
}

interface QueuedZoom {
  zoom: number;
  anchor: PendingAnchor | null;
  input: "gesture" | "wheel";
}

interface CommittedZoom extends QueuedZoom {
  generation: number;
}

function distanceToRect(x: number, y: number, rect: DOMRect): number {
  const dx = Math.max(rect.left - x, 0, x - rect.right);
  const dy = Math.max(rect.top - y, 0, y - rect.bottom);
  return dx * dx + dy * dy;
}

function captureAnchor(
  scroll: HTMLElement,
  clientX?: number,
  clientY?: number,
  fallback: PendingAnchor | null = null,
): PendingAnchor | null {
  const scrollRect = scroll.getBoundingClientRect();
  const hasUsablePoint =
    Number.isFinite(clientX) &&
    Number.isFinite(clientY) &&
    (clientX as number) >= scrollRect.left &&
    (clientX as number) <= scrollRect.right &&
    (clientY as number) >= scrollRect.top &&
    (clientY as number) <= scrollRect.bottom;
  if (!hasUsablePoint && fallback?.page.isConnected) return fallback;
  const x = hasUsablePoint
    ? (clientX as number)
    : scrollRect.left + scrollRect.width / 2;
  const y = hasUsablePoint
    ? (clientY as number)
    : scrollRect.top + scrollRect.height / 2;
  const pages = Array.from(
    scroll.querySelectorAll<HTMLElement>(".preview-pdf-page"),
  );
  const visiblePages = pages.filter((page) => {
    const rect = page.getBoundingClientRect();
    return rect.bottom >= scrollRect.top && rect.top <= scrollRect.bottom;
  });
  const candidates = visiblePages.length > 0 ? visiblePages : pages;
  const page = candidates.reduce<HTMLElement | null>((best, candidate) => {
    if (best === null) return candidate;
    return distanceToRect(x, y, candidate.getBoundingClientRect()) <
      distanceToRect(x, y, best.getBoundingClientRect())
      ? candidate
      : best;
  }, null);
  if (page === null) return null;
  const pageRect = page.getBoundingClientRect();
  return {
    page,
    anchor: {
      clientX: x,
      clientY: y,
      xRatio: Math.min(
        1,
        Math.max(0, (x - pageRect.left) / Math.max(1, pageRect.width)),
      ),
      yRatio: Math.min(
        1,
        Math.max(0, (y - pageRect.top) / Math.max(1, pageRect.height)),
      ),
    },
  };
}

export function usePdfPinchZoom({
  scrollRef,
  zoomPercent,
  onZoomChange,
  resetKey,
  enabled,
}: UsePdfPinchZoomOptions): { isPinching: boolean } {
  const [isPinching, setIsPinching] = useState(false);
  const latestZoomRef = useRef(zoomPercent);
  const gestureStartZoomRef = useRef(zoomPercent);
  const lastGestureAnchorRef = useRef<PendingAnchor | null>(null);
  const nativeGestureActiveRef = useRef(false);
  const nativeWheelSuppressionUntilRef = useRef(0);
  const queuedRef = useRef<QueuedZoom | null>(null);
  const committedRef = useRef<CommittedZoom | null>(null);
  const frameRef = useRef<number | null>(null);
  const wheelEndTimerRef = useRef<number | null>(null);
  const generationRef = useRef(0);

  useLayoutEffect(() => {
    latestZoomRef.current = zoomPercent;
    const scroll = scrollRef.current;
    const committed = committedRef.current;
    if (!committed) return;
    committedRef.current = null;
    if (
      !enabled ||
      committed.generation !== generationRef.current ||
      committed.zoom !== zoomPercent ||
      !scroll ||
      !committed.anchor?.page.isConnected
    ) {
      return;
    }
    const position = anchoredPdfScroll(
      committed.anchor.anchor,
      committed.anchor.page.getBoundingClientRect(),
      { left: scroll.scrollLeft, top: scroll.scrollTop },
    );
    scroll.scrollLeft = position.left;
    scroll.scrollTop = position.top;
  }, [enabled, scrollRef, zoomPercent]);

  useEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    queuedRef.current = null;
    committedRef.current = null;
    nativeGestureActiveRef.current = false;
    lastGestureAnchorRef.current = null;
    nativeWheelSuppressionUntilRef.current = 0;
    setIsPinching(false);

    const scroll = enabled ? scrollRef.current : null;
    if (!scroll) {
      return () => {
        generationRef.current += 1;
      };
    }

    const clearWheelEndTimer = () => {
      if (wheelEndTimerRef.current !== null) {
        window.clearTimeout(wheelEndTimerRef.current);
        wheelEndTimerRef.current = null;
      }
    };

    const cancelQueuedWheel = () => {
      if (queuedRef.current?.input !== "wheel") return;
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      queuedRef.current = null;
      if (committedRef.current?.input === "wheel") {
        committedRef.current = null;
      }
    };

    const scheduleZoom = (
      nextZoom: number,
      anchor: PendingAnchor | null,
      input: QueuedZoom["input"],
    ) => {
      const bounded = clampPdfZoom(nextZoom);
      const currentTarget = queuedRef.current?.zoom ?? latestZoomRef.current;
      if (bounded === currentTarget) {
        if (queuedRef.current) {
          queuedRef.current = { zoom: bounded, anchor, input };
        } else {
          committedRef.current = null;
        }
        return;
      }
      queuedRef.current = { zoom: bounded, anchor, input };
      if (frameRef.current !== null) return;
      const scheduledGeneration = generation;
      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null;
        if (generationRef.current !== scheduledGeneration) return;
        const queued = queuedRef.current;
        queuedRef.current = null;
        if (!queued || queued.zoom === latestZoomRef.current) {
          committedRef.current = null;
          return;
        }
        committedRef.current = { ...queued, generation: scheduledGeneration };
        latestZoomRef.current = queued.zoom;
        onZoomChange(queued.zoom);
      });
    };

    const handleGestureStart = (rawEvent: Event) => {
      const event = rawEvent as WebKitGestureEvent;
      event.preventDefault();
      cancelQueuedWheel();
      clearWheelEndTimer();
      gestureStartZoomRef.current = latestZoomRef.current;
      lastGestureAnchorRef.current = captureAnchor(
        scroll,
        event.clientX,
        event.clientY,
      );
      nativeGestureActiveRef.current = true;
      nativeWheelSuppressionUntilRef.current =
        performance.now() + GESTURE_WHEEL_SUPPRESSION_MS;
      setIsPinching(true);
    };

    const handleGestureChange = (rawEvent: Event) => {
      const event = rawEvent as WebKitGestureEvent;
      event.preventDefault();
      if (
        !nativeGestureActiveRef.current ||
        !Number.isFinite(event.scale) ||
        event.scale <= 0
      ) {
        return;
      }
      nativeWheelSuppressionUntilRef.current =
        performance.now() + GESTURE_WHEEL_SUPPRESSION_MS;
      const anchor = captureAnchor(
        scroll,
        event.clientX,
        event.clientY,
        lastGestureAnchorRef.current,
      );
      lastGestureAnchorRef.current = anchor;
      scheduleZoom(
        gestureStartZoomRef.current * event.scale,
        anchor,
        "gesture",
      );
    };

    const handleGestureEnd = (rawEvent: Event) => {
      const event = rawEvent as WebKitGestureEvent;
      event.preventDefault();
      if (
        nativeGestureActiveRef.current &&
        Number.isFinite(event.scale) &&
        event.scale > 0
      ) {
        const anchor = captureAnchor(
          scroll,
          event.clientX,
          event.clientY,
          lastGestureAnchorRef.current,
        );
        lastGestureAnchorRef.current = anchor;
        scheduleZoom(
          gestureStartZoomRef.current * event.scale,
          anchor,
          "gesture",
        );
      }
      nativeGestureActiveRef.current = false;
      lastGestureAnchorRef.current = null;
      nativeWheelSuppressionUntilRef.current =
        performance.now() + GESTURE_WHEEL_SUPPRESSION_MS;
      setIsPinching(false);
    };

    const handleWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      if (
        nativeGestureActiveRef.current ||
        performance.now() < nativeWheelSuppressionUntilRef.current
      ) {
        return;
      }
      const base = queuedRef.current?.zoom ?? latestZoomRef.current;
      scheduleZoom(
        zoomFromWheel(base, event.deltaY),
        captureAnchor(scroll, event.clientX, event.clientY),
        "wheel",
      );
      setIsPinching(true);
      clearWheelEndTimer();
      wheelEndTimerRef.current = window.setTimeout(() => {
        wheelEndTimerRef.current = null;
        if (
          generationRef.current === generation &&
          !nativeGestureActiveRef.current
        ) {
          setIsPinching(false);
        }
      }, WHEEL_PINCH_STATE_MS);
    };

    const listenerOptions: AddEventListenerOptions = { passive: false };
    scroll.addEventListener("gesturestart", handleGestureStart, listenerOptions);
    scroll.addEventListener("gesturechange", handleGestureChange, listenerOptions);
    scroll.addEventListener("gestureend", handleGestureEnd, listenerOptions);
    scroll.addEventListener("wheel", handleWheel, listenerOptions);

    return () => {
      generationRef.current += 1;
      scroll.removeEventListener("gesturestart", handleGestureStart);
      scroll.removeEventListener("gesturechange", handleGestureChange);
      scroll.removeEventListener("gestureend", handleGestureEnd);
      scroll.removeEventListener("wheel", handleWheel);
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      clearWheelEndTimer();
      queuedRef.current = null;
      committedRef.current = null;
      nativeGestureActiveRef.current = false;
      lastGestureAnchorRef.current = null;
    };
  }, [enabled, onZoomChange, resetKey, scrollRef]);

  return { isPinching };
}
