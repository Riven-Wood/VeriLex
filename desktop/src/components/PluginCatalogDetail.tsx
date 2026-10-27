import { useRef, useState, useEffect } from 'react';
import { CREDENTIAL_SITES, configuredOption, type CatalogEntry } from '../mcp/catalog';
import type { McpServerRecord } from '../runtime/contracts';
import { openPluginSource } from '../platform/pluginCatalog';
import { McpServerEditor } from './McpServerEditor';
import { ImaConnection } from './ImaConnection';
import { CatalogServiceSetup } from './CatalogServiceSetup';
import { ExcelPluginSetup } from './ExcelPluginSetup';
import { PdfPluginSetup } from './PdfPluginSetup';

interface Props {
  entry: CatalogEntry;
  servers: McpServerRecord[];
  onSaved: (record: McpServerRecord) => void;
  onComplete: () => void;
  onOpen: (record: McpServerRecord) => void;
  onYuandian: () => void;
  onClose: () => void;
}

/** Shared by the recommendation catalog and direct connection shortcuts. */
export function PluginCatalogDetail(props: Props) {
  const selected = props.entry;
  const [optionIndex, setOptionIndex] = useState(0);
  const [configuring, setConfiguring] = useState(false);
  const [imaOpen, setImaOpen] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const [editorBusy, setEditorBusy] = useState(false);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const back = () => { if (editorBusy || imaOpen) return; props.onClose(); };
  const openSource = async (entry: CatalogEntry, credentials = false) => {
    setError('');
    try { await openPluginSource(entry.id, credentials); } catch { setError('系统浏览器没有打开，请稍后重试。'); }
  };
  const option = selected.options?.[optionIndex];
  const existing = option && configuredOption(option, props.servers);
  return <dialog ref={dialog} className="plugin-catalog-modal" aria-labelledby="plugin-detail-title"
    onCancel={event => { event.preventDefault(); back(); }} onKeyDown={event => event.stopPropagation()}>
    <header className="plugin-modal-heading"><h2 id="plugin-detail-title">{selected.name}</h2>
      <button type="button" className="mcp-secondary-button" aria-label="关闭插件详情" disabled={editorBusy || imaOpen} onClick={back}>关闭</button></header>
    <section className="plugin-catalog-detail" aria-label={`${selected.name} 接入说明`}><p>{selected.summary}</p>
    {selected.guide && <ol className="mcp-guide-steps" aria-label="接入步骤"><li><span>1</span>获取密钥</li><li><span>2</span>测试连接</li><li><span>3</span>保存并使用</li></ol>}
    <dl className="plugin-catalog-facts"><dt>使用条件</dt><dd>{selected.requirements}</dd><dt>资料去向</dt><dd>{selected.dataUse}</dd></dl>
    <div className="plugin-detail-actions">
      {CREDENTIAL_SITES[selected.id] && <button type="button" className="mcp-secondary-button" onClick={() => void openSource(selected, true)}>前往官网获取 API Key</button>}
      <button type="button" className="mcp-secondary-button" onClick={() => void openSource(selected)}>官方说明</button>
    </div>
    {CREDENTIAL_SITES[selected.id] && <p className="plugin-catalog-note">{selected.guide?.credentialHint ?? '在系统浏览器中登录官网，获取密钥后回到此处填写。'}{selected.id === 'github' ? 'GitHub 使用 Personal Access Token。' : ''}</p>}
    {error && <p role="alert" className="mcp-error">{error}</p>}
    {selected.guide ? <CatalogServiceSetup key={selected.id} entry={selected} servers={props.servers} onSaved={props.onSaved}
      onComplete={props.onComplete} onCancel={back} onOpen={props.onOpen} onBusyChange={setEditorBusy} /> : <>
      {(selected.options?.length ?? 0) > 1 && <label className="plugin-catalog-service">服务范围<select disabled={editorBusy} value={optionIndex}
        onChange={event => setOptionIndex(Number(event.target.value))}>
        {selected.options?.map((item, index) => <option key={item.label} value={index}>{item.label}</option>)}
      </select></label>}
    {selected.id === 'pdf-local' ? <PdfPluginSetup servers={props.servers} onSaved={props.onSaved}
      onComplete={props.onComplete} onOpen={props.onOpen} onBusyChange={setEditorBusy} />
      : selected.id === 'excel-local' && option && !existing ? <ExcelPluginSetup draft={option.draft}
      onSaved={props.onSaved} onComplete={props.onComplete} onCancel={back} onBusyChange={setEditorBusy} />
      : (configuring || (!!option?.credential && !existing)) ? <McpServerEditor key={`${selected.id}-${optionIndex}`} mode="settings"
      initialDraft={option?.draft} credential={option?.credential}
      onSaved={props.onSaved} onBusyChange={setEditorBusy} onComplete={props.onComplete} onCancel={option?.credential ? back : () => setConfiguring(false)} /> : <>
      <p className="plugin-catalog-note">{selected.builtin ? '使用 VeriLex 现有连接流程。' : option
        ? '连接信息已按官方文档整理，需在本机测试通过后保存。' : '请从官方获取配置后导入，VeriLex 会先检查并测试连接。'}</p>
      <button type="button" className="mcp-add" onClick={() => {
        if (selected.builtin === 'yuandian') props.onYuandian();
        else if (selected.builtin === 'ima') setImaOpen(true);
        else if (existing) props.onOpen(existing);
        else setConfiguring(true);
      }}>{selected.builtin === 'ima' ? '管理 ima 连接' : selected.builtin === 'yuandian' ? '管理元典连接' : existing ? '管理已添加连接' : option ? '配置连接' : '导入官方配置'}</button>
    </>}
    </>}
    {imaOpen && <ImaConnection onClose={() => setImaOpen(false)} onSaved={() => setError('')} />}
  </section></dialog>;
}
