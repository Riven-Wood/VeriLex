import { useEffect, useRef } from "react";
import { ChevronDown } from "lucide-react";

import { groupedSearchServers } from "../mcp/providerGroups";
import { KnowledgePicker, type KnowledgeSelection } from "./KnowledgePicker";
import type { McpServerRecord } from "../runtime/contracts";

export type CaseChannel =
  | { kind: "plugin"; serverId: string | null }
  | { kind: "knowledge" };

export type CaseMenu = "plugin" | "knowledge" | null;

interface CaseChannelPickerProps {
  knowledgeSelection?: KnowledgeSelection;
  mcpServers: readonly McpServerRecord[];
  value: CaseChannel;
  onChange: (value: CaseChannel) => void;
  onOpenMcpSettings: () => void;
  /** Controlled open menu, so the search view can surface it on submit. */
  menu: CaseMenu;
  onMenuChange: (menu: CaseMenu) => void;
}

export function CaseChannelPicker({
  knowledgeSelection,
  mcpServers,
  value,
  onChange,
  onOpenMcpSettings,
  menu,
  onMenuChange,
}: CaseChannelPickerProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menu) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        onMenuChange(null);
      }
    };
    window.addEventListener("mousedown", onPointerDown);
    return () => window.removeEventListener("mousedown", onPointerDown);
  }, [menu, onMenuChange]);

  useEffect(() => {
    if (!menu) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onMenuChange(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu, onMenuChange]);

  const groups = groupedSearchServers(mcpServers);
  const selectedGroup = groups.find(group => value.kind === "plugin" && value.serverId !== null && group.ids.includes(value.serverId));

  const selectChannel = (channel: CaseMenu) => {
    if (channel === "knowledge") onChange({ kind: "knowledge" });
    onMenuChange(menu === channel ? null : channel);
  };

  return (
    <div
      ref={rootRef}
      className="legal-channel-picker"
      role="group"
      aria-label="检索渠道"
    >
      <button
        type="button"
        className="legal-channel-button"
        aria-pressed={value.kind === "plugin"}
        aria-expanded={menu === "plugin"}
        onClick={() => selectChannel("plugin")}
      >
        插件{selectedGroup ? ` · ${selectedGroup.label}` : ""}
        <ChevronDown aria-hidden="true" size={14} />
      </button>
      <button
        type="button"
        className="legal-channel-button"
        aria-pressed={value.kind === "knowledge"}
        aria-expanded={menu === "knowledge"}
        onClick={() => selectChannel("knowledge")}
      >
        知识库
        <ChevronDown aria-hidden="true" size={14} />
      </button>


      {menu === "plugin" ? (
        <div
          className="legal-channel-menu"
          data-testid="case-plugin-menu"
          role="menu"
          aria-label="插件渠道"
        >
          {mcpServers.length === 0 ? (
            <>
              <p className="legal-channel-menu-note">
                还没有接入任何插件，接入后可直接检索商业法律数据库。
              </p>
              <button
                type="button"
                role="menuitem"
                className="legal-channel-menu-action"
                onClick={() => {
                  onMenuChange(null);
                  onOpenMcpSettings();
                }}
              >
                添加插件
              </button>
            </>
          ) : (
            groups.map((group) => (
              <button
                key={group.representative.id}
                type="button"
                role="menuitemradio"
                className="legal-channel-menu-item"
                aria-checked={
                  value.kind === "plugin" && value.serverId !== null && group.ids.includes(value.serverId)
                }
                onClick={() => {
                  onChange({ kind: "plugin", serverId: group.representative.id });
                  onMenuChange(null);
                }}
              >
                {group.label}
              </button>
            ))
          )}
        </div>
      ) : null}

      {menu === "knowledge" ? (
        <div
          className="legal-channel-menu"
          data-testid="case-knowledge-menu"
          role="menu"
          aria-label="知识库渠道"
        >
          {knowledgeSelection && <KnowledgePicker {...knowledgeSelection} showTrigger={false} />}
        </div>
      ) : null}
    </div>
  );
}

export function defaultCaseChannel(servers: readonly McpServerRecord[]): CaseChannel {
  return servers.length ? { kind: "plugin", serverId: null } : { kind: "knowledge" };
}
