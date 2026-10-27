import { memo } from "react";

import type { CitationSource } from "../../runtime/contracts";
import { citationSourcesFrom } from "../../timeline/citations";

function fetchedAtLabel(value: string): string {
  const date = new Date(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

interface SourcesBlockProps {
  sources: CitationSource[];
  citationWarning?: boolean;
}

export const SourcesBlock = memo(function SourcesBlock({
  sources,
  citationWarning = false,
}: SourcesBlockProps) {
  const safeSources = citationSourcesFrom(sources) ?? [];
  if (safeSources.length === 0 && !citationWarning) return null;
  return (
    <section className="sources-block" aria-label="网页来源">
      {safeSources.length > 0 ? (
        <>
          <h3>来源</h3>
          <ol aria-label="已验证来源">
            {safeSources.map((source) => (
              <li key={`${source.number}:${source.sourceId}`}>
                <a
                  href={source.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  aria-label={`来源 ${source.number}：${source.title}`}
                >
                  <span className="source-number" aria-hidden="true">
                    [{source.number}]
                  </span>
                  <span className="source-title">{source.title}</span>
                </a>
                <div className="source-meta">
                  <span>{source.site}</span>
                  <span>读取于 {fetchedAtLabel(source.fetchedAt)}</span>
                  {source.truncated ? <span>内容已截断</span> : null}
                </div>
              </li>
            ))}
          </ol>
        </>
      ) : null}
      {citationWarning ? (
        <div className="citation-warning" role="status">
          部分引用未通过验证，未验证内容不会进入来源列表。
        </div>
      ) : null}
    </section>
  );
});
