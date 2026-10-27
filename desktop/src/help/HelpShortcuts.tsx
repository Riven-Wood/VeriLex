import { APP_COMMANDS, shortcutLabel } from '../commands/registry';
/** Derive key hints from the command controller's catalogue; never duplicate bindings. */
export function HelpShortcuts() {
  return <details className="help-shortcuts"><summary>查看快捷键</summary>
    <p>在应用窗口内使用。编辑文字、输入法选字、弹窗和终端输入时，相应区域优先处理按键。</p>
    <dl>{APP_COMMANDS.filter(command => command.key).map(command => <div key={command.id}><dt>{command.title}{command.scope ? `（${command.scope}）` : ''}</dt><dd><kbd>{shortcutLabel(command)}</kbd></dd></div>)}</dl>
    <p>更多命令与系统唤起选项见「设置」→「通用」→「快捷键」。</p>
  </details>;
}
