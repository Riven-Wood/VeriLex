import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileText, Upload, History, ChevronDown, Trash2, Save } from "lucide-react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { invoke } from "@tauri-apps/api/core";
import type { ReactNode } from "react";
import {
  contractReviewApi,
  DEFAULT_REVIEW_SKILL,
  type ContractReviewDetail,
  type ContractReviewSummary,
  type ReviewSkillEntry,
  type ReviewParty,
  type ReviewStrictness,
  type StartReviewOptions,
} from "../runtime/contractReviewClient";
import { saveContractReview } from "../platform/contractReviewExport";
import type { ThreadSummary } from "../app/navigation";
import { ContractSkillMenu } from "../components/ContractSkillMenu";
import { WordDocumentEditor, type WordEditorHandle } from "../components/wordEditor/WordDocumentEditor";
import { wordDocumentApi, type WordDocument } from "../runtime/wordDocumentClient";
import "./ContractReviewView.css";

const SIGNATURE_KEY = "verilex:contract-signature:v1";
const RETRIEVAL_SEEN_KEY = "verilex:contract-retrieval-notice:v1";
const PARTY_LABEL: Record<ReviewParty, string> = {
  party_a: "甲方",
  party_b: "乙方",
  neutral: "不指定（中立）",
};
const STRICTNESS_LABEL: Record<ReviewStrictness, string> = {
  loose: "宽松（只报重大风险）",
  normal: "常规",
  strict: "严格（事无巨细）",
};
const STATUS_LABEL: Record<string, string> = {
  pending: "等待开始",
  running: "审查中",
  completed: "已完成",
  failed: "失败",
  stopped: "已停止",
};

export interface ContractTaskSnapshot {
  skill: string; party: ReviewParty; requirements: string; strictness: ReviewStrictness;
  signature: string; retrieval: boolean; newReview: boolean;
  document: WordDocument | null; detail: ContractReviewDetail | null; threadId: string | null;
}
export interface ContractReviewViewProps {
  initialSnapshot?: ContractTaskSnapshot;
  onSnapshot?: (snapshot: ContractTaskSnapshot) => void;
  taskTabs?: ReactNode;
  onBusyChange?: (busy: boolean) => void;
  thread: ThreadSummary | null;
  hasModel: boolean;
  renderAssistant: (quote: string, clearQuote: () => void, editingCopy?: boolean) => ReactNode;
  onStartReview: (file: File, options: StartReviewOptions) => void | Promise<void>;
  onSelectReview: (threadId: string) => void;
  onRegisterLeaveGuard?: (guard: (() => Promise<boolean>) | null) => void;
}

