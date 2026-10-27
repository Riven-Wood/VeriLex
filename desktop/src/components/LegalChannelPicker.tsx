import { useEffect, useRef } from "react";
import { ChevronDown } from "lucide-react";

import { KnowledgePicker, type KnowledgeSelection } from "./KnowledgePicker";
import type { McpServerRecord } from "../runtime/contracts";

export type LegalChannel =
  | { kind: "plugin"; serverId: string | null }
  | { kind: "knowledge" }
  | { kind: "online" };

export type LegalMenu = "plugin" | "knowledge" | null;

interface LegalChannelPickerProps {
  knowledgeSelection?: KnowledgeSelection;
  mcpServers: readonly McpServerRecord[];
  value: LegalChannel;
  onChange: (value: LegalChannel) => void;
  onOpenMcpSettings: () => void;
  /** Controlled open menu, so the search view can surface it on submit. */
  menu: LegalMenu;
  onMenuChange: (menu: LegalMenu) => void;
}

export function LegalChannelPicker({
  knowledgeSelection,
  mcpServers,
  value,
  onChange,
  onOpenMcpSettings,
  menu,
  onMenuChange,
}: LegalChannelPickerProps) {
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

  const selectedServer = mcpServers.find(
    (server) => value.kind === "plugin" && server.id === value.serverId,
  );

  const channelLabel = (channel: LegalChannel["kind"]): string =>
    channel === "plugin" ? "插件" : channel === "knowledge" ? "知识库" : "联网";

  const selectChannel = (channel: LegalChannel["kind"]) => {
    if (channel === "online") {
      onMenuChange(null);
      onChange({ kind: "online" });
      return;
    }
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
        插件{selectedServer ? ` · ${selectedServer.displayName}` : ""}
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
      <button
        type="button"
        className="legal-channel-button"
        aria-pressed={value.kind === "online"}
        title="国家法律法规数据库"
        onClick={() => selectChannel("online")}
      >
        联网
      </button>

      {menu === "plugin" ? (
        <div
          className="legal-channel-menu"
          data-testid="legal-plugin-menu"
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
            mcpServers.map((server) => (
              <button
                key={server.id}
                type="button"
                role="menuitemradio"
                className="legal-channel-menu-item"
                aria-checked={
                  value.kind === "plugin" && value.serverId === server.id
                }
                onClick={() => {
                  onChange({ kind: "plugin", serverId: server.id });
                  onMenuChange(null);
                }}
              >
                {server.displayName}
              </button>
            ))
          )}
        </div>
      ) : null}

      {menu === "knowledge" ? (
        <div
          className="legal-channel-menu"
          data-testid="legal-knowledge-menu"
          role="menu"
          aria-label="知识库渠道"
        >
          {knowledgeSelection && <KnowledgePicker {...knowledgeSelection} showTrigger={false} />}
        </div>
      ) : null}
    </div>
  );
}

export function defaultLegalChannel(
  mcpServers: readonly McpServerRecord[],
): LegalChannel {
  return mcpServers.length > 0
    ? { kind: "plugin", serverId: null }
    : { kind: "online" };
}

export function legalChannelLabel(channel: LegalChannel): string {
  if (channel.kind === "online") return "联网";
  if (channel.kind === "knowledge") return "知识库";
  return "插件";
}
