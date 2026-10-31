import { useEffect, useRef, useState } from "react";
import { ChevronDown, Scale, Search } from "lucide-react";
import { listSkills } from "../runtime/api";
import type { SkillSummary } from "../runtime/contracts";
import { skillCategories, skillCategory } from "./skillCategories";
import { DetailDialog } from "./DetailDialog";
import "./LitigationSkillPicker.css";

export function LitigationSkillPicker({ onChoose, onOpen, disabled = false }: {
  onChoose: (name: string) => void;
  onOpen: () => void;
  disabled?: boolean;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  return <>
    <button ref={trigger} type="button" className="litigation-skill-trigger"
      aria-haspopup="dialog" aria-expanded={open} disabled={disabled}
      onClick={() => { onOpen(); setOpen(true); }}>
      <Scale size={16} aria-hidden="true" />文书起草技能
    </button>
    {open && <DetailDialog title="文书起草技能" className="litigation-skill-dialog"
      returnFocus={trigger.current} onClose={() => setOpen(false)}>
      <SkillList mode="drafting" onChoose={name => { onChoose(name); setOpen(false); }} />
    </DetailDialog>}
  </>;
}

export function SkillList({ onChoose, mode }: { onChoose: (name: string) => void; mode: "all" | "legal" | "drafting" }) {
  const label = mode === "all" ? "全部技能" : mode === "legal" ? "法律技能" : "文书起草技能";
  const searchInput = useRef<HTMLInputElement>(null);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [query, setQuery] = useState("");
  const [activeName, setActiveName] = useState("");
  const [expandedCategory, setExpandedCategory] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const frame = requestAnimationFrame(() => searchInput.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    listSkills(controller.signal).then(records => {
      if (controller.signal.aborted) return;
      setSkills(records.filter(skill => skill.valid && (mode === "all" || (mode === "drafting" ? skillCategory(skill) === "legal-documents" : !["uncategorized", "skill-management"].includes(skillCategory(skill)))))
        .sort((a, b) => a.name.localeCompare(b.name, "zh-CN")));
    }).catch(() => {
      if (!controller.signal.aborted) setError(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [revision, mode]);
  const search = query.trim().toLocaleLowerCase("zh-CN");
  const visible = skills.filter(skill => `${skill.name} ${skill.description} ${mode === "legal" ? skillCategories.find(([id]) => id === skillCategory(skill))?.[1] ?? "" : ""}`.toLocaleLowerCase("zh-CN").includes(search));
  const current = mode === "legal" ? visible.find(skill => skill.name === activeName && skillCategory(skill) === expandedCategory) : visible.find(skill => skill.name === activeName) ?? visible[0];
  return <>
    <label className="litigation-skill-search">
      <Search size={16} aria-hidden="true" />
      <input ref={searchInput} aria-label={`搜索${label}`} placeholder="搜索名称或用途" value={query}
        onChange={event => setQuery(event.target.value)} />
    </label>
    {loading ? <p className="litigation-skill-note" role="status">正在加载{label}…</p>
      : error ? <div className="litigation-skill-note" role="alert">
        <p>无法加载{label}，请重试。</p><button type="button" onClick={() => setRevision(value => value + 1)}>重试</button>
      </div>
      : !visible.length ? <p className="litigation-skill-note" role="status">{skills.length ? `没有找到匹配的${label}。` : "暂无有效技能。可在能力中心添加或检查技能。"}</p>
      : <div className="litigation-skill-content">
        <nav className="litigation-skill-list" aria-label={`可用${label}`}>
          {mode === "legal" ? skillCategories.filter(([id]) => visible.some(skill => skillCategory(skill) === id)).map(([id, heading]) => {
            const entries = visible.filter(skill => skillCategory(skill) === id);
            const expanded = expandedCategory === id;
            return <div key={id}>
              <button type="button" className="litigation-skill-category" aria-expanded={expanded}
                onClick={() => { setExpandedCategory(expanded ? "" : id); setActiveName(""); }}>
                <span>{heading}</span><span className="litigation-skill-category-meta">{entries.length}<ChevronDown size={15} aria-hidden="true" /></span>
              </button>
              {expanded && <div className="litigation-skill-category-items" aria-label={`${heading}技能`}>
                {entries.map(skill => <button type="button" key={skill.name}
                  aria-pressed={current?.name === skill.name} onClick={() => setActiveName(skill.name)}>{skill.name}</button>)}
              </div>}
            </div>;
          }) : visible.map(skill => <button type="button" key={skill.name}
            aria-pressed={current?.name === skill.name} onClick={() => setActiveName(skill.name)}>{skill.name}</button>)}
        </nav>
        <section className="litigation-skill-detail" aria-label="技能介绍">
          {current ? <>
            <div className="litigation-skill-description">
              <h3>{current.name}</h3>
              <p>{current.description.trim() || "此技能暂无介绍。"}</p>
            </div>
            <button type="button" className="litigation-skill-use" onClick={() => onChoose(current.name)}>加入输入框</button>
          </> : <p className="litigation-skill-placeholder">先选择分类，再选择技能查看介绍。</p>}
        </section>
      </div>}
  </>;
}
