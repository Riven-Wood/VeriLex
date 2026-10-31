import {WorkspaceFilesPanel, type WorkspaceBrowseLocation} from "./WorkspaceFilesPanel";
import {PanelCommandContext} from "../commands/PanelCommandContext";
import {useContext} from "react";
import {useCitationLocation} from "./preview/CitationLocation";
import {MaterialCoverage} from "./preview/MaterialCoverage";
import {DiagramSources} from "./preview/DiagramSources";
import {LoadingState} from "./LoadingState";
import { lazy, memo, Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  ChevronLeft,
  ExternalLink,
  FileWarning,
  FolderOpen,
  Minus,
  Plus,
  RotateCcw,
  X,
} from "lucide-react";

import { useObjectUrl } from "../hooks/useObjectUrl";
import {
  fetchAttachmentContent,
  getWorkspaceFilePreview,
  listThreadArtifacts,
  openWorkspacePath,
  RuntimeApiError,
} from "../runtime/api";
import type {
  ThreadArtifactRecord,
  WorkspaceFilePreview,
} from "../runtime/contracts";
import type { TimelineAttachment } from "../timeline/items";
import { MarkdownBody } from "./timeline/MarkdownBody";

/** pdf.js stays out of the main bundle; only PDF previews load it. */
const PdfViewer = lazy(() =>
  import("./preview/PdfViewer").then((module) => ({
    default: module.PdfViewer,
  })),
);
const DocxViewer = lazy(() => import("./preview/DocxViewer").then((module) => ({default: module.DocxViewer})));

const WIDTH_STORAGE_KEY = "verilex:preview-rail-width:v1";
const MIN_WIDTH = 300;
const MAX_WIDTH = 620;
const DEFAULT_WIDTH = 420;
const MAX_ATTACHMENT_TEXT_CHARS = 512 * 1024;

const MARKDOWN_EXTENSIONS = new Set(["md", "markdown"]);

const OPERATION_LABELS: Record<string, string> = {
  write_file: "写入",
  apply_file_patch: "修改",
  read_file: "读取",
  copy_path: "复制",
  move_path: "移动",
  document_create: "生成",
  ppt_plugin_create: "生成演示文稿",
  ppt_plugin_replace: "修改演示文稿副本",
};

function artifactListItem(
  artifact: ThreadArtifactRecord,
  currentWorkspaceId: string | null,
) {
  switch (artifact.kind) {
    case "artifact":
      return {
        key: `artifact:${artifact.id}`,
        workspaceId: artifact.published_workspace_id,
        path: artifact.published_relative_path,
        operation: artifact.operation,
        structureChecked:
          artifact.structure_status === "passed"
          && artifact.render_status === "not_required",
      };
    case "legacy_workspace":
      return {
        key: `legacy:${artifact.path}`,
        workspaceId: currentWorkspaceId,
        path: artifact.path,
        operation: artifact.last_operation,
        structureChecked: false,
      };
    default: {
      const exhaustive: never = artifact;
      return exhaustive;
    }
  }
}

export type PreviewTarget =
  | { source: "attachment"; threadId: string; attachment: TimelineAttachment }
  | { source: "workspace"; workspaceId: string; path: string; page?: number; citation?:import("../views/LitigationClient").LitigationSource }
  | { source: "artifacts-list" };

type AttachmentBody =
  | { kind: "image"; objectUrl: string }
  | { kind: "pdf"; blob: Blob }
  | { kind: "docx"; blob: Blob }
  | { kind: "text"; content: string; truncated: boolean }
  | { kind: "unsupported" };

type PreviewState =
  | { phase: "loading" }
  | { phase: "missing"; name: string }
  | { phase: "error"; message: string }
  | {
      phase: "workspace-ready";
      workspaceId: string;
      preview: WorkspaceFilePreview;
    }
  | { phase: "attachment-ready"; attachment: TimelineAttachment; body: AttachmentBody };

function isTextLike(name: string, mediaType: string): boolean {
  if (mediaType.startsWith("text/")) return true;
  const extension = name.split(".").pop()?.toLowerCase() ?? "";
  return [
    "txt", "md", "markdown", "csv", "json", "py", "js", "ts", "tsx",
    "css", "html", "xml", "yaml", "yml", "toml", "rtf", "log", "sh",
  ].includes(extension);
}

