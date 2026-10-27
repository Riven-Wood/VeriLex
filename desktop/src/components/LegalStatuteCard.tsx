import { memo, useState } from "react";
import { getCachedLawText } from "../runtime/api";

import type { LawReadSummary } from "../timeline/lawResults";

export interface LawRefreshRequest {
  bbbs: string;
  title: string;
  clause?: string;
}

interface LegalStatuteCardProps {
  summary: LawReadSummary;
  /** Present only when the host can re-send a law_read turn (refresh). */
  onRefresh?: (request: LawRefreshRequest) => void;
}

function formatFetchedAt(iso: string): string {
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) return iso;
  const date = new Date(parsed);
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** 条文卡：只在结构化摘要完整时渲染（规格第 5 节固定字段）。 */
export const LegalStatuteCard = memo(function LegalStatuteCard({
  summary,
  onRefresh,
}: LegalStatuteCardProps) {
  const read = summary;
  const [expanded, setExpanded] = useState(false);
  const [fullText, setFullText] = useState<string[] | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const paragraphs = fullText ?? read.clauseText ?? [];
  const long = read.truncated || !read.clauseText || paragraphs.join("\n").length > 600;
  async function expand() {
    if (expanded) { setExpanded(false); return; }
    if ((read.truncated || !read.clauseText) && !fullText) {
      setLoading(true);
      setError("");
      try { setFullText((await getCachedLawText(read.bbbs, read.clause)).paragraphs); }
      catch { setError("本机原文暂不可用，请重新读取。"); return; }
      finally { setLoading(false); }
    }
    setExpanded(true);
  }

  return (
    <div className="legal-statute-card" data-cached={read.cached}>
      <div className="legal-statute-head">
        <span className="legal-statute-title">{read.title}</span>
        {read.clause ? (
          <span className="legal-statute-clause">{read.clause}</span>
        ) : null}
      </div>
      {paragraphs.length > 0 ? (
        <blockquote className="legal-statute-text">
          {(expanded || !long ? paragraphs : [paragraphs.join("\n").slice(0, 600) + "…"]).map((paragraph, index) => (
            <p key={index}>{paragraph}</p>
          ))}
        </blockquote>
      ) : null}
      {long && <button type="button" onClick={() => void expand()} disabled={loading}
        aria-expanded={expanded}>{loading ? "读取中…" : expanded ? "收起全文" : "展开全文"}</button>}
      {error && <p role="alert">{error}</p>}
      <dl className="legal-statute-meta">
        <div>
          <dt>效力</dt>
          <dd>{read.effectivity}</dd>
        </div>
        {read.gbrq ? (
          <div>
            <dt>公布日期</dt>
            <dd>{read.gbrq}</dd>
          </div>
        ) : null}
        {read.sxrq ? (
          <div>
            <dt>施行日期</dt>
            <dd>{read.sxrq}</dd>
          </div>
        ) : null}
        <div>
          <dt>来源</dt>
          <dd>{read.source}</dd>
        </div>
        <div>
          <dt>读取时间</dt>
          <dd>{formatFetchedAt(read.fetchedAt)}</dd>
        </div>
      </dl>
      {read.cached ? (
        <div className="legal-statute-cache">
          <span>本机缓存 · 读取于 {formatFetchedAt(read.fetchedAt)}</span>
          {onRefresh ? (
            <button
              type="button"
              onClick={() =>
                onRefresh({
                  bbbs: read.bbbs,
                  title: read.title,
                  clause: read.clause,
                })
              }
            >
              重新读取
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
});
