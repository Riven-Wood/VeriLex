import { ConversationSettings, type ConversationSettingsProps } from "../settings/ConversationSettings";
import { ShortcutSettings } from "../commands/GlobalShortcut";
import { HelpCenter } from "../help/HelpCenter";
import {PersonalInstructions} from "../settings/PersonalInstructions";
import {StoragePreferences} from "../settings/StoragePreferences";
import { useUiPreferences, saveUiPreferences, type UiPreferences } from "../settings/preferences";
import { SettingsGroup, SettingsRow, Toggle } from "../settings/SettingsControls";
import { NativePreferences } from "../settings/NativePreferences";
import { WorkspacePermissions } from "../settings/WorkspacePermissions";
import { SETTINGS_CATEGORIES, searchSettings } from "../settings/categories";
import "../settings/settings.css";
import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import type { ConfiguredCapabilities } from "../runtime/contracts";
import { ProviderConnections } from "../components/ProviderConnections";
import { SettingsNavigation } from "../components/SettingsNavigation";
import { LegalCacheSettings } from "../components/LegalCacheSettings";
import { getPreferences, putPreference } from "../runtime/api";
import { runtimeProviderApi } from "../runtime/providerClient";
import type { ProviderApi } from "../runtime/providerClient";
import type { SettingsCategoryId } from "../settings/categories";
import { updateAction, updateProgressText } from "../updater/presentation";
import type { UpdateController, UpdatePhase } from "../updater/contracts";

interface SettingsViewProps {
  conversationSettings?: ConversationSettingsProps;
  onOpenCapabilities?: () => void;
  onWorkspaceChanged?: (id:string,revoked:boolean) => void;
  configured: ConfiguredCapabilities;
  reduceMotion: boolean;
  onReduceMotionChange: (value: boolean) => void;
  providerApi?: ProviderApi;
  runtimeStatusError?: string;
  onProvidersChanged?: () => Promise<void>;
  onModelMetadataChanged?: () => void;
  providerAutoDiscoverDelayMs?: number;
  runtimeVersion?: string;
  updates?: UpdateController;
  /** When true, SettingsView renders its own navigation (single-view mode).
   * When false (default, used by the app frame) the navigation is rendered
   * externally in a settings rail and these values are controlled. */
  embeddedNavigation?: boolean;
  selectedCategory?: SettingsCategoryId;
  onSelectCategory?: (value: SettingsCategoryId) => void;
  query?: string;
  onQueryChange?: (value: string) => void;
}

