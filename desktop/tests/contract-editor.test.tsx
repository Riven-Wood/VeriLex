import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { contractEditorApi, type ContractEditorState } from '../src/runtime/contractEditorClient';
import { ContractEditorSession } from '../src/components/contractEditor/ContractEditorSession';
import { ContractEditor } from '../src/components/contractEditor/ContractEditor';
import { ContractEditorProvider, useContractEditorContext } from '../src/components/contractEditor/ContractEditorContext';
import { saveContractReview } from '../src/platform/contractReviewExport';
import { ContractReviewView } from '../src/views/ContractReviewView';
import { contractReviewApi } from '../src/runtime/contractReviewClient';
import { useAgentChat } from '../src/hooks/useAgentChat';
import { wordDocumentApi } from '../src/runtime/wordDocumentClient';

vi.mock('../src/runtime/contractEditorClient', () => ({ contractEditorApi: { get: vi.fn(), save: vi.fn(), decide: vi.fn(), export: vi.fn() } }));
vi.mock('../src/platform/contractReviewExport', () => ({ saveContractReview: vi.fn() }));
vi.mock('../src/runtime/contractReviewClient', async () => {
  const actual = await vi.importActual<typeof import('../src/runtime/contractReviewClient')>('../src/runtime/contractReviewClient');
  return { ...actual, contractReviewApi: { list: vi.fn(), get: vi.fn(), listSkills: vi.fn().mockResolvedValue([]), export: vi.fn(), report: vi.fn(), patchOpinion: vi.fn(), stop: vi.fn() } };
});
vi.mock('../src/components/ContractAnnotationPreview', () => ({ ContractAnnotationPreview: ({ selectedOpinionId, onQuote }: { selectedOpinionId?: string; onQuote: (quote: string) => void }) => <div data-testid="original-preview" data-selected-opinion={selectedOpinionId}>原合同预览<button onClick={() => onQuote('十日内付款')}>引用付款原文</button></div> }));
const wordEditor = vi.hoisted(() => ({ flush: vi.fn() }));
vi.mock('../src/components/wordEditor/WordDocumentEditor', async () => { const React = await import('react'); return { WordDocumentEditor: React.forwardRef(({ document, review, onReady }: { document: { id: string; filename: string }; review?: { id: string }; onReady?: (ready: boolean) => void }, ref) => {
  React.useEffect(() => { onReady?.(true); }, []);
  React.useImperativeHandle(ref, () => ({ flush: wordEditor.flush }));
  return <section aria-label="本机 Word 工作副本" data-document-id={document.id} data-review-id={review?.id}>{document.filename}</section>;
}) }; });
vi.mock('../src/runtime/wordDocumentClient', () => ({ wordDocumentApi: { list: vi.fn(), get: vi.fn(), forReview: vi.fn() } }));
const api = vi.mocked(contractEditorApi);
let counter = 0;
function document(text = '甲方应在十日内付款。', revision = 0, id = 'review'): ContractEditorState {
  return { review_id: id, revision, file_hash: 'hash', mode: 'paragraph_text', updated_at: 'now', notice: '保存原有文档包中的复杂对象。', retained_objects: ['图片'],
    paragraphs: [{ id: 'p1', text, original: '甲方应在十日内付款。', editable: true }, { id: 'p2', text: '服务费', original: '服务费', editable: true, in_table: true }, { id: 'p3', text: '签字与图片', original: '签字与图片', editable: false, readonly_reason: '此段包含图片。' }], proposals: [], opinion_locations: [] };
}
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (cause: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
beforeEach(() => { vi.resetAllMocks(); api.get.mockResolvedValue(document()); });
afterEach(() => { cleanup(); });

describe('contract editor draft safety', () => {
  it('serializes autosaves and keeps typing performed during a save', async () => {
    const session = new ContractEditorSession('a'); await session.load();
    const first = deferred<ContractEditorState>();
    api.save.mockReturnValueOnce(first.promise).mockResolvedValueOnce(document('甲方应在三日内付款。', 2));
    session.edit('p1', '甲方应在五日内付款。'); const saving = session.flush();
    session.edit('p1', '甲方应在三日内付款。');
    first.resolve(document('甲方应在五日内付款。', 1)); await saving;
    expect(api.save).toHaveBeenNthCalledWith(2, 'a', 1, [{ paragraph_id: 'p1', original: '甲方应在五日内付款。', text: '甲方应在三日内付款。' }]);
    expect(session.getSnapshot().document?.paragraphs[0].text).toBe('甲方应在三日内付款。');
    expect(session.getSnapshot().edits.size).toBe(0);
  });

  it('does not lose a revert to the previous text while the first save is pending', async () => {
    const session = new ContractEditorSession('b'); await session.load();
    const first = deferred<ContractEditorState>();
    api.save.mockReturnValueOnce(first.promise).mockResolvedValueOnce(document(undefined, 2));
    session.edit('p1', '改动'); const saving = session.flush();
    session.edit('p1', '甲方应在十日内付款。'); first.resolve(document('改动', 1)); await saving;
    expect(api.save).toHaveBeenNthCalledWith(2, 'b', 1, [{ paragraph_id: 'p1', original: '改动', text: '甲方应在十日内付款。' }]);
  });

  it('retains unsaved text on conflict and requires an explicit choice before replacing the newer version', async () => {
    const session = new ContractEditorSession('c'); await session.load();
    api.save.mockRejectedValueOnce(new Error('副本已变化。'));
    session.edit('p1', '我的修改'); await expect(session.flush()).rejects.toThrow('副本已变化');
    api.get.mockResolvedValue(document('另一处保存的修改', 2)); await session.recover();
    expect(session.getSnapshot().edits.get('p1')).toBe('我的修改');
    expect(session.getSnapshot().conflicts.get('p1')).toBe('另一处保存的修改');
    await expect(session.flush()).rejects.toThrow('逐段处理');
    api.save.mockResolvedValue(document('我的修改', 3)); session.resolveConflict('p1', true); await session.flush();
    expect(api.save).toHaveBeenLastCalledWith('c', 2, [{ paragraph_id: 'p1', original: '另一处保存的修改', text: '我的修改' }]);
  });

  it('recovers a lost successful save response without writing over the current version', async () => {
    const session = new ContractEditorSession('d'); await session.load();
    api.save.mockRejectedValue(new Error('连接中断')); session.edit('p1', '已保存但响应丢失'); await expect(session.flush()).rejects.toThrow();
    api.get.mockResolvedValue(document('已保存但响应丢失', 1)); await session.recover();
    expect(session.getSnapshot().edits.size).toBe(0); expect(session.getSnapshot().conflicts.size).toBe(0);
    expect(api.save).toHaveBeenCalledTimes(1);
  });

  it('captures exact codepoint selection and blocks missing, dirty or invalid selections', async () => {
    api.get.mockResolvedValue(document('甲😀乙双方')); const session = new ContractEditorSession('unicode'); await session.load();
    expect(() => session.capture()).toThrow('请先选中');
    session.select('p1', '甲😀乙双方', 1, 4);
    expect(session.capture()).toEqual({ review_id: 'unicode', revision: 0, paragraph_id: 'p1', start: 1, end: 3, selected_text: '😀乙' });
    session.sent(); expect(session.getSnapshot().selection).toBeNull();
    const pending = deferred<ContractEditorState>(); api.save.mockReturnValue(pending.promise);
    session.edit('p1', '甲😀乙'); session.select('p1', '甲😀乙', 1, 3);
    expect(() => session.capture()).toThrow('正在保存'); pending.resolve(document('甲😀乙', 1)); await session.flush();
    expect(session.capture().revision).toBe(1);
    session.select('p1', '不一致', 0, 2); expect(() => session.capture()).toThrow('已变化');
  });

  it('a late refresh never replaces the current draft or accepted revision', async () => {
    const session = new ContractEditorSession('stale'); await session.load();
    const older = deferred<ContractEditorState>(); api.get.mockReturnValueOnce(older.promise);
    const refresh = session.load(); session.edit('p1', '最新文字'); api.save.mockResolvedValue(document('最新文字', 1)); await session.flush();
    older.resolve(document()); await refresh;
    expect(session.getSnapshot().document?.revision).toBe(1);
    expect(session.getSnapshot().document?.paragraphs[0].text).toBe('最新文字');
  });

  it('rejects unsupported paragraph splits and locks edits during an AI decision', async () => {
    const session = new ContractEditorSession('action'); await session.load();
    session.edit('p1', '两个\n段落'); expect(session.getSnapshot().edits.size).toBe(0);
    expect(session.getSnapshot().error).toContain('换行');
    const accepted = deferred<ContractEditorState>(); api.decide.mockReturnValue(accepted.promise);
    const action = session.decide('proposal', 'accept'); await Promise.resolve();
    expect(session.getSnapshot().deciding).toBe(true);
    session.edit('p1', '不应写入'); expect(session.getSnapshot().edits.size).toBe(0);
    accepted.resolve(document('已接受', 1)); await action;
    expect(session.getSnapshot().deciding).toBe(false);
  });

  it('does not save partial IME composition, then saves committed text', async () => {
    const session = new ContractEditorSession('ime'); await session.load();
    session.beginComposition(); session.edit('p1', 'zhong');
    await expect(session.flush()).rejects.toThrow('正在输入');
    expect(() => session.capture()).toThrow('正在输入');
    expect(api.save).not.toHaveBeenCalled();
    api.save.mockResolvedValue(document('中', 1)); session.endComposition('p1', '中'); await session.flush();
    expect(api.save).toHaveBeenCalledWith('ime', 0, [{ paragraph_id: 'p1', original: '甲方应在十日内付款。', text: '中' }]);
  });

  it('persists continuous input at a bounded interval instead of postponing forever', async () => {
    vi.useFakeTimers();
    try {
      const session = new ContractEditorSession('continuous'); await session.load();
      api.save.mockImplementation(async (_, revision, edits) => document(edits[0].text, revision + 1));
      for (let i = 0; i < 14; i++) { session.edit('p1', `持续输入${i}`); await vi.advanceTimersByTimeAsync(400); }
      expect(api.save).toHaveBeenCalled(); await session.flush();
      expect(session.getSnapshot().document?.paragraphs[0].text).toBe('持续输入13');
    } finally { vi.useRealTimers(); }
  });

  it('keeps an interrupted composition recoverable without silently saving its partial text', async () => {
    const session = new ContractEditorSession('paused-ime'); await session.load();
    session.beginComposition(); session.edit('p1', '未确认的输入'); session.suspendComposition();
    await expect(session.flush()).rejects.toThrow('正在输入');
    expect(api.save).not.toHaveBeenCalled(); expect(session.getSnapshot().compositionSuspended).toBe(true);
    api.save.mockResolvedValue(document('未确认的输入', 1)); session.confirmComposition(); await session.flush();
    expect(session.getSnapshot().edits.size).toBe(0); expect(session.getSnapshot().compositionSuspended).toBe(false);
  });

  it('queues a terminal refresh behind an in-flight save without overwriting unsaved typing', async () => {
    const session = new ContractEditorSession('refresh-save'); await session.load();
    const pending = deferred<ContractEditorState>(); api.save.mockReturnValue(pending.promise);
    session.edit('p1', '手动修改'); const saving = session.flush(); session.refresh();
    expect(api.get).toHaveBeenCalledTimes(1);
    const final = document('手动修改', 1); final.proposals = [{ id: 'new', turn_id: 'turn', paragraph_id: 'p2', before: '服务费', after: '价款', reason: '完成后新增的建议', status: 'pending', base_revision: 0, can_accept: true, can_undo: false }];
    api.get.mockResolvedValue(final); pending.resolve(document('手动修改', 1)); await saving; await session.load();
    expect(session.getSnapshot().document?.proposals[0].id).toBe('new');
    expect(session.getSnapshot().document?.paragraphs[0].text).toBe('手动修改');
  });

  it('refreshes again when a terminal event arrives during an older read', async () => {
    const session = new ContractEditorSession('refresh-read'); await session.load();
    const older = deferred<ContractEditorState>(); api.get.mockReturnValueOnce(older.promise);
    const read = session.load(); session.refresh();
    const final = document(); final.proposals = [{ id: 'latest', turn_id: 'turn', paragraph_id: 'p1', before: '十日', after: '五日', reason: '', status: 'pending', base_revision: 0, can_accept: true, can_undo: false }];
    api.get.mockResolvedValue(final); older.resolve(document()); await read; await session.load();
    expect(session.getSnapshot().document?.proposals[0].id).toBe('latest');
  });
});

function Capture({ receive }: { receive: (value: unknown) => void }) {
  const context = useContractEditorContext();
  return <button onClick={() => { try { receive(context.capture('thread')); context.sent('thread'); } catch (cause) { receive(cause); } }}>发送测试消息</button>;
}
function setup(options: { state?: ContractEditorState; capture?: (value: unknown) => void } = {}) {
  const id = `ui-${++counter}`;
  api.get.mockResolvedValue(options.state ?? document(undefined, 0, id));
  const onSelection = vi.fn(), onDocument = vi.fn();
  const props = { reviewId: id, threadId: 'thread', aiBusy: false, onSelection, onDocument };
  const view = render(<ContractEditorProvider><ContractEditor {...props}/>{options.capture && <Capture receive={options.capture}/>}</ContractEditorProvider>);
  return { ...view, id, props, onSelection, onDocument };
}

describe('contract editor user workflow', () => {
  it('shows suggestions on the chat terminal event even when the parent review stays idle for the whole turn', async () => {
    let socket!: QuickSocket;
    class QuickSocket {
      static OPEN = 1;
      readyState = 1;
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: (() => void) | null = null;
      send = vi.fn();
      constructor() { socket = this; }
      close() { this.readyState = 3; }
      receive(value: unknown) { this.onmessage?.({ data: JSON.stringify(value) }); }
    }
    function Discussion() {
      const chat = useAgentChat('thread');
      return <button disabled={chat.historyLoadedThreadId !== 'thread'} onClick={() => chat.sendMessage('把十日改为五日')}>发送修改要求</button>;
    }
    vi.stubGlobal('WebSocket', QuickSocket); vi.stubGlobal('__VERILEX_API_TOKEN__', 'test-token');
    vi.stubGlobal('fetch', vi.fn(async () => new Response('[]', { status: 200 })));
    try {
      const id = `quick-ui-${++counter}`;
      api.get.mockResolvedValue(document(undefined, 0, id));
      const view = render(<ContractEditorProvider><ContractEditor reviewId={id} threadId="thread" aiBusy={false} onSelection={vi.fn()} onDocument={vi.fn()}/><Discussion/></ContractEditorProvider>);
      const input = await screen.findByLabelText('段落 1') as HTMLTextAreaElement;
      await waitFor(() => expect((screen.getByText('发送修改要求') as HTMLButtonElement).disabled).toBe(false));
      act(() => socket.onopen?.()); input.focus(); input.setSelectionRange(4, 6); fireEvent.select(input);
      fireEvent.click(screen.getByText('发送修改要求'));
      expect(socket.send.mock.calls.some(([message]) => JSON.parse(message).contractEditorContext?.selected_text === '十日')).toBe(true);
      const final = document(undefined, 0, id); final.proposals = [{ id: 'quick', turn_id: 'quick-turn', paragraph_id: 'p1', before: '十日', after: '五日', reason: '短轮次生成的建议', status: 'pending', base_revision: 0, can_accept: true, can_undo: false }];
      api.get.mockResolvedValue(final);
      act(() => {
        socket.receive({ type: 'ack', status: 'queued', turnId: 'quick-turn' });
        socket.receive({ type: 'event', event: { type: 'turn_started', threadId: 'thread', turnId: 'quick-turn', sequence: 1, payload: {} } });
        socket.receive({ type: 'event', event: { type: 'turn_completed', threadId: 'thread', turnId: 'quick-turn', sequence: 2, payload: {} } });
      });
      expect(await screen.findByText('短轮次生成的建议')).toBeTruthy();
      expect(screen.getByRole('button', { name: '接受修改' })).toBeTruthy();
      view.unmount();
    } finally { vi.unstubAllGlobals(); }
  });
  it('shows editable table paragraphs and clearly readonly complex content', async () => {
    setup();
    expect(await screen.findByLabelText('段落 1')).toBeTruthy();
    expect(screen.getByLabelText('表格内段落 2')).toBeTruthy();
    expect(screen.queryByRole('textbox', { name: '段落 3' })).toBeNull();
    expect(screen.getByText('此段包含图片。')).toBeTruthy();
    expect(screen.getByText(/不呈现 Word 完整排版/)).toBeTruthy();
  });

  it('saves on blur and binds selection to the existing discussion provider', async () => {
    const capture = vi.fn(); const { id, onSelection } = setup({ capture });
    const input = await screen.findByLabelText('段落 1') as HTMLTextAreaElement;
    api.save.mockResolvedValue(document('甲方应在五日内付款。', 1, id));
    fireEvent.change(input, { target: { value: '甲方应在五日内付款。' } }); fireEvent.blur(input);
    await waitFor(() => expect(screen.getByText('已保存到本机')).toBeTruthy());
    input.focus(); input.setSelectionRange(4, 6); fireEvent.select(input);
    fireEvent.click(screen.getByText('发送测试消息'));
    expect(capture.mock.calls[0][0]).toMatchObject({ review_id: id, revision: 1, selected_text: '五日' });
    await waitFor(() => expect(onSelection).toHaveBeenLastCalledWith(null));
  });

  it('flushes on unmount and restores the saved draft when reopening', async () => {
    const { id, props, unmount } = setup(); const input = await screen.findByLabelText('段落 1');
    const pending = deferred<ContractEditorState>(); api.save.mockReturnValue(pending.promise);
    fireEvent.change(input, { target: { value: '离开后仍保存' } }); unmount();
    expect(api.save).toHaveBeenCalledWith(id, 0, [{ paragraph_id: 'p1', original: '甲方应在十日内付款。', text: '离开后仍保存' }]);
    await act(async () => pending.resolve(document('离开后仍保存', 1, id)));
    api.get.mockResolvedValue(document('离开后仍保存', 1, id));
    render(<ContractEditorProvider><ContractEditor {...props}/></ContractEditorProvider>);
    expect((await screen.findByLabelText('段落 1') as HTMLTextAreaElement).value).toBe('离开后仍保存');
  });

  it('allows one suggested change to be accepted, then undone, and disables stale proposals', async () => {
    const state = document(); state.proposals = [{ id: 's', turn_id: 'turn', paragraph_id: 'p1', before: '十日', after: '五日', reason: '按要求缩短', status: 'pending', base_revision: 0, can_accept: true, can_undo: false }, { id: 'stale', turn_id: 'old', paragraph_id: 'p2', before: '服务费', after: '价款', reason: '', status: 'pending', base_revision: 0, can_accept: false, can_undo: false }];
    const { id } = setup({ state }); await screen.findByText('按要求缩短');
    const buttons = screen.getAllByRole('button', { name: '接受修改' }) as HTMLButtonElement[];
    expect(buttons[1].disabled).toBe(true);
    const accepted = { ...state, revision: 1, proposals: [{ ...state.proposals[0], status: 'accepted' as const, can_accept: false, can_undo: true }] };
    api.decide.mockResolvedValueOnce(accepted); fireEvent.click(buttons[0]);
    const undo = await screen.findByText('撤销这处修改');
    expect(api.decide).toHaveBeenCalledWith(id, 's', 'accept', 0);
    api.decide.mockResolvedValueOnce({ ...accepted, revision: 2, proposals: [{ ...accepted.proposals[0], status: 'undone', can_undo: false }] });
    fireEvent.click(undo); expect(await screen.findByText('已撤销')).toBeTruthy();
    expect(api.decide).toHaveBeenLastCalledWith(id, 's', 'undo', 1);
  });

  it('exports the saved revision and reports cancelled save without losing the draft', async () => {
    const { id } = setup(); await screen.findByLabelText('段落 1');
    api.export.mockResolvedValue({ filename: '合同（编辑副本）.docx', content: 'base64' });
    vi.mocked(saveContractReview).mockResolvedValue(null);
    fireEvent.click(screen.getByText('另存 Word 副本'));
    expect(await screen.findByText('已取消保存 Word 副本；应用内草稿仍保留。')).toBeTruthy();
    expect(api.export).toHaveBeenCalledWith(id, 0);
    expect(vi.mocked(saveContractReview)).toHaveBeenCalledWith({ filename: '合同（编辑副本）.docx', content: 'base64' });
  });

  it('does not open the old review save dialog after navigating away', async () => {
    const { unmount } = setup(); await screen.findByLabelText('段落 1');
    const pending = deferred<{ filename: string; content: string }>(); api.export.mockReturnValue(pending.promise);
    fireEvent.click(screen.getByText('另存 Word 副本')); await waitFor(() => expect(api.export).toHaveBeenCalled());
    unmount(); await act(async () => pending.resolve({ filename: '旧合同.docx', content: 'b64' }));
    expect(saveContractReview).not.toHaveBeenCalled();
  });

  it('keeps the Word working copy while reading opinions and discussing the review', async () => {
    localStorage.setItem('verilex:contract-assistant-open:v1', 'true');
    const id = `review-ui-${++counter}`;
    const row = { id, thread_id: 'thread', filename: '虚构服务合同.docx', skill: '通用合同审查', status: 'completed', created_at: 'now', updated_at: 'now', opinion_count: 1, party: 'neutral', strictness: 'normal', signature: '测试', retrieval_enabled: false,
      opinions: [{ id: 'opinion', clause_label: '付款条款', clause_excerpt: '十日内付款', anchor_text: '十日内付款', occurrence: null, level: '中', category: '建议', body: '需协商付款期限。', suggestion: '', adopted: true, edited_text: null, citations: [{ id: 'citation', document: '合成审查依据', source_kind: 'fixture', source_label: '验收资料', verified_at: '2026-10-24' }] }] };
    vi.mocked(contractReviewApi.list).mockResolvedValue([row]); vi.mocked(contractReviewApi.get).mockResolvedValue(row); vi.mocked(contractReviewApi.listSkills).mockResolvedValue([]);
    const working = { id: `word-${id}`, filename: '本机工作副本.docx', content: 'QQ==', revision: 1, updated_at: 'now' };
    vi.mocked(wordDocumentApi.list).mockResolvedValue([]); vi.mocked(wordDocumentApi.forReview).mockResolvedValue(working);
    wordEditor.flush.mockResolvedValue({ document: working, bytes: new Uint8Array([65]) });
    const assistant = vi.fn((quote: string, clear: () => void, editing?: boolean) => <div><span>{editing ? '旧段落编辑上下文' : '本次审查讨论'}</span><p data-testid="discussion-quote">{quote}</p><button onClick={clear}>清除引用</button></div>);
    render(<ContractEditorProvider><ContractReviewView thread={{ id: 'thread', status: 'idle' } as never} hasModel onStartReview={vi.fn()} onSelectReview={vi.fn()} renderAssistant={assistant}/></ContractEditorProvider>);
    const workingView = await screen.findByRole('region', { name: '本机 Word 工作副本' });
    expect(workingView.getAttribute('data-document-id')).toBe(working.id); expect(workingView.getAttribute('data-review-id')).toBe(id);
    expect(wordDocumentApi.forReview).toHaveBeenCalledWith(id); expect(api.get).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '本次审查原文与批注' })).toBeNull();
    expect(screen.queryByTestId('original-preview')).toBeNull();
    expect(screen.queryByRole('button', { name: '展开意见栏' })).toBeNull();
    expect(screen.getByRole('region', { name: '本机 Word 工作副本' })).toBe(workingView);
    expect(wordEditor.flush).not.toHaveBeenCalled();
    expect(assistant).toHaveBeenCalled();
  });
});
