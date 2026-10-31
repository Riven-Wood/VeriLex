import { KnowledgeHighlight, KnowledgeMatches } from '../components/KnowledgeMatches';
import { KnowledgeCreateMenu } from '../components/KnowledgeCreateMenu';
import { LoadingState } from '../components/LoadingState';
import { ImaConnection } from '../components/ImaConnection';
import { openIma } from '../platform/ima';
import { useEffect, useRef, useState } from 'react';
import { knowledgeApi, type KnowledgeApi, type KnowledgeDocument, type KnowledgeLibrary, type KnowledgePage } from '../runtime/knowledgeClient';
import { pickWorkspaceDirectory } from '../platform/directoryPicker';
import { BookOpen, FileText, FolderOpen, Search, RefreshCw } from 'lucide-react';
import './KnowledgeLibraryView.css';

const statusLabel: Record<string, string> = {pending: '等待同步', ready: '可用', partial: '部分资料未同步', unavailable: '文件夹无法访问', error: '同步失败'};
const errorText = (e: unknown) => e instanceof Error ? e.message : '操作未完成，请重试。';
export function KnowledgeLibraryView({api = knowledgeApi, chooseFolder = pickWorkspaceDirectory}: {api?: KnowledgeApi; chooseFolder?: () => Promise<string | null>}) {
  const [imaOpen, setImaOpen] = useState(false);
  const [folders, setFolders] = useState<{id: string; name: string}[]>([]);
  const folderId = folders.at(-1)?.id ?? '';
  function navigateFolder(path: {id: string; name: string}[]) {
    setFolders(path); setDocument(null); setPage(null); setReadError(''); setOffset(0); setPreviousOffsets([]);
    setDocuments([]); setMore(false); setQuery(''); setSearch('');
  }
  const reader = useRef<HTMLPreElement>(null);
  const [libraries, setLibraries] = useState<KnowledgeLibrary[]>([]);
  const [selected, setSelected] = useState('');
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const [libraryError, setLibraryError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [previousOffsets, setPreviousOffsets] = useState<number[]>([]);
  const [nextOffset, setNextOffset] = useState(100);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const loadedListKey = useRef('');
  const [more, setMore] = useState(false);
  const [listError, setListError] = useState('');
  const [listLoading, setListLoading] = useState(false);
  const [document, setDocument] = useState<KnowledgeDocument | null>(null);
  const [start, setStart] = useState(0);
  const [focusMatch, setFocusMatch] = useState<number | null>(null);
  const [focusRevision, setFocusRevision] = useState(0);
  const [page, setPage] = useState<KnowledgePage | null>(null);
  const [readError, setReadError] = useState('');
  const library = libraries.find(item => item.id === selected);
  const latestDocument = documents.find(item => item.id === document?.id && item.status === 'ready');
  useEffect(() => {
    if (library?.kind === 'ima') return;
    const timer = window.setInterval(() => setRevision(n => n + 1), 5000);
    return () => window.clearInterval(timer);
  }, [library?.kind]);
  useEffect(() => {
    let active = true;
    api.list().then(items => {
      if (!active) return;
      setLibraries(items); setLoaded(true); setLibraryError('');
      setSelected(old => items.some(item => item.id === old) ? old : items[0]?.id ?? '');
    }).catch(e => {if (active) {setLibraryError(errorText(e)); setLoaded(true);}});
    return () => {active = false;};
  }, [api, revision]);
  useEffect(() => {
    setDocument(null); setPage(null); setStart(0); setQuery(''); setSearch(''); setOffset(0); setPreviousOffsets([]);
    setDocuments([]); setMore(false); setListError(''); setFolders([]);
  }, [selected]);
  useEffect(() => {
    let active = true;
    if (!selected) {setDocuments([]); return;}
    const listKey = JSON.stringify([selected, search, offset, folderId]);
    if (loadedListKey.current !== listKey) setDocuments([]);
    setListLoading(true);
    (folderId ? api.documents(selected, search, offset, folderId) : api.documents(selected, search, offset)).then(result => {
      if (!active) return;
      loadedListKey.current = listKey; setDocuments(result.items); setMore(result.has_more); setNextOffset(result.next_offset ?? offset + 100); setListError('');
    }).catch(e => {if (active) {if (loadedListKey.current !== listKey) setDocuments([]); setListError(errorText(e));}})
      .finally(() => {if (active) setListLoading(false);});
    return () => {active = false;};
  }, [api, selected, search, offset, revision, folderId]);
  useEffect(() => {
    let active = true;
    if (selected && document) api.read(selected, document, start).then(result => {
      if (active) {setPage(result); setReadError('');}
    }).catch(e => {if (active) {setPage(null); setReadError(errorText(e));}});
    return () => {active = false;};
  }, [api, selected, document, start, revision]);
  useEffect(() => {setPage(null); setReadError(''); if (reader.current) reader.current.scrollTop = 0;}, [selected, document, start]);
  useEffect(() => {
    const viewport = reader.current;
    const mark = viewport?.querySelector('.knowledge-current-hit') ?? viewport?.querySelector('mark');
    if (viewport && mark) viewport.scrollTop += mark.getBoundingClientRect().top - viewport.getBoundingClientRect().top - 60;
  }, [page?.text, page?.start, focusMatch, focusRevision]);
  async function act(operation: () => Promise<unknown>) {
    setBusy(true); setError('');
    try {await operation(); setRevision(n => n + 1);} catch (e) {setError(errorText(e));}
    finally {setBusy(false);}
  }
  return <section className="knowledge-view" aria-label="知识库">
    {imaOpen && <ImaConnection onClose={() => setImaOpen(false)} onSaved={() => {setSelected(''); setRevision(n => n + 1);}} />}
    <header className="knowledge-header">
      <h1>知识库</h1>
      {library && <div className="knowledge-header-tools"><div className="knowledge-library-heading"><h2>{library.name}</h2><span className={`knowledge-status knowledge-status--${library.status}`} role="status">{statusLabel[library.status] ?? library.status}</span></div>
        <form className="knowledge-search" onSubmit={event => {event.preventDefault(); setDocument(null); setPage(null); setFolders([]); setSearch(query); setOffset(0); setPreviousOffsets([]); setRevision(n => n + 1);}}>
          <Search size={16} aria-hidden="true" /><input aria-label="搜索库内资料" placeholder="输入原文关键词或文件名" value={query} maxLength={256} onChange={e => setQuery(e.target.value)} /><button>搜索</button>
        </form>
        <details className="knowledge-management" key={selected}><summary>管理知识库</summary><div className="knowledge-management-panel">{library.kind === 'ima' ? <><button onClick={() => setImaOpen(true)}>管理 ima 连接</button><button disabled={busy} onClick={() => void act(async () => {await api.detach(selected); setSelected('');})}>解除关联</button><p>搜索范围包括库内子文件夹。解除关联不会删除 ima 中的资料。</p></> : <><p className="knowledge-path">{library.root}</p>
          <form onSubmit={event => {event.preventDefault(); const value = new FormData(event.currentTarget).get('name'); void act(() => api.update(selected, {name: String(value)}));}}>
            <label>名称 <input name="name" defaultValue={library.name} maxLength={120} required /></label><button disabled={busy}>保存名称</button>
          </form>
          {library.directories.length > 0 && <fieldset disabled={busy}><legend>排除子目录</legend><div className="knowledge-exclusions">{library.directories.map(dir => <label key={dir}>
            <input type="checkbox" checked={library.excluded.includes(dir)} onChange={e => {const excluded = e.target.checked ? [...library.excluded, dir] : library.excluded.filter(d => d !== dir); setDocument(null); void act(() => api.update(selected, {excluded}));}} />{dir}
          </label>)}</div></fieldset>}
          <button disabled={busy} onClick={() => void act(async () => {const root = await chooseFolder(); if (root) {setDocument(null); await api.update(selected, {root});}})}>重新关联文件夹</button>
          <button disabled={busy} onClick={() => void act(async () => {await api.detach(selected); setDocument(null); setPage(null); setSelected('');})}>解除关联</button>
          <p>解除关联只移除本地索引，不删除原文件。</p>
        </>}</div></details>
        <button className="knowledge-sync" disabled={busy} onClick={() => void act(() => api.sync(selected))}><RefreshCw size={14} aria-hidden="true" />立即同步</button>
        </div>}
      <KnowledgeCreateMenu busy={busy} onIma={() => setImaOpen(true)} onAssociate={() => void act(async () => {
        const root = await chooseFolder(); if (root) {const result = await api.associate(root); setSelected(result.id);}
      })}/>
    </header>
    {error && <p role="alert">{error}</p>}
    {libraryError && <p role="alert">{libraryError}</p>}
    {!loaded ? <LoadingState>正在读取知识库…</LoadingState> : libraries.length === 0 ? <p className="knowledge-empty">点击“建立知识库”，关联本机文件夹或接入外部知识库。这里只读取资料，不修改原文件。</p> :
      <div className="knowledge-layout"><nav aria-label="已关联知识库"><div className="knowledge-nav-label">资料来源 <span>{libraries.length}</span></div>{libraries.map(item => <button key={item.id} aria-pressed={item.id === selected} onClick={() => setSelected(item.id)} disabled={busy}>
        <FolderOpen size={17} aria-hidden="true" /><span><strong>{item.name}</strong><small>{item.kind === 'ima' ? 'ima · 按需读取' : `${item.count} 篇${item.status === 'ready' || item.status === 'partial' ? '可用' : '已登记'}`}</small></span>
      </button>)}</nav>
      {library && <main>
        {library.error && <p className="knowledge-library-error" role="status">{library.error}</p>}
        {library.kind === 'ima' && <nav className="knowledge-breadcrumbs" aria-label="知识库文件夹路径"><button onClick={() => navigateFolder([])}>知识库根目录</button>{folders.map((folder, index) => <button key={folder.id} onClick={() => navigateFolder(folders.slice(0, index + 1))}>{folder.name}</button>)}</nav>}
        <div className={`knowledge-documents ${document ? "has-reader" : "is-browsing"}`}><div className="knowledge-list"><div className="knowledge-list-heading"><span>{search ? '搜索结果' : '全部资料'}</span><span>{offset + (documents.length ? 1 : 0)}–{offset + documents.length} {more ? '· 还有更多' : ''}</span></div><div className="knowledge-list-scroll" aria-label="资料列表" aria-busy={listLoading}>
          {listError ? <p role="alert">{listError}{documents.length > 0 && ' 当前显示上次加载的资料。'} <button onClick={() => setRevision(n => n + 1)}>重试</button></p> : !listLoading && !documents.length ? <p>{search ? '没有找到包含该关键词的资料。' : library.kind === 'ima' ? '当前目录没有资料，可输入关键词搜索库内子文件夹。' : '暂无可浏览的 Markdown 资料。'}</p> : null}
          {documents.map(doc => <button className="knowledge-document" aria-label={doc.path} key={doc.id} disabled={doc.status !== 'ready'} aria-pressed={document?.id === doc.id} onClick={() => {if (doc.kind === 'folder') {navigateFolder([...folders, {id: doc.id, name: doc.path}]); return;} setDocument(doc); setStart(doc.read_start ?? 0); setFocusMatch(doc.match_start ?? null);}}>{doc.kind === 'folder' ? <FolderOpen size={16} aria-hidden="true" /> : <FileText size={16} aria-hidden="true" />}<span><strong title={doc.path}>{doc.path.split('/').pop()}</strong>{doc.path.includes('/') && <span className="knowledge-document-path">{doc.path.slice(0, doc.path.lastIndexOf('/'))}</span>}<small>{doc.error || (doc.status === 'pending' ? '等待重新核对' : doc.snippet.startsWith('---') ? 'Markdown 资料' : doc.snippet)}</small></span></button>)}
          </div><div className="knowledge-pagination"><button disabled={!previousOffsets.length || listLoading} onClick={() => {setOffset(previousOffsets[previousOffsets.length - 1]); setPreviousOffsets(previous => previous.slice(0, -1));}}>上一页资料</button><button disabled={!more || !!listError || listLoading} onClick={() => {setPreviousOffsets(previous => [...previous, offset]); setOffset(nextOffset);}}>下一页资料</button></div>
        </div><aside className="knowledge-reader" aria-label="原文预览" hidden={!document}>{document ? <><div className="knowledge-reader-heading"><span className="knowledge-reader-label"><BookOpen size={15} aria-hidden="true" />原文预览<button className="knowledge-close-reader" onClick={() => setDocument(null)}>收起原文</button></span><h3>{document.path.split('/').pop()}</h3><small title={document.path}>{document.path}</small></div>{search && library.kind !== 'ima' && document.match_start != null && <KnowledgeMatches api={api} libraryId={selected} document={document} query={search} onNavigate={(position, match) => {setStart(position); setFocusMatch(match); setFocusRevision(n => n + 1);}} />}{readError ? <div className="knowledge-reader-message"><p role="alert">{readError}</p>{library.kind === 'ima' && <button onClick={() => void act(() => openIma())}>在 ima 查看</button>}{latestDocument && <button onClick={() => {setPage(null); setReadError(''); setDocument(latestDocument); setStart(latestDocument.read_start ?? 0); setRevision(n => n + 1);}}>重新读取原文</button>}</div> : page ? <>{page.external && <div className="knowledge-reader-message"><p>{page.notice}</p><button onClick={() => void act(() => openIma())}>打开 ima 官网查看</button></div>}<pre ref={reader} key={`${document.id}:${start}`} tabIndex={0} aria-label="原文内容"><KnowledgeHighlight text={page.text} query={page.external ? '' : search} focus={focusMatch === null || focusMatch < page.start ? undefined : focusMatch - page.start} /></pre><div className="knowledge-reader-footer"><span>字符 {page.start.toLocaleString()}–{page.end.toLocaleString()} / {page.total_chars.toLocaleString()}</span><div><button disabled={start === 0} onClick={() => setStart(n => Math.max(0, n - 20000))}>上一段原文</button><button disabled={!page.has_more} onClick={() => setStart(page.end)}>下一段原文</button></div></div></> : <p className="knowledge-reader-message" role="status">正在核对并读取原文…</p>}</> : <div className="knowledge-reader-empty"><BookOpen size={32} strokeWidth={1.3} aria-hidden="true" /><h3>在这里阅读原文</h3><p>选择资料查看原文。</p></div>}</aside></div>
      </main>}</div>}
  </section>;
}
