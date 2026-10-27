export interface PdfViewState { zoom: number; top: number; left: number; loaded: number }
const storageKey = 'verilex.pdf-reading.v1';
function records(): Array<[string, PdfViewState]> {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) ?? '[]');
    return Array.isArray(value) ? value.filter(item => Array.isArray(item) && typeof item[0] === 'string' && valid(item[1])).slice(-50) : [];
  } catch { return []; }
}
function valid(value: unknown): value is PdfViewState {
  if (!value || typeof value !== 'object') return false;
  const v = value as PdfViewState;
  return Number.isFinite(v.zoom) && v.zoom >= 50 && v.zoom <= 300 &&
    Number.isFinite(v.top) && v.top >= 0 && Number.isFinite(v.left) && v.left >= 0 &&
    Number.isInteger(v.loaded) && v.loaded >= 1 && v.loaded <= 10000;
}
export function readPdfView(key?: string): PdfViewState | undefined {
  return key ? records().find(item => item[0] === key)?.[1] : undefined;
}
export function savePdfView(key: string | undefined, value: PdfViewState) {
  if (!key || !valid(value)) return;
  try { localStorage.setItem(storageKey, JSON.stringify([...records().filter(item => item[0] !== key), [key, value]].slice(-50))); } catch { /* Reading remains available when storage is full. */ }
}
