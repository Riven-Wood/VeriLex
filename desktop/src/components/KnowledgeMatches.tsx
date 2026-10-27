import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { KnowledgeApi, KnowledgeDocument, KnowledgeMatch } from '../runtime/knowledgeClient';

/** Mark literal text without changing the underlying textContent (including CRLF). */
export function KnowledgeHighlight({text, query, focus}: {text: string; query: string; focus?: number}) {
  if (!query) return <>{text}</>;
  const focusIndex = focus === undefined ? -1 : Array.from(text).slice(0, Math.max(0, focus)).join('').length;
  const pieces: ReactNode[] = [];
  let cursor = 0;
  for (let n = 0; n < 200; n++) {
    const match = text.indexOf(query, cursor);
    if (match < 0) break;
    pieces.push(text.slice(cursor, match), <mark key={match} className={match <= focusIndex && focusIndex < match + query.length ? 'knowledge-current-hit' : undefined}>{text.slice(match, match + query.length)}</mark>);
    cursor = match + query.length;
  }
  pieces.push(text.slice(cursor));
  return <>{pieces}</>;
}

export function KnowledgeMatches({api, libraryId, document, query, onNavigate}: {
  api: KnowledgeApi; libraryId: string; document: KnowledgeDocument; query: string; onNavigate: (start: number, match: number) => void;
}) {
  const [items, setItems] = useState<KnowledgeMatch[]>([]);
  const [index, setIndex] = useState(0);
  const [nextStart, setNextStart] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  useEffect(() => {
    const current = ++generation.current;
    setItems([]); setIndex(0); setNextStart(null); setError(''); setBusy(true);
    api.matches(libraryId, document, query, 0).then(result => {
      if (current !== generation.current) return;
      setItems(result.items); setNextStart(result.next_start);
    }).catch(e => {if (current === generation.current) setError(e instanceof Error ? e.message : '定位失败，请重新搜索。');})
      .finally(() => {if (current === generation.current) setBusy(false);});
    return () => {generation.current++;};
  }, [api, libraryId, document, query]);
  async function next() {
    if (index + 1 < items.length) {setIndex(index + 1); onNavigate(items[index + 1].read_start, items[index + 1].match_start); return;}
    if (nextStart === null || busy) return;
    const current = generation.current;
    setBusy(true); setError('');
    try {
      const result = await api.matches(libraryId, document, query, nextStart);
      if (current !== generation.current) return;
      setNextStart(result.next_start);
      if (result.items.length) {setItems(previous => [...previous, ...result.items]); setIndex(items.length); onNavigate(result.items[0].read_start, result.items[0].match_start);}
    } catch (e) {if (current === generation.current) setError(e instanceof Error ? e.message : '定位失败，请重试。');}
    finally {if (current === generation.current) setBusy(false);}
  }
  return <div className="knowledge-match-navigation" aria-label="原文命中位置">
    <span>{busy ? '正在定位…' : items.length ? `第 ${index + 1} 处命中` : '正文中未找到该关键词'}</span>
    <button disabled={busy || index === 0} onClick={() => {setIndex(index - 1); onNavigate(items[index - 1].read_start, items[index - 1].match_start);}}>上一处命中</button>
    <button disabled={busy || (index + 1 >= items.length && nextStart === null)} onClick={() => void next()}>下一处命中</button>
    {items[index] && <button disabled={busy} onClick={() => onNavigate(items[index].read_start, items[index].match_start)}>回到命中</button>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
