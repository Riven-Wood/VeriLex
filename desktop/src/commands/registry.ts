/** The single public command/shortcut catalogue, also consumed by Help and Settings.
 * Only bind an action after its real implementation is available. */
export interface CommandDefinition {
  id: string;
  title: string;
  keywords: string;
  key?: string;
  shift?: boolean;
  scope?: string;
}
export const APP_COMMANDS: readonly CommandDefinition[] = [
  {id:'commands', title:'打开命令面板', keywords:'快捷键 command palette', key:'p', shift:true},
  {id:'new-thread', title:'新建会话', keywords:'新对话 新任务 new chat', key:'n'},
  {id:'search-threads', title:'搜索会话', keywords:'查找 历史 search find', key:'f', shift:true},
  {id:'settings', title:'打开设置', keywords:'偏好 settings preferences', key:','},
  {id:'files', title:'打开文件面板', keywords:'工作区 文件 预览 files', key:'e', scope:'普通会话'},
  {id:'terminal', title:'打开终端面板', keywords:'terminal shell 命令行', key:'j', shift:true, scope:'普通会话'},
  {id:'sidebar', title:'展开或收起侧栏', keywords:'sidebar 导航', key:'b', shift:true},
  {id:'plugins', title:'打开能力中心', keywords:'插件 技能 MCP plugins skills'},
  {id:'knowledge', title:'打开知识库', keywords:'Markdown knowledge'},
  {id:'calendar', title:'打开案件日程', keywords:'日历 calendar'},
  {id:'scheduled', title:'打开定时任务', keywords:'计划 scheduled'},
  {id:'help', title:'打开帮助与反馈', keywords:'使用说明 教程 help feedback'},
  {id:'shortcut-settings', title:'查看快捷键设置', keywords:'键位 shortcut 系统唤起'},
];
export type CommandHandlers = Partial<Record<string, () => void | Promise<void>>>;
export interface BoundCommand extends CommandDefinition { run: () => void | Promise<void> }
export function bindCommands(handlers: CommandHandlers): BoundCommand[] {
  return APP_COMMANDS.flatMap(command => handlers[command.id] ? [{...command,run:handlers[command.id]!}] : []);
}
export function filterCommands(commands: readonly BoundCommand[], query: string) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return commands.filter(c => words.every(word => `${c.title} ${c.keywords}`.toLocaleLowerCase().includes(word)));
}
export function isMacPlatform() { return /Mac|iPhone|iPad/.test(navigator.platform); }
export function shortcutLabel(command: CommandDefinition, mac = isMacPlatform()) {
  return command.key ? `${mac ? '⌘' : 'Ctrl+'}${command.shift ? (mac ? '⇧' : 'Shift+') : ''}${command.key.toUpperCase()}` : '';
}
export function matchCommand(event: KeyboardEvent, commands: readonly BoundCommand[], mac = isMacPlatform()) {
  if (event.defaultPrevented || event.repeat || event.isComposing || event.keyCode === 229 || event.altKey || event.getModifierState('AltGraph')) return;
  if (mac ? !event.metaKey || event.ctrlKey : !event.ctrlKey || event.metaKey) return;
  const target = event.target instanceof Element ? event.target : document.activeElement;
  // Let editors, IMEs, dialogs and terminal emulators own all of their keys.
  if (target?.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"],.xterm,dialog,[role="dialog"]')) return;
  if (document.querySelector('dialog[open],[role="dialog"][aria-modal="true"]')) return;
  return commands.find(c => c.key && c.key === event.key.toLowerCase() && Boolean(c.shift) === event.shiftKey);
}
export const GLOBAL_WAKE_SHORTCUT = '⌘⌥⇧V';
