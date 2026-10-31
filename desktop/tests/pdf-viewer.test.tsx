import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { render } from "@testing-library/react";

const { getDocumentMock } = vi.hoisted(() => ({
  getDocumentMock: vi.fn(),
}));

vi.mock("pdfjs-dist/legacy/build/pdf.mjs", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: getDocumentMock,
}));

vi.mock("pdfjs-dist/legacy/build/pdf.worker.min.mjs", () => ({
  WorkerMessageHandler: {},
}));

import { PdfViewer } from "../src/components/preview/PdfViewer";

let getContextSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  getContextSpy = vi
    .spyOn(HTMLCanvasElement.prototype, "getContext")
    .mockReturnValue({ fillRect: vi.fn() } as unknown as CanvasRenderingContext2D);
});

afterEach(() => {
  cleanup();
  getContextSpy.mockRestore();
  getDocumentMock.mockReset();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.querySelector("style[data-pdf-viewer-test]")?.remove();
});

function pdfGesture(
  type: string,
  scale: number,
  clientX = 120,
  clientY = 160,
) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(event, { scale, clientX, clientY });
  return event;
}

function installAnimationFrames() {
  let nextId = 1;
  const callbacks = new Map<number, FrameRequestCallback>();
  vi.stubGlobal(
    "requestAnimationFrame",
    vi.fn((callback: FrameRequestCallback) => {
      const id = nextId;
      nextId += 1;
      callbacks.set(id, callback);
      return id;
    }),
  );
  vi.stubGlobal(
    "cancelAnimationFrame",
    vi.fn((id: number) => callbacks.delete(id)),
  );
  return {
    flushAll: () => {
      const pending = [...callbacks.values()];
      callbacks.clear();
      for (const callback of pending) callback(0);
    },
  };
}

function fakePage(pageNumber: number) {
  return {
    getViewport: ({ scale }: { scale: number }) => ({
      width: 200 * scale,
      height: 300 * scale,
    }),
    render: vi.fn(({ canvas }: { canvas: HTMLCanvasElement }) => ({
      promise: Promise.resolve().then(() => {
        const context = canvas.getContext("2d");
        context?.fillRect(0, 0, 8, 8);
      }),
      cancel: vi.fn(),
    })),
    pageNumber,
  };
}

function fakeDocument(
  pageCount: number,
  pages = Array.from({ length: pageCount }, (_, index) => fakePage(index + 1)),
) {
  return {
    numPages: pageCount,
    getPage: vi.fn((pageNumber: number) =>
      Promise.resolve(pages[pageNumber - 1]),
    ),
  };
}

function fakeLoadingTask(promise: Promise<ReturnType<typeof fakeDocument>>) {
  return {
    promise,
    destroy: vi.fn().mockResolvedValue(undefined),
  };
}

function fakePdfBlob() {
  const bytes = new Uint8Array([37, 80, 68, 70]);
  const blob = new Blob([bytes], { type: "application/pdf" });
  Object.defineProperty(blob, "arrayBuffer", {
    configurable: true,
    value: vi.fn().mockResolvedValue(bytes.buffer),
  });
  return blob;
}

async function loadPdfStyles() {
  const stylesheet = document.createElement("style");
  stylesheet.dataset.pdfViewerTest = "true";
  stylesheet.textContent = await readFile(
    resolve(process.cwd(), "src/styles/views.css"),
    "utf8",
  );
  document.head.append(stylesheet);
}

test("a late-page citation scrolls to the actual canvas and paints it before intervening pages", async () => {
  const doc = fakeDocument(60);
  getDocumentMock.mockReturnValue(fakeLoadingTask(Promise.resolve(doc)));
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
    const number = Number(this.getAttribute('aria-label')?.match(/PDF 第 (\d+) 页/)?.[1] ?? 0);
    return {top:number * 1000, left:0, right:200, bottom:number * 1000 + 300, width:200, height:300, x:0, y:number * 1000, toJSON:()=>({})};
  });
  const view = render(<PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" page={45}/>);
  await view.findByText('已定位 PDF 第 45 页（文件实际页码）');
  const scroll = view.container.querySelector('.preview-pdf-scroll') as HTMLElement;
  expect(scroll.scrollTop).toBe(45000);
  const page45 = await doc.getPage(45);
  const page44 = await doc.getPage(44);
  await waitFor(()=>expect(page44.render).toHaveBeenCalled());
  expect(page45.render.mock.invocationCallOrder[0]).toBeLessThan(page44.render.mock.invocationCallOrder[0]);
  expect(getDocumentMock).toHaveBeenCalledTimes(1);
});

