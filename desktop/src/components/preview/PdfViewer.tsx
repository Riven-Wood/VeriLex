import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Minus, Plus, ScanLine } from "lucide-react";
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  RenderTask,
} from "pdfjs-dist";

import {
  PDF_FIT_WIDTH_ZOOM,
  PDF_MAX_ZOOM,
  PDF_MIN_ZOOM,
  nextFixedPdfZoom,
} from "./pdfZoom";
import { readPdfView, savePdfView } from "./pdfViewState";
import { usePdfPinchZoom } from "./usePdfPinchZoom";

/**
 * Renders a PDF page-by-page onto canvases with pdf.js, entirely inside the
 * app's WebView — WKWebView has no built-in PDF plugin, so the original bytes
 * are rasterized locally and no network request is ever made.
 *
 * The source (a blob or data URL) is converted to a `Uint8Array` before it is
 * handed to pdf.js. Feeding raw bytes via `getDocument({ data })` avoids the
 * flakiness of WebKit reading `blob:`/`data:` URLs back out of a worker.
 */

interface PdfViewerProps {
  /** Raw attachment bytes, or a data/blob URL of the PDF bytes. */
  source: Blob | string;
  /** One-based physical PDF page from a material citation. */
  page?: number;
  viewKey?: string;
}

interface PdfPageState {
  pageNumber: number;
  width: number;
  height: number;
}

const INITIAL_PAGE_COUNT = 2;
const LOAD_MORE_THRESHOLD_PX = 120;

/** Resolve raw attachment bytes or a blob/data URL to PDF bytes. */
async function sourceToBytes(source: Blob | string): Promise<Uint8Array> {
  if (source instanceof Blob) {
    return new Uint8Array(await source.arrayBuffer());
  }
  if (source.startsWith("data:")) {
    const comma = source.indexOf(",");
    const meta = source.slice(0, comma);
    const b64 = source.slice(comma + 1);
    if (meta.includes(";base64")) {
      const binary = atob(b64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
      }
      return bytes;
    }
    return new TextEncoder().encode(decodeURIComponent(b64));
  }
  const response = await fetch(source);
  if (!response.ok) {
    throw new Error(`PDF 读取失败（${response.status}）`);
  }
  return new Uint8Array(await response.arrayBuffer());
}

async function loadPdfjs() {
  // Register the worker's message handler on the main thread. pdf.js checks
  // globalThis.pdfjsWorker?.WorkerMessageHandler first and, when present, runs
  // the parser on the main thread over a loopback port — no Worker is created
  // at all, which sidesteps WKWebView's unreliable ESM-worker support. The
  // main-thread parse was verified against a real legal PDF.
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  // @ts-expect-error — legacy worker module ships without a type declaration;
  // it is only imported at runtime to register its main-thread handler.
  const workerModule = await import("pdfjs-dist/legacy/build/pdf.worker.min.mjs");
  const scope = globalThis as typeof globalThis & { pdfjsWorker?: unknown };
  scope.pdfjsWorker = workerModule;
  return pdfjs;
}

