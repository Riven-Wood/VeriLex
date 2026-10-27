import { ArrowLeft, ArrowUpRight, CheckCircle2, ChevronRight, KeyRound, SlidersHorizontal } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { PROVIDER_PRESETS, type ProviderPreset } from '../providers/presets';
import { openProviderSite } from '../platform/providerSites';
import './ProviderGuide.css';
import { ProviderBrand } from './ProviderBrand';

interface Props {
  preset: ProviderPreset | null;
  step: 'choose' | 'credentials' | 'model';
  phase: 'idle' | 'testing' | 'saving';
  apiKey: string;
  name: string;
  model: string;
  models: string[];
  status: string;
  error: string;
  canTest: boolean;
  canSave: boolean;
  onSelect: (preset: ProviderPreset | null) => void;
  onBack: () => void;
  onKeyChange: (key: string) => void;
  onNameChange: (name: string) => void;
  onModelChange: (model: string) => void;
  onTest: () => void;
  onSave: () => void;
  onClose: () => void;
}

export function ProviderGuide(props: Props) {
  const { preset, step, phase } = props;
  const [linkError, setLinkError] = useState('');
  const modelDetailsRef = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    // Open recovery input once; clearing the error while typing must not close it.
    if (props.error && modelDetailsRef.current) modelDetailsRef.current.open = true;
  }, [props.error]);
  async function openSite(site: 'console' | 'docs') {
    if (!preset) return;
    setLinkError('');
    try { await openProviderSite(preset.id, site); }
    catch { setLinkError('系统浏览器没有打开，请稍后重试。'); }
  }
  const stepIndex = step === 'choose' ? 0 : step === 'credentials' ? 1 : 2;
  return <div className="provider-guide">
    <ol className="provider-guide-steps" aria-label="接入步骤">
      {['选择服务商', '填写密钥', '选择模型'].map((label, index) => <li key={label}
        aria-current={index === stepIndex ? 'step' : undefined} data-complete={index < stepIndex}>
        <span>{index < stepIndex ? <CheckCircle2 size={15} aria-hidden="true" /> : index + 1}</span>{label}
      </li>)}
    </ol>
    {step === 'choose' ? <>
      <div className="provider-guide-intro"><h3>选择你的模型服务商</h3><p>使用国内平台的普通 API 密钥，按步骤完成接入。</p></div>
      <div className="provider-preset-grid">
        {PROVIDER_PRESETS.map((item, index) => <button key={item.id} type="button" className="provider-preset-card"
          autoFocus={index === 0} onClick={() => props.onSelect(item)} aria-label={`接入 ${item.name}`}>
          <span className="provider-preset-mark model-brand-tile" aria-hidden="true"><ProviderBrand id={item.id} /></span>
          <span className="provider-preset-copy"><strong>{item.name}</strong><small>{item.subtitle}</small></span>
          <ChevronRight size={16} aria-hidden="true" />
        </button>)}
      </div>
      <button type="button" className="provider-other-connection" onClick={() => props.onSelect(null)}>
        <SlidersHorizontal size={17} aria-hidden="true" /><span><strong>其他连接</strong><small>自定义兼容地址或本机 Ollama</small></span><ChevronRight size={16} aria-hidden="true" />
      </button>
    </> : preset && <form className="provider-guide-form" onSubmit={event => {
      event.preventDefault();
      if (step === 'credentials') props.onTest(); else props.onSave();
    }}>
      <div className="provider-guide-selected"><span className="provider-preset-mark model-brand-tile" aria-hidden="true"><ProviderBrand id={preset.id} /></span>
        <div><h3>{preset.name}</h3><p>{preset.subtitle} · 普通 API</p></div>
      </div>
      {step === 'credentials' ? <>
        <section className="provider-key-help" aria-label="获取密钥指引">
          <h4>1. 在官网获取密钥</h4><p>{preset.instructions}</p>
          <div className="provider-guide-links">
            <button type="button" className="secondary-button" onClick={() => void openSite('console')}>前往官网获取密钥<ArrowUpRight size={14} aria-hidden="true" /></button>
            <button type="button" className="text-button" onClick={() => void openSite('docs')}>查看接入说明</button>
          </div>
        </section>
        <label className="provider-field"><span>2. 粘贴 API Key</span>
          <input aria-label="API Key" autoFocus autoComplete="new-password" name="provider_api_key" type="password"
            required spellCheck={false} value={props.apiKey} disabled={phase !== 'idle'} placeholder={`粘贴 ${preset.name} 的 API 密钥`}
            onChange={event => props.onKeyChange(event.target.value)} />
        </label>
        <p className="provider-guide-note"><KeyRound size={13} aria-hidden="true" />密钥保存后不会回显；连接时仅发送给所选服务商。</p>
        <details ref={modelDetailsRef} className="provider-guide-extra" open={preset.modelRequired ? true : undefined}>
          <summary>{preset.modelRequired ? '3. 填写模型标识' : '指定模型（可选）'}</summary>
          <label className="provider-field"><span>模型标识</span>
            <input aria-label="模型标识" autoComplete="off" spellCheck={false} required={preset.modelRequired}
              value={props.model} disabled={phase !== 'idle'} placeholder="复制平台中的完整模型标识"
              onChange={event => props.onModelChange(event.target.value)} />
            <small>{preset.modelHint}</small>
          </label>
        </details>
        <p className="provider-guide-endpoint">官方连接地址 <code>{preset.baseUrl}</code></p>
        <p className="provider-guide-note">网页版会员、Coding Plan 等套餐与普通 API 分开计费。验证优先读取模型列表；平台不提供列表时会发送简短测试，可能产生少量费用。</p>
      </> : <>
        <div className="provider-guide-success"><CheckCircle2 size={20} aria-hidden="true" /><div><strong>已读取模型信息</strong><p>{props.status}</p></div></div>
        <label className="provider-field"><span>默认模型</span>
          <select aria-label="默认模型" autoFocus required value={props.model} disabled={phase !== 'idle'} onChange={event => props.onModelChange(event.target.value)}>
            {props.models.map(model => <option key={model}>{model}</option>)}
          </select><small>请选择支持对话与工具调用的文本模型；保存后可在聊天中切换。</small>
        </label>
        <details className="provider-guide-extra"><summary>连接名称与更多设置</summary>
          <label className="provider-field"><span>连接名称</span><input aria-label="连接名称" required value={props.name}
            disabled={phase !== 'idle'} onChange={event => props.onNameChange(event.target.value)} /></label>
          <p className="provider-guide-note">上下文上限会自动读取。保存后可在连接详情中逐个模型调整。</p>
        </details>
      </>}
      {linkError && <p className="provider-form-error" role="alert">{linkError}</p>}
      {props.error && <p className="provider-form-error" role="alert">{props.error}</p>}
      {phase === 'testing' && <p role="status" className="provider-guide-note">正在验证密钥并读取模型信息…</p>}
      <footer className="provider-guide-actions">
        <button type="button" className="text-button" disabled={phase === 'saving'} onClick={props.onBack}><ArrowLeft size={14} aria-hidden="true" />{step === 'credentials' ? '更换服务商' : '返回修改'}</button>
        <div><button type="button" className="secondary-button" disabled={phase === 'saving'} onClick={props.onClose}>取消</button>
          <button type="submit" className="primary-button" disabled={phase !== 'idle' || (step === 'credentials' ? !props.canTest : !props.canSave)}>
            {phase === 'testing' ? '正在验证…' : phase === 'saving' ? '正在保存…' : step === 'credentials' ? '验证并继续' : '保存连接'}
          </button></div>
      </footer>
    </form>}
  </div>;
}
