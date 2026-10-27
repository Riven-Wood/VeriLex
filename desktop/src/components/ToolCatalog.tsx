import { useToolCapabilities, type ToolCapabilitiesState } from "../hooks/useToolCapabilities";
import type {
  ToolCapabilityCategory,
  ToolCapabilityRecord,
} from "../runtime/contracts";
import {
  toolCategoryLabel,
  toolApprovalLabel,
  toolDisplayName,
  toolScopeLabels,
  toolSourceLabel,
} from "../settings/toolCopy";

interface ToolCatalogViewProps {
  query: string;
  state: ToolCapabilitiesState;
}

const CATEGORY_ORDER: readonly ToolCapabilityCategory[] = [
  "files",
  "documents",
  "terminal",
  "skills",
  "external",
  "other",
];

function boundedDescription(description: string): string {
  const normalized = description.replace(/\s+/g, " ").trim();
  return normalized.length > 180
    ? `${normalized.slice(0, 179)}…`
    : normalized;
}

function matchesQuery(tool: ToolCapabilityRecord, query: string): boolean {
  const normalized = query.trim().toLocaleLowerCase("zh-CN");
  if (!normalized) return true;
  const searchable = [
    toolDisplayName(tool.name),
    tool.name,
    boundedDescription(tool.description),
    toolSourceLabel(tool),
    toolCategoryLabel(tool.category),
    toolApprovalLabel(tool),
    ...toolScopeLabels(tool.scopes),
  ];
  return searchable.some((value) =>
    value.toLocaleLowerCase("zh-CN").includes(normalized),
  );
}

function ToolRow({ tool }: { tool: ToolCapabilityRecord }) {
  const displayName = toolDisplayName(tool.name);
  const approvalLabel = toolApprovalLabel(tool);

  return (
    <li className="tool-catalog-item">
      <article
        className="tool-catalog-card"
        aria-label={`${displayName}（${tool.name}）`}
      >
        <div className="tool-catalog-title">
          <h4>{displayName}</h4>
          <code>{tool.name}</code>
        </div>
        <p>{boundedDescription(tool.description)}</p>
        <div className="tool-catalog-badges" aria-label="工具来源与作用域">
          <span>{toolSourceLabel(tool)}</span>
          {toolScopeLabels(tool.scopes).map((scope) => (
            <span key={scope}>{scope}</span>
          ))}
          {tool.requires_workspace ? <span>需要工作区</span> : null}
        </div>
        <div className="tool-catalog-status">
          <strong data-risk={tool.risk_level}>{approvalLabel}</strong>
          <span>{tool.availability === "available" ? "可用" : "不可用"}</span>
        </div>
      </article>
    </li>
  );
}

export function ToolCatalogView({ query, state }: ToolCatalogViewProps) {
  if (state.status === "loading") {
    return <p className="tool-catalog-state" role="status">正在读取工具目录…</p>;
  }

  if (state.status === "error") {
    return (
      <div className="tool-catalog-state tool-catalog-error" role="alert">
        <p>无法读取工具目录。</p>
        <button type="button" onClick={state.retry}>重试</button>
      </div>
    );
  }

  const visibleTools = state.tools.filter((tool) => matchesQuery(tool, query));
  if (visibleTools.length === 0) {
    return (
      <p className="tool-catalog-state" role="status">
        {query.trim() ? "没有匹配的工具" : "当前没有可用工具"}
      </p>
    );
  }

  return (
    <div className="tool-catalog">
      {CATEGORY_ORDER.map((category) => {
        const categoryTools = visibleTools.filter((tool) => tool.category === category);
        if (categoryTools.length === 0) return null;
        return (
          <section className="tool-catalog-group" key={category}>
            <header>
              <h3>{toolCategoryLabel(category)}</h3>
              <small>{categoryTools.length} 项</small>
            </header>
            <ul>
              {categoryTools.map((tool) => <ToolRow key={tool.name} tool={tool} />)}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

export function ToolCatalog({ query }: { query: string }) {
  const state = useToolCapabilities();
  return <ToolCatalogView query={query} state={state} />;
}
