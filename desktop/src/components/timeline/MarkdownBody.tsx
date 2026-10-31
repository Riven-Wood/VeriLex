import { invoke, isTauri } from "@tauri-apps/api/core";
import { CopyButton } from "./MessageActions";
import { visibleAssistantText } from "../../timeline/assistantText";
import { renderCaseCitationText, focusCaseSource, type CaseCitationSource } from "../../timeline/caseCitations";
import {renderKnowledgeCitationText, type KnowledgeCitationSource} from "../../timeline/knowledgeCitations";
import {renderToolCitationText, type ToolCitationSource} from "../../timeline/toolCitations";
import {runtimeRequest} from "../../runtime/api";
import { memo, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { CitationSource } from "../../runtime/contracts";
import {
  citationTokenNonceFrom,
  citationSourcesFrom,
  renderCitationText,
  type CitationTextPart,
} from "../../timeline/citations";

interface MarkdownBodyProps {
  text: string;
  sources?: CitationSource[];
  caseSources?: CaseCitationSource[];
  knowledgeSources?: KnowledgeCitationSource[];
  toolSources?: ToolCitationSource[];
  threadId?: string;
  streaming?: boolean;
}

interface MarkdownNode {
  type?: string;
  value?: string;
  children?: MarkdownNode[];
  data?: {
    hProperties?: Record<string, unknown>;
  };
  url?: string;
  title?: string | null;
}

const CITATION_TOKEN_PREFIX = "VERILEX_CITATION_TOKEN_";

function preparedCitationMarkdown(
  text: string,
  sources: CitationSource[],
  streaming: boolean,
  caseSources: CaseCitationSource[],
  knowledgeSources: KnowledgeCitationSource[],
  toolSources: ToolCitationSource[],
): { markdown: string; tokenParts: Map<string, Extract<CitationTextPart, { kind: "citation" }>> } {
  const tokenParts = new Map<
    string,
    Extract<CitationTextPart, { kind: "citation" }>
  >();
  const nonce = citationTokenNonceFrom(text);
  const tokenPrefix = `${CITATION_TOKEN_PREFIX}${nonce}_`;
  const withTools=renderToolCitationText(text,toolSources,streaming).map((part,index)=>{
    if(part.kind==='text')return part.text;
    const token=`${tokenPrefix}TOOL_${index}_END`;
    tokenParts.set(token,{kind:'citation',number:part.source.number,title:part.source.title,url:`#verilex-source-${part.source.sourceId}`});
    return token;
  }).join('');
  const withKnowledge=renderKnowledgeCitationText(withTools,knowledgeSources,streaming).map((part,index)=>{
    if(part.kind==='text')return part.text;
    const token=`${tokenPrefix}KNOWLEDGE_${index}_END`;
    tokenParts.set(token,{kind:'citation',number:part.source.number,title:part.source.title,url:`#${part.source.cardId}`});
    return token;
  }).join('');
  const withCases = renderCaseCitationText(withKnowledge, caseSources, streaming).map((part, index) => {
    if (part.kind === "text") return part.text;
    const token = `${tokenPrefix}CASE_${index}_END`;
    tokenParts.set(token, { kind: "citation", number: part.source.number, title: part.source.title, url: `#${part.source.cardId}` });
    return token;
  }).join("");
  const markdown = renderCitationText(withCases, sources, streaming)
    .map((part, index) => {
      if (part.kind === "text") return part.value;
      const token = `${tokenPrefix}${index}_END`;
      tokenParts.set(token, part);
      return token;
    })
    .join("");
  return { markdown, tokenParts };
}

function replaceTokensInText(
  value: string,
  tokenParts: Map<string, Extract<CitationTextPart, { kind: "citation" }>>,
  allowLinks: boolean,
): MarkdownNode[] {
  const nodes: MarkdownNode[] = [];
  let cursor = 0;
  const tokens = Array.from(tokenParts.keys());
  while (cursor < value.length) {
    let nextIndex = -1;
    let nextToken = "";
    for (const token of tokens) {
      const index = value.indexOf(token, cursor);
      if (index >= 0 && (nextIndex < 0 || index < nextIndex)) {
        nextIndex = index;
        nextToken = token;
      }
    }
    if (nextIndex < 0) {
      nodes.push({ type: "text", value: value.slice(cursor) });
      break;
    }
    if (nextIndex > cursor) {
      nodes.push({ type: "text", value: value.slice(cursor, nextIndex) });
    }
    const part = tokenParts.get(nextToken);
    if (part) {
      if (allowLinks) {
        nodes.push({
          type: "link",
          url: part.url,
          title: null,
          children: [{ type: "text", value: `[${part.number}]` }],
          data: {
            hProperties: {
              className: "citation-link",
              "aria-label": `来源 ${part.number}：${part.title}`,
            },
          },
        });
      } else {
        nodes.push({ type: "text", value: `[${part.number}]` });
      }
    }
    cursor = nextIndex + nextToken.length;
  }
  return nodes.length > 0 ? nodes : [{ type: "text", value: "" }];
}

function citationPlugin(
  tokenParts: Map<string, Extract<CitationTextPart, { kind: "citation" }>>,
) {
  return () => (tree: MarkdownNode) => {
    const containsToken = (value: string): boolean =>
      Array.from(tokenParts.keys()).some((token) => value.includes(token));
    const nodeText = (node: MarkdownNode): string => {
      if (typeof node.value === "string") return node.value;
      return node.children?.map(nodeText).join("") ?? "";
    };
    const nodeContainsToken = (node: MarkdownNode): boolean =>
      (typeof node.value === "string" && containsToken(node.value)) ||
      node.children?.some(nodeContainsToken) === true;
    const visit = (node: MarkdownNode, insideLink = false): void => {
      if (
        (node.type === "inlineCode" || node.type === "code") &&
        typeof node.value === "string"
      ) {
        node.value = replaceTokensInText(node.value, tokenParts, false)
          .map((child) => child.value ?? "")
          .join("");
        return;
      }
      if (!node.children) return;
      const nextChildren: MarkdownNode[] = [];
      for (const child of node.children) {
        const citationInLinkLabel = child.type === "link" && nodeContainsToken(child);
        if (
          child.type === "link" &&
          (
            citationInLinkLabel ||
            (typeof child.url === "string" && containsToken(child.url))
          )
        ) {
          // A citation marker in a Markdown destination must never become
          // part of an href. Keep only the visible label as plain text.
          nextChildren.push(...replaceTokensInText(
            nodeText(child),
            tokenParts,
            citationInLinkLabel,
          ));
        } else if (child.type === "text" && typeof child.value === "string") {
          nextChildren.push(...replaceTokensInText(
            child.value,
            tokenParts,
            !insideLink && node.type !== "link",
          ));
        } else {
          visit(child, insideLink || child.type === "link");
          nextChildren.push(child);
        }
      }
      node.children = nextChildren;
    };
    visit(tree);
  };
}

// Assistant text is model-authored Markdown. Raw HTML is never enabled, so
// model output cannot inject markup; links open in the system browser.
// Memoized: a full markdown parse is expensive, and during streaming every
// flush re-renders the item list — completed messages must not re-parse.
export const MarkdownBody = memo(function MarkdownBody({
  text,
  sources = [],
  caseSources = [],
  knowledgeSources = [],
  toolSources = [],
  threadId,
  streaming = false,
}: MarkdownBodyProps) {
  const [sourceView,setSourceView]=useState<{title:string;text?:string;truncated?:boolean}|null>(null);
  const requestVersion=useRef(0);
  async function openToolSource(source:ToolCitationSource) {
    if (!threadId) return;
    const version=++requestVersion.current;
    setSourceView({title:source.title,text:'正在读取来源…'});
    try {
      const result=await runtimeRequest<{text?:string;truncated?:boolean}>(`/v1/threads/${encodeURIComponent(threadId)}/sources/${encodeURIComponent(source.sourceId)}`);
      if(version===requestVersion.current)setSourceView({title:source.title,text:result.text,truncated:result.truncated});
    } catch {
      if(version===requestVersion.current)setSourceView({title:source.title,text:'该来源暂不可用，无法核验。'});
    }
  }
  const safeSources = useMemo(
    () => citationSourcesFrom(sources) ?? [],
    [sources],
  );
  const prepared = useMemo(
    () => preparedCitationMarkdown(visibleAssistantText(text, streaming), safeSources, streaming, caseSources, knowledgeSources, toolSources),
    [safeSources, streaming, text, caseSources, knowledgeSources, toolSources],
  );
  const plugins = useMemo(
    () => [remarkGfm, citationPlugin(prepared.tokenParts)],
    [prepared.tokenParts],
  );
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={plugins}
        components={{
          pre: ({ children }) => <CopyableCode>{children}</CopyableCode>,
          a: ({ href, children, node: _node, ...props }) => {
            const source = caseSources.find((entry) => href === `#${entry.cardId}`);
            if (source) return <button type="button" className="citation-link" aria-label={`案例来源 ${source.number}：${source.title}`} onClick={() => focusCaseSource(source)}>{children}</button>;
            const knowledge = knowledgeSources.find((entry) => href === `#${entry.cardId}`);
            if (knowledge) return <button type="button" className="citation-link" aria-label={`知识库来源 ${knowledge.number}：${knowledge.title}`} onClick={() => {const target=document.getElementById(knowledge.cardId);target?.scrollIntoView?.({block:'center'});target?.focus({preventScroll:true});}}>{children}</button>;
            const tool = toolSources.find((entry) => href === `#verilex-source-${entry.sourceId}`);
            if (tool?.plugin && tool.url) return <a className="citation-link" href={tool.url} target="_blank" rel="noreferrer noopener" aria-label={`来源 ${tool.number}：${tool.title}`} onClick={event => { if (!isTauri()) return; event.preventDefault(); void invoke("browser_action", {action:"external", url:tool.url}).catch(() => setSourceView({title:tool.title,text:"系统浏览器没有打开，请稍后重试。"})); }}>{children}</a>;
            if (tool && threadId) return <button type="button" className="citation-link" aria-label={`工具来源 ${tool.number}：${tool.title}`} onClick={() => void openToolSource(tool)}>{children}</button>;
            return (
            <a
              {...props}
              href={href}
              target="_blank"
              rel="noreferrer noopener"
            >
              {children}
            </a>
          ); },
        }}
      >
        {prepared.markdown}
      </ReactMarkdown>
      {sourceView && <section className="tool-source-view" aria-label="来源内容" aria-live="polite"><strong>{sourceView.title} · 来源内容</strong><button type="button" onClick={()=>{requestVersion.current++;setSourceView(null);}}>关闭</button><pre style={{whiteSpace:'pre-wrap',maxHeight:400,overflow:'auto'}}>{sourceView.text}</pre>{sourceView.truncated && <p>内容较长，此处显示前 50000 字。</p>}</section>}
    </div>
  );
}, (previous, next) => previous.text === next.text
  && previous.streaming === next.streaming
  && previous.threadId === next.threadId
  && JSON.stringify(previous.sources ?? []) === JSON.stringify(next.sources ?? [])
  && JSON.stringify(previous.caseSources ?? []) === JSON.stringify(next.caseSources ?? [])
  && JSON.stringify(previous.knowledgeSources ?? []) === JSON.stringify(next.knowledgeSources ?? [])
  && JSON.stringify(previous.toolSources ?? []) === JSON.stringify(next.toolSources ?? []));

function CopyableCode({ children }: { children?: ReactNode }) {
  const ref = useRef<HTMLPreElement>(null);
  return <div className="copyable-code"><CopyButton label="复制代码" text={codeText(children)} /><pre ref={ref}>{children}</pre></div>;
}
function codeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(codeText).join("");
  if (node && typeof node === "object" && "props" in node) return codeText(node.props.children);
  return "";
}
