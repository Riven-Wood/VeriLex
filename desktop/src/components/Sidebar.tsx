import { searchThreads, openWorkspacePath, renameWorkspace, type ThreadSearchResult } from "../runtime/api";
import {
  Archive,
  CalendarClock,
  CalendarDays,
  ChevronRight,
  FilePenLine,
  FileSearch,
  FolderPlus,
  Folder,
  Gavel,
  GripVertical,
  LibraryBig,
  MessageSquarePlus,
  MoreVertical,
  MoreHorizontal,
  Pin,
  List,
  Pencil,
  Puzzle,
  Scale,
  Search,
  Settings,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

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
import { readProjectSidebarState, writeProjectSidebarState, type ProjectSidebarState } from "../app/projectSidebarState";
import { clampSidebarWidth } from "../app/sidebarLayout";
import type { UpdateController } from "../updater/contracts";
import { UpdatePanel } from "./UpdatePanel";
import {FileBranch} from '../views/LitigationWorkbench';
import type {WorkspaceRecord} from '../runtime/contracts';

interface SidebarProps {
  projectWorkspaces?: readonly WorkspaceRecord[];
  projectWorkspaceId?: string | null;
  projectSelectedFiles?: string[];
  projectFilesLocked?: boolean;
  onToggleProjectFile?: (path:string) => void;
  onProjectScope?: (project: boolean) => void;
  onSelectProjectWorkspace?: (id: string) => void;
  onNewProjectThread?: (id: string) => void;
  onSelectProjectThread?: (id: string, workspaceId: string) => void;
  onOpenProjectFile?: (path: string) => void;
  onRenameProject?: (workspace: WorkspaceRecord) => void;
  onArchiveProjectThreads?: (id: string) => Promise<void>;
  onRemoveProject?: (id: string) => Promise<void>;
  searchRequest?: number;
  threads?: readonly ThreadSummary[];
  activeThreadId: string | null;
  updates: UpdateController;
  onNewThread: () => void;
  onSelectThread: (threadId: string) => void;
  onOpenSettings?: () => void;
  onRenameThread?: (threadId: string, title: string) => void;
  onArchiveThread?: (threadId: string) => void;
  onTrashThread?: (threadId: string) => void;
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
  { id: "litigation", label: "文书起草", icon: FilePenLine, group: "法律工作" },
  { id: "case-calendar", label: "案件日程", icon: CalendarDays, group: "法律工作" },
  { id: "knowledge", label: "知识库", icon: LibraryBig, group: "通用能力" },
  { id: "scheduled", label: "定时任务", icon: CalendarClock, group: "通用能力" },
  { id: "plugins", label: "能力中心", icon: Puzzle, group: "通用能力" },
] as const;

const ACTIVE_STATUSES = new Set(["running", "awaiting_approval"]);

const NO_THREADS: readonly ThreadSummary[] = [];

function ProjectFiles({workspaceId,onOpen,selected,onToggle,locked}:{workspaceId:string;onOpen:(path:string)=>void;selected:string[];onToggle:(path:string)=>void;locked:boolean}) {
  const [revision,setRevision]=useState(0);
  useEffect(()=>{const changed=(event:Event)=>{if((event as CustomEvent).detail?.workspaceId===workspaceId)setRevision(value=>value+1);};window.addEventListener('verilex:workspace-files-changed',changed);return()=>window.removeEventListener('verilex:workspace-files-changed',changed);},[workspaceId]);
  return <FileBranch workspaceId={workspaceId} controls={null} projectSelection={{selected,locked,toggle:onToggle}} revision={String(revision)} activePath={null} onOpen={onOpen}/>;
}

/** Keep sidebar menus within the window and outside the scrollable lists. */
function SidebarActionsMenu({anchor, onClose, children, label, className}: {anchor: HTMLElement; onClose: () => void; children: ReactNode; label: string; className: string}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{left: number; top: number} | null>(null);
  useLayoutEffect(() => {
    const updatePosition = () => {
      const menu = menuRef.current;
      if (!menu || !anchor.isConnected) {onClose();return;}
      const button = anchor.getBoundingClientRect();
      const list = anchor.closest('.project-sidebar-files, .thread-list')?.getBoundingClientRect();
      if (button.bottom < 0 || button.top > window.innerHeight || (list && (button.bottom < list.top || button.top > list.bottom))) {onClose();return;}
      const bounds = menu.getBoundingClientRect();
      const padding = 8, gap = 4;
      const maxLeft = Math.max(padding, window.innerWidth - padding - bounds.width);
      const left = Math.max(padding, Math.min(button.right - bounds.width, maxLeft));
      const below = button.bottom + gap;
      const above = button.top - gap - bounds.height;
      const top = below + bounds.height <= window.innerHeight - padding ? below
        : above >= padding ? above
          : Math.max(padding, Math.min(below, window.innerHeight - padding - bounds.height));
      setPosition({left, top});
    };
    updatePosition();
    window.addEventListener('resize', updatePosition);
    window.addEventListener('scroll', updatePosition, true);
    return () => {window.removeEventListener('resize', updatePosition);window.removeEventListener('scroll', updatePosition, true);};
  }, [anchor, onClose]);
  return createPortal(<>
    <div className="menu-backdrop" aria-hidden="true" onClick={onClose}/>
    <div ref={menuRef} className={`row-menu sidebar-actions-menu ${className}`} role="menu" aria-label={label} style={{left: position?.left ?? 0, top: position?.top ?? 0, visibility: position ? 'visible' : 'hidden'}}>{children}</div>
  </>, document.body);
}

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
  projectWorkspaces,
  projectWorkspaceId,
  projectSelectedFiles = [],
  projectFilesLocked = false,
  onToggleProjectFile,
  onProjectScope,
  onSelectProjectWorkspace,
  onNewProjectThread,
  onSelectProjectThread,
  onOpenProjectFile,
  onRenameProject,
  onArchiveProjectThreads,
  onRemoveProject,
  searchRequest = 0,
  threads = NO_THREADS,
  activeThreadId,
  updates,
  onNewThread,
  onSelectThread,
  onOpenSettings,
  onRenameThread,
  onArchiveThread,
  onTrashThread,
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
  const [openFeatureGroups, setOpenFeatureGroups] = useState<Record<string, boolean>>({"法律工作": true, "通用能力": true});
  const [expandedProjectId, setExpandedProjectId] = useState<string | null>(projectWorkspaceId ?? null);
  useEffect(() => { setExpandedProjectId(projectWorkspaceId ?? null); }, [projectWorkspaceId]);
  const [projectMenuId, setProjectMenuId] = useState<string | null>(null);
  const [projectMenuAnchor, setProjectMenuAnchor] = useState<HTMLElement | null>(null);
  const [folderMode, setFolderMode] = useState(false);
  const [projectSwitcherOpen, setProjectSwitcherOpen] = useState(false);
  const [projectState, setProjectState] = useState<ProjectSidebarState>(readProjectSidebarState);
  const [projectDialog, setProjectDialog] = useState<{kind: 'rename' | 'group' | 'renameGroup' | 'archive' | 'remove'; id: string} | null>(null);
  const [projectDialogValue, setProjectDialogValue] = useState('');
  const [projectActionError, setProjectActionError] = useState('');
  const [projectActionBusy, setProjectActionBusy] = useState(false);
  function saveProjectState(next: ProjectSidebarState) {
    try { writeProjectSidebarState(next); setProjectState(next); setProjectActionError(''); }
    catch { setProjectActionError('无法保存项目设置，请检查本机存储后重试。'); }
  }
  useEffect(() => {
    const restore = (event: Event) => {
      const id = (event as CustomEvent<string>).detail;
      setProjectState(current => {
        const next = {...current, hidden: current.hidden.filter(value => value !== id)};
        try { writeProjectSidebarState(next); return next; } catch { setProjectActionError('无法恢复项目，请检查本机存储。'); return current; }
      });
    };
    window.addEventListener('verilex:restore-project', restore);
    return () => window.removeEventListener('verilex:restore-project', restore);
  }, []);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!searchRequest) return;
    setSearchOpen(true); setScope('chat');
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
  const [threadMenuAnchor, setThreadMenuAnchor] = useState<HTMLElement | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
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
    if (!menuFor && !projectMenuId) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuFor(null);
        setProjectMenuId(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuFor, projectMenuId]);

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

  const projectListRef = useRef<HTMLElement>(null);
  const projectDragRef = useRef<{ pointerId: number; cleanup: () => void } | null>(null);
  const suppressProjectClick = useRef(false);
  const [projectDraggingId, setProjectDraggingId] = useState<string | null>(null);
  const [projectDropEdge, setProjectDropEdge] = useState<{id: string; place: "before" | "after"} | null>(null);
  useEffect(() => () => { projectDragRef.current?.cleanup(); projectDragRef.current = null; }, [scope, normalizedQuery, folderMode]);

  function projectGroup(id: string, state: ProjectSidebarState): string {
    return state.pinned.includes(id) ? 'pinned' : `group:${state.groups[id] || ''}`;
  }

  function startProjectDrag(event: React.PointerEvent, id: string, orderedIds: string[], switcher = false) {
    if ((folderMode && !switcher) || normalizedQuery || event.button !== 0) return;
    projectDragRef.current?.cleanup();
    suppressProjectClick.current = false;
    const startX = event.clientX, startY = event.clientY;
    let x = startX, y = startY, moved = false, frame = 0, previousTime = 0;
    let edge: {id: string; place: "before" | "after"} | null = null;
    const updateTarget = () => {
      const list = projectListRef.current;
      const element = document.elementFromPoint?.(x, y);
      const row = element?.closest?.(switcher ? '.project-switcher-option[data-project-id]' : '.project-sidebar-row') as HTMLElement | null;
      const target = row && list?.contains(row) ? row.dataset.projectId : undefined;
      if (!row || !target || target === id || projectGroup(target, projectState) !== projectGroup(id, projectState)) {
        edge = null; setProjectDropEdge(null); return;
      }
      const bounds = row.getBoundingClientRect();
      const place = y < bounds.top + bounds.height / 2 ? 'before' : 'after';
      edge = {id: target, place}; setProjectDropEdge(edge);
    };
    const tick = (time: number) => {
      const list = projectListRef.current;
      if (!projectDragRef.current || !list) return;
      const bounds = list.getBoundingClientRect();
      const elapsed = previousTime ? Math.min(time - previousTime, 32) : 16;
      previousTime = time;
      if (moved && bounds.height > 0 && x >= bounds.left && x <= bounds.right) {
        const edgeSize = Math.min(48, bounds.height / 3);
        const speed = y < bounds.top + edgeSize ? -Math.min(1, (bounds.top + edgeSize - y) / edgeSize)
          : y > bounds.bottom - edgeSize ? Math.min(1, (y - bounds.bottom + edgeSize) / edgeSize) : 0;
        if (speed) { list.scrollTop += speed * elapsed * 0.6; updateTarget(); }
      }
      frame = window.requestAnimationFrame(tick);
    };
    const cleanup = () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onCancel);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('blur', onBlur);
      setProjectDraggingId(null); setProjectDropEdge(null);
    };
    const finish = (cancelled: boolean) => {
      cleanup(); projectDragRef.current = null;
      if (moved) suppressProjectClick.current = true;
      if (cancelled || !edge) return;
      const groupIds = orderedIds.filter(value => projectGroup(value, projectState) === projectGroup(id, projectState));
      const reordered = moveId(groupIds, id, edge.id, edge.place);
      if (!reordered) return;
      let index = 0;
      const visibleOrder = orderedIds.map(value => projectGroup(value, projectState) === projectGroup(id, projectState) ? reordered[index++] : value);
      // Retain hidden project IDs so their relative order survives restoration.
      const remaining = projectState.order.filter(value => !visibleOrder.includes(value));
      saveProjectState({...projectState, order: [...visibleOrder, ...remaining]});
    };
    const onMove = (move: PointerEvent) => {
      if (move.pointerId !== event.pointerId) return;
      x = move.clientX; y = move.clientY;
      if (!moved && Math.hypot(x - startX, y - startY) < 5) return;
      if (!moved) {moved = true; setProjectDraggingId(id);}
      updateTarget();
    };
    const onUp = (up: PointerEvent) => {if (up.pointerId === event.pointerId) finish(false);};
    const onCancel = (cancel: PointerEvent) => {if (cancel.pointerId === event.pointerId) finish(true);};
    const onKey = (key: KeyboardEvent) => {if (key.key === 'Escape') {key.preventDefault(); finish(true);}};
    const onBlur = () => finish(true);
    projectDragRef.current = {pointerId: event.pointerId, cleanup};
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onCancel);
    window.addEventListener('keydown', onKey);
    window.addEventListener('blur', onBlur);
    frame = window.requestAnimationFrame(tick);
  }

  const groupDragRef = useRef<{cleanup: () => void} | null>(null);
  const [draggingGroup, setDraggingGroup] = useState<string | null>(null);
  const [groupDropEdge, setGroupDropEdge] = useState<{id: string; place: "before" | "after"} | null>(null);
  useEffect(() => () => {groupDragRef.current?.cleanup(); groupDragRef.current = null;}, [scope, normalizedQuery, folderMode]);

  function startGroupDrag(event: React.PointerEvent, id: string, orderedGroups: string[]) {
    if (event.button !== 0 || normalizedQuery || folderMode || id === 'pinned') return;
    groupDragRef.current?.cleanup();
    const startX=event.clientX, startY=event.clientY;
    let x=startX, y=startY, moved=false, frame=0, previousTime=0;
    let edge: {id: string; place: "before" | "after"} | null = null;
    const updateTarget = () => {
      const list=projectListRef.current;
      const element=document.elementFromPoint?.(x,y);
      const group=element?.closest?.('[data-project-group]') as HTMLElement | null;
      const target=group && list?.contains(group) ? group.dataset.projectGroup : undefined;
      if(!target || target===id || target==='pinned') {edge=null;setGroupDropEdge(null);return;}
      const heading=group!.querySelector<HTMLElement>('.project-group-heading') ?? group!;
      const bounds=heading.getBoundingClientRect();
      edge={id:target,place:y < bounds.top+bounds.height/2 ? 'before' : 'after'};
      setGroupDropEdge(edge);
    };
    const tick = (time: number) => {
      const list=projectListRef.current;if(!groupDragRef.current || !list)return;
      const bounds=list.getBoundingClientRect(), elapsed=previousTime ? Math.min(time-previousTime,32) : 16;previousTime=time;
      if(moved && bounds.height>0 && x>=bounds.left && x<=bounds.right) {
        const size=Math.min(48,bounds.height/3);
        const speed=y<bounds.top+size ? -Math.min(1,(bounds.top+size-y)/size) : y>bounds.bottom-size ? Math.min(1,(y-bounds.bottom+size)/size) : 0;
        if(speed){list.scrollTop+=speed*elapsed*.6;updateTarget();}
      }
      frame=window.requestAnimationFrame(tick);
    };
    const cleanup = () => {window.cancelAnimationFrame(frame);window.removeEventListener('pointermove',onMove);window.removeEventListener('pointerup',onUp);window.removeEventListener('pointercancel',onCancel);window.removeEventListener('keydown',onKey);window.removeEventListener('blur',onBlur);setDraggingGroup(null);setGroupDropEdge(null);};
    const finish = (cancelled:boolean) => {
      cleanup();groupDragRef.current=null;
      if(cancelled || !edge)return;
      const next=moveId(orderedGroups.filter(key=>key!=='pinned'),id,edge.id,edge.place);
      if(next)saveProjectState({...projectState,groupOrder:[...next,...projectState.groupOrder.filter(key=>!next.includes(key))]});
    };
    const onMove=(move:PointerEvent)=>{if(move.pointerId!==event.pointerId)return;x=move.clientX;y=move.clientY;if(!moved && Math.hypot(x-startX,y-startY)<5)return;if(!moved){moved=true;setDraggingGroup(id);}updateTarget();};
    const onUp=(up:PointerEvent)=>{if(up.pointerId===event.pointerId)finish(false);};
    const onCancel=(cancel:PointerEvent)=>{if(cancel.pointerId===event.pointerId)finish(true);};
    const onKey=(key:KeyboardEvent)=>{if(key.key==='Escape'){key.preventDefault();finish(true);}};
    const onBlur=()=>finish(true);
    groupDragRef.current={cleanup};window.addEventListener('pointermove',onMove);window.addEventListener('pointerup',onUp);window.addEventListener('pointercancel',onCancel);window.addEventListener('keydown',onKey);window.addEventListener('blur',onBlur);frame=window.requestAnimationFrame(tick);
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
    if (menuFor !== thread.id || !threadMenuAnchor) return null;
    const busy = ACTIVE_STATUSES.has(thread.status);
    return (
      <SidebarActionsMenu anchor={threadMenuAnchor} onClose={closeMenu} label="会话操作菜单" className="thread-menu">
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
              onTrashThread?.(thread.id);
            }}
          >
            <Trash2 aria-hidden="true" size={13} />
            移入回收站
          </button>
      </SidebarActionsMenu>
    );
  };

  const activeRow = (thread: ThreadSummary, projectId?: string) => {
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
          style={projectId ? {display: "none"} : undefined}
          onPointerDown={(event) => handlePointerDown(event, thread.id)}
          aria-label="拖拽排序"
          title="按住拖动调整顺序"
        >
          <GripVertical aria-hidden="true" size={13} />
        </div>
        <button
          className="thread-select"
          type="button"
          onClick={() => projectId ? onSelectProjectThread?.(thread.id, projectId) : onSelectThread(thread.id)}
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
            onClick={(event) => {
              setThreadMenuAnchor(event.currentTarget);
              setMenuFor((current) => (current === thread.id ? null : thread.id))
            }}
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
            <h2 className="action-group-heading"><button type="button" className="feature-group-toggle" aria-expanded={openFeatureGroups[group]} onClick={() => setOpenFeatureGroups(current => ({...current, [group]: !current[group]}))}>{group}<ChevronRight size={13} aria-hidden="true"/></button></h2>
            <div className="action-grid" hidden={!openFeatureGroups[group]}>
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
                  onClick={() => {setScope("chat");onProjectScope?.(false);}}
                >
                  对话
                </button>
                <button
                  className="tab-button"
                  type="button"
                  role="tab"
                  aria-selected={scope === "project"}
                  onClick={() => { setScope("project"); onProjectScope?.(true); }}
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

        {scope === 'project' && projectWorkspaces ? (() => {
          const available = projectWorkspaces.filter(item => item.allow_read && !projectState.hidden.includes(item.id));
          const positions = new Map(projectState.order.map((id, index) => [id, index]));
          const sorted = [...available].sort((a, b) => Number(projectState.pinned.includes(b.id)) - Number(projectState.pinned.includes(a.id)) || (positions.get(a.id) ?? Number.MAX_SAFE_INTEGER) - (positions.get(b.id) ?? Number.MAX_SAFE_INTEGER));
          const current = available.find(item => item.id === projectWorkspaceId);
          const items = folderMode && current ? [current] : sorted.filter(item => !normalizedQuery || item.display_name.toLocaleLowerCase().includes(normalizedQuery.toLocaleLowerCase()));
          const groupKeys = [...new Set(items.map(item => projectGroup(item.id, projectState)))];
          const groupPositions = new Map(projectState.groupOrder.map((key,index)=>[key,index]));
          groupKeys.sort((a,b)=>Number(b==='pinned')-Number(a==='pinned') || (groupPositions.get(a) ?? Number.MAX_SAFE_INTEGER)-(groupPositions.get(b) ?? Number.MAX_SAFE_INTEGER));
          const projectItem = (item: WorkspaceRecord) => {
            const expanded = projectWorkspaceId === item.id && expandedProjectId === item.id;
            const folderOpen = folderMode && projectWorkspaceId === item.id;
            const projectThreads = applySessionOrder(threads.filter(thread => thread.workspaceId === item.id), orders.project);
            return <div className="project-sidebar-item" key={item.id}>
              <div className={`project-sidebar-row${projectWorkspaceId === item.id ? ' is-active' : ''}`} data-project-id={item.id} data-dragging={projectDraggingId === item.id || undefined} data-drop-edge={projectDropEdge?.id === item.id ? projectDropEdge.place : undefined}>
                <button type="button" className="project-sidebar-expand" aria-label={`${expanded ? '收起' : '展开'}项目：${item.display_name}`} aria-expanded={expanded} onClick={() => {
                  if (projectWorkspaceId !== item.id) onSelectProjectWorkspace?.(item.id);
                  else setExpandedProjectId(expanded ? null : item.id);
                }}><ChevronRight size={15} aria-hidden="true"/></button>
                <button type="button" className="project-sidebar-name" aria-current={projectWorkspaceId === item.id ? 'true' : undefined} onPointerDown={event => startProjectDrag(event, item.id, sorted.map(project => project.id))} onClickCapture={event => {if (suppressProjectClick.current) {event.preventDefault(); event.stopPropagation(); suppressProjectClick.current = false;}}} onClick={() => {
                  if (projectWorkspaceId !== item.id) onSelectProjectWorkspace?.(item.id);
                  else setExpandedProjectId(item.id);
                }}><Folder size={16} aria-hidden="true"/><span>{item.display_name}</span>{projectState.pinned.includes(item.id) && <Pin size={12} aria-label="已置顶"/>}</button>
                <div className="project-sidebar-actions">
                  <button type="button" className="icon-button" aria-label={`项目菜单：${item.display_name}`} aria-haspopup="menu" aria-expanded={projectMenuId === item.id} onClick={event => {setProjectActionError('');setProjectMenuAnchor(event.currentTarget);setProjectMenuId(value => value === item.id ? null : item.id);}}><MoreHorizontal size={16}/></button>
                  {projectMenuId === item.id && projectMenuAnchor && <SidebarActionsMenu anchor={projectMenuAnchor} onClose={() => setProjectMenuId(null)} label="项目操作菜单" className="project-menu">
                    <button className="row-menu-item" role="menuitem" type="button" onClick={() => {setProjectMenuId(null); if (projectWorkspaceId !== item.id) onSelectProjectWorkspace?.(item.id); setExpandedProjectId(item.id); setFolderMode(!folderOpen); if (!folderOpen) setOpenFeatureGroups({"法律工作":false,"通用能力":false});}}><Folder size={14}/>{folderOpen ? '关闭项目文件夹' : '打开项目文件夹'}</button>
                    <button className="row-menu-item" role="menuitem" type="button" onClick={() => {setProjectMenuId(null);saveProjectState({...projectState,pinned: projectState.pinned.includes(item.id) ? projectState.pinned.filter(id=>id!==item.id) : [...projectState.pinned,item.id]});}}><Pin size={14}/>{projectState.pinned.includes(item.id) ? '取消置顶' : '置顶'}</button>
                    <button className="row-menu-item" role="menuitem" type="button" onClick={() => {setProjectMenuId(null);setProjectDialogValue(item.display_name);setProjectDialog({kind:'rename',id:item.id});}}><Pencil size={14}/>编辑项目</button>
                    <button className="row-menu-item" role="menuitem" type="button" onClick={() => {setProjectMenuId(null);setProjectDialogValue(projectState.groups[item.id] ?? '');setProjectDialog({kind:'group',id:item.id});}}><List size={14}/>分区</button>
                    <button className="row-menu-item" role="menuitem" type="button" onClick={() => {setProjectMenuId(null);void openWorkspacePath(item.id,{path:'.',mode:'reveal'}).catch(error=>setProjectActionError(error instanceof Error ? error.message : '无法在文件管理器中显示项目。'));}}><Folder size={14}/>在文件管理器中显示</button>
                    <button className="row-menu-item" role="menuitem" type="button" disabled={!projectThreads.length || projectThreads.some(t=>ACTIVE_STATUSES.has(t.status))} onClick={() => {setProjectMenuId(null);setProjectDialog({kind:'archive',id:item.id});}}><Archive size={14}/>归档项目会话</button>
                    <button className="row-menu-item row-menu-danger" role="menuitem" type="button" onClick={() => {setProjectMenuId(null);setProjectDialog({kind:'remove',id:item.id});}}><X size={14}/>移除项目</button>
                  </SidebarActionsMenu>}
                  <button type="button" className="icon-button" aria-label={`在${item.display_name}中新建会话`} title="新建项目会话" onClick={() => {setProjectMenuId(null); setExpandedProjectId(item.id); onNewProjectThread?.(item.id);}}><FilePenLine size={16}/></button>
                </div>
              </div>
              {expanded && <div className="project-sidebar-children">
                {folderOpen && <div className="project-sidebar-folder"><div className="project-sidebar-heading">项目文件</div>{projectSelectedFiles.length > 0 && <p className="project-selection-hint">已选 {projectSelectedFiles.length} 个文件</p>}<ProjectFiles key={item.id} workspaceId={item.id} selected={projectSelectedFiles} locked={projectFilesLocked} onToggle={path=>onToggleProjectFile?.(path)} onOpen={path=>onOpenProjectFile?.(path)}/></div>}
                {folderOpen && <div className="project-sidebar-heading project-sessions-heading">项目会话</div>}
                {projectThreads.map(thread => activeRow(thread, item.id))}
              </div>}
            </div>;
          };
          return <section className="project-sidebar-files" ref={projectListRef} data-reordering={projectDraggingId !== null || undefined} aria-label="项目">
            {folderMode && current && <div className="project-switcher"><button type="button" className="section-toggle" aria-expanded={projectSwitcherOpen} onClick={()=>setProjectSwitcherOpen(value=>!value)}>切换项目 {projectSwitcherOpen ? '收起' : '展开'}</button>{projectSwitcherOpen && <div className="project-switcher-list">{sorted.filter(item=>item.id!==current.id).map(item=><button type="button" className="project-switcher-option" key={item.id} data-project-id={item.id} data-dragging={projectDraggingId === item.id || undefined} data-drop-edge={projectDropEdge?.id === item.id ? projectDropEdge.place : undefined} onPointerDown={event=>startProjectDrag(event,item.id,sorted.map(project=>project.id),true)} onClickCapture={event=>{if(suppressProjectClick.current){event.preventDefault();event.stopPropagation();suppressProjectClick.current=false;}}} onClick={()=>{setProjectSwitcherOpen(false);onSelectProjectWorkspace?.(item.id);}}>{item.display_name}</button>)}</div>}</div>}
            {folderMode && current ? items.map(projectItem) : <>{groupKeys.map(group=><div key={group} data-project-group={group} data-dragging={draggingGroup===group || undefined} data-drop-edge={groupDropEdge?.id===group ? groupDropEdge.place : undefined}>{(group !== 'group:' || groupKeys.length > 1) && <div className="project-sidebar-heading project-group-heading"><span className={group === 'pinned' ? '' : 'project-group-drag'} title={group === 'pinned' ? undefined : '拖动调整分区顺序'} onPointerDown={event=>startGroupDrag(event,group,groupKeys)}>{group === 'pinned' ? '置顶' : group.slice(6) || '项目'}</span>{group !== 'pinned' && group !== 'group:' && <button type="button" className="icon-button" aria-label={`编辑分区：${group.slice(6)}`} onClick={()=>{setProjectActionError('');setProjectDialogValue(group.slice(6));setProjectDialog({kind:'renameGroup',id:group.slice(6)});}}><Pencil size={13}/></button>}</div>}{items.filter(item=>projectGroup(item.id,projectState)===group).map(projectItem)}</div>)}</>}
            {!available.length && <p className="context-empty">还没有项目，请新建项目。</p>}
            {!!normalizedQuery && !items.length && <p className="context-empty">没有找到匹配的项目。</p>}
            {projectActionError && <p role="alert">{projectActionError}</p>}
            {projectDialog && <div className="project-dialog-backdrop"><div className="project-dialog" role="dialog" aria-modal="true" aria-label={projectDialog.kind === 'rename' ? '编辑项目' : projectDialog.kind === 'group' ? '项目分区' : projectDialog.kind === 'renameGroup' ? '编辑分区' : projectDialog.kind === 'archive' ? '归档项目会话' : '移除项目'}>
              <h2>{projectDialog.kind === 'rename' ? '编辑项目名称' : projectDialog.kind === 'group' ? '项目分区' : projectDialog.kind === 'renameGroup' ? '编辑分区' : projectDialog.kind === 'archive' ? '归档项目会话？' : '移除项目？'}</h2>
              {(projectDialog.kind === 'rename' || projectDialog.kind === 'group' || projectDialog.kind === 'renameGroup') && <><input aria-label={projectDialog.kind === 'rename' ? '项目名称' : '分区名称'} maxLength={200} value={projectDialogValue} onChange={event=>setProjectDialogValue(event.target.value)} autoFocus/>{projectDialog.kind === 'group' && <><p>输入分区名称，留空可移出分区。</p>{[...new Set(Object.values(projectState.groups))].filter(Boolean).map(group=><button type="button" key={group} onClick={()=>setProjectDialogValue(group)}>{group}</button>)}</>}</>}
              {projectDialog.kind === 'archive' && <p>该项目的普通会话将移入归档，可以恢复。</p>}
              {projectDialog.kind === 'remove' && <p>移除项目会收回文件夹授权，并解除会话与文件夹的关联。文件和会话保留。</p>}
              {projectActionError && <p role="alert">{projectActionError}</p>}
              <div className="project-dialog-actions"><button type="button" disabled={projectActionBusy} onClick={()=>{setProjectDialog(null);setProjectActionError('');}}>取消</button><button type="button" disabled={projectActionBusy || ((projectDialog.kind === 'rename' || projectDialog.kind === 'renameGroup') && !projectDialogValue.trim())} onClick={()=>{void (async()=>{
                setProjectActionBusy(true);setProjectActionError('');
                try {
                  if(projectDialog.kind === 'rename') {const updated=await renameWorkspace(projectDialog.id,projectDialogValue.trim());onRenameProject?.(updated);}
                  if(projectDialog.kind === 'group') {const groups={...projectState.groups};const value=projectDialogValue.trim();if(value)groups[projectDialog.id]=value;else delete groups[projectDialog.id];writeProjectSidebarState({...projectState,groups});setProjectState({...projectState,groups});}
                  if(projectDialog.kind === 'renameGroup') {
                    const value=projectDialogValue.trim();
                    if(value !== projectDialog.id && Object.values(projectState.groups).includes(value)) throw new Error('已有同名分区，请使用其他名称。');
                    if(value === '置顶' || value === '项目') throw new Error('请使用其他分区名称。');
                    const groups=Object.fromEntries(Object.entries(projectState.groups).map(([id,group])=>[id,group===projectDialog.id ? value : group]));
                    const groupOrder=projectState.groupOrder.map(group=>group===`group:${projectDialog.id}` ? `group:${value}` : group);
                    const next={...projectState,groups,groupOrder};writeProjectSidebarState(next);setProjectState(next);
                  }
                  if(projectDialog.kind === 'archive') {if(!onArchiveProjectThreads) throw new Error('无法归档项目会话。');await onArchiveProjectThreads(projectDialog.id);}
                  if(projectDialog.kind === 'remove') {if(!onRemoveProject)throw new Error('无法移除项目，请重试。');await onRemoveProject(projectDialog.id);if(projectWorkspaceId===projectDialog.id)setFolderMode(false);}
                  setProjectDialog(null);
                } catch(error) {setProjectActionError(error instanceof Error ? error.message : '操作未完成，请重试。');} finally {setProjectActionBusy(false);}
              })();}}>确定</button></div>
            </div></div>}
          </section>;
        })() : <section
          className="thread-list"
          ref={threadListRef}
          data-reordering={draggingId !== null || undefined}
          aria-label="会话"
        >
          {searchLoading && <p role="status">正在搜索…</p>}
          {searchError && <p role="alert">{searchError}</p>}
          {visibleThreads.length > 0 ? (
            visibleThreads.map(thread => activeRow(thread))
          ) : !searchLoading && !searchError ? (
            <p className="context-empty">
              {normalizedQuery
                ? "没有匹配的结果。"
                : scope === "chat"
                  ? "还没有会话，点「新建会话」或直接在右侧输入即可开始。"
                  : "还没有项目。点上方文件夹按钮，选一个文件夹即可创建。"}
            </p>
          ) : null}
        </section>}

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
