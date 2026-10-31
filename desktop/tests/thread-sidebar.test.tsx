import * as runtimeApi from "../src/runtime/api";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { Sidebar } from "../src/components/Sidebar";
import { formatRelativeTime } from "../src/app/navigation";
import type { ThreadSummary } from "../src/app/navigation";
import { orderStorageKey } from "../src/app/sessionOrder";
import type { UpdateController } from "../src/updater/contracts";
import {litigationClient} from '../src/views/LitigationClient';

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

test("entering projects keeps the feature buttons available", () => {
  renderSidebar();
  expect(screen.getByRole("button", {name: "文件脱敏"})).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", {name: "项目"}));
  expect(screen.getByRole("button", {name: "文件脱敏"})).toBeTruthy();
});

test('project scope shows the selected folder tree and opens a file', async () => {
  vi.spyOn(litigationClient,'files').mockResolvedValue({entries:[{name:'材料.txt',path:'材料.txt',directory:false}],truncated:false});
  const onOpenProjectFile=vi.fn(), onProjectScope=vi.fn();
  renderSidebar({projectWorkspaces:[{id:'w1',display_name:'示例项目',root_path:'/tmp/example',allow_read:true,allow_write:true}],projectWorkspaceId:'w1',onOpenProjectFile,onProjectScope});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  expect(onProjectScope).toHaveBeenCalledWith(true);
  expect(screen.getByRole('button',{name:'示例项目'}).getAttribute('aria-current')).toBe('true');
  expect(screen.queryByLabelText('搜索项目文件')).toBeNull();
  expect(screen.queryByText('勾选文件供本次对话或事实梳理使用')).toBeNull();
  expect(screen.queryByRole('button',{name:'材料.txt'})).toBeNull();
  expect(screen.getByRole('button',{name:/并购尽调项目/})).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'项目菜单：示例项目'}));
  fireEvent.click(screen.getByRole('menuitem',{name:'打开项目文件夹'}));
  fireEvent.click(await screen.findByRole('button',{name:'材料.txt'}));
  expect(onOpenProjectFile).toHaveBeenCalledWith('材料.txt');
  fireEvent.click(screen.getByRole('button',{name:'收起项目：示例项目'}));
  expect(screen.queryByRole('button',{name:'材料.txt'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'展开项目：示例项目'}));
  expect(await screen.findByRole('button',{name:'材料.txt'})).toBeTruthy();
});

test('project conversation and new conversation actions target the correct project', () => {
  const onNewProjectThread=vi.fn(), onSelectProjectThread=vi.fn();
  renderSidebar({projectWorkspaces:[{id:'w1',display_name:'示例项目',root_path:'/a',allow_read:true,allow_write:true}],projectWorkspaceId:'w1',onNewProjectThread,onSelectProjectThread});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  fireEvent.click(screen.getByRole('button',{name:/并购尽调项目/}));
  expect(onSelectProjectThread).toHaveBeenCalledWith('p1','w1');
  fireEvent.click(screen.getByRole('button',{name:'在示例项目中新建会话'}));
  expect(onNewProjectThread).toHaveBeenCalledWith('w1');
});