test("a citation beyond the document shows a page error rather than pretending to locate it", async () => {
  getDocumentMock.mockReturnValue(fakeLoadingTask(Promise.resolve(fakeDocument(2))));
  const view = render(<PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" page={45}/>);
  expect(await view.findByRole('alert')).toHaveProperty('textContent','出处页码超出这份文件范围，请核对材料出处。');
  expect(view.queryByText('已定位 PDF 第 45 页（文件实际页码）')).toBeNull();
});

test("renders every page with numbers and draws canvases", async () => {
  getDocumentMock.mockReturnValue(fakeLoadingTask(Promise.resolve(fakeDocument(2))));
  const { container } = render(
    <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
  );

  await waitFor(() =>
    expect(container.querySelectorAll(".preview-pdf-page")).toHaveLength(2),
  );
  await waitFor(() =>
    expect(
      container.querySelectorAll(".preview-pdf-page canvas")[0].width,
    ).toBeGreaterThan(0),
  );
  expect(container.querySelector(".preview-pdf-total")?.textContent)
    .toContain("共 2 页");
  expect(getDocumentMock).toHaveBeenCalledWith(
    expect.objectContaining({ data: expect.any(Uint8Array), disableFontFace: true, stopAtErrors: true }),
  );
});

test("adds real PDF pages as the loaded content reaches its bottom", async () => {
  const page1 = fakePage(1);
  const page2 = fakePage(2);
  const page3 = fakePage(3);
  const page4 = fakePage(4);
  const document = fakeDocument(4, [page1, page2, page3, page4]);
  getDocumentMock.mockReturnValue(
    fakeLoadingTask(Promise.resolve(document)),
  );
  const { container } = render(
    <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
  );

  await waitFor(() =>
    expect(container.querySelectorAll(".preview-pdf-page")).toHaveLength(2),
  );
  await waitFor(() => expect(document.getPage).toHaveBeenCalledWith(2));
  expect(container.querySelector(".preview-pdf-total")?.textContent).toContain(
    "已加载 2 / 共 4 页",
  );
  await waitFor(() => expect(page2.render).toHaveBeenCalledTimes(1));
  expect(page3.render).not.toHaveBeenCalled();

  const scroll = container.querySelector(".preview-pdf-scroll") as HTMLElement;
  Object.defineProperties(scroll, {
    scrollTop: { configurable: true, value: 710 },
    clientHeight: { configurable: true, value: 200 },
    scrollHeight: {
      configurable: true,
      get: () =>
        scroll.querySelectorAll(".preview-pdf-page").length >= 3 ? 1200 : 1000,
    },
  });
  fireEvent.scroll(scroll);

  await waitFor(() =>
    expect(container.querySelectorAll(".preview-pdf-page")).toHaveLength(3),
  );
  await waitFor(() => expect(page3.render).toHaveBeenCalledTimes(1));
});

test("fills an initially non-scrolling PDF viewport one page at a time", async () => {
  const clientHeightSpy = vi
    .spyOn(HTMLElement.prototype, "clientHeight", "get")
    .mockImplementation(function clientHeight() {
      return this.classList.contains("preview-pdf-scroll") ? 1000 : 0;
    });
  const scrollHeightSpy = vi
    .spyOn(HTMLElement.prototype, "scrollHeight", "get")
    .mockImplementation(function scrollHeight() {
      return this.classList.contains("preview-pdf-scroll") ? 900 : 0;
    });
  const page1 = fakePage(1);
  const page2 = fakePage(2);
  const page3 = fakePage(3);
  getDocumentMock.mockReturnValue(
    fakeLoadingTask(
      Promise.resolve(fakeDocument(3, [page1, page2, page3])),
    ),
  );

  try {
    const { container } = render(
      <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
    );

    await waitFor(() => expect(page3.render).toHaveBeenCalledTimes(1));
    expect(container.querySelectorAll(".preview-pdf-page")).toHaveLength(3);
  } finally {
    clientHeightSpy.mockRestore();
    scrollHeightSpy.mockRestore();
  }
});

test("adds one page when a resized PDF viewport has no vertical overflow", async () => {
  const page1 = fakePage(1);
  const page2 = fakePage(2);
  const page3 = fakePage(3);
  getDocumentMock.mockReturnValue(
    fakeLoadingTask(
      Promise.resolve(fakeDocument(3, [page1, page2, page3])),
    ),
  );
  const { container } = render(
    <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
  );
  await waitFor(() => expect(page2.render).toHaveBeenCalledTimes(1));
  expect(page3.render).not.toHaveBeenCalled();

  const scroll = container.querySelector(".preview-pdf-scroll") as HTMLElement;
  Object.defineProperties(scroll, {
    scrollTop: { configurable: true, value: 0 },
    clientHeight: { configurable: true, value: 1000 },
    scrollHeight: { configurable: true, value: 900 },
  });
  fireEvent(window, new Event("resize"));

  await waitFor(() => expect(page3.render).toHaveBeenCalledTimes(1));
  expect(container.querySelectorAll(".preview-pdf-page")).toHaveLength(3);
});

test("loads the next page on a downward wheel at 75 percent without overflow", async () => {
  const page1 = fakePage(1);
  const page2 = fakePage(2);
  const page3 = fakePage(3);
  getDocumentMock.mockReturnValue(
    fakeLoadingTask(
      Promise.resolve(fakeDocument(3, [page1, page2, page3])),
    ),
  );
  const { container, getByRole } = render(
    <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
  );
  await waitFor(() => expect(page2.render).toHaveBeenCalledTimes(1));
  const scroll = container.querySelector(".preview-pdf-scroll") as HTMLElement;
  Object.defineProperties(scroll, {
    scrollTop: { configurable: true, value: 0 },
    clientHeight: { configurable: true, value: 200 },
    scrollHeight: { configurable: true, value: 1000 },
  });
  fireEvent.click(getByRole("button", { name: "缩小 PDF" }));
  expect(getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
    "75%",
  );
  expect(page3.render).not.toHaveBeenCalled();

  Object.defineProperties(scroll, {
    clientHeight: { configurable: true, value: 1000 },
    scrollHeight: { configurable: true, value: 900 },
  });
  fireEvent.wheel(scroll, { deltaY: 40 });

  await waitFor(() => expect(page3.render).toHaveBeenCalledTimes(1));
});

test("keeps the outer PDF shell fixed while the inner viewport owns scrolling", async () => {
  await loadPdfStyles();
  getDocumentMock.mockReturnValue(
    fakeLoadingTask(Promise.resolve(fakeDocument(1))),
  );
  const { container } = render(
    <div className="preview-pdf">
      <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />
    </div>,
  );
  await waitFor(() =>
    expect(container.querySelector(".preview-pdf-scroll")).not.toBeNull(),
  );
  const outer = container.querySelector(".preview-pdf") as HTMLElement;
  const scroll = container.querySelector(".preview-pdf-scroll") as HTMLElement;

  expect(getComputedStyle(outer).overflow).toBe("hidden");
  expect(getComputedStyle(scroll).overflow).toBe("auto");

  const page = container.querySelector(".preview-pdf-page") as HTMLElement;
  fireEvent(scroll, pdfGesture("gesturestart", 1));
  expect(scroll.getAttribute("data-pinching")).toBe("true");
  expect(getComputedStyle(page).userSelect).toBe("none");
  fireEvent(scroll, pdfGesture("gestureend", 1));
  expect(scroll.getAttribute("data-pinching")).toBeNull();
});

test("opens the PDF once and reuses that document while rendering pages", async () => {
  getDocumentMock
    .mockReturnValueOnce(fakeLoadingTask(Promise.resolve(fakeDocument(1))))
    .mockImplementation(() =>
      fakeLoadingTask(
        Promise.reject(new Error("the original byte buffer was transferred")),
      ),
    );

  const { container } = render(
    <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
  );

  await waitFor(() =>
    expect(
      container.querySelector(".preview-pdf-page canvas")?.getAttribute("width"),
    ).not.toBeNull(),
  );
  expect(container.querySelector(".preview-pdf-error")).toBeNull();
  expect(getDocumentMock).toHaveBeenCalledTimes(1);
});

test("reads an attachment Blob directly without fetching a blob URL", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  getDocumentMock.mockReturnValue(
    fakeLoadingTask(Promise.resolve(fakeDocument(1))),
  );
  const source = fakePdfBlob();

  const { container } = render(<PdfViewer source={source} />);

  await waitFor(() =>
    expect(
      container.querySelector(".preview-pdf-page canvas")?.getAttribute("width"),
    ).not.toBeNull(),
  );
  expect(fetchSpy).not.toHaveBeenCalled();
  fetchSpy.mockRestore();
});

