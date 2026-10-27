import { useRef, useState, useEffect } from 'react';
import { CATALOG_CATEGORIES, PLUGIN_CATALOG, configuredOption, type CatalogCategory, type CatalogEntry } from '../mcp/catalog';
import type { McpServerRecord } from '../runtime/contracts';
import { PluginCatalogDetail } from './PluginCatalogDetail';

interface Props {
  query: string; servers: McpServerRecord[];
  onSaved: (record: McpServerRecord) => void;
  onComplete: () => void;
  onOpen: (record: McpServerRecord) => void;
  onYuandian: () => void;
}
export function PluginCatalog(props: Props) {
  const [category, setCategory] = useState<CatalogCategory | '全部'>('全部');
  const [selected, setSelected] = useState<CatalogEntry | null>(null);
  const triggerId = useRef<string | null>(null);
  const list = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!selected && triggerId.current) list.current?.querySelector<HTMLButtonElement>(`[data-plugin="${triggerId.current}"]`)?.focus();
  }, [selected]);
  const query = props.query.trim().toLocaleLowerCase('zh-CN');
  const entries = PLUGIN_CATALOG.filter(entry => (category === '全部' || entry.category === category)
    && entry.name.toLocaleLowerCase('zh-CN').includes(query));
  return <section ref={list} aria-label="外部工具推荐目录">
    <div className="plugin-category-filters" role="group" aria-label="推荐用途分类">
      {(['全部', ...CATALOG_CATEGORIES] as const).map(item => <button type="button" key={item} aria-pressed={category === item}
        onClick={() => setCategory(item)}>{item}（{item === '全部' ? PLUGIN_CATALOG.length : PLUGIN_CATALOG.filter(entry => entry.category === item).length}）</button>)}
    </div>
    <p className="plugin-catalog-note">按用途选择，需要时再添加。已有的文件、计算和任务管理能力继续使用应用内置工具。</p>
    {!entries.length && <p className="mcp-empty">没有找到匹配的名称</p>}
    <div className="plugin-tools-grid">{entries.map(entry => <article className="plugin-card" key={entry.id} aria-label={`推荐 ${entry.name}`}>
      <div className="plugin-card-title"><h3>{entry.name}</h3><span className="plugin-card-badge">{entry.category}</span></div>
      <p>{entry.summary}</p>
      <div className="plugin-card-footer"><p className="plugin-card-status">{entry.builtin ? '已有专用接入入口' : entry.options?.some(option => configuredOption(option, props.servers))
        ? '已有配置 · 连接状态见详情' : entry.options ? '可配置 · 尚未验证本机连接' : '需导入官方配置'}</p>
      <button type="button" className="mcp-secondary-button plugin-card-action" aria-label={`了解 ${entry.name}`}
        data-plugin={entry.id} onClick={() => { triggerId.current = entry.id; setSelected(entry); }}>查看与添加</button></div>
    </article>)}</div>
    {selected && <PluginCatalogDetail {...props} entry={selected} onClose={() => setSelected(null)} />}
  </section>;
}
