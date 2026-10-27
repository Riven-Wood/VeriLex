import * as runtimeApi from "../src/runtime/api";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { Sidebar } from "../src/components/Sidebar";
import { formatRelativeTime } from "../src/app/navigation";
import type { ThreadSummary } from "../src/app/navigation";
import { orderStorageKey } from "../src/app/sessionOrder";
import type { UpdateController } from "../src/updater/contracts";

beforeEach(() => {
  vi.stubGlobal("PointerEvent", class extends MouseEvent {
    readonly pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 0; }
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function createUpToDateUpdaterClient(): UpdateController {
  return {
    state: {
      supported: true,
      phase: "up-to-date",
      currentVersion: "0.3.0",
      update: null,
      progress: null,
      error: "",
      relaunchRequired: false,
    },
    async checkForUpdates() {},
    async installUpdate() {},
  };
}

function thread(
  id: string,
  title: string,
  overrides: Partial<ThreadSummary> = {},
): ThreadSummary {
  return {
    id,
    backendId: id,
    title,
    createdAt: "2026-08-13T00:00:00Z",
    updatedAt: "2026-08-18T00:00:00Z",
    status: "idle",
    ...overrides,
  };
}

function createOrderStore() {
  const map = new Map<string, string>();
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
    dump: () => map,
  };
}

function rowOf(name: string | RegExp): HTMLElement {
  const button = screen.getByRole("button", { name });
  return button.closest(".thread-row") as HTMLElement;
}

function rowHandleOf(name: string | RegExp): HTMLElement {
  const row = rowOf(name);
  return row.querySelector(".thread-drag-handle") as HTMLElement;
}

function renderSidebar(overrides: Record<string, unknown> = {}) {
  const orderStore = createOrderStore();
  const props = {
    threads: [
      thread("t1", "劳动合同审查"),
      thread("t2", "旧合同整理", { status: "running" }),
      thread("p1", "并购尽调项目", { workspaceId: "w1" }),
    ],
    archivedThreads: [thread("t3", "归档的会话")],
    trashedThreads: [thread("t4", "回收站的会话")],
    activeThreadId: "t1",
    updates: createUpToDateUpdaterClient(),
    onNewThread: vi.fn(),
    onSelectThread: vi.fn(),
    onOpenSettings: vi.fn(),
    onRenameThread: vi.fn(),
    onArchiveThread: vi.fn(),
    onUnarchiveThread: vi.fn(),
    onTrashThread: vi.fn(),
    onRestoreThread: vi.fn(),
    onDeleteThread: vi.fn(),
    onCreateProject: vi.fn(),
    onToggleCollapsed: vi.fn(),
    onSidebarWidthChange: vi.fn(),
    onSidebarWidthCommit: vi.fn(),
    orderStore,
    ...overrides,
  };
  render(
    <Sidebar
      {...(props as unknown as React.ComponentProps<typeof Sidebar>)}
    />,
  );
  return props;
}

test("shows chat sessions with relative time and marks the active one", () => {
  renderSidebar();

  const row = screen.getByRole("button", { name: /劳动合同审查/ });
  expect(row.getAttribute("aria-current")).toBe("true");
  expect(screen.getByRole("button", { name: /旧合同整理/ }).getAttribute("aria-current")).toBeNull();
});

test("shows a live indicator for running sessions", () => {
  renderSidebar();

  expect(screen.getByText("运行中")).toBeTruthy();
});

test("chat tab hides project sessions and the project tab lists them", () => {
  renderSidebar();

  expect(screen.queryByRole("button", { name: /并购尽调项目/ })).toBeNull();

  fireEvent.click(screen.getByRole("tab", { name: "项目" }));

  expect(screen.getByRole("button", { name: /并购尽调项目/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /劳动合同审查/ })).toBeNull();

  fireEvent.click(screen.getByRole("tab", { name: "对话" }));

  expect(screen.getByRole("button", { name: /劳动合同审查/ })).toBeTruthy();
});

test("the folder button asks the app to create a project", () => {
  const props = renderSidebar();

  fireEvent.click(screen.getByRole("button", { name: "新建项目" }));
  expect(props.onCreateProject).toHaveBeenCalledTimes(1);
});

test("lists the grouped workbench feature entries in order", () => {
  renderSidebar();

  const grid = screen.getByRole("navigation", { name: "功能入口" });
  const labels = within(grid)
    .getAllByRole("button")
    .map((button) => button.textContent);
  expect(labels).toEqual([
    "文件脱敏",
    "法律检索",
    "案例检索",
    "合同审查",
    "诉讼工作",
    "案件日程",
    "知识库",
    "定时任务",
    "能力中心",
  ]);
});

test("clicking a not-yet-built feature explains it is coming", () => {
  renderSidebar();

  fireEvent.click(screen.getByRole("button", { name: "案件日程" }));

  expect(screen.getByRole("status").textContent).toContain("案件日程");
  expect(screen.getByRole("status").textContent).toContain("准备中");
});

test("the magnifier reveals the search box and searches persisted conversations", async () => {
  vi.spyOn(runtimeApi, "searchThreads").mockResolvedValue([{thread: {id: "chat-a", title: "劳动合同审查", created_at: "2026-08-13", updated_at: "2026-08-13", status: "idle"} as never, snippet: {text: "", start: 0, end: 0}}]);
  renderSidebar();

  fireEvent.click(screen.getByRole("button", { name: "搜索会话" }));
  const search = screen.getByRole("searchbox", { name: "搜索会话" });
  fireEvent.change(search, { target: { value: "劳动" } });

  expect(await screen.findByRole("button", { name: /劳动合同审查/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /旧合同整理/ })).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "关闭搜索" }));
  expect(screen.queryByRole("searchbox", { name: "搜索会话" })).toBeNull();
  expect(screen.getByRole("button", { name: /旧合同整理/ })).toBeTruthy();
});