test('projects are listed individually and selection is requested before showing another tree',()=>{
  const onSelectProjectWorkspace=vi.fn();
  renderSidebar({projectWorkspaces:[{id:'w1',display_name:'甲项目',root_path:'/a',allow_read:true,allow_write:true},{id:'w2',display_name:'乙项目',root_path:'/b',allow_read:true,allow_write:true}],projectWorkspaceId:null,onSelectProjectWorkspace});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  expect(screen.getByRole('button',{name:'甲项目'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'展开项目：乙项目'}));
  expect(onSelectProjectWorkspace).toHaveBeenCalledWith('w2');
  expect(screen.getByRole('button',{name:'展开项目：乙项目'}).getAttribute('aria-expanded')).toBe('false');
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
    "法律工作",
    "文件脱敏",
    "法律检索",
    "案例检索",
    "合同审查",
    "文书起草",
    "案件日程",
    "通用能力",
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

test("archive and trash are not shown in the sidebar", () => {
  renderSidebar();
  expect(screen.queryByRole("region", {name: /归档/})).toBeNull();
  expect(screen.queryByRole("region", {name: /回收站/})).toBeNull();
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

test('打开项目文件夹后分别收起功能分组，文件在会话上方并可切换项目', async () => {
  vi.spyOn(litigationClient,'files').mockResolvedValue({entries:[{name:'材料.txt',path:'材料.txt',directory:false}],truncated:false});
  const workspaces = [{id:'w1',display_name:'甲项目',root_path:'/a',allow_read:true,allow_write:true},{id:'w2',display_name:'乙项目',root_path:'/b',allow_read:true,allow_write:true}];
  const onSelectProjectWorkspace = vi.fn();
  renderSidebar({projectWorkspaces:workspaces,projectWorkspaceId:'w1',onSelectProjectWorkspace});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  fireEvent.click(screen.getByRole('button',{name:'项目菜单：甲项目'}));
  fireEvent.click(screen.getByRole('menuitem',{name:'打开项目文件夹'}));
  expect(screen.getByRole('button',{name:'法律工作'}).getAttribute('aria-expanded')).toBe('false');
  expect(screen.getByRole('button',{name:'通用能力'}).getAttribute('aria-expanded')).toBe('false');
  expect(screen.queryByRole('button',{name:'乙项目'})).toBeNull();
  expect((await screen.findByRole('button',{name:'材料.txt'})).compareDocumentPosition(screen.getByRole('button',{name:/并购尽调项目/})) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'切换项目 展开'}));
  fireEvent.click(screen.getByRole('button',{name:'乙项目'}));
  expect(onSelectProjectWorkspace).toHaveBeenCalledWith('w2');
  fireEvent.click(screen.getByRole('button',{name:'法律工作'}));
  expect(screen.getByRole('button',{name:'文件脱敏'})).toBeTruthy();
  expect(screen.queryByRole('button',{name:'知识库'})).toBeNull();
});

test('项目菜单的置顶、分区、编辑、归档、移除和恢复可操作', async () => {
  localStorage.removeItem('verilex:project-sidebar:v1');
  const workspace={id:'w1',display_name:'甲项目',root_path:'/a',allow_read:true,allow_write:true};
  const rename=vi.spyOn(runtimeApi,'renameWorkspace').mockResolvedValue({...workspace,display_name:'新名称'});
  const archive=vi.fn().mockResolvedValue(undefined), renamed=vi.fn(), removed=vi.fn().mockResolvedValue(undefined);
  renderSidebar({projectWorkspaces:[workspace],projectWorkspaceId:'w1',onArchiveProjectThreads:archive,onRenameProject:renamed,onRemoveProject:removed});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  const menu=()=>fireEvent.click(screen.getByRole('button',{name:'项目菜单：甲项目'}));
  menu();fireEvent.click(screen.getByRole('menuitem',{name:'置顶'}));
  expect(screen.getByLabelText('已置顶')).toBeTruthy();
  menu();fireEvent.click(screen.getByRole('menuitem',{name:'分区'}));
  fireEvent.change(screen.getByRole('textbox',{name:'分区名称'}),{target:{value:'案件'}});
  fireEvent.click(screen.getByRole('button',{name:'确定'}));
  menu();fireEvent.click(screen.getByRole('menuitem',{name:'取消置顶'}));
  expect(screen.getByText('案件')).toBeTruthy();
  menu();fireEvent.click(screen.getByRole('menuitem',{name:'编辑项目'}));
  fireEvent.change(screen.getByRole('textbox',{name:'项目名称'}),{target:{value:'新名称'}});
  fireEvent.click(screen.getByRole('button',{name:'确定'}));
  await vi.waitFor(()=>expect(renamed).toHaveBeenCalledWith({...workspace,display_name:'新名称'}));
  expect(rename).toHaveBeenCalledWith('w1','新名称');
  menu();fireEvent.click(screen.getByRole('menuitem',{name:'归档项目会话'}));
  fireEvent.click(screen.getByRole('button',{name:'确定'}));
  await vi.waitFor(()=>expect(archive).toHaveBeenCalledWith('w1'));
  menu();fireEvent.click(screen.getByRole('menuitem',{name:'移除项目'}));
  fireEvent.click(screen.getByRole('button',{name:'确定'}));
  await vi.waitFor(()=>expect(removed).toHaveBeenCalledWith('w1'));
  expect(screen.queryByText('已移除的项目')).toBeNull();
  localStorage.removeItem('verilex:project-sidebar:v1');
});

const projectSortFixtures = [
  {id:'w1',display_name:'甲项目',root_path:'/a',allow_read:true,allow_write:true},
  {id:'w2',display_name:'乙项目',root_path:'/b',allow_read:true,allow_write:true},
  {id:'w3',display_name:'丙项目',root_path:'/c',allow_read:true,allow_write:true},
];

test('移除项目失败时保留项目并显示原因',async()=>{
  localStorage.removeItem('verilex:project-sidebar:v1');
  const removed=vi.fn().mockRejectedValue(new Error('项目仍有任务运行'));
  renderSidebar({projectWorkspaces:[projectSortFixtures[0]],projectWorkspaceId:null,onRemoveProject:removed});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  fireEvent.click(screen.getByRole('button',{name:'项目菜单：甲项目'}));
  fireEvent.click(screen.getByRole('menuitem',{name:'移除项目'}));
  fireEvent.click(screen.getByRole('button',{name:'确定'}));
  expect(await screen.findAllByText('项目仍有任务运行')).not.toHaveLength(0);
  expect(screen.getByRole('button',{name:'甲项目'})).toBeTruthy();
});
function projectNames() {
  return Array.from(document.querySelectorAll('.project-sidebar-name')).map(node=>node.textContent);
}
function dragProject(source:string, target:string) {
  const button=screen.getByRole('button',{name:new RegExp(`^${source}`)});
  const row=screen.getByRole('button',{name:new RegExp(`^${target}`)}).closest('.project-sidebar-row') as HTMLElement;
  document.elementFromPoint=()=>row;
  try {
    fireEvent.pointerDown(button,{pointerId:1,button:0,clientX:0,clientY:0});
    fireEvent.pointerMove(window,{pointerId:1,clientX:20,clientY:100});
    fireEvent.pointerUp(window,{pointerId:1,clientX:20,clientY:100});
  } finally {delete document.elementFromPoint;}
}

test('拖动项目名称可排序，重开侧栏后保持，拖动不会打开项目',()=>{
  localStorage.removeItem('verilex:project-sidebar:v1');
  const select=vi.fn();
  renderSidebar({projectWorkspaces:projectSortFixtures,projectWorkspaceId:null,onSelectProjectWorkspace:select});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  dragProject('甲项目','乙项目');
  expect(projectNames()).toEqual(['乙项目','甲项目','丙项目']);
  fireEvent.click(screen.getByRole('button',{name:'甲项目'}));
  expect(select).not.toHaveBeenCalled();
  cleanup();
  renderSidebar({projectWorkspaces:projectSortFixtures,projectWorkspaceId:null,onSelectProjectWorkspace:select});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  expect(projectNames()).toEqual(['乙项目','甲项目','丙项目']);
  fireEvent.click(screen.getByRole('button',{name:'甲项目'}));
  expect(select).toHaveBeenCalledWith('w1');
  localStorage.removeItem('verilex:project-sidebar:v1');
});

test('项目只在同一分区内调整顺序，取消拖动不会保存',()=>{
  localStorage.setItem('verilex:project-sidebar:v1',JSON.stringify({pinned:[],hidden:[],groups:{w1:'一组',w2:'一组',w3:'二组'}}));
  renderSidebar({projectWorkspaces:projectSortFixtures,projectWorkspaceId:null});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  dragProject('甲项目','丙项目');
  expect(projectNames()).toEqual(['甲项目','乙项目','丙项目']);
  const button=screen.getByRole('button',{name:'甲项目'}), target=screen.getByRole('button',{name:'乙项目'}).closest('.project-sidebar-row') as HTMLElement;
  document.elementFromPoint=()=>target;
  try {fireEvent.pointerDown(button,{pointerId:2,button:0});fireEvent.pointerMove(window,{pointerId:2,clientX:20,clientY:100});fireEvent.keyDown(window,{key:'Escape'});} finally {delete document.elementFromPoint;}
  expect(projectNames()).toEqual(['甲项目','乙项目','丙项目']);
  dragProject('甲项目','乙项目');
  expect(projectNames()).toEqual(['乙项目','甲项目','丙项目']);
  localStorage.removeItem('verilex:project-sidebar:v1');
});

test('分区可拖动排序并重命名，同时保留隐藏项目的分区', async()=>{
  localStorage.setItem('verilex:project-sidebar:v1',JSON.stringify({pinned:[],hidden:['hidden'],groups:{w1:'一组',w2:'二组',w3:'二组',hidden:'一组'}}));
  renderSidebar({projectWorkspaces:projectSortFixtures,projectWorkspaceId:null});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  const source=screen.getByText('一组'), target=screen.getByText('二组').closest('[data-project-group]') as HTMLElement;
  document.elementFromPoint=()=>target;
  try {fireEvent.pointerDown(source,{pointerId:3,button:0});fireEvent.pointerMove(window,{pointerId:3,clientX:20,clientY:100});fireEvent.pointerUp(window,{pointerId:3});} finally {delete document.elementFromPoint;}
  expect(Array.from(document.querySelectorAll('[data-project-group]')).map(node=>node.getAttribute('data-project-group'))).toEqual(['group:二组','group:一组']);
  fireEvent.click(screen.getByRole('button',{name:'编辑分区：一组'}));
  fireEvent.change(screen.getByRole('textbox',{name:'分区名称'}),{target:{value:'改名分区'}});
  fireEvent.click(screen.getByRole('button',{name:'确定'}));
  await vi.waitFor(()=>expect(screen.getByRole('button',{name:'编辑分区：改名分区'})).toBeTruthy());
  const saved=JSON.parse(localStorage.getItem('verilex:project-sidebar:v1')!);
  expect(saved.groups.hidden).toBe('改名分区');
  expect(saved.groupOrder).toEqual(['group:二组','group:改名分区']);
  cleanup();
  renderSidebar({projectWorkspaces:projectSortFixtures,projectWorkspaceId:null});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  expect(Array.from(document.querySelectorAll('[data-project-group]')).map(node=>node.getAttribute('data-project-group'))).toEqual(['group:二组','group:改名分区']);
  localStorage.removeItem('verilex:project-sidebar:v1');
});

test('项目文件夹的切换菜单可以拖动项目而不触发切换',()=>{
  localStorage.removeItem('verilex:project-sidebar:v1');
  const select=vi.fn();
  vi.spyOn(litigationClient,'files').mockResolvedValue({entries:[],truncated:false});
  renderSidebar({projectWorkspaces:projectSortFixtures,projectWorkspaceId:'w1',onSelectProjectWorkspace:select});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  fireEvent.click(screen.getByRole('button',{name:'项目菜单：甲项目'}));
  fireEvent.click(screen.getByRole('menuitem',{name:'打开项目文件夹'}));
  fireEvent.click(screen.getByRole('button',{name:'切换项目 展开'}));
  const source=screen.getByRole('button',{name:'乙项目'}), target=screen.getByRole('button',{name:'丙项目'});
  document.elementFromPoint=()=>target;
  try {fireEvent.pointerDown(source,{pointerId:4,button:0});fireEvent.pointerMove(window,{pointerId:4,clientX:10,clientY:50});fireEvent.pointerUp(window,{pointerId:4});} finally {delete document.elementFromPoint;}
  expect(Array.from(document.querySelectorAll('.project-switcher-list button')).map(node=>node.textContent)).toEqual(['丙项目','乙项目']);
  fireEvent.click(screen.getByRole('button',{name:'乙项目'}));
  expect(select).not.toHaveBeenCalled();
  localStorage.removeItem('verilex:project-sidebar:v1');
});

test('置顶项目可彼此调整但不会拖出置顶，分区不允许改为已有名称',()=>{
  localStorage.setItem('verilex:project-sidebar:v1',JSON.stringify({pinned:['w1','w2'],hidden:[],groups:{w1:'第一组',w2:'第一组',w3:'第二组'}}));
  renderSidebar({projectWorkspaces:projectSortFixtures,projectWorkspaceId:null});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  dragProject('甲项目','丙项目');
  expect(projectNames()).toEqual(['甲项目','乙项目','丙项目']);
  dragProject('甲项目','乙项目');
  expect(projectNames()).toEqual(['乙项目','甲项目','丙项目']);
  fireEvent.click(screen.getByRole('button',{name:'编辑分区：第二组'}));
  fireEvent.change(screen.getByRole('textbox',{name:'分区名称'}),{target:{value:'第一组'}});
  fireEvent.click(screen.getByRole('button',{name:'确定'}));
  expect(screen.getAllByRole('alert').some(node=>node.textContent?.includes('已有同名分区'))).toBe(true);
  expect(screen.getByRole('dialog',{name:'编辑分区'})).toBeTruthy();
  localStorage.removeItem('verilex:project-sidebar:v1');
});

test('项目菜单在窗口底部向上显示，并随窗口大小和滚动重新定位', () => {
  const workspace={id:'w-menu',display_name:'菜单测试项目',root_path:'/tmp/menu',allow_read:true,allow_write:true};
  let buttonTop=650;
  const rect=(left:number,top:number,width:number,height:number)=>({left,top,width,height,right:left+width,bottom:top+height,x:left,y:top,toJSON:()=>({})}) as DOMRect;
  vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(function(this:HTMLElement){
    if(this.classList.contains('project-menu'))return rect(0,0,180,240);
    if(this.classList.contains('project-sidebar-files'))return rect(0,0,300,768);
    if(this.getAttribute('aria-label')==='项目菜单：菜单测试项目')return rect(250,buttonTop,26,26);
    return rect(0,0,0,0);
  });
  renderSidebar({projectWorkspaces:[workspace],projectWorkspaceId:null});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  fireEvent.click(screen.getByRole('button',{name:'项目菜单：菜单测试项目'}));
  const menu=screen.getByRole('menu',{name:'项目操作菜单'});
  expect(menu.parentElement).toBe(document.body);
  expect(menu.style.top).toBe('406px');
  expect(menu.style.left).toBe('96px');
  expect(within(menu).getByRole('menuitem',{name:'移除项目'})).toBeTruthy();
  buttonTop=20;
  fireEvent.scroll(screen.getByRole('region',{name:'项目'}));
  expect(menu.style.top).toBe('50px');
  fireEvent.keyDown(window,{key:'Escape'});
  expect(screen.queryByRole('menu',{name:'项目操作菜单'})).toBeNull();
});

test('会话菜单在窗口底部完整显示，并随滚动调整位置', () => {
  let buttonTop = 650;
  const rect = (left:number, top:number, width:number, height:number) => ({left, top, width, height, right:left + width, bottom:top + height, x:left, y:top, toJSON:()=>({})}) as DOMRect;
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function(this:HTMLElement) {
    if (this.classList.contains('thread-menu')) return rect(0, 0, 160, 120);
    if (this.classList.contains('thread-list')) return rect(0, 0, 300, 768);
    if (this.getAttribute('aria-label') === '会话操作') return rect(250, buttonTop, 26, 26);
    return rect(0, 0, 0, 0);
  });
  renderSidebar();
  fireEvent.click(screen.getAllByRole('button', {name:'会话操作'})[0]);
  const menu = screen.getByRole('menu', {name:'会话操作菜单'});
  expect(menu.parentElement).toBe(document.body);
  expect(menu.style.top).toBe('526px');
  expect(within(menu).getByRole('menuitem', {name:'移入回收站'})).toBeTruthy();
  buttonTop = 20;
  fireEvent.scroll(window);
  expect(menu.style.top).toBe('50px');
});

test('项目滚动到可视区域之外时关闭菜单', () => {
  const workspace={id:'w-menu',display_name:'菜单测试项目',root_path:'/tmp/menu',allow_read:true,allow_write:true};
  let buttonTop=50;
  const rect=(top:number,height:number)=>({left:0,top,width:200,height,right:200,bottom:top+height,x:0,y:top,toJSON:()=>({})}) as DOMRect;
  vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(function(this:HTMLElement){
    if(this.classList.contains('project-menu'))return rect(0,200);
    if(this.classList.contains('project-sidebar-files'))return rect(40,400);
    if(this.getAttribute('aria-label')==='项目菜单：菜单测试项目')return rect(buttonTop,26);
    return rect(0,0);
  });
  renderSidebar({projectWorkspaces:[workspace],projectWorkspaceId:null});
  fireEvent.click(screen.getByRole('tab',{name:'项目'}));
  fireEvent.click(screen.getByRole('button',{name:'项目菜单：菜单测试项目'}));
  expect(screen.getByRole('menu',{name:'项目操作菜单'})).toBeTruthy();
  buttonTop=500;
  fireEvent.scroll(screen.getByRole('region',{name:'项目'}));
  expect(screen.queryByRole('menu',{name:'项目操作菜单'})).toBeNull();
});
