import { describe, expect, test } from "vitest";

import {
  applySessionOrder,
  loadSessionOrder,
  moveId,
  orderStorageKey,
  saveSessionOrder,
  sessionScope,
} from "../src/app/sessionOrder";
import {
  clampSidebarWidth,
  loadSidebarLayout,
  saveSidebarCollapsed,
  saveSidebarWidth,
  SIDEBAR_COLLAPSED_KEY,
  SIDEBAR_WIDTH_KEY,
} from "../src/app/sidebarLayout";

function createStore(initial: Record<string, string> = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
    removeItem: (key: string) => {
      map.delete(key);
    },
  };
}

describe("sessionScope", () => {
  test("workspace-bound sessions are projects, the rest are chats", () => {
    expect(sessionScope({ id: "a", workspaceId: null })).toBe("chat");
    expect(sessionScope({ id: "b", workspaceId: "w1" })).toBe("project");
  });
});

describe("applySessionOrder", () => {
  test("unknown sessions stay on top, ordered sessions follow saved order", () => {
    const items = [
      { id: "kept-a" },
      { id: "fresh-1" },
      { id: "kept-b" },
      { id: "fresh-2" },
    ];
    const ordered = applySessionOrder(items, ["kept-b", "kept-a"]);
    expect(ordered.map((item) => item.id)).toEqual([
      "fresh-1",
      "fresh-2",
      "kept-b",
      "kept-a",
    ]);
  });

  test("without saved order the incoming order is preserved", () => {
    const items = [{ id: "a" }, { id: "b" }];
    expect(applySessionOrder(items, []).map((item) => item.id)).toEqual(["a", "b"]);
  });

  test("ids missing from items are ignored", () => {
    const items = [{ id: "a" }];
    expect(applySessionOrder(items, ["ghost", "a"])).toEqual([{ id: "a" }]);
  });
});

describe("moveId", () => {
  test("moves a drag before the target", () => {
    expect(moveId(["a", "b", "c"], "a", "c", "before")).toEqual(["b", "a", "c"]);
  });

  test("moves a drag after the target", () => {
    expect(moveId(["a", "b", "c"], "a", "c", "after")).toEqual(["b", "c", "a"]);
    expect(moveId(["a", "b", "c"], "c", "a", "after")).toEqual(["a", "c", "b"]);
  });

  test("returns null for no-ops and unknown ids", () => {
    expect(moveId(["a", "b"], "a", "a", "after")).toBeNull();
    expect(moveId(["a", "b"], "b", "a", "after")).toBeNull();
    expect(moveId(["a", "b"], "b", "b", "before")).toBeNull();
    expect(moveId(["a"], "x", "a", "before")).toBeNull();
    expect(moveId(["a"], "a", "x", "before")).toBeNull();
  });
});

describe("order persistence", () => {
  test("scopes save and load through their own keys", () => {
    const store = createStore();
    expect(orderStorageKey("chat")).not.toBe(orderStorageKey("project"));

    saveSessionOrder("chat", ["a", "b"], store);
    saveSessionOrder("project", ["p"], store);

    expect(loadSessionOrder("chat", store)).toEqual(["a", "b"]);
    expect(loadSessionOrder("project", store)).toEqual(["p"]);
  });

  test("corrupt or non-list payloads fall back to an empty order", () => {
    expect(loadSessionOrder("chat", createStore({
      [orderStorageKey("chat")]: "{not json",
    }))).toEqual([]);
    expect(loadSessionOrder("chat", createStore({
      [orderStorageKey("chat")]: "{\"a\":1}",
    }))).toEqual([]);
    expect(loadSessionOrder("chat", createStore({
      [orderStorageKey("chat")]: "[1,null,\"ok\"]",
    }))).toEqual(["ok"]);
  });
});

describe("sidebar layout persistence", () => {
  test("width is clamped to the usable band", () => {
    expect(clampSidebarWidth(80)).toBe(232);
    expect(clampSidebarWidth(6000)).toBe(420);
    expect(clampSidebarWidth(Number.NaN)).toBe(288);
    expect(clampSidebarWidth(300.6)).toBe(301);
  });

  test("layout round-trips through storage", () => {
    const store = createStore({
      [SIDEBAR_WIDTH_KEY]: "9999",
      [SIDEBAR_COLLAPSED_KEY]: "1",
    });

    const layout = loadSidebarLayout(store);
    expect(layout.width).toBe(420);
    expect(layout.collapsed).toBe(true);

    saveSidebarWidth(260, store);
    saveSidebarCollapsed(false, store);
    const next = loadSidebarLayout(store);
    expect(next.width).toBe(260);
    expect(next.collapsed).toBe(false);
  });

  test("missing storage yields the default layout", () => {
    const layout = loadSidebarLayout(createStore());
    expect(layout).toEqual({ width: 288, collapsed: false });
  });
});
