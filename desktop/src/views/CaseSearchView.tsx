import { useEffect, useRef, useState } from "react";
import { ExternalLink, Gavel, Search } from "lucide-react";

import {
  CaseChannelPicker,
  defaultCaseChannel,
  type CaseChannel,
  type CaseMenu,
} from "../components/CaseChannelPicker";
import { CaseSkillSuggestions } from "../components/CaseSkillSuggestions";
import { openCaseDatabase } from "../platform/caseDatabase";
import type { McpServerRecord } from "../runtime/contracts";

export interface CaseHistoryEntry {
  id: string;
  keyword: string;
}

interface CaseSearchViewProps {
  mcpServers: readonly McpServerRecord[];
  /** 本页专属检索记录（查询词），来自 scene=case-retrieval 会话。 */
  history: readonly CaseHistoryEntry[];
  starting: boolean;
  activeSearch?: { id: string; channel?: "plugin" | null; skills?: string[] };
  onStartSearch: (question: string, serverId: string | null, skills: string[], knowledgeLibraryIds?: string[]) => void;
  onSelectHistory: (threadId: string) => void;
  onDeleteHistory: (threadId: string) => void;
  onOpenMcpSettings: () => void;
}

/** 案例检索入口与本页历史；官方案例网站只通过系统浏览器手动打开。 */
export function CaseSearchView({
  mcpServers,
  history,
  starting,
  activeSearch,
  onStartSearch,
  onSelectHistory,
  onDeleteHistory,
  onOpenMcpSettings,
}: CaseSearchViewProps) {
  const availableServers = mcpServers;
  const [skills, setSkills] = useState<string[]>([]);
  const [knowledgeIds, setKnowledgeIds] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [channel, setChannel] = useState<CaseChannel>(() =>
    defaultCaseChannel(availableServers),
  );
  const [menu, setMenu] = useState<CaseMenu>(null);
  const [windowError, setWindowError] = useState("");
  const touchedChannelRef = useRef(false);
  useEffect(() => {
    if (!activeSearch) return;
    setSkills(activeSearch.skills ?? []);
    if (activeSearch.channel) {
      touchedChannelRef.current = true;
      setChannel({ kind: "plugin", serverId: null });
    }
  }, [activeSearch?.id]);

  useEffect(() => {
    if (touchedChannelRef.current) return;
    setChannel(defaultCaseChannel(availableServers));
  }, [mcpServers]);

  const submit = () => {
    const trimmed = question.trim();
    if (!trimmed || starting) return;
    if (channel.kind === "plugin" && !availableServers.some((server) => server.id === channel.serverId)) {
      setMenu("plugin");
      return;
    }
    if (channel.kind === "knowledge" && !knowledgeIds.length) { setMenu("knowledge"); return; }
    onStartSearch(trimmed, channel.kind === "plugin" ? channel.serverId : null, skills, channel.kind === "knowledge" ? [...knowledgeIds] : []);
  };

  return (
    <section className="legal-search-view">
      <header className="legal-search-header">
        <Gavel aria-hidden="true" size={28} />
        <div>
          <h1>案例检索</h1>
          <p>输入法律问题或案件事实，在所选资料中检索类似案例</p>
        </div>
        <div className="case-search-database-actions">
          <button
            type="button"
            className="legal-search-database-button"
            aria-label="用系统浏览器打开人民法院案例库"
            title="用系统浏览器打开人民法院案例库"
            onClick={() => {
              setWindowError("");
              openCaseDatabase("library").catch(() =>
                setWindowError("系统浏览器没有打开，请稍后重试。"),
              );
            }}
          >
            <ExternalLink aria-hidden="true" size={14} />
            人民法院案例库
          </button>
          <button type="button" className="legal-search-database-button"
            aria-label="用系统浏览器打开中国裁判文书网"
            title="用系统浏览器打开中国裁判文书网"
            onClick={() => {
              setWindowError(""); openCaseDatabase("judgments").catch(() => setWindowError("系统浏览器没有打开，请稍后重试。"));
            }}>
            <ExternalLink aria-hidden="true" size={14} />
            中国裁判文书网
          </button>
        </div>
      </header>

      <div className="legal-search-channel-row">
        <span className="legal-search-channel-label">渠道：</span>
        <CaseChannelPicker
          knowledgeSelection={{selected: knowledgeIds, onChange: setKnowledgeIds, disabled: starting}}
          mcpServers={availableServers}
          value={channel}
          onChange={(next) => {
            touchedChannelRef.current = true;
            setChannel(next);
          }}
          onOpenMcpSettings={onOpenMcpSettings}
          menu={menu}
          onMenuChange={setMenu}
        />
        <CaseSkillSuggestions value={skills} onChange={setSkills} />
      </div>

      <form
        className="legal-search-form"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <div className="legal-search-capsule">
          <Search aria-hidden="true" size={16} />
          <input
            type="text"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="输入问题或案情，例如：竞业限制违约金过高，法院一般会怎么判？"
            aria-label="案例检索问题"
          />
          <button type="submit" disabled={starting}>
            {starting ? "创建中…" : "开始调研"}
          </button>
        </div>
      </form>

      {windowError ? (
        <p className="legal-search-error" role="alert">{windowError}</p>
      ) : null}

      {history.length > 0 ? (
        <div className="legal-search-history" aria-label="检索记录">
          <span className="legal-search-history-label">检索记录：</span>
          {history.map((item) => (
            <span key={item.id} className="legal-search-history-item">
              <button
                type="button"
                className="legal-search-history-chip"
                onClick={() => onSelectHistory(item.id)}
              >
                {item.keyword}
              </button>
              <button
                type="button"
                className="legal-search-history-remove"
                aria-label={`删除检索记录 ${item.keyword}`}
                onClick={() => onDeleteHistory(item.id)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}
