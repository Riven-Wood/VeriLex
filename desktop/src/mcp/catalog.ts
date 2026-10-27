import type { McpServerDraft, McpServerRecord } from '../runtime/contracts';

// Curated public metadata only. Sources: docs/research/2026-09-18-plugin-catalog.md
// and docs/research/2026-10-12-mcp-onboarding.md.
export const CATALOG_CATEGORIES = ['法律检索', '企业尽调', '基础办公', '知识协作', '高级自动化'] as const;
export type CatalogCategory = typeof CATALOG_CATEGORIES[number];
export interface CatalogCredential { scope: 'env' | 'headers'; key: string; label: string; prefix: string }
export interface CatalogOption { label: string; draft: McpServerDraft; credential?: CatalogCredential }
export interface CatalogEntry {
  id: string; name: string; category: CatalogCategory; summary: string;
  requirements: string; dataUse: string; source: string;
  guide?: { credentialHint: string; placeholder: string };
  options?: CatalogOption[]; builtin?: 'yuandian' | 'ima';
}
const bearer: CatalogCredential = { scope: 'headers', key: 'Authorization', label: 'API Key' , prefix: 'Bearer ' };
function base(displayName: string): McpServerDraft {
  return { displayName, transport: 'http', command: null, args: [], cwd: null, url: null,
    env: {}, headers: {}, secretEnvKeys: [], secretHeaderKeys: [], connectTimeoutMs: 10000, requestTimeoutMs: 30000 };
}
function remote(label: string, displayName: string, url: string, headers: Record<string, string> = {}): CatalogOption {
  return { label, draft: { ...base(displayName), url, headers, secretHeaderKeys: ['Authorization'] }, credential: bearer };
}
function local(label: string, displayName: string, command: string, args: string[], connectTimeoutMs = 10000): CatalogOption {
  return { label, draft: { ...base(displayName), transport: 'stdio', command, args, connectTimeoutMs } };
}
export const CREDENTIAL_SITES: Readonly<Record<string, string>> = {
  yuandian: 'https://open.chineselaw.com/',
  wkinfo: 'https://mcp.wkinfo.com.cn/',
  pkulaw: 'https://mcp.pkulaw.com/',
  qcc: 'https://agent.qcc.com/guide',
  tianyancha: 'https://www.tianyancha.com/ai/profile',
  tavily: 'https://app.tavily.com/',
  firecrawl: 'https://www.firecrawl.dev/app/api-keys',
  ima: 'https://ima.qq.com/agent-interface',
  github: 'https://github.com/settings/personal-access-tokens/new',
};
// Official HTTP endpoints verified against https://mcp.pkulaw.com/docs on 2026-10-06.
export const PKULAW_ENTRY: CatalogEntry = { id: 'pkulaw', name: '北大法宝', category: '法律检索', summary: '法规与案例检索、精准法条、引用核验和文献链接。',
    guide: { credentialHint: '登录法宝 MCP 平台，在控制台获取 MCP Token。一个 Token 可用于以下九项服务。', placeholder: '粘贴法宝 MCP Token，全部服务共用' },
    requirements: '一个法宝 MCP Token 接入全部九项服务，实际调用按各服务标准扣除账号积分。',
    dataUse: '检索词或引用文本发送至北大法宝。', source: 'https://mcp.pkulaw.com/',
    options: [remote('法规智能检索', '北大法宝-法规智能检索', 'https://apim-gateway.pkulaw.com/mcp-law-search-service'),
      remote('法规关键词检索', '北大法宝-法规关键词检索', 'https://apim-gateway.pkulaw.com/mcp-law'),
      remote('法律引用核验', '北大法宝-法律引用核验', 'https://apim-gateway.pkulaw.com/pku_citation_validator'),
      remote('法律文献链接', '北大法宝-法律文献链接', 'https://apim-gateway.pkulaw.com/add-doc-link'),
      remote('案例智能检索', '北大法宝-案例智能检索', 'https://apim-gateway.pkulaw.com/mcp-case-search-service'),
      remote('案例关键词检索', '北大法宝-案例关键词检索', 'https://apim-gateway.pkulaw.com/mcp-case'),
      remote('精准法条查询', '北大法宝-精准法条查询', 'https://apim-gateway.pkulaw.com/mcp-fatiao'),
      remote('法条识别与溯源', '北大法宝-法条识别与溯源', 'https://apim-gateway.pkulaw.com/law_recognition'),
      remote('案号识别与溯源', '北大法宝-案号识别与溯源', 'https://apim-gateway.pkulaw.com/case_number_recognition')] };

