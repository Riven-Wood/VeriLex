import { useRef, useState } from "react";

import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { usePdfPinchZoom } from "../src/components/preview/usePdfPinchZoom";

function gesture(type: string, scale: number, clientX = 200, clientY = 250) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(event, { scale, clientX, clientY });
  return event;
}

function Harness({
  enabled = true,
  resetKey = "first",
  twoPages = false,
}: {
  enabled?: boolean;
  resetKey?: string;
  twoPages?: boolean;
}) {
  const [zoomPercent, setZoomPercent] = useState(100);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const { isPinching } = usePdfPinchZoom({
    scrollRef,
    zoomPercent,
    onZoomChange: setZoomPercent,
    resetKey,
    enabled,
  });

  return (
    <>
      {enabled ? (
        <div
          key={resetKey}
          ref={scrollRef}
          className="preview-pdf-scroll"
          data-pinching={isPinching ? "true" : undefined}
        >
          <div
            className="preview-pdf-page"
            style={{ width: `${zoomPercent}%` }}
          />
          {twoPages ? (
            <div
              className="preview-pdf-page"
              style={{ width: `${zoomPercent}%` }}
            />
          ) : null}
        </div>
      ) : null}
      <output aria-label="zoom">{Math.round(zoomPercent)}%</output>
      <button type="button" onClick={() => setZoomPercent(125)}>
        external zoom
      </button>
    </>
  );
}

