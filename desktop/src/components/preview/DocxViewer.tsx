import {useEffect, useRef, useState} from 'react';
import {defaultOptions, renderAsync} from 'docx-preview';
import './DocxViewer.css';
import {runtimeRequest} from '../../runtime/api';
import type {LitigationSource} from '../../views/LitigationClient';

/** Read-only local renderer, sharing the contract preview's isolation model. */
export function DocxViewer({source,citation,workspaceId,showNote=true}: {source: Blob | string;citation?:LitigationSource;workspaceId?:string;showNote?:boolean}) {
  const [locationNote,setLocationNote]=useState('');
  const frame = useRef<HTMLIFrameElement>(null);
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error'>('loading');
  useEffect(() => {
    const doc = frame.current?.contentDocument;
    if (!doc) return;
    let current = true;let rendered=false;
    let resizeObserver: ResizeObserver | undefined;
    const styleURLs: string[] = [];
    setPhase('loading');
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
    // The sandbox forbids scripts/navigation; CSP also blocks remote images,
    // fonts and embedded HTML. Document styles stay outside the app DOM.
    doc.write(`<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; font-src data: blob:; style-src blob:;"></head><body><div id="styles"></div><div id="document"></div></body></html>`);
    doc.close();
    doc.head.append(stylesheet('body{margin:0;background:#edf0f4}.docx-wrapper{padding:12px!important;background:#edf0f4!important}.docx-wrapper>section.docx{flex-shrink:0;margin-bottom:12px;box-shadow:0 2px 8px #0002}article{overflow-wrap:anywhere}img{max-width:100%}.source-location{background:#fff0b5;outline:2px solid #ce9800}'));
    const root = doc.getElementById('document')!;
    const styles = doc.getElementById('styles')!;
    void (async () => {
      const bytes = source instanceof Blob
        ? new Uint8Array(await source.arrayBuffer())
        : Uint8Array.from(atob(source.slice(source.indexOf(',') + 1)), c => c.charCodeAt(0));
      if (!current) return;
      await renderAsync(bytes, root, styles, {
        renderAltChunks: false, renderComments: false, renderChanges: false,
        useBase64URL: true, ignoreWidth: false, ignoreHeight: false, breakPages: true,
        h: value => {
          const node = defaultOptions.h(value);
          if (node instanceof Element && node.tagName.toLowerCase() === 'style') return stylesheet(node.textContent ?? '');
          if (node instanceof Element && node.tagName.toLowerCase() === 'a') node.removeAttribute('href');
          return node;
        },
      });
      if (!current) return;
      // Preserve the document's paper, margins and footer placement. Shrink the
      // whole page in narrow panes instead of reflowing it into a short strip.
      const fitPages = () => {
        const available = doc.documentElement.clientWidth - 24;
        if (available <= 0) return;
        root.querySelectorAll<HTMLElement>('section.docx').forEach(page => {
          if (page.offsetWidth > 0) page.style.zoom = String(Math.min(1, available / page.offsetWidth));
        });
      };
      fitPages();
      if (typeof ResizeObserver !== 'undefined' && frame.current) {
        resizeObserver = new ResizeObserver(fitPages);
        resizeObserver.observe(frame.current);
      }
      rendered=true;setPhase('ready');
      if(current&&citation&&workspaceId){
        const located=await runtimeRequest<{status:string;notice:string;text?:string;locator?:{file_hash:string;index:number}}>(`/v1/litigation/workspaces/${encodeURIComponent(workspaceId)}/source-location`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(citation)});
        if(!current)return;
        const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(v=>v.toString(16).padStart(2,'0')).join('');
        const paragraphs=Array.from(root.querySelectorAll('article p'));
        const at=located.locator?paragraphs[located.locator.index]:null;
        const unique=paragraphs.filter(p=>p.textContent?.includes(citation.quote));
        const hit=located.status==='located'&&located.locator?.file_hash===hash ? (at?.textContent===located.text?at:unique.length===1?unique[0]:null):null;
        if(hit){hit.classList.add('source-location');hit.scrollIntoView({block:'center'});setLocationNote(located.notice||'已定位引文所在段落。');}
        else setLocationNote(located.notice||'当前预览不能确认引文位置，请核对原摘录。');
      }

    })().catch(() => {if(current){if(rendered)setLocationNote('引文位置暂时无法核对，原文预览仍可查看。');else setPhase('error');}});
    return () => {current = false; resizeObserver?.disconnect(); styleURLs.forEach(url => URL.revokeObjectURL(url));};
  }, [source,citation,workspaceId]);
  return <div className="preview-docx">
    {showNote && <p className="preview-docx-note">只读预览 · 完整排版、批注和修订请用 Word/WPS 查看</p>}
    {citation&&<details open><summary>{locationNote||'正在核对引文位置…'}</summary><blockquote>{citation.quote}</blockquote></details>}
    {phase === 'loading' && <p role="status">正在打开 Word…</p>}
    {phase === 'error' && <p role="alert">这份 Word 暂时无法预览，请用默认应用打开查看。</p>}
    <iframe ref={frame} title="Word 正文只读预览" sandbox="allow-same-origin" className="preview-docx-frame" hidden={phase === 'error'}/>
  </div>;
}
