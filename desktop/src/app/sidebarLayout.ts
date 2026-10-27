/**
 * Sidebar geometry persistence: drag-to-resize width and collapsed state.
 * Width is clamped so the main workspace always keeps a usable share.
 */

export const SIDEBAR_WIDTH_DEFAULT = 288;
export const SIDEBAR_WIDTH_MIN = 232;
export const SIDEBAR_WIDTH_MAX = 420;
export const SIDEBAR_WIDTH_KEY = "verilex:sidebar-width:v1";
export const SIDEBAR_COLLAPSED_KEY = "verilex:sidebar-collapsed:v1";

export interface LayoutStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface SidebarLayout {
  width: number;
  collapsed: boolean;
}

export function clampSidebarWidth(px: number): number {
  if (!Number.isFinite(px)) return SIDEBAR_WIDTH_DEFAULT;
  return Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, Math.round(px)));
}

export function loadSidebarLayout(store: LayoutStore): SidebarLayout {
  let width = SIDEBAR_WIDTH_DEFAULT;
  try {
    const raw = store.getItem(SIDEBAR_WIDTH_KEY);
    if (raw !== null) width = clampSidebarWidth(Number.parseInt(raw, 10));
  } catch {
    width = SIDEBAR_WIDTH_DEFAULT;
  }
  let collapsed = false;
  try {
    collapsed = store.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
  } catch {
    collapsed = false;
  }
  return { width, collapsed };
}

export function saveSidebarWidth(px: number, store: LayoutStore): void {
  try {
    store.setItem(SIDEBAR_WIDTH_KEY, String(clampSidebarWidth(px)));
  } catch {
    // Layout persistence is best-effort only.
  }
}

export function saveSidebarCollapsed(collapsed: boolean, store: LayoutStore): void {
  try {
    store.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    // Layout persistence is best-effort only.
  }
}
