import { useEffect, useRef, useState, type ReactNode } from "react";
import { ExternalLink, Gavel } from "lucide-react";
import { getThreadMcpSnapshot } from "../runtime/api";
import type { SearchSettings } from "./searchSettings";

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
  renderWorkspace?: (controls: ReactNode) => ReactNode;
  mcpServers: readonly McpServerRecord[];
  /** 本页专属检索记录（查询词），来自 scene=case-retrieval 会话。 */
  history: readonly CaseHistoryEntry[];
  starting: boolean;
  activeSearch?: { id: string; channel?: "plugin" | null; skills?: string[] };
  locked?: boolean;
  draftSearchId?: string;
  onSettingsChange: (settings: SearchSettings) => void;
  onNewSearch: () => void;
  onSelectHistory: (threadId: string) => void;
  onDeleteHistory: (threadId: string) => void;
  onOpenMcpSettings: () => void;
}

/** 案例检索入口与本页历史；官方案例网站只通过系统浏览器手动打开。 */
export function CaseSearchView({
  renderWorkspace,
  mcpServers,
  starting,
  activeSearch,
  draftSearchId,
  locked = false,
  onSettingsChange,
  onOpenMcpSettings,
}: CaseSearchViewProps) {
  const availableServers = mcpServers;
  const [skills, setSkills] = useState<string[]>([]);
  const [knowledgeIds, setKnowledgeIds] = useState<string[]>([]);
  const [channel, setChannel] = useState<CaseChannel>(() =>
    defaultCaseChannel(availableServers),
  );
  const [menu, setMenu] = useState<CaseMenu>(null);
  const [windowError, setWindowError] = useState("");
  const touchedChannelRef = useRef(false);
  const previousDraftId = useRef<string | undefined>(undefined);
  if (draftSearchId) previousDraftId.current = draftSearchId;
  useEffect(() => {
    if (!activeSearch) return;
    setSkills(activeSearch.skills ?? []);
    if (previousDraftId.current !== activeSearch.id) setKnowledgeIds([]);
    previousDraftId.current = undefined;
    touchedChannelRef.current = true;
    if (activeSearch.channel === "plugin") {
      setChannel({ kind: "plugin", serverId: null });
      let cancelled = false;
      getThreadMcpSnapshot(activeSearch.id).then(snapshot => {
        if (!cancelled) setChannel(current => current.kind === "plugin" && current.serverId === null
          ? {kind: "plugin", serverId: snapshot.servers.find(server => server.selected)?.serverId ?? null} : current);
      }).catch(() => undefined);
      return () => { cancelled = true; };
    }
    setChannel({ kind: "knowledge" });
  }, [activeSearch?.id]);

  useEffect(() => {
    if (touchedChannelRef.current) return;
    setChannel(defaultCaseChannel(availableServers));
  }, [mcpServers]);

  useEffect(() => {
    onSettingsChange({kind: channel.kind, serverId: channel.kind === "plugin" ? channel.serverId : null,
      skills, knowledgeIds, valid: channel.kind === "knowledge" ? knowledgeIds.length > 0 : availableServers.some(server => server.id === channel.serverId)});
  }, [channel, skills, knowledgeIds, availableServers, onSettingsChange]);

  const controls = (
    <fieldset className="legal-search-channel-row" disabled={starting || locked} aria-label="检索设置">
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
        <CaseSkillSuggestions disabled={starting || locked} value={skills} onChange={setSkills} />
      </fieldset>
  );

  return (
    <>
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



      {windowError ? (
        <p className="legal-search-error" role="alert">{windowError}</p>
      ) : null}


    </section>
    {renderWorkspace ? renderWorkspace(controls) : controls}
    </>
  );
}
