// Adapted from PurpleDirective/quillpdf-mcp, core.ts, commit 6ee740498584aa325b826490ed830b9625e71b5c.
// Copyright (c) 2026 Purple Directive. MIT; see LICENSE.txt. Only merge/split/extract/pageCount retained.
/**
 * quillpdf-mcp core — pure, local PDF operations.
 *
 * Every function takes PDF bytes in and returns PDF bytes (or a number) out.
 * Nothing here touches the filesystem or the network: the I/O layer (io.ts) and
 * the CLI / MCP front-ends are the only code that reads or writes disk. That
 * keeps the engine trivially unit-testable AND makes the privacy guarantee
 * structural — there is no code path in this module that could send a document
 * anywhere.
 *
 * Ported from the QuillPDF web engine (built on pdf-lib). The browser version
 * took `File` objects and produced object URLs; this version takes and returns
 * `Uint8Array`. The pure-pdf-lib operations port essentially unchanged.
 *
 * Canvas / rasterization operations (compress, pdf-to-image, redact, OCR) are
 * deliberately NOT here — they require a Node image backend and are slated for
 * phase 2. Shipping only fully-local, dependency-light operations keeps the
 * privacy claim honest and the install tiny.
 *
 * Note on file-size limits: the web engine capped inputs (browser memory). This
 * is a local tool operating on the caller's own files, so those artificial caps
 * are intentionally dropped — the user's machine is the real limit.
 */

import { PDFDocument } from "pdf-lib";

/**
 * Load a PDF without silently stripping encryption. If the file is password-
 * protected, throw a clear error instead of bypassing the password — matching
 * the web engine's security posture (never quietly unlock a protected doc).
 */
export async function loadPdfSafe(bytes: Uint8Array): Promise<PDFDocument> {
  try {
    return await PDFDocument.load(bytes);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (/encrypt/i.test(msg)) {
      throw new Error(
        "This PDF is password-protected. Remove the password in your PDF reader first — quillpdf will not silently strip passwords from protected documents.",
      );
    }
    throw err;
  }
}

// ── Merge ─────────────────────────────────────────────────────────────────────
/** Merge two or more PDFs into one, preserving order. */
export async function merge(inputs: Uint8Array[]): Promise<Uint8Array> {
  if (inputs.length < 2) throw new Error("Provide at least 2 PDFs to merge.");
  const out = await PDFDocument.create();
  for (const bytes of inputs) {
    const src = await loadPdfSafe(bytes);
    const pages = await out.copyPages(src, src.getPageIndices());
    pages.forEach((p) => out.addPage(p));
  }
  return out.save();
}

// ── Split / extract ─────────────────────────────────────────────────────────
/**
 * Parse a 1-based range spec like "1-3,5,7-9" into arrays of 0-based page
 * indices — one inner array per comma-separated group. Throws on out-of-bounds
 * or malformed input.
 */
export function parseRanges(input: string, totalPages: number): number[][] {
  const parts = input.split(",").map((p) => p.trim()).filter(Boolean);
  if (parts.length === 0) throw new Error("Empty range spec.");
  return parts.map((part) => {
    if (part.includes("-")) {
      const [a, b] = part.split("-").map((n) => Number(n.trim()));
      if (!Number.isInteger(a) || !Number.isInteger(b) || a < 1 || b > totalPages || a > b) {
        throw new Error(`Invalid range: "${part}" (document has ${totalPages} page(s)).`);
      }
      return Array.from({ length: b - a + 1 }, (_, i) => a + i - 1);
    }
    const n = Number(part);
    if (!Number.isInteger(n) || n < 1 || n > totalPages) {
      throw new Error(`Invalid page: "${part}" (document has ${totalPages} page(s)).`);
    }
    return [n - 1];
  });
}

/**
 * Split a PDF into multiple PDFs — one output per comma-separated range group.
 * e.g. "1-3,5" yields two PDFs: pages 1-3, and page 5.
 */
export async function splitByRanges(bytes: Uint8Array, rangeSpec: string): Promise<Uint8Array[]> {
  const src = await loadPdfSafe(bytes);
  const ranges = parseRanges(rangeSpec, src.getPageCount());
  const results: Uint8Array[] = [];
  for (const indices of ranges) {
    const doc = await PDFDocument.create();
    const pages = await doc.copyPages(src, indices);
    pages.forEach((p) => doc.addPage(p));
    results.push(await doc.save());
  }
  return results;
}

/**
 * Extract a single PDF containing exactly the given 1-based pages, in the order
 * supplied (duplicates and reordering are allowed). e.g. "3,1,1" → a 3-page PDF.
 */
export async function extractPages(bytes: Uint8Array, pageSpec: string): Promise<Uint8Array> {
  const src = await loadPdfSafe(bytes);
  const total = src.getPageCount();
  const indices = pageSpec
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => {
      const n = Number(p);
      if (!Number.isInteger(n) || n < 1 || n > total) {
        throw new Error(`Invalid page: "${p}" (document has ${total} page(s)).`);
      }
      return n - 1;
    });
  if (indices.length === 0) throw new Error("No pages specified.");
  const doc = await PDFDocument.create();
  const pages = await doc.copyPages(src, indices);
  pages.forEach((p) => doc.addPage(p));
  return doc.save();
}

// ── Page count ────────────────────────────────────────────────────────────────
export async function pageCount(bytes: Uint8Array): Promise<number> {
  const src = await loadPdfSafe(bytes);
  return src.getPageCount();
}

