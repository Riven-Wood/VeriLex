import { useEffect, useState } from 'react';
import { knowledgeApi } from '../../runtime/knowledgeClient';

export function KnowledgeQuoteCard({quoteId, threadId}: {quoteId: string; threadId: string}) {
  const [quote, setQuote] = useState<Awaited<ReturnType<typeof knowledgeApi.quote>> | null>(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let active = true; setQuote(null); setError(''); setCopied(false);
    knowledgeApi.quote(quoteId, threadId).then(value => {if (active) setQuote(value);})
      .catch(() => {if (active) setError('原文引用暂时无法读取。');});
    return () => {active = false;};
  }, [quoteId, threadId]);
  return <section id={`knowledge-quote-${threadId}-${quoteId}`} tabIndex={-1} className="tool-card" aria-label="原文引用">
    <strong>原文引用 · 程序截取</strong>
    {error && <p role="alert">{error}</p>}
    {quote ? <>
      <p>{quote.source_name} · 第 {quote.line_start}–{quote.line_end} 行</p>
      <pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',font:'inherit'}}>{quote.text}</pre>
      <p>打开时核对：{({current:"原文一致",changed:"原文已更新",removed:"资料已移除",detached:"知识库已解除关联",unavailable:"原文件暂时无法核对"} as Record<string,string>)[quote.source_state ?? ""] ?? "状态待核对"}。保留引用时的文字，不代表已核验官方原件。</p>
      <button type="button" onClick={() => {void (async () => {try {await navigator.clipboard.writeText(quote.text); setCopied(true);} catch {setError('复制未完成，请选择原文手动复制。');}})();}}>{copied ? '已复制' : '复制原文'}</button>
    </> : !error && <p>正在读取原文引用…</p>}
  </section>;
}
