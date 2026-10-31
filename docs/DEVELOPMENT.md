# 开发与构建

请从仓库根目录运行下列命令。当前可交付的桌面构建目标为 Apple Silicon macOS；Windows 原生构建、完整文档后台与办公软件兼容性尚未验收。

## 环境与依赖

- Node.js 20 或更高版本。
- uv 0.12.1、CPython 3.13.14。
- Rust 1.88.0、Xcode Command Line Tools。

```bash
npm ci
uv sync --project runtime --locked --extra dev --python 3.13.14
```

不要自动更新锁文件。本仓库不包含模型密钥、私人配置、真实案件材料或本机构建缓存。

## 准备 Word 编辑引擎

从 [v1.1.0 发布页](https://github.com/Riven-Wood/VeriLex/releases/tag/v1.1.0)下载 `VeriLex-1.1.0-word-renderer.tar.gz`，在仓库根目录解压：

```bash
tar -xzf VeriLex-1.1.0-word-renderer.tar.gz
```

归档中的 JS/WASM 文件落在 `desktop/src-tauri/generated/documents-macos-arm64/renderer/`；目录沿用构建脚本的名称，其中仅有浏览器资源。前端构建会依据 `desktop/src/documents/renderer-lock.json` 校验完整目录及关键文件，校验失败会停止，不从 CDN 临时替换。

该资源由锁定的 Docxodus 源码和本仓库补丁生成。上游提交、完整许可证及构建输入分别见 `third_party/documents/sources.lock.json`、`third_party/licenses/documents/`、`desktop/patches/docxodus-keyboard.patch`。从上游重建完整文档包使用 `npm run build:documents`，仅支持 macOS arm64，并要求预先准备脚本中校验的 .NET、NuGet、LibreOffice 和 Python 资源；这不是普通 `npm ci` 自动完成的步骤。

## 开发运行

```bash
VERILEX_SERVER_BIN="$(pwd)/runtime/.venv/bin/verilex-runtime" npm run tauri -- dev
```

建议通过 `VERILEX_STATE_DIR` 指定独立的测试数据目录，不要拿真实案件或日常使用数据做测试。运行 AI 功能需在应用中自行配置模型；项目不附带服务商账号或额度。

## 构建 macOS 应用

首次构建本机后台会取得校验过的 Node 运行时并打包插件。已有缓存时复用相同锁定文件。

```bash
npm run build
npm --workspace desktop run build:sidecar
npm run tauri -- build --target aarch64-apple-darwin --bundles app --config '{"build":{"beforeBuildCommand":""},"bundle":{"createUpdaterArtifacts":false}}'
```

结果在 `desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/VeriLex.app`。上述步骤不生成在线更新签名，不要求接触发布私钥。开发者应自行核对应用签名和首次打开行为。

PDF 转换依赖系统已安装的 LibreOffice；浏览器编辑资源不包含 LibreOffice。完整文档后台 overlay 是单独的构建路径，不能将其 Mac 二进制复制到 Windows 使用。

## 验证

```bash
npm run check:release-version -- v1.1.0
npm test
runtime/.venv/bin/python -m pytest runtime/tests -q
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml --target aarch64-apple-darwin
```

本仓库保留已知未通过的回归测试，并在 [版本验证记录](RELEASE_VALIDATION.md)中披露，不把首次发布解释为所有功能、模型和平台均已通过验收。`docs/qa/`中的文档引擎报告及合成语料记录是历史证据，不是当前版本完整验收结果。

Windows 开发需要 MSVC C++ 工具与 WebView2。文档后台构建脚本、系统终端、文件描述符路径、LibreOffice 路径、快捷键及通知均需要分别适配与真机验证。不要关闭文件权限或完整性检查来绕过平台差异。
