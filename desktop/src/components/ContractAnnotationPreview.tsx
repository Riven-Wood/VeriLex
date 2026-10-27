import { useEffect, useRef, useState } from 'react';
import { defaultOptions, parseAsync, renderDocument } from 'docx-preview';
import type { Options } from 'docx-preview';
import type { ContractOpinion } from '../runtime/contractReviewClient';
import { runtimeRequest } from '../runtime/api';
import './ContractAnnotationPreview.css';

interface OriginalComment { id: string; author: string; text: string; paragraph_indices: number[] }
export interface ContractPreviewData { content: string; paragraphs: string[]; comments: OriginalComment[] }
interface Props {
  reviewId: string;
  opinions: ContractOpinion[];
  selectedOpinionId: string | null;
  onSelectOpinion: (id: string) => void;
  onQuote: (text: string) => void;
}

// Match the writer's exact text and zero-based occurrence convention. Ambiguous
// matches stay unlocated instead of silently pointing at the first clause.
export function anchorRange(text: string, anchor: string, occurrence: number | null): [number, number] | null {
  if (!anchor) return null;
  const matches: number[] = [];
  for (let i = text.indexOf(anchor); i >= 0; i = text.indexOf(anchor, i + 1)) matches.push(i);
  const index = occurrence ?? (matches.length === 1 ? 0 : -1);
  return index >= 0 && index < matches.length ? [matches[index], matches[index] + anchor.length] : null;
}

// DOCX soft breaks have no DOM text; docx-preview renders tabs as em spaces.
// Keep the writer's character offsets without changing the rendered layout.
function paragraphSegments(p: HTMLElement) {
  const walker = p.ownerDocument.createTreeWalker(p, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  const segments: {node: Node; text: string}[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.nodeType === Node.TEXT_NODE) segments.push({node, text: node.textContent && node.parentElement?.closest('[data-preview-tab]') ? '\t' : node.textContent ?? ''});
    else if ((node as Element).tagName === 'BR') segments.push({node, text: '\n'});
  }
  return segments;
}

function paragraphsAlign(paragraphs: HTMLElement[], expected: string[]) {
  return paragraphs.length === expected.length && paragraphs.every((p, i) => paragraphSegments(p).map(s => s.text).join('') === expected[i]);
}

interface PreviewNode { type?: string; break?: string; children?: PreviewNode[] }

// The writer counts explicit page/column breaks as newlines, but the pinned
// renderer omits them in continuous preview. Preserve those offsets in the
// parsed preview model only; the original DOCX and export remain untouched.
function preservePreviewBreaks(node: PreviewNode) {
  if (node.type === 'break' && (node.break === 'page' || node.break === 'column')) node.break = 'textWrapping';
  node.children?.forEach(preservePreviewBreaks);
}

function markText(p: HTMLElement, start: number, end: number, id: string, selected: boolean, onSelect: (id: string) => void) {
  const doc = p.ownerDocument;
  const spans: {node: Text; lo: number; hi: number}[] = [];
  let offset = 0;
  for (const {node, text} of paragraphSegments(p)) {
    const next = offset + text.length;
    if (node.nodeType === Node.TEXT_NODE && offset < end && next > start) spans.push({node: node as Text, lo: Math.max(0, start - offset), hi: Math.min(text.length, end - offset)});
    offset = next;
  }
  for (const {node, lo, hi} of spans) {
    const range = doc.createRange(); range.setStart(node, lo); range.setEnd(node, hi);
    const mark = doc.createElement('mark');
    mark.dataset.opinionId = id;
    mark.className = selected ? 'review-anchor selected' : 'review-anchor';
    mark.tabIndex = 0; mark.setAttribute('role', 'button'); mark.setAttribute('aria-label', '查看此处审查意见');
    mark.onclick = event => { event.stopPropagation(); onSelect(id); };
    mark.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(id); } };
    range.surroundContents(mark);
  }
}