// PDF stacked-canvas structure adapted from OpenWorker RightRail.tsx
// (MIT, https://github.com/andrewyng/openworker, commit 7fc3ee68).
// VeriLex changes: Blob input, single-document reuse, WKWebView main-thread
// pdf.js loading, progressive pages, render deduplication, and zoom controls.
export function PdfViewer({ source, page, viewKey }: PdfViewerProps) {
  const [pages, setPages] = useState<PdfPageState[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedPageCount, setLoadedPageCount] = useState(1);
  const [zoomPercent, setZoomPercent] = useState(PDF_FIT_WIDTH_ZOOM);
  const canvasesRef = useRef<Array<HTMLCanvasElement | null>>([]);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const renderedRef = useRef(new Set<number>());
  const renderingRef = useRef(new Map<number, Promise<void>>());
  const renderTasksRef = useRef(new Map<number, RenderTask>());
  const documentRef = useRef<PDFDocumentProxy | null>(null);
  const pendingPageRef = useRef<number | null>(null);
  const [pageInput, setPageInput] = useState("1");
  const [navigation, setNavigation] = useState(0);
  const restoredRef = useRef(false);
  const activeViewKey = useRef<string | undefined>();
  const restoreRef = useRef(readPdfView(viewKey));
  const rememberView = useCallback(() => {
    const scroll = scrollRef.current;
    if (!scroll || !restoredRef.current || !pages || activeViewKey.current !== viewKey) return;
    savePdfView(viewKey, { zoom: zoomPercent, top: scroll.scrollTop, left: scroll.scrollLeft, loaded: loadedPageCount });
  }, [viewKey, zoomPercent, loadedPageCount, pages]);
  useEffect(() => { rememberView(); }, [rememberView]);
  const [locatedPage, setLocatedPage] = useState<number | null>(null);
  const { isPinching } = usePdfPinchZoom({
    scrollRef,
    zoomPercent,
    onZoomChange: setZoomPercent,
    resetKey: source,
    enabled: pages !== null,
  });

  const loadNextPageIfNearBottom = useCallback(
    (scroll: HTMLElement) => {
      if (pages === null || scroll.clientHeight <= 0) return;
      if (
        scroll.scrollTop + scroll.clientHeight >=
        scroll.scrollHeight - LOAD_MORE_THRESHOLD_PX
      ) {
        setLoadedPageCount((current) =>
          Math.min(pages.length, current + 1),
        );
      }
    },
    [pages],
  );

  // Load the document (on the main thread as bytes) and record page sizes.
  useEffect(() => {
    let cancelled = false;
    let loadingTask: PDFDocumentLoadingTask | null = null;
    restoredRef.current = false;
    restoreRef.current = readPdfView(viewKey);
    setPageInput("1");
    setPages(null);
    setError(null);
    setLoadedPageCount(1);
    setZoomPercent(restoreRef.current?.zoom ?? PDF_FIT_WIDTH_ZOOM);
    setLocatedPage(null);
    pendingPageRef.current = null;
    documentRef.current = null;
    renderedRef.current = new Set();
    renderingRef.current.clear();
    for (const task of renderTasksRef.current.values()) task.cancel();
    renderTasksRef.current.clear();
    (async () => {
      const bytes = await sourceToBytes(source);
      if (cancelled) return;
      const pdfjs = await loadPdfjs();
      if (cancelled) return;
      // Draw embedded glyph outlines directly. WKWebView's production CSP
      // rejects dynamically loaded PDF fonts; using paths avoids blank pages
      // without broadening the app's font or script permissions.
      loadingTask = pdfjs.getDocument({
        data: bytes,
        disableFontFace: true,
        stopAtErrors: true,
        cMapUrl: new URL("/pdfjs/cmaps/", window.location.href).href,
        cMapPacked: true,
        standardFontDataUrl: new URL("/pdfjs/standard_fonts/", window.location.href).href,
      });
      const doc = await loadingTask.promise;
      if (cancelled) return;
      documentRef.current = doc;
      const sizes: PdfPageState[] = [];
      for (let pageNumber = 1; pageNumber <= doc.numPages; pageNumber += 1) {
        const page = await doc.getPage(pageNumber);
        if (cancelled) return;
        const viewport = page.getViewport({ scale: 1 });
        sizes.push({
          pageNumber,
          width: viewport.width,
          height: viewport.height,
        });
      }
      if (cancelled) return;
      activeViewKey.current = viewKey;
      setPages(sizes);
      setLoadedPageCount(Math.min(Math.max(INITIAL_PAGE_COUNT, restoreRef.current?.loaded ?? 1), sizes.length));
    })().catch(() => {
      if (!cancelled) setError("PDF 无法渲染，文件可能已损坏。");
    });
    return () => {
      cancelled = true;
      documentRef.current = null;
      for (const task of renderTasksRef.current.values()) task.cancel();
      renderTasksRef.current.clear();
      renderingRef.current.clear();
      void loadingTask?.destroy().catch(() => undefined);
    };
  }, [source, viewKey]);


  useEffect(() => {
    if (!pages || restoredRef.current) return;
    const timer = setTimeout(() => {
      const scroll = scrollRef.current;
      if (!scroll) return;
      if (page === undefined && restoreRef.current) {
        scroll.scrollTop = restoreRef.current.top;
        scroll.scrollLeft = restoreRef.current.left;
      }
      restoredRef.current = true;
    }, 0);
    return () => clearTimeout(timer);
  }, [pages, page]);

  function goToPage() {
    const requested = Number(pageInput);
    if (!pages || !Number.isInteger(requested) || requested < 1 || requested > pages.length) return;
    pendingPageRef.current = requested;
    setLoadedPageCount(current => Math.max(current, requested));
    setNavigation(current => current + 1);
  }
  useEffect(() => {
    if (!pages || page === undefined || !Number.isInteger(page) || page < 1 || page > pages.length) return;
    pendingPageRef.current = page;
    setLoadedPageCount(current => Math.max(current, page));
  }, [pages, page]);

  useEffect(() => {
    const requested = pendingPageRef.current;
    if (!pages || requested === null || loadedPageCount < requested) return;
    const timer = setTimeout(() => {
      const scroll = scrollRef.current;
      const canvas = canvasesRef.current[requested - 1];
      if (!scroll || !canvas) return;
      // Move only this viewer: scrollIntoView can also move the workbench and
      // discussion when the rail is embedded in a narrow native window.
      const top = canvas.getBoundingClientRect().top - scroll.getBoundingClientRect().top + scroll.scrollTop;
      scroll.scrollTop = Math.max(0, top);
      pendingPageRef.current = null;
      setLocatedPage(requested);
      setPageInput(String(requested));
      rememberView();
    }, 0);
    return () => clearTimeout(timer);
  }, [pages, page, loadedPageCount, navigation, rememberView]);

  // Draw each page once it scrolls near the viewport (progressive).
  useEffect(() => {
    if (pages === null) return;
    const doc = documentRef.current;
    if (doc === null) return;
    let cancelled = false;
    (async () => {
      const pageNumbers = Array.from({length: Math.min(loadedPageCount, pages.length)}, (_, index) => index + 1);
      // A citation near the end must paint immediately, without waiting for
      // all preceding pages to rasterize first.
      const priority = pendingPageRef.current ?? page;
      if (priority && pageNumbers.includes(priority)) pageNumbers.splice(0, 0, ...pageNumbers.splice(pageNumbers.indexOf(priority), 1));
      for (const pageNumber of pageNumbers) {
        if (cancelled) return;
        if (renderedRef.current.has(pageNumber)) continue;
        const canvas = canvasesRef.current[pageNumber - 1];
        if (!canvas) continue;
        let rendering = renderingRef.current.get(pageNumber);
        if (!rendering) {
          rendering = (async () => {
            const page = await doc.getPage(pageNumber);
            if (documentRef.current !== doc) return;
            const viewport = page.getViewport({ scale: 1.5 });
            canvas.width = viewport.width;
            canvas.height = viewport.height;
            const task = page.render({
              canvas,
              canvasContext: canvas.getContext("2d")!,
              viewport,
            });
            renderTasksRef.current.set(pageNumber, task);
            try {
              await task.promise;
              if (documentRef.current === doc) {
                renderedRef.current.add(pageNumber);
              }
            } finally {
              if (renderTasksRef.current.get(pageNumber) === task) {
                renderTasksRef.current.delete(pageNumber);
              }
            }
          })();
          renderingRef.current.set(pageNumber, rendering);
          void rendering.then(
            () => {
              if (renderingRef.current.get(pageNumber) === rendering) {
                renderingRef.current.delete(pageNumber);
              }
            },
            () => {
              if (renderingRef.current.get(pageNumber) === rendering) {
                renderingRef.current.delete(pageNumber);
              }
            },
          );
        }
        await rendering;
        if (cancelled) return;
      }
    })().catch(() => {
      if (!cancelled) setError("PDF 渲染中断，请重新打开预览。");
    });
    return () => {
      cancelled = true;
    };
  }, [pages, loadedPageCount, source, page]);

  // A short document (or 75% zoom) may have no scrollbar and therefore can
  // never emit `scroll`. Check after every relevant layout and one page at a
  // time until the viewport overflows or the whole document is loaded.
  useEffect(() => {
    // Yield to browser layout between additions. A temporarily unconstrained
    // preview can otherwise synchronously add 50+ pages and crash React.
    const timer = window.setTimeout(() => {
      const scroll = scrollRef.current;
      if (scroll) loadNextPageIfNearBottom(scroll);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadedPageCount, loadNextPageIfNearBottom, zoomPercent]);

  // Re-check if resizing the preview/window removes the current overflow.
  useEffect(() => {
    const scroll = scrollRef.current;
    if (!scroll || pages === null) return;
    const checkLayout = () => loadNextPageIfNearBottom(scroll);
    window.addEventListener("resize", checkLayout);
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(checkLayout);
    observer?.observe(scroll);
    return () => {
      window.removeEventListener("resize", checkLayout);
      observer?.disconnect();
    };
  }, [loadNextPageIfNearBottom, pages]);

  if (error) {
    return (
      <div className="preview-pdf-error" role="alert">
        {error}
      </div>
    );
  }
  if (pages === null) {
    return (
      <div className="preview-pdf-pending" role="status">
        正在打开 PDF…
      </div>
    );
  }

  return (
    <div className="preview-pdf-viewer">
      <div
        className="preview-pdf-toolbar"
        role="toolbar"
        aria-label="PDF 缩放工具栏"
      >
        <button
          type="button"
          aria-label="缩小 PDF"
          disabled={zoomPercent <= PDF_MIN_ZOOM}
          onClick={() =>
            setZoomPercent((current) => nextFixedPdfZoom(current, "out"))
          }
        >
          <Minus aria-hidden="true" size={14} />
        </button>
        <span role="status" aria-label="PDF 缩放比例">
          {Math.round(zoomPercent)}%
        </span>
        <button
          type="button"
          aria-label="放大 PDF"
          disabled={zoomPercent >= PDF_MAX_ZOOM}
          onClick={() =>
            setZoomPercent((current) => nextFixedPdfZoom(current, "in"))
          }
        >
          <Plus aria-hidden="true" size={14} />
        </button>
        <button
          type="button"
          aria-label="适合宽度"
          disabled={zoomPercent === PDF_FIT_WIDTH_ZOOM}
          onClick={() => setZoomPercent(PDF_FIT_WIDTH_ZOOM)}
        >
          <ScanLine aria-hidden="true" size={14} />
          适合宽度
        </button>
        <label>页码 <input aria-label="PDF 页码" type="number" min={1} max={pages.length} value={pageInput} onChange={event => setPageInput(event.target.value)} onKeyDown={event => { if (event.key === "Enter") goToPage(); }} style={{width: 56}} /></label>
        <button type="button" onClick={goToPage} disabled={!Number.isInteger(Number(pageInput)) || Number(pageInput) < 1 || Number(pageInput) > pages.length}>前往</button>
      </div>
      {locatedPage !== null && <div className="preview-pdf-total" role="status">已定位 PDF 第 {locatedPage} 页（文件实际页码）</div>}
      {page !== undefined && (!Number.isInteger(page) || page < 1 || page > pages.length) && <div className="preview-pdf-error" role="alert">出处页码超出这份文件范围，请核对材料出处。</div>}
      <div
        className="preview-pdf-scroll"
        ref={scrollRef}
        data-pinching={isPinching ? "true" : undefined}
        onScroll={(event) => {
          rememberView();
          loadNextPageIfNearBottom(event.currentTarget);
        }}
        onWheel={(event) => {
          if (!event.ctrlKey && event.deltaY > 0) {
            loadNextPageIfNearBottom(event.currentTarget);
          }
        }}
      >
        {pages.slice(0, loadedPageCount).map((page) => (
          <div
            className="preview-pdf-page"
            key={page.pageNumber}
            style={{
              width: `${zoomPercent}%`,
              marginInline:
                zoomPercent < PDF_FIT_WIDTH_ZOOM ? "auto" : 0,
            }}
          >
            <canvas
              aria-label={`PDF 第 ${page.pageNumber} 页`}
              ref={(node) => {
                canvasesRef.current[page.pageNumber - 1] = node;
              }}
              style={{
                aspectRatio: `${page.width} / ${page.height}`,
                width: "100%",
                height: 'auto',
              }}
            />
            <span className="preview-pdf-page-number">{page.pageNumber}</span>
          </div>
        ))}
        <div className="preview-pdf-total">
          {loadedPageCount < pages.length
            ? `已加载 ${loadedPageCount} / 共 ${pages.length} 页`
            : `共 ${pages.length} 页`}
        </div>
      </div>
    </div>
  );
}
