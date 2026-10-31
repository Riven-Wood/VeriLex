export interface HelpArticle { id: string; title: string; category: string; keywords: string; steps: readonly string[]; note?: string; }
/** Local, versioned help: describe only functionality already implemented. */
export const HELP_ARTICLES: readonly HelpArticle[] = [
  { id: 'models', title: '连接模型，开始第一次对话', category: '开始使用', keywords: 'API 密钥 deepseek 百炼 智谱 Kimi 豆包 MiniMax Ollama 连接失败', steps: [
    '点左下角「设置」→「模型」→「添加模型连接」，选择你使用的服务商。',
    '按页面指引前往官网获取 API 密钥，回到 VeriLex 粘贴并验证；选择默认模型后保存。普通 API 与会员或编程套餐可能不通用。',
    '点「返回应用」→「新建会话」，检查输入框下方的模型，输入“请帮我列出合同审查的工作步骤”并发送。',
    '如果连接失败，回到该连接检查密钥、余额和服务地址后重新测试。本地 Ollama 需要先在电脑上启动服务并下载模型。',
  ], note: '使用外部模型时，本轮必要内容会发送到你选择的服务；帮助页面本身不调用模型。' },
  { id: 'chats', title: '继续、查找与整理会话', category: '开始使用', keywords: '聊天 历史 搜索 重命名 归档 回收站 编辑 停止', steps: [
    '点左侧会话名称继续工作；用会话列表的搜索入口输入关键词查找历史。',
    '点会话旁的三个点，可重命名、归档或移入回收站；在设置中恢复或彻底删除回收站中的对话。',
    '助手工作时可点停止。运行中追加消息时，按页面选择排队或打断后发送。',
    '普通会话中，将鼠标移到自己发出的消息，点铅笔修改并重新发送，会从这条指令重新开始。已经写入的文件不会因此撤销。',
  ] },
  { id: 'files', title: '添加文件、授权文件夹与查看成果', category: '文件与工具', keywords: '附件 工作区 文件夹 文件预览 Word PDF 下载 保存', steps: [
    '在普通会话输入框点「＋」→「添加附件」，选择要交给助手的文件；也可拖入文件。等待附件就绪再发送。',
    '需要操作整个文件夹时，点「选择工作区」并选择本机目录，按页面选择允许的权限。',
    '点对话右上角的右侧栏按钮，在「文件」中查看当前会话文件；点文件可预览，不能预览的类型按提示用系统应用打开。',
    '需要收回权限时，点「设置」→「助手与权限」→「已授权文件夹」。有任务使用该目录时，先等任务结束。',
  ], note: '文件读取与修改以当前授权范围为准；看到待确认操作时，先核对目标文件和操作内容。' },
  { id: 'terminal', title: '使用终端运行本机命令', category: '文件与工具', keywords: '命令行 shell 终端 关闭 中断', steps: [
    '先在普通会话选择一个允许读写的工作区。',
    '打开对话右侧栏，切到「终端」，选择文件夹后点「新建终端」。',
    '输入你了解用途的命令并回车；需要中断当前命令时，点终端下方的「中断当前命令」按钮。',
    '收起右侧栏会保留终端；结束后用关闭按钮关闭该终端。终端输出不会自动加入助手对话。',
  ], note: '终端适合熟悉命令的用户；文件处理也可以直接在对话里描述需求。目前终端不允许联网。' },
  { id: 'plugins', title: '连接插件与选择本次使用的工具', category: '文件与工具', keywords: '能力中心 MCP Skill 技能 外部工具 北大法宝 威科 企查查 天眼查 Excel PPT PDF', steps: [
    '点左侧「能力中心」，进入「外部工具」，选择服务或导入已有配置。',
    '需要密钥的服务，先按官网指引取得密钥，回到应用测试，核对允许使用的工具并保存。',
    '回到当前对话，在输入区选择本次需要的外部工具或技能。只完成安装或连接，不等于本次对话已经启用。',
    '连接失败时，在该服务卡片中修改配置并重新测试。本机插件还需要对应程序能在这台电脑上运行。',
  ], note: 'Excel、PPT 和一般 PDF 扩展以插件为主，具体能否使用以能力中心的安装与测试结果为准。合同、诉讼所需的 Word 能力由应用内置。' },
  { id: 'legal', title: '法律检索、案例检索与核对来源', category: '法律工作', keywords: '法条 案例 来源 引用 检索', steps: [
    '在左侧「法律工作」中选择「法律检索」或「案例检索」。',
    '输入要研究的问题，按当前页面选择检索渠道及可用工具，再开始检索。',
    '收到结果后，打开来源核对正文、发布日期与适用范围，再将资料用于工作。',
    '如果外部服务未连接，先到「能力中心」连接对应服务；连接成功只代表服务可用，不代表每条结论已经核实。',
  ] },
  { id: 'contract', title: '审查 Word 合同并交付意见', category: '法律工作', keywords: '合同审查 doc docx 批注 编辑 导出', steps: [
    '点「合同审查」，选择合同或把 Word 文件拖到页面；按页面填写本次审查条件。',
    '审查开始后，在意见列表逐条查看；需要讨论具体文字时，选中文字后追问。',
    '在合同编辑区域修改可编辑的段落，保存后按页面提供的导出入口交付文件。',
    '新合同使用「新建审查」。导出后用 Word 或 WPS 再核对格式与内容。',
  ], note: '图片、域、修订等复杂对象所在段落可能只读。旧 .doc 在 macOS 需要已安装 LibreOffice；.docx 无需该转换组件。' },
  { id: 'litigation', title: '编辑文书并与 AI 协作', category: '法律工作', keywords: '诉讼 案件 证据 文书 起诉状', steps: [
    '点「文书起草」→「选择案件文件夹」，打开存放资料的本机目录；也可在左侧选择已有工作区。',
    '在文件列表选择资料或文书，在中央区域查看；查看识别不足的提示并补充缺失信息。',
    '需要助手修改时，选中文字，在右侧说明要求；核对建议后接受。',
    '保存并导出需要交付的文书，核对当事人、金额、日期及证据来源。',
  ] },
  { id: 'knowledge', title: '关联 Markdown 知识库', category: '法律工作', keywords: '知识库 Obsidian md markdown 同步', steps: [
    '点「知识库」，关联保存 Markdown 笔记的本机文件夹。',
    '同步后输入关键词，查看命中的笔记和原文。',
    '回到普通会话，在输入区选择本次使用的知识库，再提出问题。原文卡可以用于核对引用。',
  ], note: '本地知识库仅收录 .md 和 .markdown；Word、PDF、图片不入库。' },
  { id: 'redaction', title: '对文件脱敏', category: '法律工作', keywords: '脱敏 隐私 替换 Word Markdown md', steps: [
    '点「文件脱敏」，选择 Word 或 UTF-8 Markdown 文件，勾选识别类型；也可逐项填写要脱敏的文字和替代文字。',
    '输入导出文件名并导出新文件，打开结果检查是否遗漏。',
  ], note: 'Word 修订版仍包含被删除的原文，分享前需接受修订并检查。Markdown 内的图片及链接文件不会被处理；不提供 PDF 或扫描件脱敏。' },
  { id: 'calendar', title: '记录案件日程与安排定时任务', category: '法律工作', keywords: '提醒 日历 北京时间 日期 时间 自动 计划', steps: [
    '点「案件日程」新建事项，填写名称、类型、日期和时间；不指定时刻可选全天。时间按北京时间显示。',
    '在月、周或列表中查看日程，打开事项可修改或确认提醒。',
    '需要助手按计划执行工作时，使用独立的「定时任务」入口，按页面填写任务和运行计划。',
  ], note: '应用退出或电脑休眠时无法即时执行或提醒，重新打开后补查；系统通知也受电脑的通知设置影响。' },
  { id: 'recovery', title: '连接失败、卡住或离线时怎么办', category: '排查问题', keywords: '报错 故障 失败 网络 离线 重试 后台 更新', steps: [
    '先看出错位置的一行提示，优先使用旁边的「重试」或「重新测试」。',
    '模型连接失败：到「设置」→「模型」检查服务；插件失败：到「能力中心」检查该连接。',
    '本地后台未连接时，先保存可保存的工作，正常退出并重新打开 VeriLex；仍有问题时使用下方问题反馈。',
    '没有网络时，外部模型、联网检索和在线插件不可用；已经保存在本机的资料可按各页面当前状态查看。',
    '版本和更新状态在「设置」→「关于」。无法检查更新不等于已是最新版本。',
  ] },
];
export function searchHelp(query: string): readonly HelpArticle[] {
  const terms = query.trim().toLocaleLowerCase('zh-CN').split(/\s+/).filter(Boolean);
  return HELP_ARTICLES.filter(article => terms.every(term => `${article.title} ${article.category} ${article.keywords} ${article.steps.join(' ')} ${article.note ?? ''}`.toLocaleLowerCase('zh-CN').includes(term))).sort((a, b) => {
    const rank = (article: HelpArticle) => terms.filter(term => article.title.toLocaleLowerCase('zh-CN').includes(term)).length;
    return rank(b) - rank(a);
  });
}