test("does not start the same canvas render twice when scrolling during a render", async () => {
  let finishFirstPage!: () => void;
  const firstPagePromise = new Promise<void>((resolve) => {
    finishFirstPage = resolve;
  });
  const firstPage = fakePage(1);
  const secondPage = fakePage(2);
  const thirdPage = fakePage(3);
  firstPage.render = vi.fn(() => ({
    promise: firstPagePromise,
    cancel: vi.fn(),
  }));
  getDocumentMock.mockReturnValue(
    fakeLoadingTask(
      Promise.resolve(fakeDocument(3, [firstPage, secondPage, thirdPage])),
    ),
  );

  const { container } = render(
    <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
  );
  await waitFor(() => expect(firstPage.render).toHaveBeenCalledTimes(1));

  const scroll = container.querySelector(".preview-pdf-scroll") as HTMLElement;
  Object.defineProperties(scroll, {
    scrollTop: { configurable: true, value: 900 },
    clientHeight: { configurable: true, value: 200 },
    scrollHeight: { configurable: true, value: 1000 },
  });
  fireEvent.scroll(scroll);
  await waitFor(() =>
    expect(container.querySelectorAll(".preview-pdf-page")).toHaveLength(3),
  );

  expect(firstPage.render).toHaveBeenCalledTimes(1);
  expect(thirdPage.render).not.toHaveBeenCalled();
  finishFirstPage();
  await waitFor(() => expect(secondPage.render).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(thirdPage.render).toHaveBeenCalledTimes(1));
  expect(firstPage.render).toHaveBeenCalledTimes(1);
  expect(secondPage.render).toHaveBeenCalledTimes(1);
});

