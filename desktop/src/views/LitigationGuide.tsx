import {useState} from 'react';
import {ArrowUpRight, ListTree, Files, Scale, Compass, FilePenLine, SlidersHorizontal} from 'lucide-react';

// Original VeriLex instructions. Third-party skill packages are not bundled.
export const LITIGATION_GUIDES = {
  facts: {name: '事实梳理', instruction: '根据本次材料整理案件经过。区分材料记载、各方陈述和你的推断；重要事件注明文件及可核对的位置。合并重复事件，列明矛盾、时间不明和材料缺口。按本次需要选择时间线或简要叙述，不必制作图表。'},
  evidence: {name: '证据整理', instruction: '围绕本次请求或抗辩，分析各份材料可以证明什么、不能证明什么，以及相互支持或冲突之处。按证明目的提出分组和补证建议。需要递交目录时，将目录与内部风险分析分开；不要声称已完成原件核验、编页或装册。'},
  issues: {name: '争议焦点', instruction: '从当事人的请求、抗辩和材料分歧中识别需要解决的问题，区分事实争议与法律争议。对关键问题分析请求权基础、要件、证据和缺口；说明不同解释及待研究事项。可以只分析用户指定的一个焦点，无需先生成完整要件表。'},
  strategy: {name: '策略备忘', instruction: '结合用户目标和已有材料比较可行的处理方案，说明依据、不利因素、对方可能的反驳和需要补充的证据。重点提出下一步可采取的行动及其条件，可按需加入对方律师或法官视角。不编造胜诉概率，不替用户决定和解、放弃权利等事项。'},
  documents: {name: '文书起草', instruction: '按用户指定的文种、用途和立场组织文书。利用相关材料及已有成果，区分已知信息和待补内容；对诉请、事实理由和引证检查一致性。格式按用户提供的样式或要求处理，不限制案由及当事人人数。形成律师可继续修改的草稿。'},
} as const;
export const ACTIVE_WORK_KINDS = ['facts','evidence','documents'] as const;
export type GuideKind = keyof typeof LITIGATION_GUIDES;
export const LITIGATION_WORK = {
  facts: {title:'梳理案件经过', description:'理清时间顺序，找出材料里的矛盾和缺口。', outcome:'案件时间线 · 事实出处 · 待核实事项', example:'例如：重点梳理签约、交付和付款的经过', icon:ListTree},
  evidence: {title:'整理证据', description:'看看现有材料能证明什么，还需要补什么。', outcome:'证明目的 · 证据分组 · 补证建议', example:'例如：围绕欠付货款整理证据，指出薄弱之处', icon:Files},
  issues: {title:'分析争议焦点', description:'围绕请求与抗辩，找出真正需要解决的问题。', outcome:'争议问题 · 双方理由 · 证据与法律依据', example:'例如：只分析是否达到解除合同的条件', icon:Scale},
  strategy: {title:'考虑诉讼策略', description:'比较可行方案，推演对方反驳，明确下一步。', outcome:'方案比较 · 风险分析 · 下一步行动', example:'例如：站在被告立场，分析主要抗辩与补证方向', icon:Compass},
  documents: {title:'起草诉讼文书', description:'说明文种和我方立场，先审阅正文，再按需生成 Word。', outcome:'文书草稿 · 待补内容 · 引用出处', example:'例如：根据现有材料，为被告起草一份答辩状', icon:FilePenLine},
} as const;
export function guidePrompt(kind: GuideKind, instruction: string, request: string, materials: string[]) {
  return `本次工作：${LITIGATION_GUIDES[kind].name}。使用开放工作方式，直接处理本次目标，不要求先完成其他环节或核对旧要件表。\n工作指引：\n${instruction.trim()}\n本次要求：\n${request.trim() || '按上述指引处理已有材料，缺少必要信息时说明缺口。'}\n${materials.length ? `本轮选中材料（JSON路径列表）：${JSON.stringify(materials)}。请围绕这些文件处理本轮请求。` : ''}\n已有案件记录可按需用litigation_read读取，空记录不阻塞本次工作。尊重当前讨论已选择的技能与本次要求，不强制执行无关步骤。不自动保存旧结构化记录或套用固定模板。\n${kind === 'documents' ? '请生成 Word 文件交付，可用document_create新增文件。' : '先在讨论中给出结果；只有我要求保存文件时才用document_create生成新Word。'}保留出处与待核实事项，不编造事实、法条、页码或执行结果；原件只读，不改动任何既有文件。`;
}
export function LitigationGuide({kind, materials, disabled, onStart}: {
  kind: GuideKind; materials: string[]; disabled: boolean; onStart: (message: string) => void;
}) {
  const guide = LITIGATION_GUIDES[kind];
  const [instruction, setInstruction] = useState<string>(guide.instruction);
  const [request, setRequest] = useState('');
  const work = LITIGATION_WORK[kind];
  const Icon = work.icon;
  return <section className="litigation-guide" aria-label={`${guide.name}工作指引`}>
    <header className="litigation-work-intro">
      <span className="litigation-work-icon"><Icon size={22} strokeWidth={1.6}/></span>
      <h2>{work.title}</h2>
      <p>{work.description}</p>
      <span className="litigation-outcome">{work.outcome}</span>
    </header>
    <div className="litigation-request-box">
      <label><span>{kind === 'documents' ? '起草要求' : '本次要求'}{kind !== 'documents' && <small>可不填</small>}</span><textarea aria-label={kind === 'documents' ? '起草要求' : '本次要求'} rows={3} maxLength={6000} value={request}
        placeholder={work.example} onChange={e=>setRequest(e.target.value)}/></label>
      <div className="litigation-request-footer"><span>{materials.length ? `使用上方所选 ${materials.length} 份材料` : '使用已有案件记录和你的说明'}</span>
        <button className="litigation-primary" type="button" disabled={disabled || !instruction.trim() || (kind === 'documents' && !request.trim())}
          onClick={()=>onStart(guidePrompt(kind,instruction,request,materials))}>{kind === 'documents' ? '开始起草' : `开始${guide.name}`}<ArrowUpRight size={16}/></button>
      </div>
    </div>
    <p className="litigation-delivery">{kind === 'documents' ? '草稿将在案件讨论中交付，生成后可打开预览。' : '结果会显示在案件讨论中，你可以继续追问、修改或要求保存为文件。'}</p>
    <details className="litigation-method"><summary><SlidersHorizontal size={14}/>查看或调整工作指引</summary>
      <label>工作指引<textarea rows={5} maxLength={12000} value={instruction} onChange={e=>setInstruction(e.target.value)}/></label>
      <button type="button" disabled={disabled} onClick={()=>setInstruction(guide.instruction)}>恢复默认指引</button>
    </details>
  </section>;
}
