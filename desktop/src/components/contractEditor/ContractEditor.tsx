import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { contractEditorApi, type ContractEditorState } from '../../runtime/contractEditorClient';
import { saveContractReview } from '../../platform/contractReviewExport';
import { useContractEditorContext } from './ContractEditorContext';
import { contractEditorSession, type EditorSelection } from './ContractEditorSession';
import './ContractEditor.css';

interface Props {
  reviewId: string;
  threadId: string;
  aiBusy: boolean;
  onSelection: (selection: EditorSelection | null) => void;
  onDocument: (document: ContractEditorState) => void;
}
const PROPOSAL_STATUS = { pending: '待确认', accepted: '已接受', rejected: '已拒绝', undone: '已撤销' };

export function ContractEditor({ reviewId, threadId, aiBusy, onSelection, onDocument }: Props) {
  const session = useMemo(() => contractEditorSession(reviewId), [reviewId]);
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const context = useContractEditorContext();
  const callbacks = useRef({ onSelection, onDocument });
  callbacks.current = { onSelection, onDocument };
  const [exporting, setExporting] = useState(false);
  const [exportStatus, setExportStatus] = useState('');
  const [exportError, setExportError] = useState('');
  const exportLock = useRef(false);
  const mounted = useRef(true);
  const { document, edits, selection, saving, loading, error, conflicts } = snapshot;

  useLayoutEffect(() => context.register(threadId, session), [context, session, threadId]);
  useEffect(() => {
    mounted.current = true;
    void session.load();
    const save = () => { void session.flush().catch(() => {}); };
    const leaving = (event: BeforeUnloadEvent) => {
      const current = session.getSnapshot();
      if (current.edits.size || current.saving) { save(); event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('pagehide', save);
    window.addEventListener('beforeunload', leaving);
    return () => {
      mounted.current = false;
      window.removeEventListener('pagehide', save);
      window.removeEventListener('beforeunload', leaving);
      session.clearSelection();
      session.suspendComposition();
      save();
    };
  }, [session]);
  useEffect(() => {
    void session.load();
    if (!aiBusy) return;
    const timer = window.setInterval(() => void session.load(), 2000);
    return () => window.clearInterval(timer);
  }, [session, aiBusy]);
  useEffect(() => callbacks.current.onSelection(selection), [selection]);
  useEffect(() => { if (document) callbacks.current.onDocument(document); }, [document]);

  const exportCopy = async () => {
    if (exportLock.current) return;
    exportLock.current = true;
    setExporting(true); setExportStatus(''); setExportError('');
    try {
      await session.flush();
      const saved = session.getSnapshot();
      if (!saved.document || saved.edits.size || saved.conflicts.size) throw new Error('请先处理尚未保存的正文。');
      const result = await contractEditorApi.export(reviewId, saved.document.revision);
      // Navigation should not open a save dialog for the previously selected contract.
      if (!mounted.current) return;
      const path = await saveContractReview(result);
      if (mounted.current) setExportStatus(path === null ? '已取消保存 Word 副本；应用内草稿仍保留。' : Reflect.has(globalThis, '__TAURI_INTERNALS__') ? `Word 副本已保存：${path}` : '已开始下载 Word 副本。');
    } catch (cause) {
      if (mounted.current) setExportError(cause instanceof Error ? cause.message : typeof cause === 'string' ? cause : 'Word 副本保存失败，请重试。');
    } finally { exportLock.current = false; if (mounted.current) setExporting(false); }
  };

  if (!document) return <section className="contract-editor" aria-label="合同编辑副本">
    {loading ? <p role="status">正在读取可编辑段落…</p> : <p role="alert">{error || '编辑副本尚未就绪。'}</p>}
    {!loading && <button onClick={() => void session.load()}>重新读取副本</button>}
  </section>;

  const locked = exporting || snapshot.deciding || conflicts.size > 0;
  return <section className="contract-editor" aria-label="合同编辑副本">
    <header className="contract-editor-toolbar">
      <div><strong>编辑副本</strong><span role="status">{error || conflicts.size ? '有文字尚未确认保存' : snapshot.composing ? '完成输入后保存' : saving ? '正在保存…' : edits.size ? '等待保存…' : '已保存到本机'}</span></div>
      {document.proposals.length > 0 && <button onClick={() => window.document.getElementById('contract-editor-proposals')?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })}>查看 AI 建议（{document.proposals.filter(p => p.status === 'pending').length} 待确认）</button>}
      <button className="primary-button" disabled={exporting || snapshot.deciding || snapshot.composing || conflicts.size > 0} onClick={() => void exportCopy()}>{exporting ? '正在保存 Word…' : '另存 Word 副本'}</button>
    </header>
    <details className="contract-editor-support">
      <summary>段落文字编辑 · 保留原件与复杂内容</summary>
      <p>这里显示可编辑的段落文字，不呈现 Word 完整排版。表格按单元格中的段落列出。</p>
      <p>{document.notice}</p>
      <p>原合同保持不变；图片、页眉页脚等未开放编辑的内容保留在 Word 副本中。请用 Word/WPS 核对交付排版。</p>
      {document.retained_objects.length > 0 && <p>检测到的保留内容：{document.retained_objects.join('、')}</p>}
    </details>
    {(error || exportError) && <div className="contract-editor-error" role="alert">
      <p>{error || exportError}</p>
      {edits.size > 0 && <p>未保存文字仍留在当前应用中；请先处理提示，再关闭应用。</p>}
      {snapshot.compositionSuspended ? <button disabled={saving} onClick={() => session.confirmComposition()}>确认输入完成并保存</button> : error && <button disabled={saving || loading} onClick={() => void session.recover().catch(() => {})}>检查已保存版本并重试</button>}
    </div>}
    {exportStatus && <p className="contract-editor-result" role="status">{exportStatus}</p>}
    {conflicts.size > 0 && <section className="contract-editor-conflicts" aria-label="处理保存差异">
      <h3>已保存的文字发生了变化</h3><p>逐段选择要保留的文字。确认前不会覆盖任何一版。</p>
      {[...conflicts].map(([id, savedText]) => <article key={id}>
        <p><strong>已保存文字</strong></p><p>{savedText || '（空段落）'}</p>
        <p><strong>本次未保存文字</strong></p><p>{edits.get(id) || '（空段落）'}</p>
        <button disabled={!document.paragraphs.find(p => p.id === id)?.editable} onClick={() => session.resolveConflict(id, true)}>保留本次文字</button>
        <button onClick={() => session.resolveConflict(id, false)}>使用已保存文字</button>
      </article>)}
    </section>}
    <div className="contract-editor-paragraphs">
      {document.paragraphs.map((paragraph, index) => {
        const text = edits.get(paragraph.id) ?? paragraph.text;
        const changed = text !== paragraph.original;
        return <article key={paragraph.id} id={`contract-edit-paragraph-${index}`} className={`contract-editor-paragraph${changed ? ' is-changed' : ''}${paragraph.editable ? '' : ' is-readonly'}`}>
          <div className="contract-editor-paragraph-label"><label htmlFor={`contract-edit-text-${index}`}>{paragraph.in_table ? '表格内段落' : '段落'} {index + 1}</label>{changed && <span>已修改</span>}{!paragraph.editable && <span>只读</span>}</div>
          {paragraph.editable ? <textarea id={`contract-edit-text-${index}`} aria-label={`${paragraph.in_table ? '表格内段落' : '段落'} ${index + 1}`} rows={Math.min(12, Math.max(2, Math.ceil(Array.from(text).length / 42)))} value={text} readOnly={locked}
            onChange={event => session.edit(paragraph.id, event.currentTarget.value)}
            onCompositionStart={() => session.beginComposition()}
            onCompositionEnd={event => session.endComposition(paragraph.id, event.currentTarget.value)}
            onBlur={() => void session.flush().catch(() => {})}
            onSelect={event => {
              const input = event.currentTarget;
              session.select(paragraph.id, input.value, input.selectionStart, input.selectionEnd);
            }} /> : <><p className="contract-editor-readonly-text">{text || '（此段含非文字内容）'}</p><p className="contract-editor-readonly-note">{paragraph.readonly_reason || '此段含暂不支持编辑的内容，请在原合同预览中核对。'}</p></>}
        </article>;
      })}
      {!document.paragraphs.length && <p>没有可显示的正文段落。请返回原合同预览核对。</p>}
    </div>
    <section className="contract-editor-proposals" id="contract-editor-proposals" aria-label="AI 修改建议">
      <header><h3>AI 修改建议</h3><button disabled={saving || loading || edits.size > 0} onClick={() => void session.load()}>刷新建议</button></header>
      {document.proposals.length === 0 && <p>在正文中选中文字并发送修改要求后，建议会出现在这里。</p>}
      {document.proposals.map(proposal => <article key={proposal.id}>
        <header><strong>{PROPOSAL_STATUS[proposal.status]}</strong><button onClick={() => {
          const index = document.paragraphs.findIndex(p => p.id === proposal.paragraph_id);
          window.document.getElementById(`contract-edit-paragraph-${index}`)?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
        }}>查看段落</button></header>
        <p className="contract-editor-before"><span>原文字</span>{proposal.before || '（空文字）'}</p>
        <p className="contract-editor-after"><span>建议改为</span>{proposal.after || '（删除所选文字）'}</p>
        {proposal.reason && <p>{proposal.reason}</p>}
        {proposal.status === 'pending' && !proposal.can_accept && <p className="contract-editor-stale">对应段落已变化，此建议不能再接受。请重新选中文字提出要求。</p>}
        {proposal.status === 'accepted' && !proposal.can_undo && <p className="contract-editor-stale">对应段落已继续修改，无法安全撤销此建议；可直接编辑正文。</p>}
        <div className="contract-editor-proposal-actions">
          {proposal.status === 'pending' && <><button className="primary-button" disabled={saving || exporting || edits.size > 0 || !proposal.can_accept} onClick={() => void session.decide(proposal.id, 'accept').catch(() => {})}>接受修改</button><button disabled={saving || exporting || edits.size > 0} onClick={() => void session.decide(proposal.id, 'reject').catch(() => {})}>拒绝修改</button></>}
          {proposal.status === 'accepted' && <button disabled={saving || exporting || edits.size > 0 || !proposal.can_undo} onClick={() => void session.decide(proposal.id, 'undo').catch(() => {})}>撤销这处修改</button>}
        </div>
      </article>)}
    </section>
  </section>;
}
