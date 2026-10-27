import type { ToolItem } from "../../timeline/items";
import { litigationApprovalPreview, litigationWorkName } from "../../timeline/litigationApproval";
import "./LitigationSaveApproval.css";

export function LitigationSaveApproval({item}: {item: ToolItem}) {
  const preview = litigationApprovalPreview(item);
  const revision = preview?.expected_revision ?? item.argumentsSummary?.expected_revision;
  const validRevision = typeof revision === "number" && Number.isSafeInteger(revision) && revision >= 0;
  return <section className="litigation-save-review" aria-label="本次保存内容">
    <p className="litigation-save-target">{preview
      ? `${litigationWorkName(preview.kind)} · ${preview.title}`
      : "将本次讨论成果保存到工作台"}</p>
    <p className="litigation-save-scope">{preview?.scope === "section"
      ? "只修改当前讨论的条目，其他条目保持原样。"
      : preview ? `更新整份成果，共 ${preview.section_count} 个条目。` : "保存范围以当前讨论绑定的成果为准。"}
      {validRevision && <span>{revision === 0 ? "首次保存为第 1 版。" : `第 ${revision} 版 → 第 ${revision + 1} 版，旧版保留。`}</span>}
    </p>
    {preview ? <details className="litigation-save-detail" open>
      <summary>审阅本次内容</summary>
      <div className="litigation-save-sections">
        {preview.sections.map((section, index) => <article key={index}>
          <h4>{section.title}</h4>
          <div className="litigation-save-body">{section.body}</div>
          {section.sources.length > 0 && <details className="litigation-save-sources">
            <summary>出处与摘录（{section.sources.length}）</summary>
            {section.sources.map((source, sourceIndex) => <div key={sourceIndex}>
              <p>{source.path}{source.page ? ` · PDF 第 ${source.page} 页` : ""}</p>
              <blockquote>{source.quote}</blockquote>
            </div>)}
          </details>}
          {section.pending.length > 0 && <div className="litigation-save-pending">
            <strong>待核实</strong><ul>{section.pending.map((entry, pendingIndex) => <li key={pendingIndex}>{entry}</li>)}</ul>
          </div>}
        </article>)}
      </div>
      {preview.truncated && <p className="litigation-save-limited">内容较长，这里只展示部分。可先拒绝并要求分批保存，以便逐项审阅。</p>}
    </details> : <p className="litigation-save-limited">这条记录没有可审阅的内容预览。若尚未核对，可拒绝本次保存并要求重新提交。</p>}
  </section>;
}
