import { useEffect, useRef, useState } from "react";
import { ExternalLink, Scale, Search } from "lucide-react";

import {
  LegalChannelPicker,
  defaultLegalChannel,
  type LegalChannel,
  type LegalMenu,
} from "../components/LegalChannelPicker";
import { LegalSkillSuggestions } from "../components/LegalSkillSuggestions";
import { openLawDatabase } from "../platform/lawDatabase";
import type { McpServerRecord } from "../runtime/contracts";

export interface LegalHistoryEntry {
  id: string;
  keyword: string;
}

interface LegalSearchViewProps {
  mcpServers: readonly McpServerRecord[];
  /** 本页专属检索记录（查询词），来自 scene=legal-retrieval 会话。 */
  history: readonly LegalHistoryEntry[];
  starting: boolean;
  activeSearch?: { id: string; channel?: "web" | "plugin" | null; skills?: string[] };
  onStartSearch: (question: string, serverId: string | null, skills: string[], knowledgeLibraryIds?: string[]) => void;
  onSelectHistory: (threadId: string) => void;
  onDeleteHistory: (threadId: string) => void;
  onOpenMcpSettings: () => void;
}

/**
 * 法律检索页头部（2026-09-05 产品定稿）：三渠道 + 输入 + 本页检索记录 +
 * 国家法律法规数据库内置浏览器入口。检索由下方内嵌的会话视图执行
 * （智能体调用 MCP/知识库/联网），这里只负责发起与记录。
 */
export function LegalSearchView({
  mcpServers,
  history,
  starting,
  activeSearch,
  onStartSearch,
  onSelectHistory,
  onDeleteHistory,
  onOpenMcpSettings,
}: LegalSearchViewProps) {
  const [skills, setSkills] = useState<string[]>([]);
  const [knowledgeIds, setKnowledgeIds] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [channel, setChannel] = useState<LegalChannel>(() =>
    defaultLegalChannel(mcpServers),
  );
  const [menu, setMenu] = useState<LegalMenu>(null);
  const [windowError, setWindowError] = useState("");
  const touchedChannelRef = useRef(false);
  useEffect(() => {
    if (!activeSearch) return;
    setSkills(activeSearch.skills ?? []);
    if (activeSearch.channel) {
      touchedChannelRef.current = true;
      setChannel(activeSearch.channel === "web" ? { kind: "online" } : { kind: "plugin", serverId: null });
    }
  }, [activeSearch?.id]);

  useEffect(() => {
    if (touchedChannelRef.current) return;
    setChannel(defaultLegalChannel(mcpServers));
  }, [mcpServers]);

  const submit = () => {
    const trimmed = question.trim();
    if (!trimmed || starting) return;
    if (channel.kind === "plugin" && !channel.serverId) {
      setMenu("plugin");
      return;
    }
    if (channel.kind === "knowledge" && !knowledgeIds.length) { setMenu("knowledge"); return; }
    onStartSearch(trimmed, channel.kind === "plugin" ? channel.serverId : null, skills, channel.kind === "knowledge" ? [...knowledgeIds] : []);
  };

  return (
    <section className="legal-search-view">
      <header className="legal-search-header">
        <Scale aria-hidden="true" size={28} />
        <div>
          <h1>法律检索</h1>
          <p>输入法律问题或法条名称，由智能体按所选渠道检索并呈现结果</p>
        </div>
        <button
          type="button"
          className="legal-search-database-button"
          onClick={() => {
            setWindowError("");
            openLawDatabase().catch(() =>
              setWindowError("官方数据库窗口没有打开，请稍后重试。"),
            );
          }}
        >
          <ExternalLink aria-hidden="true" size={14} />
          国家法律法规数据库
        </button>
      </header>

      <div className="legal-search-channel-row">
        <span className="legal-search-channel-label">渠道：</span>
        <LegalChannelPicker
          knowledgeSelection={{selected: knowledgeIds, onChange: setKnowledgeIds, disabled: starting}}
          mcpServers={mcpServers}
          value={channel}
          onChange={(next) => {
            touchedChannelRef.current = true;
            setChannel(next);
          }}
          onOpenMcpSettings={onOpenMcpSettings}
          menu={menu}
          onMenuChange={setMenu}
        />
        <LegalSkillSuggestions value={skills} onChange={setSkills} />
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
            placeholder="输入法律问题，例如：劳动合同解除的赔偿标准"
            aria-label="法律检索问题"
          />
          <button type="submit" disabled={starting}>
            {starting ? "创建中…" : "检索"}
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
