import { useEffect, useRef, useState } from 'react';
import { configuredOption, type CatalogEntry, type CatalogOption } from '../mcp/catalog';
import { mcpErrorCopy } from '../mcp/errorCopy';
import { createMcpServer, listMcpServers, listMcpServerTools, reviewMcpServerTool, RuntimeApiError,
  testMcpServerDraft, testSavedMcpServer, updateMcpServer } from '../runtime/api';
import type { McpProbeDraftRequest, McpSavedProbeRequest, McpServerRecord, McpTestResult, McpToolDecision } from '../runtime/contracts';
import './PkulawSetup.css';

interface Item {
  option: CatalogOption;
  state: 'idle' | 'testing' | 'tested' | 'saving' | 'saved' | 'error';
  error?: string;
  existing?: McpServerRecord;
  persisted?: McpServerRecord;
  request?: McpProbeDraftRequest;
  savedRequest?: McpSavedProbeRequest;
  result?: McpTestResult;
  decisions: McpToolDecision[];
}
export interface CatalogServiceSetupProps {
  entry: CatalogEntry;
  servers: McpServerRecord[];
  onSaved: (record: McpServerRecord) => void;
  onComplete: () => void;
  onCancel: () => void;
  onOpen: (record: McpServerRecord) => void;
  onBusyChange: (busy: boolean) => void;
}

const errorCopy = (error: unknown) => mcpErrorCopy(error instanceof RuntimeApiError ? error.code : undefined);

