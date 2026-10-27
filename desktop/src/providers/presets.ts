/** Official mainland API endpoints checked 2026-10-12. See the feature spec for sources. */
export interface ProviderPreset {
  id: string;
  name: string;
  mark: string;
  subtitle: string;
  baseUrl: string;
  consoleUrl: string;
  docsUrl: string;
  instructions: string;
  modelHint: string;
  modelRequired?: boolean;
}

export const PROVIDER_PRESETS: readonly ProviderPreset[] = [
  {
    id: 'deepseek', name: 'DeepSeek', mark: 'D', subtitle: '深度求索',
    baseUrl: 'https://api.deepseek.com',
    consoleUrl: 'https://platform.deepseek.com/api_keys',
    docsUrl: 'https://api-docs.deepseek.com/zh-cn/',
    instructions: '登录 DeepSeek 开放平台，在 API keys 页面创建密钥，再复制到下方。',
    modelHint: '默认读取平台返回的模型列表。',
  },
  {
    id: 'qwen', name: '通义千问', mark: '千', subtitle: '阿里云百炼 · 北京',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    consoleUrl: 'https://bailian.console.aliyun.com/',
    docsUrl: 'https://help.aliyun.com/zh/model-studio/get-api-key/',
    instructions: '登录阿里云百炼，选择北京地域，在密钥管理中创建普通 API Key。',
    modelHint: '使用北京地域的普通 API 密钥。业务空间专属地址可通过“其他连接”配置。',
  },
  {
    id: 'glm', name: '智谱 GLM', mark: '智', subtitle: '智谱开放平台',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    consoleUrl: 'https://bigmodel.cn/usercenter/proj-mgmt/apikeys',
    docsUrl: 'https://docs.bigmodel.cn/cn/guide/develop/openai/introduction',
    instructions: '登录智谱开放平台，在 API Keys 中创建密钥，并从模型页面复制已开通的模型标识。',
    modelHint: '填入平台显示的完整模型标识，区分大小写；不要填写模型的中文名称。',
    modelRequired: true,
  },
  {
    id: 'kimi', name: 'Kimi', mark: 'K', subtitle: '月之暗面',
    baseUrl: 'https://api.moonshot.cn/v1',
    consoleUrl: 'https://platform.moonshot.cn/console/api-keys',
    docsUrl: 'https://platform.kimi.com/docs/guide/start-using-kimi-api',
    instructions: '登录 Kimi 开放平台，在 API Key 管理中创建密钥，再复制到下方。',
    modelHint: '使用开放平台 API 密钥，网页版会员与编程套餐不等同于普通 API 额度。',
  },
  {
    id: 'doubao', name: '豆包', mark: '豆', subtitle: '火山方舟 · 北京',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    consoleUrl: 'https://ark.volcengine.com/region:cn-beijing/apikey',
    docsUrl: 'https://docs.volcengine.com/docs/ark/quick-start',
    instructions: '登录火山方舟，在北京地域创建 API Key，并开通需要使用的文本模型。',
    modelHint: '从控制台示例代码复制已开通文本模型的 Model ID，不要填写控制台网页地址。',
    modelRequired: true,
  },
  {
    id: 'minimax', name: 'MiniMax', mark: 'M', subtitle: 'MiniMax 国内开放平台',
    baseUrl: 'https://api.minimax.cn/v1',
    consoleUrl: 'https://platform.minimax.cn/',
    docsUrl: 'https://platform.minimax.cn/docs/api-reference/text-openai-api',
    instructions: '登录 MiniMax 国内开放平台，创建普通 API 密钥，并复制账户可用的文本模型标识。',
    modelHint: '填写普通 API 支持的模型标识。仅限 M Plan 的模型需要对应套餐，不适用此预设。',
    modelRequired: true,
  },
];