function BackgroundLimitSettings() {
  const [draft, setDraft] = useState("");
  const [loaded, setLoaded] = useState<number | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);

  useEffect(() => {
    let active = true;
    void getPreferences()
      .then((preferences) => {
        if (!active) return;
        setLoaded(preferences.background_task_max_minutes);
        setDraft(String(preferences.background_task_max_minutes));
      })
      .catch(() => {
        if (active) setLoadFailed(true);
      });
    return () => {
      active = false;
    };
  }, []);

  const parsed = Number(draft);
  const valid = draft !== "" && Number.isInteger(parsed) && parsed >= 1 && parsed <= 120;

  const save = async () => {
    if (!valid || saving) return;
    setSaving(true);
    setSaved(false);
    setSaveFailed(false);
    try {
      await putPreference("background_task_max_minutes", parsed);
      setLoaded(parsed);
      setSaved(true);
    } catch {
      setSaveFailed(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="background-limit-settings">
      <strong>后台命令最长运行时间</strong>
      <small>
        新启动的后台命令到达此时间后会自动停止；已运行的命令不受影响。
      </small>
      <div className="background-limit-row">
        <input
          aria-label="后台命令最长运行时间（分钟）"
          type="number"
          min={1}
          max={120}
          step={1}
          value={draft}
          disabled={loadFailed}
          onChange={(event) => {
            setDraft(event.target.value);
            setSaved(false);
            setSaveFailed(false);
          }}
        />
        <span>分钟</span>
        <button
          type="button"
          className="background-limit-save"
          disabled={!valid || saving || loadFailed}
          onClick={() => void save()}
        >
          保存
        </button>
      </div>
      {draft !== "" && !valid && (
        <p className="background-limit-error" role="alert">
          请输入 1 到 120 之间的整数
        </p>
      )}
      {loadFailed && (
        <p className="background-limit-error" role="alert">
          后台命令设置暂时无法读取，请稍后重试。
        </p>
      )}
      {saved && (
        <p className="background-limit-status" role="status">
          已保存，对新启动的后台命令生效。
        </p>
      )}
      {saveFailed && (
        <p className="background-limit-error" role="alert">
          保存失败，请稍后重试。
        </p>
      )}
      {typeof loaded === "number" && (
        <small>当前生效上限：{loaded} 分钟</small>
      )}
    </div>
  );
}

function updateStatus(phase: UpdatePhase, version: string | undefined): string {
  switch (phase) {
    case "checking":
      return "正在检查更新";
    case "up-to-date":
      return "当前已是最新版本";
    case "available":
      return `可更新至 ${version ?? "新版本"}`;
    case "downloading":
      return "正在下载更新";
    case "installing":
      return "正在安装更新";
    case "ready-to-relaunch":
      return "更新已安装，正在重新启动";
    case "failed":
      return "检查更新失败";
    case "idle":
      return "尚未检查更新";
  }
}

function SoftwareUpdateSettings({ updates }: { updates: UpdateController }) {
  const { currentVersion, error, phase, supported, update } = updates.state;
  const busy = phase === "checking" || phase === "downloading" || phase === "installing";
  const action = updateAction(updates.state);
  const status = !supported
    ? "开发模式不检查更新"
    : error || updateProgressText(updates.state) || updateStatus(phase, update?.version);
  const displayedVersion = !supported
    ? "开发模式"
    : currentVersion || (phase === "checking" ? "正在读取" : "未知");

  return (
    <SettingsGroup title="软件更新">
      <div className="software-update-row">
        <div className="software-update-copy">
          <strong>当前版本 {displayedVersion}</strong>
          <div aria-live="polite">
            <p className={error ? "software-update-error" : undefined}>{status}</p>
            {phase === "available" ? <small>更新完成后将自动重启</small> : null}
            {update?.body ? <details><summary>查看更新说明</summary><p className="update-notes">{update.body}</p></details> : null}
          </div>
        </div>
        <button
          className="software-update-check"
          type="button"
          disabled={action.disabled}
          data-busy={busy}
          onClick={() => void (action.installs ? updates.installUpdate() : updates.checkForUpdates())}
        >
          <RefreshCw aria-hidden="true" size={14} />
          {action.label}
        </button>
      </div>
    </SettingsGroup>
  );
}

export function SettingsView({
  reduceMotion, onReduceMotionChange, providerApi = runtimeProviderApi,
  runtimeStatusError = "", onProvidersChanged = async () => undefined,
  onModelMetadataChanged = () => undefined, providerAutoDiscoverDelayMs,
  runtimeVersion = "未知", updates, embeddedNavigation = false,
  selectedCategory, onSelectCategory, query, onQueryChange,
  onOpenCapabilities, onWorkspaceChanged, conversationSettings,
}: SettingsViewProps) {
  const [internalCategory,setInternalCategory] = useState<SettingsCategoryId>("general");
  const [internalQuery,setInternalQuery] = useState("");
  const [preferenceError,setPreferenceError] = useState("");
  const prefs=useUiPreferences();
  const activeCategory = embeddedNavigation ? internalCategory : selectedCategory ?? "general";
  const activeQuery = embeddedNavigation ? internalQuery : query ?? "";
  const selectCategory = embeddedNavigation ? setInternalCategory : onSelectCategory ?? setInternalCategory;
  const changeQuery = embeddedNavigation ? setInternalQuery : onQueryChange ?? setInternalQuery;
  const save = (patch:Partial<UiPreferences>) => {
    try {saveUiPreferences(patch);if(patch.reduceMotion!==undefined)onReduceMotionChange(patch.reduceMotion);setPreferenceError("");}
    catch {setPreferenceError("未能保存界面偏好，请重试。原设置仍然有效。");}
  };
  useEffect(()=>{
    if(!activeQuery)document.querySelector(".workspace")?.scrollTo?.({top:0});
    const target=searchSettings(activeQuery).find(e=>e.category===activeCategory);
    const element=target ? document.getElementById(`setting-${target.id}`) : null;
    if(!element)return;
    if(element instanceof HTMLDetailsElement)element.open=true;
    element.dataset.searchMatch="true";
    element.scrollIntoView?.({block:"nearest",behavior:"instant"});
    return ()=>{delete element.dataset.searchMatch;};
  },[activeCategory,activeQuery]);
  const choice = <K extends keyof UiPreferences>(key:K,label:string,options:readonly [string,string][]) =>
    <select aria-label={label} value={String(prefs[key])} onChange={e=>save({[key]:e.target.value})}>{options.map(([value,text])=><option key={value} value={value}>{text}</option>)}</select>;
  let content;
  switch(activeCategory) {
    case "general": content=<>
      <SettingsGroup title="输入">
        <SettingsRow id="send-mode" title="发送消息" description="Shift＋Enter 始终换行。中文输入法选字时不会发送。">{choice("sendMode","发送消息",[["enter","Enter 发送"],["modifier","⌘ / Ctrl＋Enter 发送"]])}</SettingsRow>
        <SettingsRow id="follow-up" title="运行中追加消息" description="任务执行期间发送新消息时，默认如何处理。">{choice("followUp","运行中追加消息",[["ask","每次选择"],["queue","排队，完成后继续"],["interrupt","打断后发送"]])}</SettingsRow>
      </SettingsGroup><ShortcutSettings/><NativePreferences />
    </>; break;
    case "conversations": content=conversationSettings ? <ConversationSettings {...conversationSettings}/> : null; break;
    case "appearance": content=<>
      <SettingsGroup title="界面">
        <SettingsRow id="theme" title="界面主题">{choice("theme","界面主题",[["light","浅色"],["dark","深色"],["system","跟随系统"]])}</SettingsRow>
        <SettingsRow id="font-size" title="文字大小" description="调整应用文字，不改变文书原有字号。">{choice("fontSize","文字大小",[["small","较小"],["default","标准"],["large","较大"]])}</SettingsRow>
        <SettingsRow id="reduce-motion" title="减少动态效果" description="关闭非必要的过渡与动画。"><Toggle label="减少动态效果" checked={reduceMotion} onChange={v=>save({reduceMotion:v})}/></SettingsRow>
      </SettingsGroup>
      <SettingsGroup title="对话">
        <SettingsRow id="execution-detail" title="执行过程" description="简洁模式收起已完成的操作；待确认和失败事项始终可见。">{choice("executionDetail","执行过程",[["summary","简洁"],["detailed","详细"]])}</SettingsRow>
      </SettingsGroup>
    </>; break;
    case "models": content=<div id="setting-models" className="settings-models"><ProviderConnections api={providerApi} runtimeStatusError={runtimeStatusError} onProvidersChanged={onProvidersChanged} onModelMetadataChanged={onModelMetadataChanged} autoDiscoverDelayMs={providerAutoDiscoverDelayMs}/></div>;break;
    case "tools": case "agent": content=<>
      <PersonalInstructions/>
      <WorkspacePermissions onChanged={onWorkspaceChanged}/>
      <SettingsGroup title="运行限制" id="background-limit"><BackgroundLimitSettings/></SettingsGroup>
      <SettingsGroup title="能力" id="capabilities"><SettingsRow title="技能与外部工具" description="在能力中心统一管理已安装的技能和连接。"><button disabled={!onOpenCapabilities} onClick={onOpenCapabilities}>打开能力中心</button></SettingsRow></SettingsGroup>
    </>;break;
    case "skills": case "mcp": content=<SettingsGroup title="能力中心"><SettingsRow title="技能与外部工具" description="管理入口已统一到能力中心。"><button onClick={onOpenCapabilities} disabled={!onOpenCapabilities}>打开能力中心</button></SettingsRow></SettingsGroup>;break;
    case "workspaces":content=<>
      <StoragePreferences/>
      <SettingsGroup title="缓存" id="cache"><LegalCacheSettings/></SettingsGroup>
      <p className="settings-data-note">对话和配置保存在本机。使用外部模型或工具时，本轮必要内容会发送到你选择的服务。</p>
    </>;break;
    case "help":content=<HelpCenter appVersion={updates?.state.currentVersion} runtimeVersion={runtimeVersion} runtimeConnected={!runtimeStatusError && runtimeVersion !== "未知"}/>;break;
    case "diagnostics":content=<>
      <div id="setting-updates">{updates&&<SoftwareUpdateSettings updates={updates}/>}</div>
      <SettingsGroup title="关于 VeriLex"><SettingsRow title="使用许可"><span>PolyForm Noncommercial 1.0.0（仅限非商业用途）</span></SettingsRow></SettingsGroup>
      <details id="setting-diagnostics" className="settings-diagnostics settings-disclosure"><summary>运行状态</summary><p>{runtimeStatusError||"本地后台已连接"}</p><p>后台版本 {runtimeVersion}</p></details>
    </>;break;
  }
  const category=SETTINGS_CATEGORIES.find(c=>c.id===activeCategory);
  return <section className="settings-view page-view settings-redesigned">
    {embeddedNavigation&&<SettingsNavigation selected={activeCategory} query={activeQuery} onQueryChange={changeQuery} onSelect={selectCategory}/>}
    <div className={`settings-content${category?.id === "models" ? " settings-model-page" : ""}`}><header className="settings-page-heading"><h1>{category?.id === "models" ? "模型与密钥" : category?.label??"设置"}</h1><p>{category?.description}</p></header>{preferenceError&&<p className="preference-error" role="alert">{preferenceError}</p>}{content}</div>
  </section>;
}
