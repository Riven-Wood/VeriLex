/**
 * Sidebar session scoping and manual ordering.
 *
 * 对话区 holds sessions without a workspace; 项目区 holds sessions bound to
 * one. Drag ordering is a frontend-only concern for now: each scope keeps an
 * ordered id list in localStorage, unknown ids (fresh sessions) stay on top,
 * and backend listing order remains the fallback.
 */

export type SessionScope = "chat" | "project";

export interface ScopedById {
  id: string;
  workspaceId: string | null;
}

export interface OrderStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const ORDER_STORAGE_PREFIX = "verilex:session-order:";

export function sessionScope(thread: ScopedById): SessionScope {
  return thread.workspaceId ? "project" : "chat";
}

export function orderStorageKey(scope: SessionScope): string {
  return `${ORDER_STORAGE_PREFIX}${scope}:v1`;
}

export function loadSessionOrder(
  scope: SessionScope,
  store: OrderStore,
): string[] {
  try {
    const raw = store.getItem(orderStorageKey(scope));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === "string");
  } catch {
    return [];
  }
}

export function saveSessionOrder(
  scope: SessionScope,
  ids: readonly string[],
  store: OrderStore,
): void {
  try {
    store.setItem(orderStorageKey(scope), JSON.stringify(ids));
  } catch {
    // Ordering is a convenience; a failing quota must never break the UI.
  }
}

/** Ordered sessions keep their saved positions; fresh sessions stay on top. */
export function applySessionOrder<T extends { id: string }>(
  items: readonly T[],
  order: readonly string[],
): T[] {
  const position = new Map(order.map((id, index) => [id, index] as const));
  return [...items].sort((a, b) => {
    const aIndex = position.get(a.id);
    const bIndex = position.get(b.id);
    if (aIndex !== undefined && bIndex !== undefined) {
      return aIndex - bIndex;
    }
    if (aIndex !== undefined) return 1;
    if (bIndex !== undefined) return -1;
    return 0;
  });
}

/**
 * Move `draggedId` to the position directly before/after `targetId`.
 * Returns null when the move would be a no-op or either id is unknown.
 */
export function moveId(
  ids: readonly string[],
  draggedId: string,
  targetId: string,
  place: "before" | "after",
): string[] | null {
  const from = ids.indexOf(draggedId);
  const to = ids.indexOf(targetId);
  if (from === -1 || to === -1 || draggedId === targetId) return null;
  let insertAt = place === "before" ? to : to + 1;
  if (from < insertAt) insertAt -= 1;
  if (insertAt === from) return null;
  const next = ids.slice();
  next.splice(from, 1);
  next.splice(insertAt, 0, draggedId);
  return next;
}
