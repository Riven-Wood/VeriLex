import { SkillMenu } from "./SkillMenu";
import type { ReviewSkillEntry } from "../runtime/contractReviewClient";

export function ContractSkillMenu({ value, entries, disabled, onChange }: {
  value: string;
  entries: ReviewSkillEntry[];
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return <SkillMenu label="审查技能" value={[value]} entries={entries} disabled={disabled}
    onChange={names => onChange(names[0])} />;
}
