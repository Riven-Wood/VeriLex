import { useEffect, useRef, useState } from 'react';
import { WordDocumentEditor, type WordEditorHandle } from '../wordEditor/WordDocumentEditor';
import { WordSession, type WordDocument } from '../../runtime/wordDocumentClient';
import { useEditorDiscussion } from './DiscussionContext';
import { LoadingState } from '../LoadingState';
import { loadStandaloneDraft, saveStandaloneDraft, setStandaloneDraftGuard } from './standaloneDraftStorage';

export function StandaloneDraft() {
  const [native, setNative] = useState<{document:WordDocument; session:WordSession}|null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const editor = useRef<WordEditorHandle>(null);
  const discussion = useEditorDiscussion();
  const register = discussion?.register, unregister = discussion?.unregister;
  useEffect(() => {
    let live = true;
    setError('');
    void loadStandaloneDraft().then(document => {
      if (!live) return;
      const session = new WordSession(document, (_id, revision, bytes) => saveStandaloneDraft(document, revision, bytes));
      setNative({ document, session });
    }).catch(reason => { if (live) setError(reason instanceof Error ? reason.message : '无法打开文书草稿。'); });
    return () => { live = false; };
  }, [retry]);
  useEffect(() => {
    const flush = async () => { if (editor.current?.isDirty()) await editor.current.flush(); };
    setStandaloneDraftGuard(flush);
    register?.({id:'standalone-drafting', title:'未命名文书', kind:'documents', flush, capture:()=>null, clearSelection:()=>{}, apply:()=>{}});
    return () => { setStandaloneDraftGuard(null); unregister?.('standalone-drafting'); };
  }, [register, unregister]);
  return <section className="standalone-draft" aria-label="未归档文书">
    {error ? <p role="alert">{error} <button onClick={() => setRetry(value => value + 1)}>重试</button></p>
      : native ? <WordDocumentEditor ref={editor} document={native.document} session={native.session} label="文书 Word 编辑器" tracking={false} />
      : <LoadingState>正在打开 Word 编辑台…</LoadingState>}
  </section>;
}