export function ContractReviewView({
  thread,
  hasModel,
  renderAssistant,
  onStartReview,
  onSelectReview,
  onRegisterLeaveGuard, initialSnapshot, onSnapshot, taskTabs, onBusyChange,
}: ContractReviewViewProps) {
  const [skills, setSkills] = useState<ReviewSkillEntry[]>([]);
  const [skill, setSkill] = useState<string>(initialSnapshot?.skill ?? DEFAULT_REVIEW_SKILL);
  const [party, setParty] = useState<ReviewParty>(initialSnapshot?.party ?? "neutral");
  const [requirements, setRequirements] = useState(initialSnapshot?.requirements ?? "");
  const [strictness, setStrictness] = useState<ReviewStrictness>(initialSnapshot?.strictness ?? "normal");
  const [signature, setSignature] = useState<string>(
    () => initialSnapshot?.signature ?? window.localStorage.getItem(SIGNATURE_KEY) ?? "VeriLex",
  );
  const [retrieval, setRetrieval] = useState(initialSnapshot?.retrieval ?? false);
  const [retrievalNotice, setRetrievalNotice] = useState<string | null>(null);
  const [history, setHistory] = useState<ContractReviewSummary[]>([]);
  const [detail, setDetail] = useState<ContractReviewDetail | null>(initialSnapshot?.detail ?? null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportStatus, setExportStatus] = useState("");
  const [newReview, setNewReview] = useState(initialSnapshot?.newReview ?? false);
  const [wordDocument, setWordDocument] = useState<WordDocument | null>(initialSnapshot?.document ?? null);
  const [wordReady, setWordReady] = useState(false);
  const [localDocuments, setLocalDocuments] = useState<{id:string;filename:string}[]>([]);
  const wordEditor = useRef<WordEditorHandle>(null);
  const snapshotRef = useRef<ContractTaskSnapshot>(null!);
  snapshotRef.current = {skill, party, requirements, strictness, signature, retrieval, newReview, document:wordDocument, detail, threadId:newReview ? null : thread?.id ?? null};
  const captureRef = useRef(onSnapshot); captureRef.current = onSnapshot;
  useEffect(() => {
    onRegisterLeaveGuard?.(async () => {
      try { if (wordEditor.current) { const saved = await wordEditor.current.flush(); setWordDocument(saved.document); captureRef.current?.({...snapshotRef.current, document:saved.document}); } return true; }
      catch (cause) { setError(cause instanceof Error ? cause.message : "当前合同未能保存，请先另存需要保留的内容。"); return false; }
    });
    return () => onRegisterLeaveGuard?.(null);
  }, [onRegisterLeaveGuard]);
  useEffect(() => {
    onSnapshot?.({skill, party, requirements, strictness, signature, retrieval, newReview, document:wordDocument, detail, threadId:newReview ? null : thread?.id ?? null});
  }, [skill, party, requirements, strictness, signature, retrieval, newReview, wordDocument, detail, thread?.id, onSnapshot]);
  const initialThread = useRef(true);
  const initialRequirements = useRef(true);
  const openRequest = useRef(0);
  const [deleteTarget, setDeleteTarget] = useState<{id:string;filename:string;kind:"document"|"review"}|null>(null);
  const [deleting, setDeleting] = useState(false);
  const deleteLock = useRef(false);
  useEffect(() => {
    const closeMenus = (event: PointerEvent) => {
      document.querySelectorAll<HTMLDetailsElement>(".contract-review-view details[open]").forEach(menu => {
        if (!menu.contains(event.target as Node)) menu.open = false;
      });
    };
    const iframeFocus = () => { window.setTimeout(() => { if (document.activeElement?.tagName === "IFRAME") document.querySelectorAll<HTMLDetailsElement>(".contract-review-view details[open]").forEach(menu => {menu.open=false;}); }, 0); };
    document.addEventListener("pointerdown", closeMenus, true);
    window.addEventListener("blur", iframeFocus);
    return () => {document.removeEventListener("pointerdown",closeMenus,true);window.removeEventListener("blur",iframeFocus);};
  }, []);
  const requirementsMenu = useRef<HTMLDetailsElement>(null);
  const positionRequirements = () => {
    const menu = requirementsMenu.current;
    if (!menu?.open) return;
    const bounds = menu.closest(".contract-review-view")!.getBoundingClientRect();
    const trigger = menu.getBoundingClientRect();
    const width = Math.min(460, Math.max(0, bounds.width - 24));
    const left = Math.max(bounds.left + 12, Math.min(trigger.left, bounds.right - width - 12));
    menu.style.setProperty("--requirements-width", `${width}px`);
    menu.style.setProperty("--requirements-shift", `${left - trigger.left}px`);
  };
  useEffect(() => {
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(positionRequirements);
    const heading = requirementsMenu.current?.closest(".contract-review-heading");
    if (heading) observer?.observe(heading);
    window.addEventListener("resize", positionRequirements);
    return () => { observer?.disconnect(); window.removeEventListener("resize", positionRequirements); };
  }, []);
  const historyMenu = useRef<HTMLDetailsElement>(null);
  const exportMenu = useRef<HTMLDetailsElement>(null);
  const startLock = useRef(false);
  const dropReadLock = useRef(false);
  const [readingDrop, setReadingDrop] = useState(false);
  const selectedThread = useRef(thread?.id);
  selectedThread.current = thread?.id;
  const detailRequest = useRef(0);
  const fileInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    contractReviewApi
      .listSkills()
      .then((rows) => {
        setSkills(rows);
        if (rows.length && !rows.some((row) => row.name === skill)) {
          setSkill(rows[0].name);
        }
      })
      .catch(() => setSkills([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshHistory = useCallback(async () => {
    try { setHistory(await contractReviewApi.list()); }
    catch { setError("审查历史加载失败，请重试。"); }
  }, []);
  useEffect(() => {
    refreshHistory();
  }, [refreshHistory, thread?.id, thread?.status]);

  const activeReviewId =
    detail != null && detail.thread_id === thread?.id ? detail.id : null;
  const refreshDetail = useCallback(
    async (reviewId: string) => {
      const request = ++detailRequest.current;
      const threadId = selectedThread.current;
      try {
        const row = await contractReviewApi.get(reviewId);
        if (request === detailRequest.current && threadId === selectedThread.current) {
          setDetail(row);
          if (row) setHistory(current => current.map(item => item.id === row.id
            ? {...item, status: row.status, updated_at: row.updated_at, opinion_count: row.opinions.length}
            : item));
        }
      } catch {
        if (request === detailRequest.current && threadId === selectedThread.current) setError("审查详情加载失败，请重试。");
      }
    },
    [],
  );
  useEffect(() => {
    if (!activeReviewId) return;
    if (!["pending", "running"].includes(detail?.status ?? "") && (!thread || !["running", "awaiting_approval"].includes(thread.status))) return;
    const timer = window.setInterval(() => refreshDetail(activeReviewId!), 3000);
    return () => window.clearInterval(timer);
  }, [activeReviewId, detail?.status, thread?.status, refreshDetail]);

  const selectedHistoryId = history.find(row => row.thread_id === thread?.id)?.id;

  useEffect(() => {
    if (initialThread.current) { initialThread.current = false; if(initialSnapshot) return; }
    ++detailRequest.current;
    setDetail(null); setNewReview(false); setExportStatus("");
    setWordDocument(null); setWordReady(false); setRequirements("");
  }, [thread?.id]);

  useEffect(() => {
    if (newReview) return;
    if (!thread) {
      setDetail(null);
      return;
    }
    if (selectedHistoryId) refreshDetail(selectedHistoryId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thread?.id, thread?.status, selectedHistoryId, refreshDetail, newReview]);

  useEffect(() => {
    if (initialRequirements.current) { initialRequirements.current = false; if(initialSnapshot) return; }
    if (detail) setRequirements(detail.requirements ?? "");
    // Polling the same review must not overwrite the next review's draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.id]);


  const acceptFile = useCallback(
    async (files: File[]) => {
      if (startLock.current || dropReadLock.current || thread && ["running", "awaiting_approval"].includes(thread.status)) return;
      if (detail && !newReview) { setError("请先点“新建任务”打开另一份合同。"); return; }
      setError("");
      if (files.length !== 1) {
        setError("一次请审查一份合同。");
        return;
      }
      const file = files[0];
      if (! /\.docx?$/i.test(file.name)) {
        setError("请选择 .doc 或 .docx 文件；加密或损坏的文件请先用 Word/WPS 另存后重试。");
        return;
      }
      setStarting(true);
      startLock.current = true;
      const request = ++openRequest.current;
      try {
        if (wordEditor.current) await wordEditor.current.flush();
        if (request !== openRequest.current) return;
        const document = await wordDocumentApi.open(file);
        if (request !== openRequest.current) return;
        if (wordDocument) setRequirements("");
        setNewReview(true); setDetail(null); setWordDocument(document); setWordReady(false);
        window.localStorage.setItem("verilex:contract-word:last", document.id);
        void wordDocumentApi.list().then(setLocalDocuments).catch(() => {});
      } catch (cause) { setError(cause instanceof Error ? cause.message : "无法打开合同，请重试。"); }
      finally { setStarting(false); startLock.current = false; }
    },
    [thread, detail, newReview, wordDocument],
  );

  useEffect(() => {
    void wordDocumentApi.list().then(setLocalDocuments).catch(() => {});
    const request = ++openRequest.current;
    const last = window.localStorage.getItem("verilex:contract-word:last");
    if (thread || !last || initialSnapshot) return;
    void wordDocumentApi.get(last).then(row => {
      if (request === openRequest.current) setWordDocument(row);
    }).catch(() => setError("上次打开的合同未能恢复，请从本机文档中重试。"));
    return () => { ++openRequest.current; };
  }, [thread?.id]);

  useEffect(() => {
    if (!activeReviewId || newReview) return;
    const request = ++openRequest.current;
    void wordDocumentApi.forReview(activeReviewId).then(row => {
      if (request === openRequest.current) { if(snapshotRef.current.document?.id !== row.id) setWordReady(false); setWordDocument(row); }
    }).catch(cause => setError(cause instanceof Error ? cause.message : "合同编辑副本未能打开。"));
    return () => { ++openRequest.current; };
  }, [activeReviewId, newReview]);

  const startWordReview = async () => {
    if (!wordEditor.current || startLock.current) return;
    if (!hasModel) { setError("请先在设置中连接模型服务商，再开始审查。文档仍可直接编辑。"); return; }
    setStarting(true); startLock.current = true; setError("");
    try {
      const {document, bytes} = await wordEditor.current.flush();
      window.localStorage.setItem(SIGNATURE_KEY, signature);
      const file = new File([bytes.slice().buffer], document.filename.replace(/\.doc$/i, ".docx"), {type:"application/vnd.openxmlformats-officedocument.wordprocessingml.document"});
      const options = !newReview ? detail : null;
      await onStartReview(file, {skill:options?.skill ?? skill, party:(options?.party as ReviewParty) ?? party, strictness:(options?.strictness as ReviewStrictness) ?? strictness, signature:options?.signature ?? signature, retrieval:options?.retrieval_enabled ?? retrieval, requirements:requirements.trim(), providerId:null, model:"", documentId:document.id, documentRevision:document.revision});
      await refreshHistory();
    } catch(cause) { setError(cause instanceof Error ? cause.message : "审查没有启动，请重试。"); }
    finally { setStarting(false); startLock.current = false; }
  };

  const dropOptions = useRef({ acceptFile, thread, detail, newReview });
  dropOptions.current = { acceptFile, thread, detail, newReview };
  useEffect(() => {
    if (!Reflect.has(globalThis, "__TAURI_INTERNALS__")) return;
    let cancelled = false;
    let dispose: (() => void) | undefined;
    void getCurrentWebview().onDragDropEvent(event => {
      if (cancelled || event.payload.type !== "drop" || startLock.current || dropReadLock.current) return;
      const current = dropOptions.current;
      if (current.thread && ["running", "awaiting_approval"].includes(current.thread.status)) return;
      // Existing reviews may have an assistant attachment listener of their own.
      if (current.detail && !current.newReview) return;
      const paths = event.payload.paths;
      if (paths.length !== 1) { setError("一次请审查一份合同。"); return; }
      const name = paths[0].split(/[\\/]/).pop() ?? "";
      if (!/\.docx?$/i.test(name)) { setError("请选择 .doc 或 .docx 文件；加密或损坏的文件请先用 Word/WPS 另存后重试。"); return; }
      dropReadLock.current = true;
      setReadingDrop(true);
      setError("");
      void (async () => {
        try {
          const payload = await invoke<ArrayBuffer | number[]>("read_dropped_file", { path: paths[0] });
          const bytes = payload instanceof ArrayBuffer ? new Uint8Array(payload) : Uint8Array.from(payload);
          if (bytes.byteLength < 8) throw new Error("invalid dropped file payload");
          if (cancelled || dropOptions.current.thread?.id !== current.thread?.id) return;
          dropReadLock.current = false;
          await dropOptions.current.acceptFile([new File([bytes.slice(8)], name, {
            type: /\.docx$/i.test(name) ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document" : "application/msword",
            lastModified: Number(new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getBigUint64(0, true)),
          })]);
        } catch (cause) {
          if (!cancelled) setError(cause === "dropped_file_too_large" ? "拖入的文件过大，请缩小文件后重试。" : "无法读取拖入的文件，请使用“选择合同”重试。");
        } finally {
          dropReadLock.current = false;
          if (!cancelled) setReadingDrop(false);
        }
      })();
    }).then(unlisten => { if (cancelled) unlisten(); else dispose = unlisten; })
      .catch(() => { if (!cancelled) setError("无法启用文件拖放，请使用“选择合同”。"); });
    return () => { cancelled = true; dispose?.(); };
  }, []);

  const toggleRetrieval = useCallback((enabled: boolean) => {
    setRetrieval(enabled);
    if (!enabled) return;
    if (window.localStorage.getItem(RETRIEVAL_SEEN_KEY)) return;
    window.localStorage.setItem(RETRIEVAL_SEEN_KEY, "1");
    setRetrievalNotice(
      "开启后，涉及法条时会向法规检索渠道（国家法律法规数据库或你所选法规库插件）发送法律概念词；只发概念词，不发送合同正文、当事人名称或金额。",
    );
  }, []);

  const runExport = useCallback(async (report = false) => {
    if (!detail) return;
    setExporting(true);
    setError("");
    setExportStatus("");
    try {
      const result = await (report ? contractReviewApi.report(detail.id) : contractReviewApi.export(detail.id));
      const path = await saveContractReview(result);
      const label = report ? "审查意见书" : "批注版";
      setExportStatus(path === null ? "已取消保存。" : Reflect.has(globalThis, '__TAURI_INTERNALS__') ? `${label}已保存：${path}` : `已开始下载${label}。`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : typeof cause === "string" ? cause : "导出没有成功；原件没有被改动。");
    } finally {
      setExporting(false);
    }
  }, [detail]);

  const stopReview = useCallback(async () => {
    if (!detail) return;
    try {
      await contractReviewApi.stop(detail.id);
      refreshDetail(detail.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "停止失败，请重试。");
    }
  }, [detail, refreshDetail]);

  const busy = ["pending", "running"].includes(detail?.status ?? "") || thread != null && ["running", "awaiting_approval"].includes(thread.status);
  useEffect(() => { onBusyChange?.(busy || starting || readingDrop); }, [busy, starting, readingDrop, onBusyChange]);
  const saved = !newReview && detail?.thread_id === thread?.id ? detail : null;
  async function deleteHistory() {
    if (!deleteTarget || deleteLock.current || busy || starting) return;
    const target=deleteTarget;deleteLock.current=true;setDeleting(true);
    try {
      if(wordEditor.current) await wordEditor.current.flush();
      await (target.kind === "document" ? wordDocumentApi.remove(target.id) : contractReviewApi.remove(target.id));
      ++openRequest.current;++detailRequest.current;
      if(target.kind === "document") setLocalDocuments(rows=>rows.filter(row=>row.id!==target.id));
      else setHistory(rows=>rows.filter(row=>row.id!==target.id));
      if((target.kind === "document" && wordDocument?.id===target.id)||(target.kind === "review" && detail?.id===target.id)) {
        setNewReview(true);setDetail(null);setRequirements("");setWordDocument(null);setWordReady(false);
        window.localStorage.removeItem("verilex:contract-word:last");
      } else if(target.kind === "document" && window.localStorage.getItem("verilex:contract-word:last")===target.id) window.localStorage.removeItem("verilex:contract-word:last");
      setDeleteTarget(null);setError("");
    } catch {setError("删除未完成。请确认相关审查已停止，再重试。");}
    finally {deleteLock.current=false;setDeleting(false);}
  }
  const reviewSkills = useMemo(() => {
    const rows = skills.length ? skills : [{ name: DEFAULT_REVIEW_SKILL, description: "" }];
    return saved && !rows.some(row => row.name === saved.skill) ? [{ name: saved.skill, description: "" }, ...rows] : rows;
  }, [skills, saved?.skill]);

  return (
    <section
      className="contract-review-view"
      aria-label="合同审查"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        acceptFile(Array.from(event.dataTransfer.files));
      }}
    >
      <header className="contract-review-heading">
        <h1>合同审查</h1>
      <div className="contract-review-bar" id="contract-review-settings" role="group" aria-label="审查设置">
        <div className="contract-review-bar-group">
          <ContractSkillMenu value={saved?.skill ?? skill} entries={reviewSkills}
            onChange={setSkill} disabled={starting || readingDrop || !!saved} />
        </div>
        <label>
          代表方
          <select
            value={saved?.party ?? party}
            onChange={(event) => setParty(event.target.value as ReviewParty)}
            disabled={starting || readingDrop || !!saved}
          >
            {(Object.keys(PARTY_LABEL) as ReviewParty[]).map((key) => (
              <option key={key} value={key}>
                {PARTY_LABEL[key]}
              </option>
            ))}
          </select>
        </label>
        <label>
          审查口径
          <select
            value={saved?.strictness ?? strictness}
            onChange={(event) => setStrictness(event.target.value as ReviewStrictness)}
            disabled={starting || readingDrop || !!saved}
          >
            {(Object.keys(STRICTNESS_LABEL) as ReviewStrictness[]).map((key) => (
              <option key={key} value={key}>
                {STRICTNESS_LABEL[key]}
              </option>
            ))}
          </select>
        </label>
        <label>
          批注署名
          <input
            value={saved?.signature ?? signature}
            maxLength={80}
            onChange={(event) => setSignature(event.target.value)}
            disabled={starting || readingDrop || !!saved}
          />
        </label>
        <label className="contract-review-toggle">
          <input
            type="checkbox"
            checked={saved?.retrieval_enabled ?? retrieval}
            onChange={(event) => toggleRetrieval(event.target.checked)}
            disabled={starting || readingDrop || !!saved}
          />
          法条检索
        </label>
        <details className="contract-requirements-menu" ref={requirementsMenu} onToggle={positionRequirements} onKeyDown={event => { if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); } }}>
          <summary>审查要求{requirements.trim() && <span className="contract-requirements-filled" aria-label="已填写">·</span>}<ChevronDown size={14}/></summary>
          <div className="contract-review-requirements">
        <label htmlFor="contract-review-requirements">审查要求（选填）</label>
        <textarea id="contract-review-requirements" rows={2} maxLength={4000}
          value={requirements} onChange={event => setRequirements(event.target.value)}
          disabled={starting || readingDrop || busy}
          placeholder="例如：我方为出租方，重点审查租金支付、提前解约和装修恢复责任；尽量保留原有条款。"
          aria-describedby={saved && requirements.trim() !== (saved.requirements ?? "") ? "contract-requirements-change" : undefined} />
        {saved && requirements.trim() !== (saved.requirements ?? "") && <div id="contract-requirements-change" className="contract-requirements-change">
          <span>修改后的要求将在重新审查时使用。</span>
          <details><summary>本次审查使用的要求</summary><p>{saved.requirements || "未填写，按审查设置审查。"}</p></details>
        </div>}
      </div>
        </details>
      </div>
        <div className="contract-review-actions">
          <details className="contract-review-history" ref={historyMenu} onKeyDown={event => { if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); } }}>
            <summary className="contract-icon-action" aria-label="审查历史" title="审查历史"><History size={17}/><ChevronDown size={12}/></summary>
            <div className="contract-review-panel" aria-label="审查历史记录">
              {localDocuments.length > 0 && <><p className="contract-review-hint">本机文档</p><ul>{localDocuments.map(row => <li className="contract-history-row" key={row.id}><button className="contract-history-open" disabled={starting || busy || deleting} onClick={async () => {
                if (historyMenu.current) historyMenu.current.open = false;
                try { if (wordEditor.current) await wordEditor.current.flush(); const request = ++openRequest.current; const document = await wordDocumentApi.get(row.id); if (request !== openRequest.current) return; setNewReview(true); setDetail(null); setRequirements(""); setWordDocument(document); window.localStorage.setItem("verilex:contract-word:last",row.id); }
                catch(cause){setError(cause instanceof Error?cause.message:"文档未能打开。");}
              }}><strong>{row.filename}</strong><span>本机编辑副本</span></button><button className="contract-history-delete" aria-label={`删除文档：${row.filename}`} title="删除本机编辑副本" disabled={starting||busy||deleting} onClick={()=>setDeleteTarget({...row,kind:"document"})}><Trash2 size={15}/></button></li>)}</ul></>}            {history.length === 0 ? (
              <p className="contract-review-hint">还没有审查记录。</p>
            ) : (
              <><p className="contract-review-hint">审查记录</p><ul>
                {history.map((row) => (
                  <li className="contract-history-row" key={row.id}>
                    <button disabled={starting||busy||deleting}
                      className={`contract-history-open ${detail?.id === row.id ? "contract-review-active" : ""}`}
                      onClick={async () => {
                        try {
                          if (wordEditor.current) await wordEditor.current.flush();
                          if (historyMenu.current) historyMenu.current.open = false;
                          if (!taskTabs) setNewReview(false); onSelectReview(row.thread_id); if (row.thread_id === thread?.id) void refreshDetail(row.id);
                        } catch (cause) { setError(cause instanceof Error ? cause.message : "请先保存当前合同，再打开审查历史。"); }
                      }}
                    >
                      <strong>{row.filename}</strong>
                      <span>
                        {STATUS_LABEL[row.status] ?? row.status} · {row.opinion_count} 条意见
                      </span>
                    </button>
                    <button className="contract-history-delete" aria-label={`删除审查：${row.filename}`} title="删除审查记录" disabled={starting||busy||deleting||["pending","running"].includes(row.status)} onClick={()=>setDeleteTarget({...row,kind:"review"})}><Trash2 size={15}/></button>
                  </li>
                ))}
              </ul></>
            )}
</div>
          </details>
          {deleteTarget && <div className="contract-delete-backdrop"><section onKeyDown={event=>{if(event.key==="Escape"&&!deleting)setDeleteTarget(null);if(event.key==="Tab"){const buttons=[...event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];if(!buttons.length){event.preventDefault();return;}const next=event.shiftKey?buttons[0]:buttons[buttons.length-1];if(document.activeElement===next){event.preventDefault();(event.shiftKey?buttons[buttons.length-1]:buttons[0]).focus();}}}} role="dialog" aria-modal="true" aria-label="删除历史记录" className="contract-delete-dialog">
            <h2>删除{deleteTarget.kind === "document" ? "本机编辑副本" : "审查记录"}？</h2><p className="contract-delete-name">{deleteTarget.filename}</p>
            <p>{deleteTarget.kind === "document" ? "将删除这份编辑副本及其中的修改，已有审查记录保留。" : "将删除本次审查记录和意见，本机编辑副本保留。"} 原始合同文件不受影响。删除后无法恢复，请先另存需要保留的成果。</p>
            <div><button autoFocus disabled={deleting} onClick={()=>setDeleteTarget(null)}>取消</button><button disabled={deleting} onClick={()=>void deleteHistory()}>{deleting?"正在删除…":"确认删除"}</button></div>
          </section></div>}

          {busy && <button disabled={starting} onClick={() => void stopReview()}>停止审查</button>}
          {wordDocument && <button className="primary-button" disabled={busy || starting || !wordReady} onClick={() => void startWordReview()}>{starting ? "正在准备审查…" : saved ? "按当前文档重新审查" : "开始审查"}</button>}
          {saved && <details className="contract-review-export" ref={exportMenu} onKeyDown={event => { if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); } }}>
            <summary>{exporting ? "正在导出…" : "导出审查成果"}<ChevronDown size={14} /></summary>
            <div>
              <button disabled={exporting || busy || !saved.opinions.some(opinion => opinion.adopted)} onClick={() => { if (exportMenu.current) exportMenu.current.open = false; void runExport(); }}>导出批注版</button>
              <button disabled={exporting || busy || !saved.opinions.some(opinion => opinion.adopted)} onClick={() => { if (exportMenu.current) exportMenu.current.open = false; void runExport(true); }}>出具审查意见 Word</button>
            </div>
          </details>}
        </div>
      </header>
      <input ref={fileInput} aria-label="选择合同文件" type="file" accept=".doc,.docx" hidden onChange={event => { acceptFile(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
      {retrievalNotice ? (
        <p className="contract-review-notice" role="status">
          {retrievalNotice}
        </p>
      ) : null}
      {error ? (
        <p className="contract-review-error" role="alert">
          {error}
          <button onClick={() => setError("")}>知道了</button>
        </p>
      ) : null}
      {exportStatus ? <p className="contract-review-notice" role="status">{exportStatus}</p> : null}
      <div className="contract-task-bar">{taskTabs ?? <span className="contract-task-filename">{wordDocument?.filename ?? detail?.filename ?? "新建任务"}</span>}
        {wordDocument && <button className="contract-task-save" aria-label="另存 Word" title="另存 Word" disabled={!wordReady} onClick={() => void wordEditor.current?.exportCopy()}><Save size={16}/></button>}
      </div>
      <div className="contract-review-columns" data-empty={!detail && !wordDocument} style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
        <div className="contract-review-center">
          {!detail && wordDocument ? <WordDocumentEditor key={wordDocument.id} ref={wordEditor} document={wordDocument} author={signature} onReady={setWordReady} hideHeader quiet historyToolbar /> : !detail ? (
            <div className="contract-review-empty">
              <div className="contract-review-file-icon"><FileText size={36} strokeWidth={1.4} /></div>
              <h2>把合同拖到这里打开编辑</h2>
              <p className="contract-review-hint">支持 Word（.doc / .docx）文件</p>
              <button className="primary-button" disabled={starting || readingDrop || busy} onClick={() => fileInput.current?.click()}><Upload size={16} />{readingDrop ? "正在读取合同…" : starting ? "正在打开合同…" : "选择合同"}</button>
              <p className="contract-review-upload-note">先在本机打开编辑；点击“开始审查”后才发送给模型服务商。<br />旧版 .doc 在本机转换，结果另存为 .docx，原件不变。</p>
            </div>
          ) : (
            <div className="contract-review-doc" role="region" aria-label={`${detail.filename} 的审查文档`}>
              {wordDocument ? <WordDocumentEditor key={wordDocument.id} ref={wordEditor} document={wordDocument} author={detail.signature} review={{id:detail.id,status:detail.status,opinion_count:detail.opinions.length}} onReady={setWordReady} hideHeader quiet historyToolbar /> : <p role="status">正在打开 Word 文档…</p>}
            </div>
          )}
        </div>
      </div>
      {/* Keep the review controller mounted so queued turns can start without a chat panel. */}
      <div hidden>{!newReview && thread && renderAssistant("", () => {}, false)}</div>
    </section>
  );
}