/** One credential, independent tested receipts and explicit tool review for each official endpoint. */
export function CatalogServiceSetup(props: CatalogServiceSetupProps) {
  const { entry } = props;
  const initialItems = (): Item[] => entry.options!.map(option => ({ option, state: 'idle', decisions: [] }));
  const single = entry.options!.length === 1;
  const prefix = entry.options![0].credential!.prefix;
  const [key, setKey] = useState('');
  const [items, setItems] = useState(initialItems);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const controllerRef = useRef<AbortController | null>(null);
  useEffect(() => () => controllerRef.current?.abort(), []);
  useEffect(() => { props.onBusyChange(busy); }, [busy, props.onBusyChange]);
  const changeItem = (index: number, patch: Partial<Item>) => setItems(current => current.map((item, i) => i === index ? { ...item, ...patch } : item));
  const token = prefix === 'Bearer ' ? key.trim().replace(/^Bearer\s+/i, '') : key.trim();
  const keyValid = !!token && !/[\s\x00-\x1f\x7f]/.test(token) && token.length <= 4096;
  const ready = items.filter(item => item.result && item.state !== 'saved');
  const pending = items.filter(item => !item.result && item.state !== 'saved');
  const savedCount = items.filter(item => item.state === 'saved').length;

  async function testAll() {
    if (controllerRef.current || !keyValid || !pending.length) return;
    const controller = new AbortController(); controllerRef.current = controller;
    setBusy(true); setError('');
    try {
      // Refresh before every attempt, including recovery after an uncertain save response.
      const records = await listMcpServers(controller.signal);
      for (let index = 0; index < items.length; index++) {
        const item = items[index];
        if (controller.signal.aborted) return;
        if (item.result || item.state === 'saved') continue;
        changeItem(index, { state: 'testing', error: undefined });
        try {
          const existing = configuredOption(item.option, records);
          const authName = existing?.secretHeaderKeys.find(name => name.toLowerCase() === 'authorization') ?? 'Authorization';
          const request: McpProbeDraftRequest = { draft: item.option.draft,
            secretPatch: { env: {}, headers: { [authName]: `${prefix}${token}` } }, acceptedRiskCodes: [] };
          const savedRequest: McpSavedProbeRequest | undefined = existing ? {
            expectedRevision: existing.configRevision,
            patch: { transport: 'http',
              headers: Object.fromEntries(Object.entries(existing.headers).filter(([name]) => name.toLowerCase() !== 'authorization')),
              secretHeaderKeys: [...new Set([...existing.secretHeaderKeys, authName])] },
            secretPatch: request.secretPatch, acceptedRiskCodes: [],
          } : undefined;
          // The existing test APIs perform authoritative config/risk and secret-echo checks.
          const previous = existing ? await listMcpServerTools(existing.id, controller.signal) : [];
          const result = existing && savedRequest
            ? await testSavedMcpServer(existing.id, savedRequest, controller.signal)
            : await testMcpServerDraft(request, controller.signal);
          if (controller.signal.aborted) return;
          const decisions = result.tools.map(tool => {
            const prior = previous.find(old => old.toolName === tool.toolName);
            return { toolName: tool.toolName, expectedDescriptionHash: tool.descriptionHash, expectedSchemaHash: tool.schemaHash,
              enabled: existing ? !!prior && prior.reviewState === 'known' && prior.descriptionHash === tool.descriptionHash
                && prior.schemaHash === tool.schemaHash && prior.enabled : true };
          });
          changeItem(index, { existing, request, savedRequest, result, decisions, persisted: undefined, state: 'tested' });
        } catch (caught) {
          if (controller.signal.aborted) return;
          changeItem(index, { state: 'error', error: errorCopy(caught), result: undefined });
        }
      }
    } catch (caught) {
      if (!controller.signal.aborted) setError(errorCopy(caught));
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function saveAll() {
    if (controllerRef.current || !ready.length) return;
    const controller = new AbortController(); controllerRef.current = controller;
    setBusy(true); setError('');
    let totalSaved = savedCount;
    for (let index = 0; index < items.length; index++) {
      const item = items[index];
      if (!item.result || !item.request || item.state === 'saved') continue;
      changeItem(index, { state: 'saving', error: undefined });
      let persisted = item.persisted;
      try {
        if (!persisted) {
          persisted = item.existing && item.savedRequest
            ? await updateMcpServer(item.existing.id, { ...item.savedRequest,
              testReceipt: item.result.testReceipt, toolDecisions: item.decisions }, controller.signal)
            : await createMcpServer({ ...item.request, testReceipt: item.result.testReceipt, toolDecisions: item.decisions }, controller.signal);
          if (controller.signal.aborted) return;
          changeItem(index, { persisted });
          props.onSaved(persisted);
        }
        if (item.existing) {
          // A credential update deliberately marks all tools changed in Runtime. Apply the user's reviewed choices afterwards.
          for (const decision of item.decisions) {
            await reviewMcpServerTool(persisted.id, decision.toolName, decision, controller.signal);
            if (controller.signal.aborted) return;
          }
        }
        changeItem(index, { state: 'saved', error: undefined }); totalSaved++;
      } catch (caught) {
        if (controller.signal.aborted) return;
        // Keep successful writes; retry review without creating or updating the same server again.
        changeItem(index, { state: 'error', error: errorCopy(caught), persisted,
          result: persisted ? item.result : undefined });
      }
    }
    if (controllerRef.current === controller) controllerRef.current = null;
    if (!controller.signal.aborted) {
      setBusy(false);
      if (totalSaved === items.length) props.onComplete();
    }
  }

  return <section className="pkulaw-setup" aria-label={`${entry.name}统一接入`}>
    <label className="mcp-catalog-credential"><span>API Key</span>
      <input type="password" autoComplete="off" spellCheck={false} maxLength={4103} aria-label="API Key"
        placeholder={entry.guide?.placeholder} value={key} disabled={busy}
        onChange={event => { setKey(event.target.value); setItems(initialItems()); setError(''); }} />
    </label>
    <p className="plugin-catalog-note">测试只获取工具清单，不发起业务查询。实际使用范围和费用以服务商账号权益为准。</p>
    {key && !keyValid && <p className="mcp-error" role="alert">请粘贴完整 Token，不要包含空格或换行。</p>}
    {error && <p className="mcp-error" role="alert">{error}</p>}
    <ul className="pkulaw-services" aria-label={entry.id === 'pkulaw' ? '法宝服务连接状态' : `${entry.name}服务连接状态`}>
      {items.map((item, index) => {
        const existing = item.persisted ?? configuredOption(item.option, props.servers);
        const status = item.state === 'testing' ? '正在测试…' : item.state === 'saving' ? '正在保存…'
          : item.state === 'saved' ? '已保存' : item.state === 'error' ? item.error
          : item.result ? `测试通过 · ${item.result.tools.length} 个工具` : existing ? '已添加 · 可统一更新密钥' : '待接入';
        return <li key={item.option.label}>
          <div className="pkulaw-service-row"><strong>{item.option.label}</strong><span role="status">{status}</span>
            {existing && !busy && !item.result && <button type="button" className="mcp-secondary-button"
              onClick={() => props.onOpen(existing)} aria-label={`管理 ${item.option.label}`}>管理</button>}</div>
          {item.result && item.state !== 'saved' && <details><summary>查看与选择工具</summary>
            {item.result.tools.map((tool, toolIndex) => <label className="pkulaw-tool" key={tool.toolName}>
              <input type="checkbox" checked={item.decisions[toolIndex].enabled} disabled={busy}
                aria-label={`${item.option.label}：${tool.toolName}`} onChange={event => changeItem(index, {
                  decisions: item.decisions.map((decision, i) => i === toolIndex ? { ...decision, enabled: event.target.checked } : decision),
                })} />
              <span><strong>{tool.toolName}</strong><small>{tool.description}</small></span>
            </label>)}
          </details>}
        </li>;
      })}
    </ul>
    {!!savedCount && <p role="status">已保存 {savedCount} / {items.length} 项服务。</p>}
    {!!ready.length && <p className="plugin-catalog-note">保存前可展开查看与选择工具。已有工具保留原有开关；保存后，在对话中选择插件即可使用。</p>}
    <div className="mcp-editor-actions">
      <button type="button" className="mcp-secondary-button" disabled={busy} onClick={props.onCancel}>取消</button>
      <button type="button" className="mcp-secondary-button" disabled={busy || !keyValid || !pending.length} onClick={() => void testAll()}>
        {items.some(item => item.state === 'error') ? '重试失败服务' : single ? '测试并查看工具' : '测试全部服务'}</button>
      <button type="button" className="mcp-add" disabled={busy || !ready.length} onClick={() => void saveAll()}>
        {single ? '保存' : ready.length === items.length ? '保存全部服务' : `保存已通过的服务${ready.length ? `（${ready.length}）` : ''}`}</button>
    </div>
  </section>;
}