export const PLUGIN_CATALOG: readonly CatalogEntry[] = [
  { id: 'yuandian', name: '元典智库', category: '法律检索', builtin: 'yuandian',
    summary: '法律法规、裁判案例、企业信息与证券资料。', requirements: '需要元典 API Key，额度和费用以账号权益为准。',
    dataUse: '查询内容发送至元典；返回资料供当前模型处理。', source: 'https://github.com/yuandian-ailaw/yuandian-mcp-server' },
  { id: 'wkinfo', name: '威科先行', category: '法律检索', summary: '法规检索、案例检索与法律引用识别。',
    guide: { credentialHint: '登录威科 MCP 平台，在「我的服务」中创建应用并复制应用凭证。请为应用开通需要的法规、案例或引用核验服务。', placeholder: '粘贴威科应用凭证' },
    requirements: '在威科 MCP「我的服务」获取应用凭证，费用以账号权益为准。',
    dataUse: '检索词和待核验引用发送至威科先行。', source: 'https://mcp.wkinfo.com.cn/docs',
    options: [remote('综合服务', '威科先行', 'https://mcp.wkinfo.com.cn/mcp-servers/integrated/')] },
  PKULAW_ENTRY,
  { id: 'qcc', name: '企查查', category: '企业尽调', summary: '核验企业工商信息、股权关系及风险线索。',
    guide: { credentialHint: '登录企查查智能体数据平台，点击头像，再点击「获取我的 API KEY」。以下三项常用尽调服务共用一个密钥。', placeholder: '粘贴企查查 API Key' },
    requirements: '需要企查查智能体数据平台 API Key；查询按账号额度计费。',
    dataUse: '企业名称和查询条件发送至企查查。', source: 'https://agent.qcc.com/guide',
    options: [remote('企业信息', '企查查-企业信息', 'https://agent.qcc.com/mcp/company/stream'),
      remote('企业风险', '企查查-企业风险', 'https://agent.qcc.com/mcp/risk/stream'),
      remote('知识产权', '企查查-知识产权', 'https://agent.qcc.com/mcp/ipr/stream')] },
  { id: 'tianyancha', name: '天眼查', category: '企业尽调', summary: '查询企业工商、股权关系、司法风险与经营信息。',
    guide: { credentialHint: '登录天眼查官方平台获取 API Key（OpenAPI Token），并确认已开通所需数据权限。这里使用密钥接入。', placeholder: '粘贴天眼查 API Key，不加 Bearer 前缀' },
    requirements: '需要天眼查 API Key 与相应数据权限；查询额度和费用以账号权益为准。',
    dataUse: '企业名称和查询条件发送至天眼查。', source: 'https://github.com/tyc-tech/tyc-cli',
    options: [{ ...remote('企业数据服务', '天眼查', 'https://mcp.tianyancha.com/mcp'),
      credential: { ...bearer, prefix: '' } }] },
  { id: 'tavily', name: 'Tavily', category: '基础办公', summary: '搜索网络资料并提取网页正文。',
    requirements: '需要 Tavily API Key 和 Node.js；首次测试可能下载官方程序，费用以平台为准。',
    dataUse: '搜索词、网址发送至 Tavily。', source: 'https://docs.tavily.com/documentation/mcp',
    options: [{ ...local('搜索与网页读取', 'Tavily', 'npx', ['-y', 'tavily-mcp']),
      draft: { ...local('', 'Tavily', 'npx', ['-y', 'tavily-mcp']).draft, secretEnvKeys: ['TAVILY_API_KEY'] },
      credential: { scope: 'env', key: 'TAVILY_API_KEY', label: 'API Key', prefix: '' } }] },
  { id: 'firecrawl', name: 'Firecrawl', category: '基础办公', summary: '读取网页、搜索资料及采集网站内容。',
    requirements: '需要 Firecrawl API Key；费用以服务套餐为准。',
    dataUse: '搜索词和网址发送至 Firecrawl；文件解析时会向该服务提交文件。', source: 'https://github.com/firecrawl/firecrawl-mcp-server',
    options: [remote('网页工具', 'Firecrawl', 'https://mcp.firecrawl.dev/v2/mcp')] },
  { id: 'excel-local', name: 'Excel 本地插件', category: '基础办公',
    summary: '读取多工作表、整理数据并交付新的 Excel 文件，原件保留。',
    requirements: '已随应用提供，无需密钥或安装 Excel。支持 .xlsx；公式不重算，带图表/透视表的源文件仅支持读取。',
    dataUse: '文件在所选文件夹内处理；读取的内容会交给当前对话使用的模型。',
    source: 'https://github.com/haris-musa/excel-mcp-server',
    options: [local('本机表格处理', 'Excel 本地插件', 'verilex-excel-plugin', [], 60000)] },
  { id: 'pdf-local', name: 'PDF 页面处理', category: '基础办公',
    summary: '合并、拆分或提取 PDF 页面，原件保留。',
    requirements: 'QuillPDF 开源插件已随应用提供，无需密钥或另装程序。支持有文字或扫描 PDF 的页面处理；不提供脱敏或文字编辑。',
    dataUse: '仅在所选文件夹处理。文件名、路径和页数交给当前模型；页面处理不发送正文。复杂表单、签名和书签不保证保留。',
    source: 'https://github.com/PurpleDirective/quillpdf-mcp',
    options: [local('本机 PDF 页面处理', 'PDF 页面处理', 'verilex-pdf-plugin', [])] },
  { id: 'officecli', name: 'OfficeCLI', category: '基础办公', summary: '在本机创建、读取和修改 Word、Excel、PowerPoint。',
    requirements: '需先安装官方 OfficeCLI，并让应用能找到程序；开源软件。',
    dataUse: '可读写本机文件。读取的内容可能交给当前模型；不替代合同审查的原件保护。', source: 'https://github.com/iOfficeAI/OfficeCLI',
    options: [local('本机 OfficeCLI', 'OfficeCLI', 'officecli', ['mcp'])] },
  { id: 'markitdown', name: 'MarkItDown', category: '基础办公', summary: '把 PDF、Word、Excel 等转换为便于分析的文本。',
    requirements: '需先安装微软 markitdown-mcp；用于内容读取，不保证原排版。',
    dataUse: '本机运行，可读取文件和网址；提取内容可能交给当前模型。', source: 'https://github.com/microsoft/markitdown/tree/main/packages/markitdown-mcp',
    options: [local('本机文档解析', 'MarkItDown', 'markitdown-mcp', [])] },
  { id: 'filesystem', name: 'Filesystem', category: '基础办公', summary: '为外部工作流提供指定目录的文件读写。',
    requirements: 'VeriLex 已有工作区文件工具，通常无需重复添加；确需扩展时按官方说明限定目录并导入配置。',
    dataUse: '本机程序可读写配置目录；读取内容可能交给当前模型。', source: 'https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem' },
  { id: 'ima', name: 'ima', category: '知识协作', builtin: 'ima', summary: '检索已接入的腾讯 ima 知识库资料。',
    requirements: '需要 Client ID、API Key 和知识库读取权限；复用现有 ima 连接。',
    dataUse: '搜索词发送至 ima，所选资料片段交给当前模型；VeriLex 只读取资料。', source: 'https://ima.qq.com/agent-interface' },
  { id: 'github', name: 'GitHub', category: '知识协作', summary: '查阅代码仓库、Issues 和项目资料，默认只读。',
    requirements: '需要有目标仓库读取权限的 GitHub Personal Access Token。',
    dataUse: '查询发送至 GitHub，返回内容交给当前模型；模板只开放读取。', source: 'https://github.com/github/github-mcp-server',
    options: [remote('只读仓库与 Issues', 'GitHub', 'https://api.githubcopilot.com/mcp/', { 'X-MCP-Readonly': 'true', 'X-MCP-Toolsets': 'repos,issues' })] },
  { id: 'playwright', name: 'Playwright', category: '高级自动化', summary: '让智能体打开网页、点击和填写表单。',
    requirements: '需要 Node.js 和浏览器；首次测试可能下载官方程序。',
    dataUse: '访问目标网站，填写的内容发送至该网站；模板使用独立浏览器会话。', source: 'https://github.com/microsoft/playwright-mcp',
    options: [local('独立浏览器会话', 'Playwright', 'npx', ['-y', '@playwright/mcp', '--isolated'])] },
];

export const GUIDED_MCP_ENTRIES = ['wkinfo', 'pkulaw', 'qcc', 'tianyancha'].map(id => PLUGIN_CATALOG.find(entry => entry.id === id)!);

function sameUrl(a: string | null, b: string | null): boolean {
  if (!a || !b) return false;
  try { const left = new URL(a); const right = new URL(b);
    return left.origin === right.origin && left.pathname.replace(/\/$/, '') === right.pathname.replace(/\/$/, '')
      && left.search === right.search;
  } catch { return false; }
}
/** Match exact endpoints/commands, never a service's editable display name. Not a connection-health claim. */
export function configuredOption(option: CatalogOption, servers: readonly McpServerRecord[]) {
  return servers.find(server => option.draft.transport === 'stdio'
    ? server.transport === 'stdio' && server.command === option.draft.command && JSON.stringify(server.args) === JSON.stringify(option.draft.args)
    : server.transport !== 'stdio' && sameUrl(server.url, option.draft.url));
}