test("search also works inside the project tab", async () => {
  vi.spyOn(runtimeApi, "searchThreads").mockResolvedValue([]);
  renderSidebar();

  fireEvent.click(screen.getByRole("tab", { name: "项目" }));
  fireEvent.click(screen.getByRole("button", { name: "搜索会话" }));
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索会话" }), {
    target: { value: "不存在" },
  });

  expect(await screen.findByText("没有匹配的结果。")).toBeTruthy();
});

test("row menu offers rename, archive, and trash for idle sessions", () => {
  const props = renderSidebar();

  fireEvent.click(screen.getAllByRole("button", { name: "会话操作" })[0]);
  const menu = screen.getByRole("menu");
  fireEvent.click(within(menu).getByRole("menuitem", { name: "重命名" }));

  const input = screen.getByRole("textbox", { name: "会话标题" });
  fireEvent.change(input, { target: { value: "新的标题" } });
  fireEvent.keyDown(input, { key: "Enter" });

  expect(props.onRenameThread).toHaveBeenCalledWith("t1", "新的标题");
});

test("rename editing cancels with Escape and skips empty titles", () => {
  const props = renderSidebar();

  fireEvent.click(screen.getAllByRole("button", { name: "会话操作" })[0]);
  fireEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "重命名" }));

  const input = screen.getByRole("textbox", { name: "会话标题" });
  fireEvent.change(input, { target: { value: "   " } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(props.onRenameThread).not.toHaveBeenCalled();

  fireEvent.keyDown(input, { key: "Escape" });
  expect(screen.queryByRole("textbox", { name: "会话标题" })).toBeNull();
});

test("running sessions refuse archive and trash from the menu", () => {
  renderSidebar();

  const menus = screen.getAllByRole("button", { name: "会话操作" });
  fireEvent.click(menus[1]);
  const menu = screen.getByRole("menu");
  expect(within(menu).getByRole("menuitem", { name: "归档" }).hasAttribute("disabled")).toBe(true);
  expect(within(menu).getByRole("menuitem", { name: "移入回收站" }).hasAttribute("disabled")).toBe(true);
});

test("archive action moves a session into the archived section", () => {
  const props = renderSidebar();

  fireEvent.click(screen.getAllByRole("button", { name: "会话操作" })[0]);
  fireEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "归档" }));
  expect(props.onArchiveThread).toHaveBeenCalledWith("t1");
});

test("archived section lists sessions with restore and trash actions", () => {
  const props = renderSidebar();

  fireEvent.click(screen.getByRole("button", { name: /归档/ }));
  const section = screen.getByRole("region", { name: /归档/ });
  fireEvent.click(within(section).getByRole("button", { name: "恢复" }));
  expect(props.onRestoreThread).not.toHaveBeenCalled();
  expect(props.onUnarchiveThread).toHaveBeenCalledWith("t3");
  fireEvent.click(within(section).getByRole("button", { name: "移入回收站" }));
  expect(props.onTrashThread).toHaveBeenCalledWith("t3");
});

test("trash section requires a two-step confirmation to delete forever", () => {
  const props = renderSidebar();

  fireEvent.click(screen.getByRole("button", { name: /回收站/ }));
  const section = screen.getByRole("region", { name: /回收站/ });
  fireEvent.click(within(section).getByRole("button", { name: "恢复" }));
  expect(props.onRestoreThread).toHaveBeenCalledWith("t4");

  fireEvent.click(within(section).getByRole("button", { name: "彻底删除" }));
  expect(props.onDeleteThread).not.toHaveBeenCalled();
  fireEvent.click(within(section).getByRole("button", { name: "确认删除" }));
  expect(props.onDeleteThread).toHaveBeenCalledWith("t4");
});

