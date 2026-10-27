import { useState } from 'react';
import { Search, ExternalLink, Copy } from 'lucide-react';
import { searchHelp } from './content';
import { feedbackTemplate, openFeedback, type HelpDiagnostics } from './feedback';
import './help.css';
import { HelpShortcuts } from './HelpShortcuts';

export function HelpCenter({ appVersion, runtimeVersion, runtimeConnected }: HelpDiagnostics) {
  const [query, setQuery] = useState('');
  const [includeDiagnostics, setIncludeDiagnostics] = useState(true);
  const [notice, setNotice] = useState('');
  const articles = searchHelp(query);
  const template = feedbackTemplate({ appVersion, runtimeVersion, runtimeConnected }, includeDiagnostics);
  async function copy() {
    try { await navigator.clipboard.writeText(template); setNotice('已复制问题模板。填写并检查后，由你决定是否提交。'); }
    catch { setNotice('未能复制，请选中下方模板手动复制。'); }
  }
  async function open() {
    try { await openFeedback(); setNotice('已请求打开项目问题页。内容不会自动粘贴或提交。'); }
    catch { setNotice('系统浏览器未能打开。可手动访问下方项目地址。'); }
  }
  return <div className="help-center">
    <section id="setting-help" aria-label="使用说明">
      <label className="help-search"><Search size={17} aria-hidden="true"/><input type="search" aria-label="搜索使用说明" placeholder="搜索模型、文件、合同、连接失败…" value={query} onChange={event => setQuery(event.target.value)}/></label>
      <p className="help-local-note">说明保存在本机，无需联网。按你要做的事查找。</p>
      <div className="help-topics" aria-label="帮助主题">{['开始使用','文件与工具','法律工作','排查问题'].map(category => <button key={category} type="button" onClick={() => setQuery(category)}>{category}</button>)}{query && <button type="button" onClick={() => setQuery('')}>显示全部</button>}</div>
      <p className="help-count" role="status">{articles.length ? `${articles.length} 篇说明` : '没有找到相关说明。试试“模型”“文件”或“连接失败”，也可使用下方反馈。'}</p>
      <div className="help-articles">{articles.map(article => <details key={`${article.id}:${query}`} className="help-article" open={query.trim() ? true : undefined}>
        <summary><span className="help-article-title">{article.title}</span><span className="help-article-category">{article.category}</span></summary>
        <ol>{article.steps.map(step => <li key={step}>{step}</li>)}</ol>
        {article.note && <p className="help-article-note">{article.note}</p>}
      </details>)}</div>
    </section>
    <HelpShortcuts/>
    <section id="setting-feedback" className="help-feedback" aria-labelledby="help-feedback-heading">
      <h2 id="help-feedback-heading">问题反馈</h2>
      <p>复制模板，用虚构示例描述问题。项目问题页为公开的 GitHub 页面，提交需要你自行登录；不登录也可复制模板留存。</p>
      <label className="help-diagnostics-choice"><input type="checkbox" checked={includeDiagnostics} onChange={event => {setIncludeDiagnostics(event.target.checked);setNotice('');}}/>附上基础诊断（版本与后台连接状态）</label>
      <details><summary>查看将复制的完整模板</summary><textarea aria-label="问题反馈模板" readOnly value={template} rows={16}/></details>
      <div className="help-actions"><button type="button" onClick={() => void copy()}><Copy size={15} aria-hidden="true"/>复制问题模板</button><button type="button" onClick={() => void open()}><ExternalLink size={15} aria-hidden="true"/>打开项目问题页</button></div>
      <p className="help-address">github.com/Riven-Wood/VeriLex/issues</p>
      <p className="help-local-note">不收集对话、案件文件、密钥或日志，也不会自动上传或提交。</p>
      {notice && <p role="status" className="help-notice">{notice}</p>}
    </section>
  </div>;
}
