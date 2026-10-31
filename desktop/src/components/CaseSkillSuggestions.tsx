import { skillCategory } from "./skillCategories";
import { useEffect, useState } from "react";
import { SkillMenu } from "./SkillMenu";
import { listSkills } from "../runtime/api";
import type { SkillSummary } from "../runtime/contracts";

export function CaseSkillSuggestions({ disabled = false, value = [], onChange = () => undefined }: {
  disabled?: boolean;
  value?: string[]; onChange?: (names: string[]) => void;
}) {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    listSkills(controller.signal).then((records) => {
      if (!controller.signal.aborted) setSkills(records.filter((skill) => skill.valid && skillCategory(skill) === "case-retrieval"));
    }).catch(() => { if (!controller.signal.aborted) setSkills([]); });
    return () => controller.abort();
  }, []);
  return <SkillMenu disabled={disabled} placement="above" label="检索技能" entries={skills} value={value} multiple onChange={onChange} />;
}
