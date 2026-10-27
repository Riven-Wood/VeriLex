import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";
import "./ContractSkillMenu.css";

export function SkillMenu({ value, entries, disabled = false, onChange, label, multiple = false, maxSelected = 10 }: {
  value: string[];
  entries: { name: string; description: string }[];
  disabled?: boolean;
  onChange: (value: string[]) => void;
  label: string;
  multiple?: boolean;
  maxSelected?: number;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(value[0] ?? "");
  const [position, setPosition] = useState({ left: 16, top: 0, width: 640, maxHeight: 400 });
  const expanded = open && !disabled;
  const current = entries.find(entry => entry.name === active) ?? entries.find(entry => entry.name === value[0]) ?? entries[0];
  const activeIndex = entries.findIndex(entry => entry.name === current?.name);
  const selectedDescription = entries.find(entry => entry.name === value[0])?.description;

  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  useLayoutEffect(() => {
    if (!expanded) return;
    const place = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(640, window.innerWidth - 32);
      const top = rect.bottom + 6;
      setPosition({ left: Math.max(16, Math.min(rect.left, window.innerWidth - width - 16)), top, width,
        maxHeight: Math.max(120, Math.min(440, window.innerHeight - top - 16)) });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [expanded]);
  useEffect(() => {
    if (!expanded) return;
    const outside = (event: PointerEvent) => {
      if (!trigger.current?.contains(event.target as Node) && !panel.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [expanded]);
  useEffect(() => {
    if (expanded) document.getElementById(`${id}-option-${activeIndex}`)?.scrollIntoView?.({ block: "nearest" });
  }, [expanded, activeIndex, id]);

  function choose(name: string) {
    if (multiple) {
      if (!value.includes(name) && value.length >= maxSelected) return;
      onChange(value.includes(name) ? value.filter(item => item !== name) : [...value, name]);
    } else {
      onChange([name]);
      setOpen(false);
    }
    trigger.current?.focus();
  }
  return <div className={`contract-skill-menu${multiple ? " search-skill-picker" : ""}`} onBlur={event => {
    const next = event.relatedTarget as Node | null;
    if (!next || (!event.currentTarget.contains(next) && !panel.current?.contains(next))) setOpen(false);
  }} onKeyDown={event => {
    if (disabled) return;
    if (event.key === "Escape" || event.key === "Tab") {
      setOpen(false);
      if (event.key === "Escape" && expanded) { event.preventDefault(); event.stopPropagation(); trigger.current?.focus(); }
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const selected = Math.max(0, entries.findIndex(entry => entry.name === value[0]));
      const index = event.key === "Home" ? 0 : event.key === "End" ? entries.length - 1 :
        !expanded ? selected : Math.max(0, Math.min(entries.length - 1, activeIndex + (event.key === "ArrowDown" ? 1 : -1)));
      if (entries[index]) setActive(entries[index].name);
      setOpen(true);
    } else if (expanded && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      if (current) choose(current.name);
    }
  }}>
    <span id={`${id}-label`} className={multiple ? "skill-menu-label-hidden" : "contract-skill-label"}>{label}</span>
    <button ref={trigger} type="button" role="combobox" aria-labelledby={`${id}-label`}
      aria-expanded={expanded} aria-haspopup="listbox" aria-controls={expanded ? `${id}-list` : undefined}
      aria-activedescendant={expanded && current ? `${id}-option-${activeIndex}` : undefined}
      className="contract-skill-trigger" disabled={disabled} title={multiple ? undefined : selectedDescription}
      onClick={() => { trigger.current?.focus(); setActive(value[0] ?? entries[0]?.name ?? ""); setOpen(!expanded); }}>
      <span>{multiple ? `${label}${value.length ? `（${value.length}）` : ""}` : value[0]}</span><ChevronDown size={14} aria-hidden="true" />
    </button>
    {expanded && createPortal(<div ref={panel} className={`contract-skill-panel${multiple ? " search-skill-panel" : ""}`} style={position}>
      <div id={`${id}-list`} role="listbox" aria-label={label} aria-multiselectable={multiple || undefined} className="contract-skill-options">
        {!entries.length && <p className="contract-skill-empty">暂无可用的检索技能</p>}
        {entries.map((entry, index) => <button key={entry.name} id={`${id}-option-${index}`} type="button"
          role="option" aria-selected={value.includes(entry.name)}
          aria-disabled={multiple && !value.includes(entry.name) && value.length >= maxSelected || undefined} tabIndex={-1}
          aria-describedby={entry.name === current?.name ? `${id}-description` : undefined}
          className={entry.name === current?.name ? "is-active" : ""}
          onMouseEnter={() => setActive(entry.name)} onFocus={() => setActive(entry.name)}
          onMouseDown={event => event.preventDefault()} onClick={() => choose(entry.name)}>
          <span>{entry.name}</span><Check size={15} aria-hidden="true" style={{ visibility: value.includes(entry.name) ? "visible" : "hidden" }} />
        </button>)}
      </div>
      {current && <div key={current.name} id={`${id}-description`} role="tooltip" className="contract-skill-description">
        <strong>{current.name}</strong><p>{current.description.trim() || "此技能暂无介绍。"}</p>
      </div>}
    </div>, document.body)}
  </div>;
}
