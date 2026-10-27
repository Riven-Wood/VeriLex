import { searchThreads, type ThreadSearchResult } from "../runtime/api";
import {
  Archive,
  ArchiveRestore,
  CalendarClock,
  CalendarDays,
  FilePenLine,
  FileSearch,
  FolderPlus,
  Gavel,
  GripVertical,
  LibraryBig,
  MessageSquarePlus,
  MoreVertical,
  Pencil,
  Puzzle,
  RotateCcw,
  Scale,
  Search,
  Settings,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  formatRelativeTime,
  type ThreadSummary,
} from "../app/navigation";
import {
  applySessionOrder,
  loadSessionOrder,
  moveId,
  saveSessionOrder,
  type OrderStore,
  type SessionScope,
  sessionScope,
} from "../app/sessionOrder";
import { clampSidebarWidth } from "../app/sidebarLayout";
import type { UpdateController } from "../updater/contracts";
import { UpdatePanel } from "./UpdatePanel";

interface SidebarProps {
  searchRequest?: number;
  threads?: readonly ThreadSummary[];
  archivedThreads?: readonly ThreadSummary[];
  trashedThreads?: readonly ThreadSummary[];
  activeThreadId: string | null;
  updates: UpdateController;
  onNewThread: () => void;
  onSelectThread: (threadId: string) => void;
  onOpenSettings?: () => void;
  onRenameThread?: (threadId: string, title: string) => void;
  onArchiveThread?: (threadId: string) => void;
  onUnarchiveThread?: (threadId: string) => void;
  onTrashThread?: (threadId: string) => void;
  onRestoreThread?: (threadId: string) => void;
  onDeleteThread?: (threadId: string) => void;
  onCreateProject?: () => void;
  onOpenLegalSearch?: () => void;
  onOpenRedaction?: () => void;
  onOpenKnowledge?: () => void;
  onOpenCaseSearch?: () => void;
  onOpenLitigation?: () => void;
  onOpenContractReview?: () => void;
  onOpenScheduled?: () => void;
  onOpenCaseCalendar?: () => void;
  onOpenPlugins?: () => void;
  collapsed?: boolean;
  onSidebarWidthChange?: (width: number) => void;
  onSidebarWidthCommit?: (width: number) => void;
  orderStore?: OrderStore;
}

/** Feature entries below 新建会话; functionality lands incrementally. */
const FEATURE_ENTRIES = [
  { id: "redact", label: "文件脱敏", icon: ShieldCheck, group: "法律工作" },
  { id: "legal-search", label: "法律检索", icon: Scale, group: "法律工作" },
  { id: "case-search", label: "案例检索", icon: Gavel, group: "法律工作" },
  { id: "contract-review", label: "合同审查", icon: FileSearch, group: "法律工作" },
  { id: "litigation", label: "诉讼工作", icon: FilePenLine, group: "法律工作" },
  { id: "case-calendar", label: "案件日程", icon: CalendarDays, group: "法律工作" },
  { id: "knowledge", label: "知识库", icon: LibraryBig, group: "通用能力" },
  { id: "scheduled", label: "定时任务", icon: CalendarClock, group: "通用能力" },
  { id: "plugins", label: "能力中心", icon: Puzzle, group: "通用能力" },
] as const;

const ACTIVE_STATUSES = new Set(["running", "awaiting_approval"]);

const NO_THREADS: readonly ThreadSummary[] = [];

function StatusChip({ status }: { status: string }) {
  if (status === "running") {
    return (
      <span className="thread-status thread-status-running">
        <span className="status-dot" aria-hidden="true" />
        运行中
      </span>
    );
  }
  if (status === "awaiting_approval") {
    return (
      <span className="thread-status thread-status-approval">待审批</span>
    );
  }
  return null;
}

function memoryStore(): OrderStore {
  return {
    getItem: () => null,
    setItem: () => undefined,
  };
}

const documentStore: OrderStore = typeof window !== "undefined" && window.localStorage
  ? {
      getItem: (key) => window.localStorage.getItem(key),
      setItem: (key, value) => window.localStorage.setItem(key, value),
    }
  : memoryStore();