function isMarkdown(name: string): boolean {
  const extension = name.split(".").pop()?.toLowerCase() ?? "";
  return MARKDOWN_EXTENSIONS.has(extension);
}

function formatSize(sizeBytes: number): string {
  if (sizeBytes >= 1024 * 1024) {
    return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (sizeBytes >= 1024) {
    return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  }
  return `${sizeBytes} B`;
}

function describeError(error: unknown): { missing: boolean; message: string } {
  if (error instanceof RuntimeApiError) {
    return {
      missing: error.code === "file_not_found",
      message: error.message,
    };
  }
  return { missing: false, message: "预览加载失败，请稍后重试。" };
}

async function loadAttachmentBody(
  threadId: string,
  attachment: TimelineAttachment,
): Promise<AttachmentBody> {
  const blob = await fetchAttachmentContent(threadId, attachment.id);
  if (attachment.mediaType.startsWith("image/")) {
    return { kind: "image", objectUrl: URL.createObjectURL(blob) };
  }
  if (attachment.mediaType === "application/pdf") {
    return { kind: "pdf", blob };
  }
  if (attachment.mediaType === "application/vnd.openxmlformats-officedocument.wordprocessingml.document") {
    return { kind: "docx", blob };
  }
  if (isTextLike(attachment.name, attachment.mediaType)) {
    const text = await blob.text();
    return {
      kind: "text",
      content: text.slice(0, MAX_ATTACHMENT_TEXT_CHARS),
      truncated: text.length > MAX_ATTACHMENT_TEXT_CHARS,
    };
  }
  return { kind: "unsupported" };
}

interface TextPreviewProps {
  content: string;
  truncated: boolean;
  markdown: boolean;
  locatedLine?: number;
}

const TextPreview = memo(function TextPreview({
  content,
  truncated,
  markdown,
  locatedLine,
}: TextPreviewProps) {
  const [rendered, setRendered] = useState(true);
  const locatedRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (locatedLine === undefined) return;
    setRendered(false);
    const timer = window.setTimeout(() => locatedRef.current?.scrollIntoView({block:'center'}), 0);
    return () => window.clearTimeout(timer);
  }, [content, locatedLine]);
  const lines = content.split("\n");
  return (
    <div className="preview-text">
      {markdown && (
        <div className="preview-text-toggle">
          <button
            type="button"
            aria-pressed={rendered}
            onClick={() => setRendered((value) => !value)}
          >
            {rendered ? "查看源码" : "查看渲染"}
          </button>
        </div>
      )}
      {truncated && (
        <div className="preview-truncated" role="status">
          文件较大，仅显示前面部分；可用系统应用打开全文。
        </div>
      )}
      {markdown && rendered ? (
        <div className="preview-markdown">
          <MarkdownBody text={content} />
        </div>
      ) : (
        <pre className="preview-code">
          {lines.map((line, index) => (
            <span key={index} className="preview-code-line" ref={index === locatedLine ? locatedRef : undefined} style={index === locatedLine ? {background:'var(--accent-subtle, #fff2bf)'} : undefined}>
              <span className="preview-line-number">{index + 1}</span>
              <span className="preview-line-text">{line}</span>
            </span>
          ))}
        </pre>
      )}
    </div>
  );
});

interface ImagePreviewProps {
  objectUrl: string;
  alt: string;
}

const ZOOM_STEPS = [1, 1.5, 2, 3];

const ImagePreview = memo(function ImagePreview({ objectUrl, alt }: ImagePreviewProps) {
  const [stepIndex, setStepIndex] = useState(0);
  return (
    <div className="preview-image">
      <div className="preview-image-controls">
        <button
          type="button"
          aria-label="缩小"
          disabled={stepIndex === 0}
          onClick={() => setStepIndex((value) => Math.max(0, value - 1))}
        >
          <Minus aria-hidden="true" size={14} />
        </button>
        <span>{Math.round(ZOOM_STEPS[stepIndex] * 100)}%</span>
        <button
          type="button"
          aria-label="放大"
          disabled={stepIndex === ZOOM_STEPS.length - 1}
          onClick={() =>
            setStepIndex((value) => Math.min(ZOOM_STEPS.length - 1, value + 1))
          }
        >
          <Plus aria-hidden="true" size={14} />
        </button>
        <button type="button" aria-label="重置缩放" onClick={() => setStepIndex(0)}>
          <RotateCcw aria-hidden="true" size={14} />
        </button>
      </div>
      <div className="preview-image-canvas">
        <img
          alt={alt}
          src={objectUrl}
          style={{ width: `${ZOOM_STEPS[stepIndex] * 100}%` }}
        />
      </div>
    </div>
  );
});

