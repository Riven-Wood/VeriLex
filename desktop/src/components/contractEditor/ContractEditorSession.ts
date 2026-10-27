import { contractEditorApi, type ContractEditorState, type ContractEditorTurnContext } from '../../runtime/contractEditorClient';

export interface EditorSelection { paragraphId: string; start: number; end: number; text: string }
export interface SessionSnapshot {
  document: ContractEditorState | null;
  edits: ReadonlyMap<string, string>;
  selection: EditorSelection | null;
  saving: boolean;
  deciding: boolean;
  composing: boolean;
  compositionSuspended: boolean;
  loading: boolean;
  error: string;
  conflicts: ReadonlyMap<string, string>;
}
const message = (cause: unknown) => cause instanceof Error ? cause.message : '编辑副本未能保存，请重试。';

/** One controller per review keeps late saves and unsaved recovery isolated across navigation. */
export class ContractEditorSession {
  private listeners = new Set<() => void>();
  private snapshot: SessionSnapshot = { document: null, edits: new Map(), selection: null, saving: false, deciding: false, composing: false, compositionSuspended: false, loading: false, error: '', conflicts: new Map() };
  private pending: Promise<void> | null = null;
  private loading: Promise<void> | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private deadline: ReturnType<typeof setTimeout> | null = null;
  private epoch = 0;
  private refreshRequested = false;
  private capturedSelection: EditorSelection | null = null;
  constructor(readonly reviewId: string) {}
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(patch: Partial<SessionSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach(listener => listener());
  }
  refresh = () => { this.refreshRequested = true; this.drainRefresh(); };
  private drainRefresh() {
    if (!this.refreshRequested || this.loading || this.pending || this.snapshot.edits.size || this.snapshot.composing || this.snapshot.compositionSuspended || this.snapshot.deciding) return;
    this.refreshRequested = false;
    void this.load();
  }
  async load() {
    if (this.loading) return this.loading;
    if (this.pending || this.snapshot.edits.size || this.snapshot.composing || this.snapshot.deciding) return;
    const epoch = this.epoch;
    if (!this.snapshot.document) this.update({ loading: true });
    this.loading = (async () => {
      try {
        const document = await contractEditorApi.get(this.reviewId);
        if (epoch === this.epoch) {
          if (document.revision < (this.snapshot.document?.revision ?? -1)) return;
          const selection = this.snapshot.selection;
          const oldText = this.snapshot.document?.paragraphs.find(p => p.id === selection?.paragraphId)?.text;
          const newText = document.paragraphs.find(p => p.id === selection?.paragraphId)?.text;
          this.update({ document, error: '', selection: oldText === newText ? selection : null });
        } else this.refreshRequested = true;
      } catch (cause) { if (epoch === this.epoch) this.update({ error: message(cause) }); }
      finally { this.loading = null; this.update({ loading: false }); this.drainRefresh(); }
    })();
    return this.loading;
  }
  edit(paragraphId: string, text: string) {
    const paragraph = this.snapshot.document?.paragraphs.find(p => p.id === paragraphId);
    if (!paragraph?.editable || this.snapshot.deciding || this.snapshot.conflicts.size) return;
    if (/[\r\n\t]/.test(text)) { this.update({ error: '当前只支持段落内修改文字，请勿插入换行或制表符。' }); return; }
    ++this.epoch;
    const edits = new Map(this.snapshot.edits);
    // While a save is in flight even reverting to the old text must remain queued.
    if (!this.pending && text === paragraph.text) edits.delete(paragraphId); else edits.set(paragraphId, text);
    this.update({ edits, selection: null, error: '', compositionSuspended: false });
    if (this.snapshot.composing) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = null; void this.flush().catch(() => {}); }, 450);
    if (!this.deadline) this.deadline = setTimeout(() => { this.deadline = null; void this.flush().catch(() => {}); }, 5000);
  }
  beginComposition() {
    ++this.epoch;
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    if (this.deadline) { clearTimeout(this.deadline); this.deadline = null; }
    this.update({ composing: true, selection: null });
  }
  endComposition(paragraphId: string, text: string) {
    this.update({ composing: false });
    this.edit(paragraphId, text);
    void this.flush().catch(() => {});
  }
  suspendComposition() {
    if (!this.snapshot.composing) return;
    this.update({ composing: false, compositionSuspended: this.snapshot.edits.size > 0,
      error: this.snapshot.edits.size ? '上次输入尚未确认。文字仍留在当前应用，请继续输入或确认完成后保存。' : '' });
  }
  confirmComposition() {
    if (!this.snapshot.compositionSuspended) return;
    this.update({ compositionSuspended: false, error: '' });
    void this.flush().catch(() => {});
  }
  select(paragraphId: string, text: string, start: number, end: number) {
    if (end <= start) { this.update({ selection: null }); return; }
    this.update({ selection: { paragraphId, start: Array.from(text.slice(0, start)).length, end: Array.from(text.slice(0, end)).length, text: text.slice(start, end) } });
  }
  clearSelection() { this.update({ selection: null }); }
  capture = (): ContractEditorTurnContext => {
    if (this.snapshot.composing || this.snapshot.compositionSuspended) throw new Error('文字正在输入，请完成当前输入后再发送。');
    if (this.snapshot.edits.size || this.pending) {
      void this.flush().catch(() => {});
      throw new Error('正文正在保存，请稍后重试。');
    }
    if (this.snapshot.error) throw new Error('编辑副本尚未就绪，请先处理编辑区的提示再发送。');
    const { document, selection } = this.snapshot;
    if (!document || !selection) throw new Error('请先选中要讨论的合同文字，或返回原合同继续审查。');
    const paragraph = document.paragraphs.find(p => p.id === selection.paragraphId);
    if (!paragraph?.editable || Array.from(paragraph.text).slice(selection.start, selection.end).join('') !== selection.text) {
      throw new Error('所选文字已变化，请重新选择后发送。');
    }
    this.capturedSelection = selection;
    return { review_id: this.reviewId, revision: document.revision, paragraph_id: paragraph.id, start: selection.start, end: selection.end, selected_text: selection.text };
  };
  sent = () => {
    if (this.snapshot.selection === this.capturedSelection) this.clearSelection();
    this.capturedSelection = null;
  };
  async flush(): Promise<void> {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    if (this.deadline) { clearTimeout(this.deadline); this.deadline = null; }
    if (this.snapshot.composing || this.snapshot.compositionSuspended) throw new Error('文字正在输入，请完成当前输入后再保存。');
    if (this.pending) return this.pending;
    if (this.snapshot.deciding) throw new Error('正在检查已保存版本，请稍后重试。');
    if (this.snapshot.conflicts.size) throw new Error('请先逐段处理未保存文字与已保存版本的差异。');
    if (!this.snapshot.edits.size || !this.snapshot.document) { this.drainRefresh(); return; }
    ++this.epoch;
    this.update({ saving: true, error: '' });
    this.pending = (async () => {
      try {
        while (this.snapshot.edits.size && this.snapshot.document && !this.snapshot.composing && !this.snapshot.compositionSuspended) {
          const document = this.snapshot.document;
          const sent = new Map(this.snapshot.edits);
          const edits = [...sent].map(([paragraph_id, text]) => ({ paragraph_id, text, original: document.paragraphs.find(p => p.id === paragraph_id)!.text }));
          const saved = await contractEditorApi.save(this.reviewId, document.revision, edits);
          const remaining = new Map(this.snapshot.edits);
          for (const [id, text] of sent) if (remaining.get(id) === text) remaining.delete(id);
          this.update({ document: saved, edits: remaining });
        }
      } catch (cause) { this.update({ error: message(cause) }); throw cause; }
      finally { this.pending = null; this.update({ saving: false }); this.drainRefresh(); }
    })();
    return this.pending;
  }
  async decide(proposal: string, action: 'accept' | 'reject' | 'undo') {
    await this.flush();
    if (!this.snapshot.document || this.pending) return;
    ++this.epoch;
    this.update({ saving: true, deciding: true, error: '' });
    this.pending = (async () => {
      try {
        const document = await contractEditorApi.decide(this.reviewId, proposal, action, this.snapshot.document!.revision);
        this.update({ document, selection: null });
      } catch (cause) { this.update({ error: message(cause) }); throw cause; }
      finally { this.pending = null; this.update({ saving: false, deciding: false }); this.drainRefresh(); }
    })();
    return this.pending;
  }
  async recover() {
    if (this.pending || !this.snapshot.document || this.snapshot.composing) return;
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    if (this.deadline) { clearTimeout(this.deadline); this.deadline = null; }
    ++this.epoch;
    this.update({ loading: true, deciding: true });
    try {
      const latest = await contractEditorApi.get(this.reviewId);
      const edits = new Map(this.snapshot.edits), conflicts = new Map<string, string>();
      for (const [id, text] of edits) {
        const saved = latest.paragraphs.find(p => p.id === id);
        const previous = this.snapshot.document.paragraphs.find(p => p.id === id);
        if (saved?.text === text) edits.delete(id); // A lost response may already have persisted it.
        else if (!saved?.editable || saved.text !== previous?.text) conflicts.set(id, saved?.text ?? '此段已无法安全编辑。');
      }
      this.update({ document: latest, edits, conflicts, selection: null, error: '' });
    } catch (cause) { this.update({ error: message(cause) }); }
    finally { this.update({ loading: false, deciding: false }); }
    if (!this.snapshot.error && !this.snapshot.conflicts.size) await this.flush();
  }
  resolveConflict(id: string, keepLocal: boolean) {
    if (!this.snapshot.conflicts.has(id)) return;
    if (keepLocal && !this.snapshot.document?.paragraphs.find(p => p.id === id)?.editable) return;
    const conflicts = new Map(this.snapshot.conflicts), edits = new Map(this.snapshot.edits);
    conflicts.delete(id);
    if (!keepLocal) edits.delete(id);
    this.update({ conflicts, edits });
    if (!conflicts.size) void this.flush().catch(() => {});
  }
}

const sessions = new Map<string, ContractEditorSession>();
export function contractEditorSession(reviewId: string) {
  let session = sessions.get(reviewId);
  if (!session) { session = new ContractEditorSession(reviewId); sessions.set(reviewId, session); }
  return session;
}
