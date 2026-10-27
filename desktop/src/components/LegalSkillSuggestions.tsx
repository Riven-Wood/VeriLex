import { useEffect, useState } from "react";
import { SkillMenu } from "./SkillMenu";
import { listSkills } from "../runtime/api";
import type { SkillSummary } from "../runtime/contracts";

export function LegalSkillSuggestions({ value = [], onChange = () => undefined }: {
  value?: string[];
  onChange?: (names: string[]) => void;
}) {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    listSkills(controller.signal).then((records) => {
      if (!controller.signal.aborted) setSkills(records.filter(
        (skill) => skill.valid,
      ));
    }).catch(() => { if (!controller.signal.aborted) setSkills([]); });
    return () => controller.abort();
  }, []);
  return <SkillMenu label="检索技能" entries={skills} value={value} multiple onChange={onChange} />;
}