test("empty states invite the right first action per scope", () => {
  renderSidebar({
    threads: [],
    archivedThreads: [],
    trashedThreads: [],
  });

  expect(screen.getByText("还没有会话，点「新建会话」或直接在右侧输入即可开始。")).toBeTruthy();

  fireEvent.click(screen.getByRole("tab", { name: "项目" }));

  expect(screen.getByText("还没有项目。点上方文件夹按钮，选一个文件夹即可创建。")).toBeTruthy();
});

test("pointer-dragging a chat session reorders the list and persists it", () => {
  const props = renderSidebar();

  const handle = rowHandleOf(/劳动合同审查/);
  const targetRow = rowOf(/旧合同整理/);
  // jsdom has no elementFromPoint; provide one aimed at the target row.
  document.elementFromPoint = () => targetRow;
  try {
    fireEvent.pointerDown(handle, { pointerId: 1, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 20, clientY: 100 });
    fireEvent.pointerUp(handle, { pointerId: 1 });
  } finally {
    delete document.elementFromPoint;
  }

  const buttons = screen
    .getAllByRole("button")
    .filter((button) => button.classList.contains("thread-select"))
    .map((button) => button.textContent);
  expect(buttons[0]).toContain("旧合同整理");
  expect(buttons[1]).toContain("劳动合同审查");
  expect(props.orderStore.dump().get(orderStorageKey("chat"))).toBe(
    JSON.stringify(["t2", "t1"]),
  );
});

test("pointer-dragging continues after the handle loses pointer capture", () => {
  const props = renderSidebar({
    threads: [
      thread("t1", "劳动合同审查"),
      thread("t2", "旧合同整理"),
      thread("t5", "证据目录"),
      thread("p1", "并购尽调项目", { workspaceId: "w1" }),
    ],
  });

  const handle = rowHandleOf(/劳动合同审查/);
  let pointedRow = rowOf(/旧合同整理/);
  document.elementFromPoint = () => pointedRow;
  try {
    fireEvent.pointerDown(handle, { pointerId: 1, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 20, clientY: 100 });

    // WebKit can drop capture when React moves the row in the DOM. The drag
    // session must continue from window-level events instead of ending here.
    fireEvent(handle, new Event("lostpointercapture", { bubbles: true }));
    pointedRow = rowOf(/证据目录/);
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 20, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1 });
  } finally {
    delete document.elementFromPoint;
  }

  expect(props.orderStore.dump().get(orderStorageKey("chat"))).toBe(
    JSON.stringify(["t2", "t5", "t1"]),
  );
});

test("a handle press without movement does not save an order", () => {
  const props = renderSidebar();

  fireEvent.click(screen.getByRole("tab", { name: "项目" }));
  const handle = rowHandleOf(/并购尽调项目/);
  fireEvent.pointerDown(handle, { pointerId: 1, button: 0 });
  fireEvent.pointerUp(handle, { pointerId: 1 });

  expect(props.orderStore.dump().has(orderStorageKey("chat"))).toBe(false);
});

test("pressing the reorder handle prevents native text selection", () => {
  renderSidebar();
  const handle = rowHandleOf(/劳动合同审查/);
  const list = screen.getByRole("region", { name: "会话" });
  const pointerDown = new PointerEvent("pointerdown", {
    bubbles: true,
    cancelable: true,
  });

  fireEvent(handle, pointerDown);

  expect(pointerDown.defaultPrevented).toBe(true);
  expect(list.getAttribute("data-reordering")).toBe("true");
  fireEvent.pointerUp(window);
  expect(list.getAttribute("data-reordering")).toBeNull();
});

test("double-click resizer reaches the app", () => {
  const props = renderSidebar();

  fireEvent.doubleClick(screen.getByRole("separator", { name: "调整侧栏宽度" }));
  expect(props.onSidebarWidthCommit).toHaveBeenCalledWith(288);
});

test("collapsed rail keeps new session, features, and settings reachable", () => {
  const props = renderSidebar({ collapsed: true });

  // The collapse/expand toggle moved to the app toolbar (2026-08-31); the
  // collapsed rail itself still exposes everything a keyboard user needs.
  fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
  expect(props.onNewThread).toHaveBeenCalledTimes(1);

  expect(screen.getByRole("button", { name: "知识库" }).getAttribute("aria-disabled")).toBe("true");

  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  expect(props.onOpenSettings).toHaveBeenCalledTimes(1);
});

