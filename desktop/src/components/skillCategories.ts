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

const hints: [string, RegExp][] = [
  ["file-redaction", /脱敏|redact/iu],
  ["employment", /劳动|工资|用工|employment|wage/iu],
  ["intellectual-property", /知识产权|专利|商标|版权|intellectual.property|patent|trademark/iu],
  ["compliance-privacy", /合规|监管|隐私|个人信息|compliance|privacy/iu],
  ["corporate-diligence", /尽调|公司治理|企业|corporate|diligence/iu],
  ["contract-review", /合同|协议|contract/iu],
  ["case-management", /日程|期限|案件管理|排期|calendar|deadline|scheduling/iu],
  ["case-retrieval", /类案|案例|判例|裁判文书|case.retrieval|case.research/iu],
  ["legal-retrieval", /法规检索|法律检索|法条|法律法规|legal.retrieval|law.search/iu],
  ["litigation-work", /诉讼|证据|庭审|审判|争议|litigation|evidence|trial/iu],
  ["legal-documents", /文书|文档|word|docx|pdf|document/iu],
  ["legal-reasoning", /推理|论证|法律解释|reasoning|argument/iu],
  ["legal-education", /法学|教学|论文|学术|education|thesis/iu],
  ["skill-management", /技能管理|插件|skill.management|plugin|工具管理/iu],
];

export function automaticSkillCategory(skill: SkillSummary): string {
  const declared = skillCategories.find(([id, label]) => skill.category === id || skill.category === label)?.[0];
  if (declared) return declared;
  let best = "uncategorized";
  let score = 0;
  for (const [category, pattern] of hints) {
    const candidate = (pattern.test(skill.name) ? 3 : 0) + (pattern.test(skill.description) ? 1 : 0);
    if (candidate > score) { best = category; score = candidate; }
  }
  return best;
}

export function skillCategory(skill: SkillSummary): string {
  return skillCategories.find(([id, label]) => skill.category === id || skill.category === label)?.[0] ?? "uncategorized";
}

export function managedSkillCategory(skill: SkillSummary): string {
  return skill.categoryOverride ?? automaticSkillCategory(skill);
}

export function skillCategoryLabel(skill: SkillSummary, custom: readonly {id: string; name: string}[] = []): string {
  const category = managedSkillCategory(skill);
  return skillCategories.find(([id]) => id === category)?.[1] ?? custom.find(item => item.id === category)?.name ?? "未分类";
}
