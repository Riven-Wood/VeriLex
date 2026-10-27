import type { CaseResultSummary } from "../../timeline/caseResults";

export function CaseCard({ summary, sourceName, fetchedAt, id }: {
  summary: CaseResultSummary; sourceName: string; fetchedAt: string; id?: string;
}) {
  sourceName = summary.sourceName ?? sourceName;
  fetchedAt = summary.fetchedAt ?? fetchedAt;
  return <article className="legal-statute-card" id={id} tabIndex={-1} aria-label={`案例：${summary.title}`}>
    <header className="legal-statute-head"><h3 className="legal-statute-title">{summary.title}</h3>{summary.level ? <span>{summary.level}</span> : null}</header>
    <p>{summary.caseNumber ?? "未能确认案号"}</p>
    <dl className="legal-statute-meta">{summary.court ? <div><dt>法院</dt><dd>{summary.court}</dd></div> : null}
      {summary.cause ? <div><dt>案由</dt><dd>{summary.cause}</dd></div> : null}
      {summary.judgedAt ? <div><dt>裁判日期</dt><dd>{summary.judgedAt}</dd></div> : null}</dl>
    {summary.gist ? <div><strong>裁判要旨</strong><p>{summary.gist}</p></div> : null}
    {summary.excerpt ? <details><summary>查看渠道返回片段</summary><p style={{ whiteSpace: "pre-wrap" }}>{summary.excerpt}</p></details> : null}
    <footer>来源：{sourceName} · 读取时间：<time dateTime={fetchedAt}>{fetchedAt}</time></footer>
  </article>;
}