const TerminalPanel = lazy(() => import('./TerminalPanel'));
const BrowserPanel = lazy(() => import('./BrowserPanel'));

interface PreviewRailProps {
  embedded?: boolean;
  terminalEnabled?: boolean;
  open: boolean;
  target: PreviewTarget | null;
  threadId: string | null;
  workspaceId: string | null;
  artifactsRevision?: number;
  onClose: () => void;
  onOpenWorkspaceFile: (workspaceId: string, path: string) => void;
  onOpenCitation?: (source: import('../views/LitigationClient').LitigationSource) => void;
  onOpenList: () => void;
}

export const PreviewRail = memo(function PreviewRail({
  embedded = false,
  terminalEnabled = false,
  open,
  target,
  threadId,
  workspaceId,
  artifactsRevision = 0,
  onClose,
  onOpenWorkspaceFile,
  onOpenCitation,
  onOpenList,
}: PreviewRailProps) {
  const [panel, setPanel] = useState<'files' | 'terminal' | 'browser'>('files');
  const [browserRequest, setBrowserRequest] = useState<{url: string; id: number}>();
  useEffect(() => {
    if (!terminalEnabled) return;
    const listener = (event: Event) => {
      const url = (event as CustomEvent<{url?: string}>).detail?.url;
      if (url) setBrowserRequest({url, id: Date.now()});
      setPanel('browser');
    };
    window.addEventListener('verilex:open-browser', listener);
    return () => window.removeEventListener('verilex:open-browser', listener);
  }, [terminalEnabled]);
  const panelCommand = useContext(PanelCommandContext);
  useEffect(() => { if (terminalEnabled && panelCommand) setPanel(panelCommand.panel); }, [panelCommand,terminalEnabled]);
  useEffect(() => { if (target && target.source !== 'artifacts-list') setPanel('files'); }, [target]);
  const [filesMode, setFilesMode] = useState<'workspace' | 'artifacts'>('workspace');
  const [browseLocation, setBrowseLocation] = useState<WorkspaceBrowseLocation>({workspaceId, path: ''});
  useEffect(() => {
    setBrowseLocation({workspaceId, path: ''});
    setFilesMode('workspace');
  }, [threadId, workspaceId]);
  const [width, setWidth] = useState<number>(() => {
    const stored = Number(localStorage.getItem(WIDTH_STORAGE_KEY));
    if (Number.isFinite(stored) && stored >= MIN_WIDTH && stored <= MAX_WIDTH) {
      return stored;
    }
    return DEFAULT_WIDTH;
  });
  const [state, setState] = useState<PreviewState>({ phase: "loading" });
  const citation=target?.source==='workspace'?target.citation:undefined;
  const rawLocation=useCitationLocation(target?.source==='workspace'?target.workspaceId:undefined,citation);
  const located=state.phase==='workspace-ready'&&rawLocation?.locator&&state.preview.file_hash&&rawLocation.locator.file_hash!==state.preview.file_hash?{...rawLocation,status:'stale',notice:'文件在定位时发生变化，请刷新后重新定位。'}:rawLocation;
  const [artifacts, setArtifacts] = useState<ThreadArtifactRecord[] | null>(null);
  const [artifactsError, setArtifactsError] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [openPending, setOpenPending] = useState(false);
  const objectUrlRef = useRef<string | null>(null);

  // Esc closes the rail while it is open.
  useEffect(() => {
    if (!open || (terminalEnabled && panel !== 'files')) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || document.querySelector('dialog[open],[role="dialog"][aria-modal="true"]')) return;
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose, terminalEnabled, panel]);

  // Load the active viewer target, revoking any previous object URL and
  // ignoring results that arrive after the target changed.
  useEffect(() => {
    if (!open) return;
    setOpenError(null);
    if (objectUrlRef.current !== null) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
    if (target === null || target.source === "artifacts-list") {
      setState({ phase: "loading" });
      return;
    }
    let cancelled = false;
    setState({ phase: "loading" });
    if (target.source === "workspace") {
      getWorkspaceFilePreview(target.workspaceId, target.path)
        .then((preview) => {
          if (!cancelled) {
            setState({
              phase: "workspace-ready",
              workspaceId: target.workspaceId,
              preview,
            });
          }
        })
        .catch((error: unknown) => {
          if (cancelled) return;
          const described = describeError(error);
          setState(
            described.missing
              ? { phase: "missing", name: target.path.split("/").pop() ?? target.path }
              : { phase: "error", message: described.message },
          );
        });
    } else {
      loadAttachmentBody(target.threadId, target.attachment)
        .then((body) => {
          if (cancelled) {
            if (body.kind === "image") {
              URL.revokeObjectURL(body.objectUrl);
            }
            return;
          }
          if (body.kind === "image") {
            objectUrlRef.current = body.objectUrl;
          }
          setState({ phase: "attachment-ready", attachment: target.attachment, body });
        })
        .catch((error: unknown) => {
          if (!cancelled) {
            const described = describeError(error);
            setState(
              described.missing
                ? { phase: "missing", name: target.attachment.name }
                : { phase: "error", message: described.message },
            );
          }
        });
    }
    return () => {
      cancelled = true;
    };
  }, [target, open]);

  // Revoke the last object URL when the rail closes or unmounts.
  useEffect(
    () => () => {
      if (objectUrlRef.current !== null) {
        URL.revokeObjectURL(objectUrlRef.current);
      }
    },
    [],
  );

  // Load the artifacts list for list mode.
  useEffect(() => {
    if (target?.source !== "artifacts-list") return;
    setArtifacts(null);
    setArtifactsError(null);
    if (threadId === null) return;
    let cancelled = false;
    listThreadArtifacts(threadId)
      .then((records) => {
        if (!cancelled) setArtifacts(records);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setArtifactsError(
            error instanceof RuntimeApiError
              ? error.message
              : "会话文件列表加载失败。",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [target, threadId, artifactsRevision]);

  const startResize = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = width;
      const onMove = (moveEvent: PointerEvent) => {
        const next = startWidth + (startX - moveEvent.clientX);
        setWidth(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, next)));
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        localStorage.setItem(WIDTH_STORAGE_KEY, String(width));
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [width],
  );

  const runSystemOpen = useCallback(
    async (mode: "open" | "reveal") => {
      if (state.phase !== "workspace-ready") return;
      setOpenPending(true);
      setOpenError(null);
      try {
        await openWorkspacePath(state.workspaceId, {
          path: state.preview.path,
          mode,
        });
      } catch (error: unknown) {
        setOpenError(
          error instanceof RuntimeApiError
            ? error.message
            : "系统打开失败，请稍后重试。",
        );
      } finally {
        setOpenPending(false);
      }
    },
    [state],
  );

  if (!open || target === null) return null;

  return (
    <aside
      className={`preview-rail${embedded ? " preview-embedded" : ""}`}
      style={embedded ? undefined : { width }}
      aria-label={terminalEnabled ? "右侧栏" : "文件预览"}
      role="complementary"
    >
      {!embedded && <div
        className="preview-rail-resizer"
        onPointerDown={startResize}
        role="separator"
        aria-orientation="vertical"
        aria-label="调整预览栏宽度"
      />}
      {terminalEnabled && <div className="rail-panel-tabs">
        <button type="button" aria-pressed={panel === 'files'} onClick={() => setPanel('files')}>文件</button>
        <button type="button" aria-pressed={panel === 'terminal'} onClick={() => setPanel('terminal')}>终端</button>
        <button type="button" aria-pressed={panel === 'browser'} onClick={() => setPanel('browser')}>浏览器</button>
        {panel !== 'files' && <button className="rail-panel-close" type="button" aria-label={panel === 'browser' ? "收起浏览器面板" : "收起终端面板"} onClick={onClose}><X size={16}/></button>}
      </div>}
      {terminalEnabled && panel === 'browser' ? <Suspense fallback={<div className="preview-loading">正在打开浏览器…</div>}><BrowserPanel request={browserRequest}/></Suspense> : terminalEnabled && panel === 'terminal' ? <Suspense fallback={<div className="preview-loading">正在打开终端…</div>}><TerminalPanel preferredWorkspaceId={workspaceId}/></Suspense> : target.source === "artifacts-list" ? (
        <div className="preview-rail-body">
          <div className="preview-rail-head">
            <strong>{terminalEnabled ? "文件" : "会话文件"}</strong>
            <button type="button" aria-label={embedded ? "关闭文件预览" : "关闭预览栏"} onClick={onClose}>
              <X aria-hidden="true" size={16} />
            </button>
          </div>
          {terminalEnabled && <div className="workspace-files-modes" aria-label="文件来源">
            <button type="button" aria-pressed={filesMode === 'workspace'} onClick={() => setFilesMode('workspace')}>工作区文件</button>
            <button type="button" aria-pressed={filesMode === 'artifacts'} onClick={() => setFilesMode('artifacts')}>会话成果</button>
          </div>}
          {terminalEnabled && filesMode === 'workspace' ? <WorkspaceFilesPanel
            location={browseLocation} onLocation={setBrowseLocation}
            onOpen={onOpenWorkspaceFile} revision={artifactsRevision}/>
          : <>
          {terminalEnabled && !threadId && <div className="preview-empty">开始对话后，这里会显示生成或修改过的文件。</div>}
          {artifactsError && (
            <div className="preview-error" role="alert">{artifactsError}</div>
          )}
          {threadId && artifacts === null && !artifactsError && (
            <LoadingState>正在加载会话文件…</LoadingState>
          )}
          {artifacts?.length === 0 && (
            <div className="preview-empty">本次对话还没有生成或修改过文件。</div>
          )}
          {artifacts !== null && artifacts.length > 0 && (
            <ul className="preview-artifact-list">
              {artifacts.map((artifact) => {
                const item = artifactListItem(artifact, workspaceId);
                return (
                  <li key={item.key}>
                    <button
                      type="button"
                      onClick={() => {
                        if (item.workspaceId) {
                          onOpenWorkspaceFile(item.workspaceId, item.path);
                        }
                      }}
                      disabled={!item.workspaceId}
                      title={item.path}
                    >
                      <span className="preview-artifact-name">{artifact.name}</span>
                      <span className="preview-artifact-meta">
                        {OPERATION_LABELS[item.operation] ?? item.operation}
                        {" · "}
                        {item.path}
                      </span>
                      {item.structureChecked && (
                        <span className="preview-artifact-meta">
                          文件结构已检查 · 未做应用内排版预览
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          </>}
        </div>
      ) : (
        <div className="preview-rail-body">
          <div className="preview-rail-head preview-file-toolbar">
            {target.source === "workspace" && (
              <button
                type="button"
                className="preview-back"
                aria-label={terminalEnabled ? "返回文件列表" : "返回会话文件列表"}
                onClick={onOpenList}
                hidden={!threadId && !terminalEnabled}
              >
                <ChevronLeft aria-hidden="true" size={16} />
              </button>
            )}
            <strong className="preview-title" title={target.source === "workspace" ? target.path : target.attachment.name}>
              {target.source === "workspace"
                ? (target.path.split("/").pop() ?? target.path)
                : target.attachment.name}
            </strong>
            {state.phase === "workspace-ready" && <>
              <span className="preview-file-size">{formatSize(state.preview.size_bytes)}</span>
              {state.preview.kind === "docx" && <span className="preview-file-note">只读预览 · 完整排版、批注和修订请用 Word/WPS 查看</span>}
              <div className="preview-system-actions">
                <button
                  type="button"
                  disabled={openPending}
                  onClick={() => void runSystemOpen("open")}
                >
                  <ExternalLink aria-hidden="true" size={14} />
                  用默认应用打开
                </button>
                <button
                  type="button"
                  disabled={openPending}
                  onClick={() => void runSystemOpen("reveal")}
                >
                  <FolderOpen aria-hidden="true" size={14} />
                  在访达中显示
                </button>
              </div>
            </>}
            <button type="button" aria-label={embedded ? "关闭文件预览" : "关闭预览栏"} onClick={onClose}>
              <X aria-hidden="true" size={16} />
            </button>
          </div>

          {state.phase === "loading" && (
            <LoadingState>正在加载预览…</LoadingState>
          )}
          {state.phase === "missing" && (
            <div className="preview-missing" role="status">
              <FileWarning aria-hidden="true" size={20} />
              文件已不在工作区。
            </div>
          )}
          {state.phase === "error" && (
            <div className="preview-error" role="alert">{state.message}</div>
          )}

          {state.phase === "workspace-ready" && (
            <>
              {openError && (
                <div className="preview-error" role="alert">{openError}</div>
              )}
              {citation&&state.preview.kind!=='docx'&&<details open><summary>{located?.notice||(located?.status==='located'?'已核对引文位置。':'正在核对引文位置…')}</summary><blockquote>{citation.quote}</blockquote>{state.preview.kind==='text'&&located?.status==='located'&&located.locator&&<p>第 {located.locator.index+1} 行</p>}</details>}
              {state.preview.kind === "text" && (
                <TextPreview
                  content={state.preview.content ?? ""}
                  truncated={state.preview.truncated}
                  markdown={isMarkdown(state.preview.name)}
                  locatedLine={located?.status==='located' ? located.locator?.index : undefined}
                />
              )}
              {state.preview.kind === "image" && state.preview.data_url && (
                <ImagePreview
                  objectUrl={state.preview.data_url}
                  alt={state.preview.name}
                />
              )}
              {target?.source === 'workspace' && /^(?:文书起草|工作成果)\/图表-[a-f0-9]{8}\.svg$/.test(target.path) && <DiagramSources workspaceId={target.workspaceId} path={target.path} onPage={path=>onOpenWorkspaceFile(target.workspaceId,path)} onOpen={source => onOpenCitation ? onOpenCitation(source) : onOpenWorkspaceFile(target.workspaceId, source.path)}/>}
              {state.preview.kind === "pdf" && state.preview.data_url && (
                <div className="preview-pdf">
                  <Suspense
                    fallback={
                      <div className="preview-pdf-pending" role="status">
                        正在打开 PDF…
                      </div>
                    }
                  >
                    <PdfViewer viewKey={target.source === "workspace" ? `${target.workspaceId}:${target.path}:${state.preview.file_hash ?? ""}` : undefined} source={state.preview.data_url} page={target.source === "workspace" ? (located?.status==='located'?located.page:target.page) : undefined} />
                  </Suspense>
                </div>
              )}
              {!terminalEnabled && target.source === "workspace"&&<MaterialCoverage workspaceId={target.workspaceId} path={target.path}/>}
              {state.preview.kind === "docx" && state.preview.data_url && (
                <Suspense fallback={<div role="status">正在打开 Word…</div>}>
                  <DocxViewer showNote={false} source={state.preview.data_url} citation={target.source === "workspace" ? target.citation : undefined} workspaceId={target.source === "workspace" ? target.workspaceId : undefined}/>
                </Suspense>
              )}
              {state.preview.kind === "unsupported" && (
                <div className="preview-unsupported" role="status">
                  <FileWarning aria-hidden="true" size={20} />
                  <p>暂不支持在应用内预览此格式。</p>
                  <p className="preview-unsupported-hint">
                    点击上方“用默认应用打开”查看文件。
                  </p>
                </div>
              )}
            </>
          )}

          {state.phase === "attachment-ready" && (
            <>
              <div className="preview-file-meta">
                <span>{formatSize(state.attachment.sizeBytes)}</span>
                <span>附件</span>
              </div>
              {state.body.kind === "image" && (
                <ImagePreview
                  objectUrl={state.body.objectUrl}
                  alt={state.attachment.name}
                />
              )}
              {state.body.kind === "text" && (
                <TextPreview
                  content={state.body.content}
                  truncated={state.body.truncated}
                  markdown={isMarkdown(state.attachment.name)}
                />
              )}
              {state.body.kind === "pdf" && (
                <div className="preview-pdf">
                  <Suspense
                    fallback={
                      <div className="preview-pdf-pending" role="status">
                        正在打开 PDF…
                      </div>
                    }
                  >
                    <PdfViewer source={state.body.blob} />
                  </Suspense>
                </div>
              )}
              {state.body.kind === "docx" && (
                <Suspense fallback={<div role="status">正在打开 Word…</div>}>
                  <DocxViewer source={state.body.blob}/>
                </Suspense>
              )}
              {state.body.kind === "unsupported" && (
                <div className="preview-unsupported" role="status">
                  <FileWarning aria-hidden="true" size={20} />
                  <p>暂不支持在应用内预览此格式。</p>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </aside>
  );
});