function installAnimationFrames() {
  let nextId = 1;
  const callbacks = new Map<number, FrameRequestCallback>();
  const cancelledCallbacks: FrameRequestCallback[] = [];
  const request = vi.fn((callback: FrameRequestCallback) => {
    const id = nextId;
    nextId += 1;
    callbacks.set(id, callback);
    return id;
  });
  const cancel = vi.fn((id: number) => {
    const callback = callbacks.get(id);
    if (callback) cancelledCallbacks.push(callback);
    callbacks.delete(id);
  });
  vi.stubGlobal("requestAnimationFrame", request);
  vi.stubGlobal("cancelAnimationFrame", cancel);

  return {
    cancel,
    pendingCount: () => callbacks.size,
    request,
    runCancelled: () => {
      for (const callback of cancelledCallbacks.splice(0)) callback(0);
    },
    flushAll: () => {
      const pending = [...callbacks.entries()];
      callbacks.clear();
      for (const [, callback] of pending) callback(0);
    },
  };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("binds pinch handling when the PDF scroll area appears after loading", () => {
  const frames = installAnimationFrames();
  const view = render(<Harness enabled={false} />);

  view.rerender(<Harness enabled />);
  const scroll = view.container.querySelector(".preview-pdf-scroll")!;
  act(() => {
    scroll.dispatchEvent(gesture("gesturestart", 1));
    scroll.dispatchEvent(gesture("gesturechange", 1.37));
    frames.flushAll();
  });

  expect(view.getByLabelText("zoom").textContent).toBe("137%");
});

test("coalesces native pinch changes into one continuous zoom update", () => {
  const frames = installAnimationFrames();
  const view = render(<Harness />);
  const scroll = view.container.querySelector(".preview-pdf-scroll")!;
  const start = gesture("gesturestart", 1);
  const invalid = gesture("gesturechange", Number.NaN);
  const firstChange = gesture("gesturechange", 1.2);
  const latestChange = gesture("gesturechange", 1.37);

  act(() => {
    scroll.dispatchEvent(start);
    scroll.dispatchEvent(invalid);
    scroll.dispatchEvent(firstChange);
    scroll.dispatchEvent(latestChange);
  });

  expect(frames.request).toHaveBeenCalledTimes(1);
  expect(view.getByLabelText("zoom").textContent).toBe("100%");
  act(() => frames.flushAll());
  expect(start.defaultPrevented).toBe(true);
  expect(latestChange.defaultPrevented).toBe(true);
  expect(view.getByLabelText("zoom").textContent).toBe("137%");
  expect(scroll.getAttribute("data-pinching")).toBe("true");
});

test("uses the final scale reported by gestureend", () => {
  const frames = installAnimationFrames();
  const view = render(<Harness />);
  const scroll = view.container.querySelector(".preview-pdf-scroll")!;

  act(() => {
    scroll.dispatchEvent(gesture("gesturestart", 1));
    scroll.dispatchEvent(gesture("gesturechange", 1.2));
    scroll.dispatchEvent(gesture("gestureend", 1.25));
  });
  expect(frames.request).toHaveBeenCalledTimes(1);
  act(() => frames.flushAll());

  expect(view.getByLabelText("zoom").textContent).toBe("125%");
  expect(scroll.getAttribute("data-pinching")).toBeNull();
});

test("keeps the page point under the gesture stable after zoom", () => {
  const frames = installAnimationFrames();
  const view = render(<Harness />);
  const scroll = view.container.querySelector(
    ".preview-pdf-scroll",
  ) as HTMLElement;
  const page = view.container.querySelector(".preview-pdf-page") as HTMLElement;
  Object.defineProperties(scroll, {
    scrollLeft: { configurable: true, writable: true, value: 0 },
    scrollTop: { configurable: true, writable: true, value: 0 },
  });
  scroll.getBoundingClientRect = vi.fn(
    () =>
      ({
        left: 0,
        right: 500,
        top: 0,
        bottom: 700,
        width: 500,
        height: 700,
      }) as DOMRect,
  );
  page.getBoundingClientRect = vi.fn(() =>
    page.style.width === "200%"
      ? ({
          left: 100,
          right: 900,
          top: 100,
          bottom: 1300,
          width: 800,
          height: 1200,
        } as DOMRect)
      : ({
          left: 100,
          right: 500,
          top: 100,
          bottom: 400,
          width: 400,
          height: 300,
        } as DOMRect),
  );

  act(() => {
    scroll.dispatchEvent(gesture("gesturestart", 1, 200, 250));
    scroll.dispatchEvent(gesture("gesturechange", 2, 200, 250));
    frames.flushAll();
  });

  expect(scroll.scrollLeft).toBe(100);
  expect(scroll.scrollTop).toBe(450);
});

test("reuses the gesture-start anchor when a later event lacks coordinates", () => {
  const frames = installAnimationFrames();
  const view = render(<Harness />);
  const scroll = view.container.querySelector(
    ".preview-pdf-scroll",
  ) as HTMLElement;
  const page = view.container.querySelector(".preview-pdf-page") as HTMLElement;
  Object.defineProperties(scroll, {
    scrollLeft: { configurable: true, writable: true, value: 0 },
    scrollTop: { configurable: true, writable: true, value: 0 },
  });
  scroll.getBoundingClientRect = vi.fn(
    () =>
      ({
        left: 0,
        right: 500,
        top: 0,
        bottom: 700,
        width: 500,
        height: 700,
      }) as DOMRect,
  );
  page.getBoundingClientRect = vi.fn(() =>
    page.style.width === "200%"
      ? ({
          left: 100,
          right: 900,
          top: 100,
          bottom: 1300,
          width: 800,
          height: 1200,
        } as DOMRect)
      : ({
          left: 100,
          right: 500,
          top: 100,
          bottom: 400,
          width: 400,
          height: 300,
        } as DOMRect),
  );

  act(() => {
    scroll.dispatchEvent(gesture("gesturestart", 1, 200, 250));
    scroll.dispatchEvent(
      gesture("gesturechange", 2, Number.NaN, Number.NaN),
    );
    frames.flushAll();
  });

  expect(scroll.scrollLeft).toBe(100);
  expect(scroll.scrollTop).toBe(450);
});

test("uses the nearest visible page when the gesture starts in a page gap", () => {
  const frames = installAnimationFrames();
  const view = render(<Harness twoPages />);
  const scroll = view.container.querySelector(
    ".preview-pdf-scroll",
  ) as HTMLElement;
  const pages = Array.from(
    view.container.querySelectorAll<HTMLElement>(".preview-pdf-page"),
  );
  Object.defineProperties(scroll, {
    scrollLeft: { configurable: true, writable: true, value: 0 },
    scrollTop: { configurable: true, writable: true, value: 0 },
  });
  scroll.getBoundingClientRect = vi.fn(
    () =>
      ({
        left: 0,
        right: 500,
        top: 0,
        bottom: 700,
        width: 500,
        height: 700,
      }) as DOMRect,
  );
  pages[0].getBoundingClientRect = vi.fn(() =>
    pages[0].style.width === "200%"
      ? ({ left: 100, right: 900, top: 0, bottom: 600, width: 800, height: 600 } as DOMRect)
      : ({ left: 100, right: 500, top: 0, bottom: 300, width: 400, height: 300 } as DOMRect),
  );
  pages[1].getBoundingClientRect = vi.fn(() =>
    pages[1].style.width === "200%"
      ? ({ left: 100, right: 900, top: 700, bottom: 1300, width: 800, height: 600 } as DOMRect)
      : ({ left: 100, right: 500, top: 400, bottom: 700, width: 400, height: 300 } as DOMRect),
  );

  act(() => {
    scroll.dispatchEvent(gesture("gesturestart", 1, 200, 380));
    scroll.dispatchEvent(gesture("gesturechange", 2, 200, 380));
    frames.flushAll();
  });

  expect(scroll.scrollTop).toBe(320);
});

test("a gesture without a zoom change leaves no anchor for later controls", () => {
  const view = render(<Harness />);
  const scroll = view.container.querySelector(
    ".preview-pdf-scroll",
  ) as HTMLElement;
  Object.defineProperties(scroll, {
    scrollLeft: { configurable: true, writable: true, value: 14 },
    scrollTop: { configurable: true, writable: true, value: 28 },
  });

  act(() => {
    scroll.dispatchEvent(gesture("gesturestart", 1));
    scroll.dispatchEvent(gesture("gestureend", 1));
  });
  act(() => view.getByRole("button", { name: "external zoom" }).click());

  expect(view.getByLabelText("zoom").textContent).toBe("125%");
  expect(scroll.scrollLeft).toBe(14);
  expect(scroll.scrollTop).toBe(28);
});

test("uses ctrl-wheel for zoom and leaves an ordinary wheel untouched", () => {
  const frames = installAnimationFrames();
  const view = render(<Harness />);
  const scroll = view.container.querySelector(".preview-pdf-scroll")!;
  const plain = new WheelEvent("wheel", {
    deltaY: -500,
    bubbles: true,
    cancelable: true,
  });
  const pinch = new WheelEvent("wheel", {
    deltaY: -500,
    ctrlKey: true,
    clientX: 200,
    clientY: 250,
    bubbles: true,
    cancelable: true,
  });

  act(() => scroll.dispatchEvent(plain));
  expect(plain.defaultPrevented).toBe(false);
  expect(view.getByLabelText("zoom").textContent).toBe("100%");

  act(() => {
    scroll.dispatchEvent(pinch);
    frames.flushAll();
  });
  expect(pinch.defaultPrevented).toBe(true);
  expect(view.getByLabelText("zoom").textContent).toBe("115%");
});

test("does not apply compatibility wheel events around a native gesture", () => {
  vi.useFakeTimers();
  const frames = installAnimationFrames();
  let now = 1_000;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  const view = render(<Harness />);
  const scroll = view.container.querySelector(".preview-pdf-scroll")!;

  const wheelBeforeStart = new WheelEvent("wheel", {
    deltaY: -500,
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  });
  act(() => scroll.dispatchEvent(wheelBeforeStart));
  expect(frames.pendingCount()).toBe(1);

  act(() => {
    scroll.dispatchEvent(gesture("gesturestart", 1));
  });
  expect(frames.pendingCount()).toBe(0);
  act(() => vi.advanceTimersByTime(200));
  expect(scroll.getAttribute("data-pinching")).toBe("true");

  const wheelDuring = new WheelEvent("wheel", {
    deltaY: -500,
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  });
  act(() => scroll.dispatchEvent(wheelDuring));
  expect(frames.pendingCount()).toBe(0);

  act(() => scroll.dispatchEvent(gesture("gestureend", 1)));
  now = 1_100;
  const wheelAfterEnd = new WheelEvent("wheel", {
    deltaY: -500,
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  });
  act(() => scroll.dispatchEvent(wheelAfterEnd));
  expect(frames.pendingCount()).toBe(0);
  expect(view.getByLabelText("zoom").textContent).toBe("100%");

  now = 1_251;
  const laterWheel = new WheelEvent("wheel", {
    deltaY: -500,
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  });
  act(() => {
    scroll.dispatchEvent(laterWheel);
    frames.flushAll();
  });
  expect(view.getByLabelText("zoom").textContent).toBe("115%");
});

test("cancels queued zoom work and clears pinch state on source reset", () => {
  const frames = installAnimationFrames();
  const view = render(<Harness />);
  const scroll = view.container.querySelector(".preview-pdf-scroll")!;
  act(() => {
    scroll.dispatchEvent(gesture("gesturestart", 1));
    scroll.dispatchEvent(gesture("gesturechange", 1.2));
  });
  expect(scroll.getAttribute("data-pinching")).toBe("true");
  expect(frames.pendingCount()).toBe(1);

  act(() => view.rerender(<Harness resetKey="second" />));

  const replacement = view.container.querySelector(".preview-pdf-scroll")!;
  expect(frames.cancel).toHaveBeenCalled();
  expect(frames.pendingCount()).toBe(0);
  expect(replacement.getAttribute("data-pinching")).toBeNull();
  act(() => frames.runCancelled());
  expect(view.getByLabelText("zoom").textContent).toBe("100%");

  const staleGesture = gesture("gesturestart", 1);
  act(() => scroll.dispatchEvent(staleGesture));
  expect(staleGesture.defaultPrevented).toBe(false);
});

test("ends temporary selection suppression after ctrl-wheel activity", () => {
  vi.useFakeTimers();
  const frames = installAnimationFrames();
  const view = render(<Harness />);
  const scroll = view.container.querySelector(".preview-pdf-scroll")!;
  const pinch = new WheelEvent("wheel", {
    deltaY: -100,
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  });

  act(() => {
    scroll.dispatchEvent(pinch);
    frames.flushAll();
  });
  expect(scroll.getAttribute("data-pinching")).toBe("true");
  act(() => vi.advanceTimersByTime(119));
  expect(scroll.getAttribute("data-pinching")).toBe("true");
  act(() => vi.advanceTimersByTime(1));
  expect(scroll.getAttribute("data-pinching")).toBeNull();
});
