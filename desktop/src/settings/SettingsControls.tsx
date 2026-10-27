import type { ReactNode } from "react";
export function SettingsGroup({title, children, id}: {title:string; children:ReactNode; id?:string}) {
  return <section className="preference-group" id={id ? `setting-${id}` : undefined}><h2>{title}</h2><div className="preference-group-body">{children}</div></section>;
}
export function SettingsRow({title,description,children,id}: {title:string;description?:string;children:ReactNode;id?:string}) {
  return <div className="preference-row" id={id ? `setting-${id}` : undefined}><div className="preference-copy"><strong>{title}</strong>{description && <p>{description}</p>}</div><div className="preference-control">{children}</div></div>;
}
export function Toggle({label,checked,disabled,onChange}: {label:string;checked:boolean;disabled?:boolean;onChange:(value:boolean)=>void}) {
  return <input className="preference-toggle" type="checkbox" role="switch" aria-label={label} checked={checked} disabled={disabled} onChange={e=>onChange(e.target.checked)}/>;
}
