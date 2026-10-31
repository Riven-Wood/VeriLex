import { Command, ArrowLeft, ArrowRight, PanelLeftClose, PanelLeftOpen, PanelRightOpen } from "lucide-react";

interface AppToolbarProps {
  onOpenCommands?: () => void;
  sidebarCollapsed: boolean;
  showSidebarToggle?: boolean;
  onToggleSidebar: () => void;
  canGoBack: boolean;
  onBack: () => void;
  canGoForward: boolean;
  onForward: () => void;
  /** Present only in the agent workspace: toggles the right preview rail. */
  rightRail?: {
    open: boolean;
    onToggle: () => void;
  };
}

/**
 * The single top toolbar (product layout decision 2026-08-31, modeled after
 * the reference image): traffic lights sit over the spacer, then the sidebar
 * back/forward and sidebar toggle; the right-rail toggle lives at the far right. The
 * strip doubles as the window drag region; buttons opt out by being real
 * controls.
 */
export function AppToolbar({
  onOpenCommands,
  sidebarCollapsed,
  showSidebarToggle = true,
  onToggleSidebar,
  canGoBack,
  onBack,
  canGoForward,
  onForward,
  rightRail,
}: AppToolbarProps) {
  return (
    <header className="app-toolbar" data-tauri-drag-region>
      <span className="app-toolbar-traffic-spacer" aria-hidden="true" />
      <button
        type="button"
        className="icon-button app-toolbar-button"
        aria-label="后退"
        title="后退"
        disabled={!canGoBack}
        onClick={onBack}
      >
        <ArrowLeft aria-hidden="true" size={15} />
      </button>
      <button
        type="button"
        className="icon-button app-toolbar-button"
        aria-label="前进"
        title="前进"
        disabled={!canGoForward}
        onClick={onForward}
      >
        <ArrowRight aria-hidden="true" size={15} />
      </button>
      {showSidebarToggle && <button
        type="button"
        className="icon-button app-toolbar-button"
        aria-label={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}
        title={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}
        onClick={onToggleSidebar}
      >
        {sidebarCollapsed
          ? <PanelLeftOpen aria-hidden="true" size={15} />
          : <PanelLeftClose aria-hidden="true" size={15} />}
      </button>}
      <span className="app-toolbar-drag" data-tauri-drag-region />
      {onOpenCommands && <button type="button" className="icon-button app-toolbar-button" aria-label="打开命令面板" title="命令面板" onClick={onOpenCommands}><Command aria-hidden="true" size={15}/></button>}
      {rightRail ? (
        <button
          type="button"
          className="icon-button app-toolbar-button"
          aria-label={rightRail.open ? "关闭右侧栏" : "打开右侧栏"}
          title={rightRail.open ? "关闭右侧栏" : "打开右侧栏"}
          aria-pressed={rightRail.open}
          onClick={rightRail.onToggle}
        >
          <PanelRightOpen aria-hidden="true" size={15} />
        </button>
      ) : null}
    </header>
  );
}