export function ContractAnnotationPreview({reviewId, opinions, selectedOpinionId, onSelectOpinion, onQuote}: Props) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [data, setData] = useState<ContractPreviewData | null>(null);
  const [ready, setReady] = useState(0);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [unlocated, setUnlocated] = useState<string[]>([]);
  const callbacks = useRef({onSelectOpinion, onQuote});
  callbacks.current = {onSelectOpinion, onQuote};

  useEffect(() => {
    const controller = new AbortController();
    setData(null); setReady(0); setError(''); setUnlocated([]);
    runtimeRequest<ContractPreviewData>(`/v1/contract-reviews/${encodeURIComponent(reviewId)}/preview`, {signal: controller.signal})
      .then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(() => { if (!controller.signal.aborted) setError('合同预览读取失败，请重试。'); });
    return () => controller.abort();
  }, [reviewId, retry]);

  useEffect(() => {
    if (!data || !frame.current) return;
    let current = true;
    const doc = frame.current.contentDocument;
    if (!doc) return;
    const styleURLs: string[] = [];
    const stylesheet = (css: string) => {
      const link = doc.createElement('link');
      link.rel = 'stylesheet';
      if (current) {
        link.href = URL.createObjectURL(new Blob([css], {type: 'text/css'}));
        styleURLs.push(link.href);
      }
      return link;
    };
    doc.open();
    // Isolate document CSS and disable scripts, embedded HTML, external resources
    // and navigation. The preview never needs the local Runtime token.
    doc.write(`<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; font-src data: blob:; style-src blob:;"></head><body><div id="styles"></div><div id="document"></div></body></html>`);
    doc.close();
    // about:blank inherits the production CSP; a meta tag cannot relax it.
    // Blob stylesheets work with style-src blob: without allowing inline CSS.
    doc.head.append(stylesheet('body{margin:0;background:#edf0f4} .docx-wrapper{padding:16px!important} section.docx{max-width:100%;box-sizing:border-box} .review-anchor{background:#fff0af;cursor:pointer} .review-anchor.selected{background:#ffe18b;outline:2px solid #b78012} .original-anchor{border-left:3px solid #7b6b98}'));
    const root = doc.getElementById('document')!;
    const styles = doc.getElementById('styles')!;
    const select = () => {
      const selection = doc.getSelection();
      if (selection && !selection.isCollapsed && root.contains(selection.anchorNode) && root.contains(selection.focusNode)) {
        const quote = selection.toString().trim();
        if (quote) callbacks.current.onQuote(quote.slice(0, 6000));
      }
    };
    doc.addEventListener('mouseup', select);
    doc.addEventListener('keyup', select);
    const bytes = Uint8Array.from(atob(data.content), c => c.charCodeAt(0));
    const options: Partial<Options> = {
      renderAltChunks: false, renderComments: false, renderChanges: false,
      useBase64URL: true, ignoreWidth: true, ignoreHeight: true, breakPages: false,
      h: value => {
        const node = defaultOptions.h(value);
        if (node instanceof Element && node.tagName.toLowerCase() === 'style') return stylesheet(node.textContent ?? '');
        if (node instanceof Element && node.tagName.toLowerCase() === 'a') node.removeAttribute('href');
        // This exact renderTab descriptor is specific to the pinned 0.4.0 API.
        if (typeof value === 'object' && 'tagName' in value && value.tagName === 'span' && value.children?.length === 1 && value.children[0] === '\u2003' && node instanceof HTMLElement) node.dataset.previewTab = 'true';
        return node;
      },
    };
    void (async () => {
      const parsed = await parseAsync(bytes, options);
      if (!current) return;
      preservePreviewBreaks(parsed.documentPart.body);
      const nodes = await renderDocument(parsed, options);
      if (!current) return;
      for (const node of nodes) (node.nodeName === 'STYLE' ? styles : root).appendChild(node);
      setReady(v => v + 1);
    })().catch(() => { if (current) setError('这份合同暂时无法预览；可继续核对意见并导出至 Word/WPS。'); });
    return () => { current = false; doc.removeEventListener('mouseup', select); doc.removeEventListener('keyup', select); styleURLs.forEach(url => URL.revokeObjectURL(url)); };
  }, [data]);

  useEffect(() => {
    if (!ready || !data) return;
    const doc = frame.current?.contentDocument;
    if (!doc) return;
    doc.querySelectorAll('mark.review-anchor').forEach(mark => mark.replaceWith(...mark.childNodes));
    const paragraphs = Array.from(doc.querySelectorAll<HTMLElement>('article p'));
    paragraphs.forEach(p => {p.normalize(); p.classList.remove('original-anchor');});
    // No coordinate guessing when the renderer cannot preserve paragraph order.
    const aligned = paragraphsAlign(paragraphs, data.paragraphs);
    const missing: string[] = [];
    const fullText = data.paragraphs.join('\n');
    for (const opinion of opinions) {
      const range = anchorRange(fullText, opinion.anchor_text, opinion.occurrence);
      if (!aligned || !range) {missing.push(opinion.id); continue;}
      let cursor = 0;
      paragraphs.forEach((p, i) => {
        const end = cursor + data.paragraphs[i].length;
        if (cursor < range[1] && end > range[0]) markText(p, Math.max(0, range[0] - cursor), Math.min(end, range[1]) - cursor, opinion.id, opinion.id === selectedOpinionId, id => callbacks.current.onSelectOpinion(id));
        cursor = end + 1;
      });
    }
    if (aligned) data.comments.forEach(comment => comment.paragraph_indices.forEach(index => paragraphs[index]?.classList.add('original-anchor')));
    setUnlocated(missing);
    const selected = Array.from(doc.querySelectorAll<HTMLElement>('[data-opinion-id]')).find(mark => mark.dataset.opinionId === selectedOpinionId);
    selected?.scrollIntoView?.({block: 'center', behavior: 'instant'});
  }, [data, opinions, ready, selectedOpinionId]);

  return <div className="contract-annotation-preview">
    <p className="contract-preview-note">只读预览 · 排版以 Word/WPS 为准 · 选中文字可在右侧追问</p>
    {error ? <p role="alert">{error} <button onClick={() => setRetry(v => v + 1)}>重试</button></p> : !ready && <p role="status">正在读取合同…</p>}
    {!!unlocated.length && <p role="status">{unlocated.length} 条意见未能在预览中准确定位，请核对意见中的原文摘录。</p>}
    <iframe key={reviewId} ref={frame} title="合同正文只读预览" sandbox="allow-same-origin" className="contract-preview-frame" />
    {!!data?.comments.length && <details className="contract-original-comments"><summary>合同既有批注（{data.comments.length}）</summary>
      {data.comments.map(comment => <article key={comment.id}><strong>原有批注 · {comment.author}</strong><p>{comment.text}</p><button onClick={() => {
        const paragraphs = Array.from(frame.current?.contentDocument?.querySelectorAll<HTMLElement>('article p') ?? []);
        if (paragraphsAlign(paragraphs, data.paragraphs)) paragraphs[comment.paragraph_indices[0]]?.scrollIntoView?.({block:'center'});
      }}>查看原文</button></article>)}
    </details>}
  </div>;
}