test("pinches continuously and keeps toolbar steps without reopening or redrawing", async () => {
  const frames = installAnimationFrames();
  const page1 = fakePage(1);
  const page2 = fakePage(2);
  const document = fakeDocument(2, [page1, page2]);
  getDocumentMock.mockReturnValue(fakeLoadingTask(Promise.resolve(document)));
  const { container, getByRole } = render(
    <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
  );

  await waitFor(() => expect(page2.render).toHaveBeenCalledTimes(1));
  const page = container.querySelector(".preview-pdf-page") as HTMLElement;
  const scroll = container.querySelector(".preview-pdf-scroll") as HTMLElement;
  const toolbar = getByRole("toolbar", { name: "PDF 缩放工具栏" });
  expect(scroll.contains(toolbar)).toBe(false);
  expect(getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
    "100%",
  );

  const pageReadsBeforeZoom = document.getPage.mock.calls.length;
  const start = pdfGesture("gesturestart", 1);
  const change = pdfGesture("gesturechange", 1.37);
  const end = pdfGesture("gestureend", 1.37);
  fireEvent(scroll, start);
  fireEvent(scroll, change);
  fireEvent(scroll, end);
  act(() => frames.flushAll());

  expect(start.defaultPrevented).toBe(true);
  expect(change.defaultPrevented).toBe(true);
  expect(page.style.width).toBe("137%");
  expect(getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
    "137%",
  );

  fireEvent.click(getByRole("button", { name: "缩小 PDF" }));
  expect(page.style.width).toBe("125%");
  fireEvent(scroll, pdfGesture("gesturestart", 1));
  fireEvent(scroll, pdfGesture("gesturechange", 137 / 125));
  fireEvent(scroll, pdfGesture("gestureend", 137 / 125));
  act(() => frames.flushAll());
  expect(getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
    "137%",
  );
  fireEvent.click(getByRole("button", { name: "放大 PDF" }));
  expect(page.style.width).toBe("150%");

  expect(getDocumentMock).toHaveBeenCalledTimes(1);
  expect(document.getPage).toHaveBeenCalledTimes(pageReadsBeforeZoom);
  expect(page1.render).toHaveBeenCalledTimes(1);
  expect(page2.render).toHaveBeenCalledTimes(1);

  fireEvent.click(getByRole("button", { name: "适合宽度" }));
  expect(page.style.width).toBe("100%");
  expect(getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
    "100%",
  );
});

