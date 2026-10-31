import { useEffect, useRef, useState, type ReactNode } from "react";
import { ExternalLink, Scale } from "lucide-react";
import { getThreadMcpSnapshot } from "../runtime/api";
import type { SearchSettings } from "./searchSettings";

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
  renderWorkspace?: (controls: ReactNode) => ReactNode;
  mcpServers: readonly McpServerRecord[];
  /** 本页专属检索记录（查询词），来自 scene=legal-retrieval 会话。 */
  history: readonly LegalHistoryEntry[];
  starting: boolean;
  activeSearch?: { id: string; channel?: "web" | "plugin" | null; skills?: string[] };
  locked?: boolean;
  draftSearchId?: string;
  onSettingsChange: (settings: SearchSettings) => void;
  onNewSearch: () => void;
  onSelectHistory: (threadId: string) => void;
  onDeleteHistory: (threadId: string) => void;
  onOpenMcpSettings: () => void;
}

/**
 * 法律检索工作台：页头提供记录和官方库入口，专用渠道与技能交给输入区呈现。
 */
export function LegalSearchView({
  renderWorkspace,
  mcpServers,
  starting,
  activeSearch,
  draftSearchId,
  locked = false,
  onSettingsChange,
  onOpenMcpSettings,
}: LegalSearchViewProps) {
  const [skills, setSkills] = useState<string[]>([]);
  const [knowledgeIds, setKnowledgeIds] = useState<string[]>([]);
  const [channel, setChannel] = useState<LegalChannel>(() =>
    defaultLegalChannel(mcpServers),
  );
  const [menu, setMenu] = useState<LegalMenu>(null);
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
    setChannel(activeSearch.channel === "web" ? { kind: "online" } : { kind: "knowledge" });
  }, [activeSearch?.id]);

  useEffect(() => {
    if (touchedChannelRef.current) return;
    setChannel(defaultLegalChannel(mcpServers));
  }, [mcpServers]);

  useEffect(() => {
    onSettingsChange({kind: channel.kind, serverId: channel.kind === "plugin" ? channel.serverId : null,
      skills, knowledgeIds, valid: channel.kind === "online" || (channel.kind === "knowledge" ? knowledgeIds.length > 0 : mcpServers.some(server => server.id === channel.serverId))});
  }, [channel, skills, knowledgeIds, mcpServers, onSettingsChange]);

  const controls = (
    <fieldset className="legal-search-channel-row" disabled={starting || locked} aria-label="检索设置">
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
        <LegalSkillSuggestions disabled={starting || locked} value={skills} onChange={setSkills} />
      </fieldset>
  );

  return (
    <>
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



      {windowError ? (
        <p className="legal-search-error" role="alert">{windowError}</p>
      ) : null}


    </section>
    {renderWorkspace ? renderWorkspace(controls) : controls}
    </>
  );
}
