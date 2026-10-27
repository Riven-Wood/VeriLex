import { useRef, useState } from "react";
import { McpConnections } from "../components/McpConnections";
import { SkillsManager } from "../components/SkillsManager";
import "./PluginCenterView.css";

const tabs = [{ id: "tools", label: "外部工具" }, { id: "skills", label: "技能" }] as const;
export function PluginCenterView() {
  const [active, setActive] = useState<"tools" | "skills">("tools");
  const [queries, setQueries] = useState({ tools: "", skills: "" });
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  return <section className="plugin-center" aria-label="能力中心">
    <header className="plugin-center-heading"><h1>能力中心</h1>
    <div className="plugin-center-toolbar">
      <div role="tablist" aria-label="能力分类">
        {tabs.map((tab, index) => <button key={tab.id} ref={el => { tabRefs.current[index] = el; }}
          id={`plugin-tab-${tab.id}`} type="button" role="tab" aria-selected={active === tab.id}
          aria-controls={`plugin-panel-${tab.id}`} tabIndex={active === tab.id ? 0 : -1}
          onClick={() => setActive(tab.id)} onKeyDown={event => {
            if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : 1 - index;
            setActive(tabs[next].id); tabRefs.current[next]?.focus();
          }}>{tab.label}</button>)}
      </div>
      <div className="plugin-search"><input type="search" aria-label="搜索名称" placeholder="搜索名称"
        value={queries[active]} onChange={e => setQueries(current => ({ ...current, [active]: e.target.value }))} />
        {queries[active] && <button type="button" onClick={() => setQueries(current => ({ ...current, [active]: "" }))}>清空搜索</button>}
      </div>
    </div>
    </header>
    <div role="tabpanel" id={`plugin-panel-${active}`} aria-labelledby={`plugin-tab-${active}`}>
      {active === "tools" ? <McpConnections variant="center" query={queries.tools} /> : <SkillsManager query={queries.skills} />}
    </div>
  </section>;
}
