import type { SkillSummary } from "../runtime/contracts";

export const skillCategories = [
  ["legal-retrieval", "法律检索"],
  ["case-retrieval", "案例检索与分析"],
  ["contract-review", "合同审查与起草"],
  ["litigation-work", "诉讼工作"],
  ["case-management", "案件管理与期限"],
  ["corporate-diligence", "公司治理与尽调"],
  ["compliance-privacy", "合规监管与隐私"],
  ["employment", "劳动用工"],
  ["intellectual-property", "知识产权"],
  ["legal-documents", "法律文书与文档处理"],
  ["legal-reasoning", "法律推理与论证"],
  ["legal-education", "法学研究与教学"],
  ["skill-management", "技能与工具管理"],
  ["file-redaction", "文件脱敏"],
  ["uncategorized", "未分类"],
] as const;

export function skillCategory(skill: SkillSummary): string {
  return skillCategories.find(([id, label]) => skill.category === id || skill.category === label)?.[0] ?? "uncategorized";
}

export function skillCategoryLabel(skill: SkillSummary): string {
  return skillCategories.find(([id]) => id === skillCategory(skill))![1];
}