test("relative time formatting covers the documented buckets", () => {
  const now = Date.parse("2026-08-18T12:00:00Z");
  expect(formatRelativeTime("2026-08-18T11:59:30Z", now)).toBe("刚刚");
  expect(formatRelativeTime("2026-08-18T11:30:00Z", now)).toBe("30分钟前");
  expect(formatRelativeTime("2026-08-18T08:00:00Z", now)).toBe("4小时前");
  expect(formatRelativeTime("2026-08-15T12:00:00Z", now)).toBe("3天前");
  expect(formatRelativeTime("2026-07-01T12:00:00Z", now)).toBe("7月1日");
  expect(formatRelativeTime("2025-07-01T12:00:00Z", now)).toBe("2025年7月1日");
  expect(formatRelativeTime("not-a-date", now)).toBe("");
});

for (const collapsed of [false, true]) {
  test(`case calendar opens independently (collapsed=${collapsed})`, () => {
    const onOpenCaseCalendar = vi.fn();
    renderSidebar({ collapsed, onOpenCaseCalendar });
    const entry = screen.getByRole("button", { name: "案件日程" });
    expect(entry.getAttribute("aria-disabled")).not.toBe("true");
    fireEvent.click(entry);
    expect(onOpenCaseCalendar).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("案件日程准备中")).toBeNull();
  });
  test(`plugin entry opens the real view (collapsed=${collapsed})`, () => {
    const onOpenPlugins = vi.fn();
    renderSidebar({ collapsed, onOpenPlugins });
    const entry = screen.getByRole("button", { name: "能力中心" });
    expect(entry.getAttribute("aria-disabled")).not.toBe("true");
    fireEvent.click(entry);
    expect(onOpenPlugins).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("能力中心准备中")).toBeNull();
  });
}


test.each(['chat', 'project'] as const)('one drag crosses several %s rows without moving targets under the pointer', scope => {
  const props = renderSidebar({ threads: Array.from({length: 6}, (_, i) =>
    thread(`drag-${i}`, `排序样本${i}`, scope === 'project' ? {workspaceId:'w1'} : {})) });
  if(scope === 'project') fireEvent.click(screen.getByRole('tab', {name:'项目'}));
  const handle = rowHandleOf(/排序样本5/);
  const target = rowOf(/排序样本0/);
  vi.spyOn(target, 'getBoundingClientRect').mockReturnValue({top:100,height:40,bottom:140} as DOMRect);
  document.elementFromPoint = () => target;
  try {
    fireEvent.pointerDown(handle, {pointerId:1,button:0});
    fireEvent.pointerMove(window, {pointerId:1,clientX:20,clientY:101});
    expect(screen.getAllByRole('button').filter(b=>b.classList.contains('thread-select'))[0].textContent).toContain('排序样本0');
    expect(props.orderStore.dump().has(orderStorageKey(scope))).toBe(false);
    fireEvent.pointerUp(window,{pointerId:1});
    expect(JSON.parse(props.orderStore.dump().get(orderStorageKey(scope))!)).toEqual(['drag-5','drag-0','drag-1','drag-2','drag-3','drag-4']);
  } finally { delete document.elementFromPoint; }
});

test.each(['escape','cancel','blur'])('%s cancels a pending reorder without saving', method=>{
  const props=renderSidebar();
  document.elementFromPoint=()=>rowOf(/旧合同整理/);
  try {
    fireEvent.pointerDown(rowHandleOf(/劳动合同审查/),{pointerId:1,button:0});
    fireEvent.pointerMove(window,{pointerId:1,clientX:20,clientY:100});
    if(method==='escape') fireEvent.keyDown(window,{key:'Escape'});
    else if(method==='cancel') fireEvent.pointerCancel(window,{pointerId:1});
    else fireEvent.blur(window);
    expect(props.orderStore.dump().has(orderStorageKey('chat'))).toBe(false);
    expect(screen.getByRole('region',{name:'会话'}).getAttribute('data-reordering')).toBeNull();
  } finally {delete document.elementFromPoint;}
});

test('holding the pointer at the list edge continues scrolling until release', ()=>{
  let tick!:FrameRequestCallback;
  vi.spyOn(window,'requestAnimationFrame').mockImplementation(callback=>{tick=callback;return 42;});
  const cancel=vi.spyOn(window,'cancelAnimationFrame');
  renderSidebar();
  const list=screen.getByRole('region',{name:'会话'});
  vi.spyOn(list,'getBoundingClientRect').mockReturnValue({top:100,bottom:400,left:0,right:280,height:300} as DOMRect);
  document.elementFromPoint=()=>rowOf(/旧合同整理/);
  try {
    fireEvent.pointerDown(rowHandleOf(/劳动合同审查/),{pointerId:1,button:0});
    fireEvent.pointerMove(window,{pointerId:1,clientX:20,clientY:399});
    act(()=>tick(16));const first=list.scrollTop;
    act(()=>tick(32));expect(list.scrollTop).toBeGreaterThan(first);expect(first).toBeGreaterThan(0);
    fireEvent.pointerUp(window,{pointerId:1});expect(cancel).toHaveBeenCalledWith(42);
  } finally {delete document.elementFromPoint;}
});
