import { useState } from 'react';
import type { McpServerDraft, McpServerRecord } from '../runtime/contracts';
import { pickWorkspaceDirectory } from '../platform/directoryPicker';
import { McpServerEditor } from './McpServerEditor';

export function ExcelPluginSetup({draft, onSaved, onComplete, onCancel, onBusyChange}: {
  draft: McpServerDraft; onSaved: (record: McpServerRecord) => void;
  onComplete: () => void; onCancel: () => void; onBusyChange: (busy: boolean) => void;
}) {
  const [folder, setFolder] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [choosing, setChoosing] = useState(false);
  if (folder) return <McpServerEditor mode="settings" initialDraft={{...draft, cwd: folder}}
    onSaved={onSaved} onComplete={onComplete} onCancel={onCancel} onBusyChange={onBusyChange} />;
  return <div><p>选择存放表格的文件夹。插件只能读取这里的 .xlsx，处理后生成新文件，原件保留。</p>
    <button type="button" className="mcp-add" disabled={choosing} onClick={() => {
      setChoosing(true); onBusyChange(true); setError('');
      void pickWorkspaceDirectory().then(setFolder).catch(() => setError('未能选择文件夹，请重试。'))
        .finally(() => {setChoosing(false); onBusyChange(false);});
    }}>选择表格文件夹</button>{error && <p role="alert">{error}</p>}</div>;
}
