# VeriLex

面向法律工作的本地优先 AI 桌面应用。

VeriLex 将对话、文件、合同审查、诉讼材料和文书编辑放在同一个工作区中。可以连接兼容 OpenAI 接口的模型服务或本机 Ollama，通过所选模型和工具完成工作；不需要 VeriLex 账号。

**首版 v1.0.0 · 源码公开，仅限非商业用途 · 当前安装包支持 Apple Silicon Mac**

[下载首版](https://github.com/Riven-Wood/VeriLex/releases/tag/v1.0.0) · [安装说明](docs/install-macos.md) · [开发说明](docs/DEVELOPMENT.md) · [报告问题](https://github.com/Riven-Wood/VeriLex/issues)

## 主要功能

- **合同审查**：导入 Word 合同，填写审查要求，查看原文、审查意见和批注，编辑副本并导出成果。
- **诉讼工作**：围绕案件文件开展事实梳理、证据整理、文书起草和案件讨论，制作证据目录、证据册及诉讼图表。
- **Word 编辑**：在应用内编辑正文和常见表格，调整文字与段落格式，查找替换、撤销重做；AI 修改建议经人工采用后进入正文。
- **法律与案例检索**：使用联网检索、已连接的法律数据服务或知识库，并保留可核对的来源。
- **资料与日程**：Markdown 知识库、Word 文件脱敏、案件日程和本机定时任务。
- **通用助手**：多轮对话、附件、文件预览、浏览器与受权限控制的文件和终端操作。
- **扩展能力**：管理技能和 MCP 外部工具，接入已有 Excel、PPT、PDF 页面处理插件。

模型服务与第三方数据服务需要自行配置，相关费用由对应服务商收取。AI 生成内容、法律依据、脱敏结果及最终文书须由使用者核对。

## 安装与使用

1. 在[发布页](https://github.com/Riven-Wood/VeriLex/releases/tag/v1.0.0)下载 `VeriLex-1.0.0-macos-arm64.zip`。
2. 解压后将 `VeriLex.app` 拖入“应用程序”，再双击打开。
3. 在“设置 → 模型”添加模型连接，填写自己的密钥并测试连接。
4. 选择工作目录或相应法律工作入口，添加材料后开始使用。

安装包面向 Apple Silicon（M 系列芯片）Mac，声明最低系统版本为 macOS 12。当前验证环境不代表所有旧系统均已实测。应用未作 Apple Developer ID 签名与公证，首次打开的系统提示请参照[安装说明](docs/install-macos.md)。首版通过发布页手动下载安装，未提供应用内自动更新包。

Windows 源码适配仍需继续验证；本次不提供 Windows、Intel Mac 或 Linux 安装包。不要将 GitHub 的 Source code 压缩包当作可直接安装的应用。

## 数据与权限

会话、配置和工作记录保存在本机。选择云模型、联网检索或外部工具时，完成任务所需的内容会发送到所选服务；“本地优先”不意味着云模型调用离线进行。密钥在本机保存，不随项目源码或安装包分发。

知识库当前只接收 Markdown 文件，文件脱敏当前限于 Word。PDF 页面插件提供合并、拆分和提取页面，不提供 OCR 或 PDF 脱敏。Word 的复杂对象、跨软件排版和中文输入法仍有兼容性边界；重要文件请保留原件，并在目标办公软件中检查交付结果。

## 开发

前端使用 React、TypeScript 和 Tauri 2；本机后台使用 Python、FastAPI 与 SQLite。依赖版本由 `package-lock.json`、`runtime/uv.lock` 和 `desktop/src-tauri/Cargo.lock` 锁定。

源码构建需要额外准备浏览器 Word 引擎资源；完整起步步骤、验证命令和平台限制见[开发说明](docs/DEVELOPMENT.md)。本次发布的检查结果及已知问题见[首版验证记录](docs/RELEASE_VALIDATION.md)。

## 许可证

VeriLex 的原创代码采用 [PolyForm Noncommercial License 1.0.0](LICENSE)，仅许可协议允许的非商业用途。商业用途（包括商业业务中的使用）不因免费取得软件而自动获准，具体范围以许可证原文为准。

这是带非商业限制的源码公开项目，不属于 OSI 定义的开放源代码许可证。第三方依赖及改编代码继续适用各自许可证，完整署名见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，原始许可文件保留在对应目录中。