test("disables PDF zoom controls only at 50 and 300 percent", async () => {
  getDocumentMock.mockReturnValue(
    fakeLoadingTask(Promise.resolve(fakeDocument(1))),
  );
  const { getByRole } = render(
    <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
  );

  const shrink = await waitFor(() =>
    getByRole("button", { name: "缩小 PDF" }),
  );
  const enlarge = getByRole("button", { name: "放大 PDF" });
  fireEvent.click(shrink);
  expect(getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
    "75%",
  );
  expect((shrink as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(shrink);
  expect(getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
    "50%",
  );
  expect((shrink as HTMLButtonElement).disabled).toBe(true);

  for (let click = 0; click < 6; click += 1) fireEvent.click(enlarge);
  expect(getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
    "300%",
  );
  expect((enlarge as HTMLButtonElement).disabled).toBe(true);
});

test("ctrl-wheel zoom and ordinary wheel pagination remain separate", async () => {
  const frames = installAnimationFrames();
  const page1 = fakePage(1);
  const page2 = fakePage(2);
  const page3 = fakePage(3);
  getDocumentMock.mockReturnValue(
    fakeLoadingTask(Promise.resolve(fakeDocument(3, [page1, page2, page3]))),
  );
  const { container, getByRole } = render(
    <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
  );
  await waitFor(() => expect(page2.render).toHaveBeenCalledTimes(1));
  const scroll = container.querySelector(".preview-pdf-scroll") as HTMLElement;
  Object.defineProperties(scroll, {
    scrollTop: { configurable: true, writable: true, value: 500 },
    clientHeight: { configurable: true, value: 200 },
    scrollHeight: { configurable: true, value: 1000 },
  });

  const pinch = new WheelEvent("wheel", {
    deltaY: -500,
    ctrlKey: true,
    clientX: 120,
    clientY: 160,
    bubbles: true,
    cancelable: true,
  });
  fireEvent(scroll, pinch);
  act(() => frames.flushAll());
  expect(pinch.defaultPrevented).toBe(true);
  expect(getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
    "115%",
  );
  expect(page3.render).not.toHaveBeenCalled();

  Object.defineProperty(scroll, "scrollTop", {
    configurable: true,
    writable: true,
    value: 710,
  });
  fireEvent.wheel(scroll, { deltaY: 40, ctrlKey: false });
  await waitFor(() => expect(page3.render).toHaveBeenCalledTimes(1));
});

test("resets PDF zoom to fit width when the source changes", async () => {
  const firstDocument = fakeDocument(1);
  const secondDocument = fakeDocument(1);
  const firstLoadingTask = fakeLoadingTask(Promise.resolve(firstDocument));
  const secondLoadingTask = fakeLoadingTask(Promise.resolve(secondDocument));
  getDocumentMock
    .mockReturnValueOnce(firstLoadingTask)
    .mockReturnValueOnce(secondLoadingTask);
  const firstSource = fakePdfBlob();
  const secondSource = fakePdfBlob();
  const view = render(<PdfViewer source={firstSource} />);

  await waitFor(() => expect(getDocumentMock).toHaveBeenCalledTimes(1));
  expect(getDocumentMock.mock.results[0]?.value).toBe(firstLoadingTask);
  await waitFor(() =>
    expect(view.getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
      "100%",
    ),
  );
  fireEvent.click(view.getByRole("button", { name: "放大 PDF" }));
  expect(view.getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
    "125%",
  );

  view.rerender(<PdfViewer source={secondSource} />);

  await waitFor(() => expect(getDocumentMock).toHaveBeenCalledTimes(2));
  await waitFor(() =>
    expect(view.getByRole("status", { name: "PDF 缩放比例" }).textContent).toBe(
      "100%",
    ),
  );
});

test("cancels in-flight rendering and destroys loading state on source change", async () => {
  let finishFirstRender!: () => void;
  const firstRenderTask = {
    promise: new Promise<void>((resolve) => {
      finishFirstRender = resolve;
    }),
    cancel: vi.fn(),
  };
  const firstPage = fakePage(1);
  firstPage.render = vi.fn(() => firstRenderTask);
  const firstLoadingTask = fakeLoadingTask(
    Promise.resolve(fakeDocument(1, [firstPage])),
  );
  const secondLoadingTask = fakeLoadingTask(
    Promise.resolve(fakeDocument(1)),
  );
  getDocumentMock
    .mockReturnValueOnce(firstLoadingTask)
    .mockReturnValueOnce(secondLoadingTask);
  const firstSource = fakePdfBlob();
  const secondSource = fakePdfBlob();
  const view = render(<PdfViewer source={firstSource} />);
  await waitFor(() => expect(firstPage.render).toHaveBeenCalledTimes(1));

  view.rerender(<PdfViewer source={secondSource} />);

  await waitFor(() => expect(firstRenderTask.cancel).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(firstLoadingTask.destroy).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(getDocumentMock).toHaveBeenCalledTimes(2));
  finishFirstRender();
});

test("renders an explicit error when the document cannot load", async () => {
  getDocumentMock.mockReturnValue(
    fakeLoadingTask(Promise.reject(new Error("corrupt"))),
  );
  const { container } = render(
    <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
  );

  await waitFor(() =>
    expect(container.querySelector(".preview-pdf-error")).not.toBeNull(),
  );
  expect(container.textContent).toContain("PDF 无法渲染");
});

test("shows the loading state before the document resolves", () => {
  getDocumentMock.mockReturnValue(fakeLoadingTask(new Promise(() => {})));
  const { container } = render(
    <PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />,
  );
  expect(container.querySelector('[role="status"]')?.textContent).toContain('正在打开 PDF');
});

test('a 60-page case file cannot crash React while the preview layout settles', async () => {
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function () {
    return this.classList.contains('preview-pdf-scroll') ? 1000 : 0;
  });
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(function () {
    return this.classList.contains('preview-pdf-scroll') ? 900 : 0;
  });
  getDocumentMock.mockReturnValue(fakeLoadingTask(Promise.resolve(fakeDocument(60))));
  const {container} = render(<PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ="/>);
  await waitFor(() => expect(container.querySelectorAll('.preview-pdf-page')).toHaveLength(60), {timeout:5000});
  expect(container.querySelector('.preview-pdf-error')).toBeNull();
});


test("desktop policy permits bundled PDF fonts and CMaps", async () => {
  const config = JSON.parse(await readFile(resolve(process.cwd(), "src-tauri/tauri.conf.json"), "utf8"));
  const connect = config.app.security.csp.split(";").find((rule: string) => rule.trim().startsWith("connect-src "));
  expect(connect.split(/\s+/)).toContain("'self'");
});


test("manual page navigation loads and locates a later page", async () => {
  getDocumentMock.mockReturnValue(fakeLoadingTask(Promise.resolve(fakeDocument(8))));
  const view = render(<PdfViewer source="data:application/pdf;base64,JVBERi0xLjQ=" />);
  await waitFor(() => expect(view.getByLabelText("PDF 页码")).toBeTruthy());
  fireEvent.change(view.getByLabelText("PDF 页码"), { target: { value: '8' } });
  fireEvent.click(view.getByRole('button', { name: '前往' }));
  await waitFor(() => expect(view.getByLabelText('PDF 第 8 页')).toBeTruthy());
  await waitFor(() => expect(view.getByText('已定位 PDF 第 8 页（文件实际页码）')).toBeTruthy());
});

test("reading position and zoom restore only for the same file version", async () => {
  localStorage.clear();
  getDocumentMock.mockImplementation(() => fakeLoadingTask(Promise.resolve(fakeDocument(8))));
  const source = "data:application/pdf;base64,JVBERi0xLjQ=";
  const first = render(<PdfViewer source={source} viewKey="case:file:hash-a" />);
  await waitFor(() => expect(first.getByLabelText('PDF 第 1 页')).toBeTruthy());
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  fireEvent.click(first.getByRole('button', { name: '放大 PDF' }));
  const scroll = first.container.querySelector('.preview-pdf-scroll')!;
  scroll.scrollTop = 520;
  fireEvent.scroll(scroll);
  first.unmount();
  const second = render(<PdfViewer source={source} viewKey="case:file:hash-a" />);
  await waitFor(() => expect(second.getByLabelText('PDF 缩放比例').textContent).toBe('125%'));
  await waitFor(() => expect(second.container.querySelector('.preview-pdf-scroll')!.scrollTop).toBe(520));
  second.rerender(<PdfViewer source={source} viewKey="case:file:hash-b" />);
  await waitFor(() => expect(second.getByLabelText('PDF 缩放比例').textContent).toBe('100%'));
  await waitFor(() => expect(second.container.querySelector('.preview-pdf-scroll')!.scrollTop).toBe(0));
  localStorage.clear();
});