export function Sidebar({
  searchRequest = 0,
  threads = NO_THREADS,
  archivedThreads = NO_THREADS,
  trashedThreads = NO_THREADS,
  activeThreadId,
  updates,
  onNewThread,
  onSelectThread,
  onOpenSettings,
  onRenameThread,
  onArchiveThread,
  onUnarchiveThread,
  onTrashThread,
  onRestoreThread,
  onDeleteThread,
  onCreateProject,
  onOpenLegalSearch,
  onOpenRedaction,
  onOpenKnowledge,
  onOpenCaseSearch,
  onOpenLitigation,
  onOpenContractReview,
  onOpenScheduled,
  onOpenCaseCalendar,
  onOpenPlugins,
  collapsed = false,
  onSidebarWidthChange,
  onSidebarWidthCommit,
  orderStore = documentStore,
}: SidebarProps) {
  const [scope, setScope] = useState<SessionScope>("chat");
  const [searchOpen, setSearchOpen] = useState(false);
  const searchInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!searchRequest) return;
    setSearchOpen(true); setScope('chat'); setShowArchived(false); setShowTrash(false);
    const frame=requestAnimationFrame(() => searchInput.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [searchRequest]);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<ThreadSearchResult[]>([]);
  const [searchError, setSearchError] = useState("");
  const [searchLoading, setSearchLoading] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const q = query.trim();
    setSearchResults([]); setSearchError(""); setSearchLoading(!!q);
    if (!q) return;
    const timer = setTimeout(() => {
      searchThreads(q, controller.signal).then(result => {
        if (!controller.signal.aborted) setSearchResults(result);
      }).catch(() => {if (!controller.signal.aborted) setSearchError("搜索失败，请修改关键词重试。");})
        .finally(() => {if (!controller.signal.aborted) setSearchLoading(false);});
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query]);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [showTrash, setShowTrash] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [featureNotice, setFeatureNotice] = useState("");
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dropEdge, setDropEdge] = useState<{ id: string; place: "before" | "after" } | null>(null);
  const [resizing, setResizing] = useState(false);
  const [orders, setOrders] = useState<Record<SessionScope, string[]>>(() => ({
    chat: loadSessionOrder("chat", orderStore),
    project: loadSessionOrder("project", orderStore),
  }));

  const noticeTimerRef = useRef<number | null>(null);
  useEffect(() => {
    return () => {
      if (noticeTimerRef.current !== null) window.clearTimeout(noticeTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!menuFor) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuFor(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuFor]);

  const normalizedQuery = query.trim();
  const scopedThreads = useMemo(() => {
    const base = threads.filter((thread) => sessionScope(thread) === scope);
    return applySessionOrder(base, orders[scope]);
  }, [threads, scope, orders]);
  const visibleThreads = normalizedQuery
    ? searchResults.map(({thread: t}) => ({id: t.id, backendId: t.id, title: t.title, providerId: t.provider_id, model: t.model, workspaceId: t.workspace_id, createdAt: t.created_at, updatedAt: t.updated_at, status: t.status, isDraft: t.is_draft, reasoningEffort: t.reasoning_effort, scene: t.scene, legalChannel: t.legal_channel, legalSkills: t.legal_skills}))
    : scopedThreads;

  function closeMenu() {
    setMenuFor(null);
  }

  function closeSearch() {
    setSearchOpen(false);
    setQuery("");
  }

  function announceFeature(label: string) {
    setFeatureNotice(`「${label}」还在准备中，上线后会在这里和你见面。`);
    if (noticeTimerRef.current !== null) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => setFeatureNotice(""), 3600);
  }

  function beginRename(thread: ThreadSummary) {
    setMenuFor(null);
    setEditingId(thread.id);
    setEditValue(thread.title);
  }

  function commitRename(threadId: string) {
    const next = editValue.trim();
    setEditingId(null);
    if (next) onRenameThread?.(threadId, next);
  }

  // Pointer-based reordering: WKWebView never delivers HTML5 drag events for
  // in-page drags (Tauri's native file-drop handler owns the drag session), so
  // the handle drives the reorder with plain pointer events, which are always
  // delivered.
  const threadListRef = useRef<HTMLElement>(null);
  const pointerDragRef = useRef<{
    id: string;
    pointerId: number;
    edge: { id: string; place: "before" | "after" } | null;
    cleanup: () => void;
  } | null>(null);

  useEffect(() => {
    return () => {
      pointerDragRef.current?.cleanup();
      pointerDragRef.current = null;
    };
  }, [scope, normalizedQuery]);

  function handlePointerDown(event: React.PointerEvent, threadId: string) {
    if (normalizedQuery || (event.button !== undefined && event.button !== 0)) return;
    event.preventDefault();
    pointerDragRef.current?.cleanup();
    let x = event.clientX;
    let y = event.clientY;
    let frame = 0;
    let previousTime = 0;
    let moved = false;
    const tick = (time: number) => {
      const list = threadListRef.current;
      if (!pointerDragRef.current || !list) return;
      const bounds = list.getBoundingClientRect();
      const elapsed = previousTime ? Math.min(time - previousTime, 32) : 16;
      previousTime = time;
      if (moved && bounds.height > 0 && x >= bounds.left && x <= bounds.right) {
        const edgeSize = Math.min(48, bounds.height / 3);
        const speed = y < bounds.top + edgeSize
          ? -Math.min(1, (bounds.top + edgeSize - y) / edgeSize)
          : y > bounds.bottom - edgeSize
            ? Math.min(1, (y - bounds.bottom + edgeSize) / edgeSize) : 0;
        if (speed) {
          list.scrollTop += speed * elapsed * 0.6;
          continuePointerDrag(x, Math.max(bounds.top + 1, Math.min(bounds.bottom - 1, y)));
        }
      }
      frame = window.requestAnimationFrame(tick);
    };
    const onMove = (moveEvent: PointerEvent) => {
      if (moveEvent.pointerId !== pointerDragRef.current?.pointerId) return;
      moved = true;
      x = moveEvent.clientX;
      y = moveEvent.clientY;
      continuePointerDrag(x, y);
    };
    const onFinish = (finishEvent: PointerEvent) => {
      if (finishEvent.pointerId !== pointerDragRef.current?.pointerId) return;
      finishPointerDrag(finishEvent.type === "pointercancel");
    };
    const onKey = (keyEvent: KeyboardEvent) => {
      if (keyEvent.key === "Escape") { keyEvent.preventDefault(); finishPointerDrag(true); }
    };
    const onBlur = () => finishPointerDrag(true);
    const cleanup = () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onFinish);
      window.removeEventListener("pointercancel", onFinish);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("blur", onBlur);
      setDraggingId(null);
      setDropEdge(null);
    };
    pointerDragRef.current = { id: threadId, pointerId: event.pointerId, edge: null, cleanup };
    setDraggingId(threadId);
    setDropEdge(null);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onFinish);
    window.addEventListener("pointercancel", onFinish);
    window.addEventListener("keydown", onKey);
    window.addEventListener("blur", onBlur);
    frame = window.requestAnimationFrame(tick);
  }

  function continuePointerDrag(clientX: number, clientY: number) {
    const state = pointerDragRef.current;
    const list = threadListRef.current;
    if (!state || !list || normalizedQuery) return;
    const element = typeof document.elementFromPoint === "function"
      ? document.elementFromPoint(clientX, clientY) : null;
    let row = element?.closest?.(".thread-row") as HTMLElement | null;
    // Gaps and the empty area below the last row are valid drop positions too.
    if (!row && element && list.contains(element)) {
      const rows = Array.from(list.querySelectorAll<HTMLElement>(".thread-row"));
      row = rows.find(item => item.getBoundingClientRect().bottom > clientY) ?? rows.at(-1) ?? null;
    }
    const rowId = row && list.contains(row) ? row.getAttribute("data-thread-id") : null;
    if (!rowId || rowId === state.id) {
      state.edge = null;
      setDropEdge(null);
      return;
    }
    const bounds = row!.getBoundingClientRect();
    const place = clientY < bounds.top + bounds.height / 2 ? "before" : "after";
    if (state.edge?.id === rowId && state.edge.place === place) return;
    state.edge = { id: rowId, place };
    setDropEdge(state.edge);
  }

  function finishPointerDrag(cancelled = false) {
    const state = pointerDragRef.current;
    if (!state) return;
    state.cleanup();
    pointerDragRef.current = null;
    if (!cancelled && state.edge) {
      const ids = scopedThreads.map(thread => thread.id);
      const next = moveId(ids, state.id, state.edge.id, state.edge.place);
      if (next) {
        setOrders(prev => ({ ...prev, [scope]: next }));
        saveSessionOrder(scope, next, orderStore);
      }
    }
  }

  function startResize(event: React.MouseEvent) {
    if (collapsed) return;
    event.preventDefault();
    setResizing(true);
    const onMove = (moveEvent: MouseEvent) => {
      onSidebarWidthChange?.(clampSidebarWidth(moveEvent.clientX));
    };
    const onUp = (upEvent: MouseEvent) => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      setResizing(false);
      onSidebarWidthCommit?.(clampSidebarWidth(upEvent.clientX));
    };
    document.body.style.cursor = "col-resize";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  const rowMenu = (thread: ThreadSummary) => {
    if (menuFor !== thread.id) return null;
    const busy = ACTIVE_STATUSES.has(thread.status);
    return (
      <>
        <div className="menu-backdrop" onClick={closeMenu} aria-hidden="true" />
        <div className="row-menu" role="menu" aria-label="会话操作菜单">
          <button
            className="row-menu-item"
            role="menuitem"
            type="button"
            onClick={() => beginRename(thread)}
          >
            <Pencil aria-hidden="true" size={13} />
            重命名
          </button>
          <button
            className="row-menu-item"
            role="menuitem"
            type="button"
            disabled={busy}
            title={busy ? "会话正在运行，请先等待完成或停止" : undefined}
            onClick={() => {
              closeMenu();
              onArchiveThread?.(thread.id);
            }}
          >
            <Archive aria-hidden="true" size={13} />
            归档
          </button>
          <button
            className="row-menu-item row-menu-danger"
            role="menuitem"
            type="button"
            disabled={busy}
            title={busy ? "会话正在运行，请先等待完成或停止" : undefined}
            onClick={() => {
              closeMenu();
              setConfirmDeleteId(null);
              onTrashThread?.(thread.id);
            }}
          >
            <Trash2 aria-hidden="true" size={13} />
            移入回收站
          </button>
        </div>
      </>
    );
  };

  const activeRow = (thread: ThreadSummary) => {
    const editing = editingId === thread.id;
    if (editing) {
      return (
        <div className="thread-row thread-row-editing" key={thread.id}>
          <input
            aria-label="会话标题"
            value={editValue}
            autoFocus
            onChange={(event) => setEditValue(event.target.value)}
            onBlur={() => commitRename(thread.id)}
            onKeyDown={(event) => {
              if (event.key === "Enter") commitRename(thread.id);
              else if (event.key === "Escape") setEditingId(null);
            }}
          />
        </div>
      );
    }
    return (
      <div
        className="thread-row"
        key={thread.id}
        data-thread-id={thread.id}
        data-dragging={draggingId === thread.id || undefined}
        data-drop-edge={dropEdge?.id === thread.id ? dropEdge.place : undefined}
      >
        <div
          className="thread-drag-handle"
          onPointerDown={(event) => handlePointerDown(event, thread.id)}
          aria-label="拖拽排序"
          title="按住拖动调整顺序"
        >
          <GripVertical aria-hidden="true" size={13} />
        </div>
        <button
          className="thread-select"
          type="button"
          onClick={() => onSelectThread(thread.id)}
          aria-current={thread.id === activeThreadId ? "true" : undefined}
          title={thread.title}
        >
          <span className="thread-title">{thread.title}</span>
          {normalizedQuery && (() => {
            const snippet = searchResults.find(r => r.thread.id === thread.id)?.snippet;
            if (!snippet?.text) return null;
            const chars = Array.from(snippet.text);
            return <span className="thread-search-snippet">{chars.slice(0, snippet.start).join("")}<mark>{chars.slice(snippet.start, snippet.end).join("")}</mark>{chars.slice(snippet.end).join("")}</span>;
          })()}
          <span className="thread-meta">
            <StatusChip status={thread.status} />
            <small>{formatRelativeTime(thread.updatedAt)}</small>
          </span>
        </button>
        <div className="thread-actions">
          <button
            className="icon-button"
            type="button"
            aria-label="会话操作"
            aria-haspopup="menu"
            aria-expanded={menuFor === thread.id}
            onClick={() =>
              setMenuFor((current) => (current === thread.id ? null : thread.id))
            }
          >
            <MoreVertical aria-hidden="true" size={14} />
          </button>
          {rowMenu(thread)}
        </div>
      </div>
    );
  };

  if (collapsed) {
    return (
      <aside className="sidebar sidebar-collapsed" aria-label="主导航">
        <div className="rail-actions">
          <button
            className="rail-button rail-button-primary"
            type="button"
            aria-label="新建会话"
            title="新建会话"
            onClick={onNewThread}
          >
            <MessageSquarePlus aria-hidden="true" size={17} />
          </button>
          {FEATURE_ENTRIES.map((feature) => {
            const live = feature.id === "redact" ? onOpenRedaction : feature.id === "legal-search" ? onOpenLegalSearch : feature.id === "case-search" ? onOpenCaseSearch : feature.id === "litigation" ? onOpenLitigation : feature.id === "contract-review" ? onOpenContractReview : feature.id === "knowledge" ? onOpenKnowledge : feature.id === "plugins" ? onOpenPlugins : feature.id === "scheduled" ? onOpenScheduled : feature.id === "case-calendar" ? onOpenCaseCalendar : undefined;
            return (
              <button
                className="rail-button"
                key={feature.id}
                type="button"
                aria-label={feature.label}
                title={live ? feature.label : `${feature.label}（准备中）`}
                aria-disabled={live ? undefined : "true"}
                onClick={live ? live : () => undefined}
              >
                <feature.icon aria-hidden="true" size={16} />
              </button>
            );
          })}
        </div>
        <footer className="rail-footer">
          <button
            className="rail-button"
            type="button"
            aria-label="设置"
            title="设置"
            onClick={onOpenSettings}
          >
            <Settings aria-hidden="true" size={16} />
          </button>
        </footer>
      </aside>
    );
  }

  return (
    <aside className="sidebar" aria-label="主导航">
      <div className="sidebar-main">
        <button className="new-thread-button" type="button" onClick={onNewThread}>
          <MessageSquarePlus aria-hidden="true" size={16} />
          新建会话
        </button>

        <nav className="action-groups" aria-label="功能入口">
          {(["法律工作", "通用能力"] as const).map(group => <section className="action-group" key={group} aria-label={group}>
            <h2 className="action-group-heading">{group}</h2>
            <div className="action-grid">
          {FEATURE_ENTRIES.filter(feature => feature.group === group).map((feature) => {
            const live = feature.id === "redact" ? onOpenRedaction : feature.id === "legal-search" ? onOpenLegalSearch : feature.id === "case-search" ? onOpenCaseSearch : feature.id === "litigation" ? onOpenLitigation : feature.id === "contract-review" ? onOpenContractReview : feature.id === "knowledge" ? onOpenKnowledge : feature.id === "plugins" ? onOpenPlugins : feature.id === "scheduled" ? onOpenScheduled : feature.id === "case-calendar" ? onOpenCaseCalendar : undefined;
            return (
              <button
                className={`action-tile${feature.id === "plugins" ? " action-tile-wide" : ""}`}
                key={feature.id}
                type="button"
                title={live ? feature.label : `${feature.label}（准备中）`}
                onClick={live ? live : () => announceFeature(feature.label)}
              >
                <feature.icon aria-hidden="true" size={15} />
                <span className="action-tile-label">{feature.label}</span>
              </button>
            );
          })}
            </div>
          </section>)}
        </nav>

        {featureNotice && (
          <p className="action-notice" role="status">{featureNotice}</p>
        )}

        <div className="sessions-toolbar">
          {searchOpen ? (
            <div className="thread-search session-search">
              <Search aria-hidden="true" size={14} />
              <input
                type="search"
                role="searchbox"
                aria-label="搜索会话"
                ref={searchInput}
                placeholder={scope === "chat" ? "搜索对话" : "搜索项目"}
                value={query}
                maxLength={200}
                autoFocus
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") closeSearch();
                }}
              />
              <button
                className="icon-button"
                type="button"
                aria-label="关闭搜索"
                onClick={closeSearch}
              >
                <X aria-hidden="true" size={13} />
              </button>
            </div>
          ) : (
            <>
              <div className="session-tabs" role="tablist" aria-label="会话分区">
                <button
                  className="tab-button"
                  type="button"
                  role="tab"
                  aria-selected={scope === "chat"}
                  onClick={() => setScope("chat")}
                >
                  对话
                </button>
                <button
                  className="tab-button"
                  type="button"
                  role="tab"
                  aria-selected={scope === "project"}
                  onClick={() => setScope("project")}
                >
                  项目
                </button>
              </div>
              <button
                className="toolbar-icon"
                type="button"
                aria-label="搜索会话"
                title="搜索会话"
                onClick={() => setSearchOpen(true)}
              >
                <Search aria-hidden="true" size={14} />
              </button>
              <button
                className="toolbar-icon"
                type="button"
                aria-label="新建项目"
                title="选择一个文件夹，新建项目"
                onClick={onCreateProject}
              >
                <FolderPlus aria-hidden="true" size={15} />
              </button>
            </>
          )}
        </div>

        <section
          className="thread-list"
          ref={threadListRef}
          data-reordering={draggingId !== null || undefined}
          aria-label="会话"
        >
          {searchLoading && <p role="status">正在搜索…</p>}
          {searchError && <p role="alert">{searchError}</p>}
          {visibleThreads.length > 0 ? (
            visibleThreads.map(activeRow)
          ) : !searchLoading && !searchError ? (
            <p className="context-empty">
              {normalizedQuery
                ? "没有匹配的结果。"
                : scope === "chat"
                  ? "还没有会话，点「新建会话」或直接在右侧输入即可开始。"
                  : "还没有项目。点上方文件夹按钮，选一个文件夹即可创建。"}
            </p>
          ) : null}
        </section>

        {!normalizedQuery && scope === "chat" && archivedThreads.length > 0 && (
          <section className="thread-section" aria-label={`归档 ${archivedThreads.length}`}>
            <button
              className="section-toggle"
              type="button"
              aria-expanded={showArchived}
              onClick={() => setShowArchived((value) => !value)}
            >
              <Archive aria-hidden="true" size={13} />
              归档 ({archivedThreads.length})
            </button>
            {showArchived && (
              <div className="section-body">
                {archivedThreads.map((thread) => (
                  <div className="section-row" key={thread.id}>
                    <span className="thread-title" title={thread.title}>
                      {thread.title}
                    </span>
                    <span className="thread-actions">
                      <button
                        className="icon-button"
                        type="button"
                        aria-label="恢复"
                        title="取消归档，回到会话列表"
                        onClick={() => onUnarchiveThread?.(thread.id)}
                      >
                        <ArchiveRestore aria-hidden="true" size={14} />
                      </button>
                      <button
                        className="icon-button"
                        type="button"
                        aria-label="移入回收站"
                        onClick={() => onTrashThread?.(thread.id)}
                      >
                        <Trash2 aria-hidden="true" size={14} />
                      </button>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {!normalizedQuery && scope === "chat" && trashedThreads.length > 0 && (
          <section className="thread-section" aria-label={`回收站 ${trashedThreads.length}`}>
            <button
              className="section-toggle"
              type="button"
              aria-expanded={showTrash}
              onClick={() => setShowTrash((value) => !value)}
            >
              <Trash2 aria-hidden="true" size={13} />
              回收站 ({trashedThreads.length})
            </button>
            {showTrash && (
              <div className="section-body">
                {trashedThreads.map((thread) => (
                  <div className="section-row" key={thread.id}>
                    <span className="thread-title" title={thread.title}>
                      {thread.title}
                    </span>
                    <span className="thread-actions">
                      <button
                        className="icon-button"
                        type="button"
                        aria-label="恢复"
                        title="恢复到会话列表"
                        onClick={() => onRestoreThread?.(thread.id)}
                      >
                        <RotateCcw aria-hidden="true" size={14} />
                      </button>
                      {confirmDeleteId === thread.id ? (
                        <button
                          className="danger-confirm"
                          type="button"
                          aria-label="确认删除"
                          onClick={() => {
                            setConfirmDeleteId(null);
                            onDeleteThread?.(thread.id);
                          }}
                        >
                          确认删除
                        </button>
                      ) : (
                        <button
                          className="icon-button icon-button-danger"
                          type="button"
                          aria-label="彻底删除"
                          title="彻底删除，不可恢复"
                          onClick={() => setConfirmDeleteId(thread.id)}
                        >
                          <Trash2 aria-hidden="true" size={14} />
                        </button>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </div>

      <footer className="sidebar-footer">
        <UpdatePanel controller={updates} />
        <button
          className="footer-action"
          type="button"
          onClick={onOpenSettings}
          aria-label="设置"
        >
          <Settings aria-hidden="true" size={18} />
          <span>设置</span>
        </button>
      </footer>

      <div
        className="sidebar-resizer"
        data-active={resizing || undefined}
        role="separator"
        aria-label="调整侧栏宽度"
        aria-orientation="vertical"
        onMouseDown={startResize}
        onDoubleClick={() => onSidebarWidthCommit?.(288)}
      />
    </aside>
  );
}
