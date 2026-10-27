export type SettingsCategoryId = "general" | "appearance" | "models" | "agent" | "tools" | "workspaces" | "skills" | "mcp" | "diagnostics" | "help";
export interface SettingsCategory { id: SettingsCategoryId; label: string; description: string; keywords: readonly string[]; }
export const SETTINGS_CATEGORIES: readonly SettingsCategory[] = [
  {id:"general",label:"通用",description:"输入、通知与任务运行",keywords:["general"]},
  {id:"appearance",label:"外观",description:"主题、字号与动态效果",keywords:["appearance"]},
  {id:"models",label:"模型",description:"连接与模型管理",keywords:["模型","API","提供商","密钥"]},
  {id:"tools",label:"助手与权限",description:"使用偏好与授权文件夹",keywords:["智能体","agent","工作区"]},
  {id:"workspaces",label:"数据",description:"存储位置与缓存",keywords:["隐私","数据","storage"]},
  {id:"help",label:"帮助与反馈",description:"使用说明与问题反馈",keywords:["帮助","反馈","教程","help"]},
  {id:"diagnostics",label:"关于",description:"版本、更新与诊断",keywords:["关于","版本","诊断"]},
];
export interface SettingsEntry { id: string; category: SettingsCategoryId; label: string; keywords: string; }
export const SETTINGS_ENTRIES: readonly SettingsEntry[] = [
  {id:"shortcuts",category:"general",label:"快捷键",keywords:"命令面板 键位 系统 唤起 command shortcut"},
  {id:"send-mode",category:"general",label:"发送消息",keywords:"发送方式 换行 Enter 快捷键"},
  {id:"follow-up",category:"general",label:"运行中追加消息",keywords:"队列 排队 引导 交互"},
  {id:"notifications",category:"general",label:"任务通知",keywords:"完成 失败 授权 确认 提醒 桌面 声音 日程"},
  {id:"keep-awake",category:"general",label:"任务运行时保持唤醒",keywords:"防休眠 锁屏 后台运行"},
  {id:"theme",category:"appearance",label:"界面主题",keywords:"深色 浅色 跟随系统 dark light"},
  {id:"font-size",category:"appearance",label:"文字大小",keywords:"字号 字体 font"},
  {id:"reduce-motion",category:"appearance",label:"减少动态效果",keywords:"动画 motion"},
  {id:"execution-detail",category:"appearance",label:"执行过程",keywords:"工具 详情 摘要 思考"},
  {id:"models",category:"models",label:"模型连接",keywords:"模型 提供商 provider API 密钥 测试 上下文 上限"},
  {id:"personal-instructions",category:"tools",label:"全局指令",keywords:"AGENTS.md agents 全局 个人长期偏好 指令 回答 语言 格式 风格"},
  {id:"workspace-permissions",category:"tools",label:"已授权文件夹",keywords:"工作区 权限 读取 修改 收回 撤销 目录"},
  {id:"background-limit",category:"tools",label:"后台命令最长运行时间",keywords:"后台 终端 超时 时间 分钟"},
  {id:"capabilities",category:"tools",label:"技能与外部工具",keywords:"能力中心 插件 skill mcp 连接器"},
  {id:"storage",category:"workspaces",label:"存储位置",keywords:"数据目录 默认工作目录 文件夹"},
  {id:"cache",category:"workspaces",label:"法律检索缓存",keywords:"缓存 清除 法条"},
  {id:"help",category:"help",label:"使用说明",keywords:"帮助 教程 入门 操作 指南 help"},
  {id:"feedback",category:"help",label:"问题反馈",keywords:"反馈 报错 故障 issue"},
  {id:"updates",category:"diagnostics",label:"软件更新",keywords:"更新 检查 下载 安装 重启 版本"},
  {id:"diagnostics",category:"diagnostics",label:"运行状态",keywords:"后台 连接 诊断"},
];
export function searchSettings(query: string): readonly SettingsEntry[] {
  const q = query.trim().toLocaleLowerCase("zh-CN");
  if (!q) return [];
  return SETTINGS_ENTRIES.filter(e => `${e.label} ${e.keywords}`.toLocaleLowerCase("zh-CN").includes(q));
}
export function filterSettingsCategories(query: string): readonly SettingsCategory[] {
  const q = query.trim().toLocaleLowerCase("zh-CN");
  if (!q) return SETTINGS_CATEGORIES;
  const matches = searchSettings(q);
  return SETTINGS_CATEGORIES.filter(c => matches.some(e=>e.category===c.id) || `${c.label} ${c.description} ${c.keywords.join(" ")}`.toLocaleLowerCase("zh-CN").includes(q));
}
