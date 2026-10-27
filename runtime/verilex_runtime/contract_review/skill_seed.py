"""内置「通用合同审查」Skill 种子：兜底方法论，独立撰写（来源见 provenance）。

种子机制与 legal-research 种子同构：每个数据目录只种一次；用户删除后
不复活；目录已被占用（同名技能已存在）时不覆盖。
"""
from __future__ import annotations

from pathlib import Path

SEED_MARKER = ".seeded-contract-review"
SKILL_NAME = "通用合同审查"

CONTRACT_REVIEW_SKILL_MD = """---
name: 通用合同审查
description: |
  通用合同审查兜底技能：四层审核（主体与形式、基础要件、商务条款、法律条款），
  违约责任八项专项，必须修改/建议协商二分与高/中/低三档分级；逐条意见按
  五要素输出，锚定合同原文。
verilex-category: contract-review
---
# 通用合同审查

按以下方法论审查合同；每条意见通过 contract_opinion 工具提交，锚定文本逐字摘自合同原文。

## 分级与分类

- 等级三档：高（可能导致重大损失或效力风险，必须处理）；中（有明显谈判空间）；低（提示性风险）。
- 分类二分：必须修改（不改则本方承担实质风险）；建议协商（可谈可不谈，谈下更稳）。

## 四层审核（按序推进，不跳层）

1. 主体与形式：当事人名称与签章、统一社会信息核对入口、签署日期与编号、份数与效力声明。
2. 基础要件：标的、数量与质量、价款或报酬、履行期限与地点与方式、争议解决、违约责任是否齐备且明确。
3. 商务条款：付款节点与发票、交付与验收标准及期限、知识产权归属与许可、保密范围与期限、排他与竞业、变更与解除条件。
4. 法律条款：无效与可撤销情形、免责条款边界、管辖或仲裁、通知与送达、不可抗力、法律适用。

## 违约责任八项专项（每份必查）

1. 违约情形是否明确到可直接执行；
2. 违约金计算方式与上限（无上限要点出）；
3. 损害赔偿范围与举证负担；
4. 定金、保证金及其返还或抵扣处理；
5. 解除权触发条件与补救程序（催告、宽限期）；
6. 免责事由是否被滥用为逃责通道；
7. 多种责任并存时的并用与折抵顺序；
8. 违约责任与合同终止、争议解决条款的衔接。

## 逐条风险点五要素

每条意见必须完整包含：条款定位；原文摘录（逐字、可跨句但不改写）；风险说明（对本方的具体后果）；
修改建议（可确定的替换文字通过 replacement_text 提交为待接受的 Word 修订，理由写入批注；
仅提示风险或缺少事实时只批注）；依据（仅在本轮真实检索成功时附规范性文件援引，
未开启检索时只提法律概念、不点名具体文件）。

## 效力核验三维度（附规范性文件援引前）

时间效力（是否已施行、是否废止或修正）→ 层级效力（法律、行政法规、部门规章、地方性法规的适用顺序）
→ 冲突检查（特别规定与一般规定、新规定与旧规定）。核验以检索返回为准，不以记忆为准。

## 立场与口径

按所选代表方（甲方/乙方/中立）评估风险归属；宽松口径只报高等级风险，常规口径报高与中，
严格口径逐条过并点出低等级风险。「未提出意见」不代表合同没有问题；
审查结果是辅助草稿，最终判断与责任由执业人员承担。
"""


def seed_contract_review_skill(skills_root: Path) -> bool:
    """Seed the built-in skill once per data directory. True if written now."""
    try:
        if not skills_root.is_dir():
            return False
        marker = skills_root / SEED_MARKER
        if marker.exists():
            return False
        target = skills_root / SKILL_NAME
        if target.exists():
            # The name is taken (e.g. user's own skill): never overwrite, but
            # record that seeding ran so a later deletion stays deleted.
            marker.write_text("", encoding="utf-8")
            return False
        target.mkdir(mode=0o700, exist_ok=True)
        (target / "SKILL.md").write_text(
            CONTRACT_REVIEW_SKILL_MD, encoding="utf-8"
        )
        marker.write_text("", encoding="utf-8")
        return True
    except OSError:
        return False
