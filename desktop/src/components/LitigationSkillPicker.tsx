import { useEffect, useRef, useState } from "react";
import { Scale, Search } from "lucide-react";
import { listSkills } from "../runtime/api";
import type { SkillSummary } from "../runtime/contracts";
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
      <Scale size={16} aria-hidden="true" />诉讼技能
    </button>
    {open && <DetailDialog title="诉讼技能" className="litigation-skill-dialog"
      returnFocus={trigger.current} onClose={() => setOpen(false)}>
      <LitigationSkillList onChoose={name => { onChoose(name); setOpen(false); }} />
    </DetailDialog>}
  </>;
}

function LitigationSkillList({ onChoose }: { onChoose: (name: string) => void }) {
  const searchInput = useRef<HTMLInputElement>(null);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [query, setQuery] = useState("");
  const [activeName, setActiveName] = useState("");
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
      setSkills(records.filter(skill => skill.valid)
        .sort((a, b) => a.name.localeCompare(b.name, "zh-CN")));
    }).catch(() => {
      if (!controller.signal.aborted) setError(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [revision]);
  const search = query.trim().toLocaleLowerCase("zh-CN");
  const visible = skills.filter(skill => `${skill.name} ${skill.description}`.toLocaleLowerCase("zh-CN").includes(search));
  const current = visible.find(skill => skill.name === activeName) ?? visible[0];
  return <>
    <label className="litigation-skill-search">
      <Search size={16} aria-hidden="true" />
      <input ref={searchInput} aria-label="搜索诉讼技能" placeholder="搜索名称或用途" value={query}
        onChange={event => setQuery(event.target.value)} />
    </label>
    {loading ? <p className="litigation-skill-note" role="status">正在加载诉讼技能…</p>
      : error ? <div className="litigation-skill-note" role="alert">
        <p>无法加载诉讼技能，请重试。</p><button type="button" onClick={() => setRevision(value => value + 1)}>重试</button>
      </div>
      : !current ? <p className="litigation-skill-note" role="status">{skills.length ? "没有找到匹配的诉讼技能。" : "暂无有效技能。可在能力中心添加或检查技能。"}</p>
      : <div className="litigation-skill-content">
        <nav className="litigation-skill-list" aria-label="可用诉讼技能">
          {visible.map(skill => <button type="button" key={skill.name}
            aria-pressed={current.name === skill.name} onClick={() => setActiveName(skill.name)}>
            {skill.name}
          </button>)}
        </nav>
        <section className="litigation-skill-detail" aria-label="技能介绍">
          <div className="litigation-skill-description">
            <h3>{current.name}</h3>
            <p>{current.description.trim() || "此技能暂无介绍。"}</p>
          </div>
          <button type="button" className="litigation-skill-use" onClick={() => onChoose(current.name)}>加入输入框</button>
        </section>
      </div>}
  </>;
}
