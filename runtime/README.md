# VeriLex Local Runtime

`verilex-runtime` 是随 VeriLex 桌面应用运行的本地 FastAPI 服务。它不是互联网服务，也不应暴露到局域网或公网。

## 安全约束

- 仅允许绑定 `127.0.0.1`。
- 必须通过 `VERILEX_API_TOKEN` 提供非空启动令牌。
- 每个请求都必须携带 `X-VeriLex-Token`。
- HTTP `Host` 只接受 `127.0.0.1` 或 `localhost`。
- 浏览器跨域仅允许 VeriLex 的 localhost/Tauri 来源。
- 响应使用 `Cache-Control: no-store`。
- Runtime 请求体最多 1 MiB；Provider 响应最多读取 4 MiB。
- 日志、响应和 SQLite 不得记录令牌或模型密钥。
- API Key 只写入 Runtime 状态目录中的 `secrets.json`；POSIX 下文件权限为 `0600`、目录为 `0700`，不会写入 SQLite、日志、事件或 API 响应。
- 状态目录、`logs` 与 `workspaces` 在 POSIX 系统上使用 `0700` 权限。

## Runtime API

- `GET /v1/health`：返回版本、本地存储模式、无云后端声明和各类连接的真实数量。
- `GET /v1/providers`：列出全部非秘密 Provider 配置。
- `GET /v1/providers/{provider_id}`：读取一个 Provider。
- `POST /v1/providers/test`：测试尚未保存的连接。
- `POST /v1/providers`：测试并创建 Provider。
- `PATCH /v1/providers/{provider_id}`：更新配置、认证模式或启用状态。
- `DELETE /v1/providers/{provider_id}`：删除 Provider，并在适用时删除系统密钥。
- `POST /v1/providers/{provider_id}/test`：测试已保存的 Provider。
- `GET /v1/providers/{provider_id}/models`：发现模型。

全部端点都需要 `X-VeriLex-Token`，响应使用 `Cache-Control: no-store`。公开 Provider 对象只返回 `has_secret`，不会返回 API Key 或内部 `secret_ref`。

OpenAI-compatible 远程地址必须使用 HTTPS；仅 IPv4/IPv6 loopback 可以使用 HTTP，明文 `localhost` 会固定为 `127.0.0.1`。Ollama 首版只允许 loopback HTTP 或 HTTPS。连接测试使用 5 秒连接、20 秒总超时，不跟随重定向、不继承系统代理、不接受压缩响应，也不携带用户文件、会话或提示词。

Provider 非秘密配置使用 SQLite；启动时会验证 migration 历史、列约束和索引指纹。共享同一状态目录的多个 Runtime 通过私有锁文件串行化 Provider 更新。API Key 替换先写入新的版本化本地引用，SQLite 切换成功后才删除旧引用，避免进程中断把新凭据错误关联到旧端点。

首次测试 Ollama 时可以把 `default_model` 留空，Runtime 会返回模型目录并选择排序后的首个模型；创建和更新 Provider 时仍必须提交非空默认模型。

React 设置页已经使用这些 Provider API 提供列表、首次连接测试、安全保存、已保存连接复测和模型发现。Runtime 仍未提供案件数据库、法律数据库、MCP、Skill 或文件索引。

## 安装与运行

从仓库根目录执行：

```bash
uv venv --python 3.13.14 runtime/.venv
runtime/.venv/bin/python -m pip install -e 'runtime[dev]'

VERILEX_API_TOKEN=local-dev-token \
  runtime/.venv/bin/python -m verilex_runtime \
  --host 127.0.0.1 \
  --port 8766 \
  --state-dir .verilex-data
```

Runtime 支持 CPython 3.10–3.13；当前开发与 sidecar 打包基线为
CPython 3.13.14。项目元数据会拒绝 Python 3.14，避免在未经完整兼容性验证的
解释器上生成发布产物。

认证请求示例：

```bash
curl -H 'X-VeriLex-Token: local-dev-token' \
  http://127.0.0.1:8766/v1/health
```

创建 OpenAI-compatible Provider 的示例。模型密钥通过隐藏提示读取并经标准输入传给 `curl`，不会写入 shell 历史或进程参数；不要把真实密钥写入脚本、环境文件或 Git：

```bash
runtime/.venv/bin/python -c 'import getpass,json; print(json.dumps({"name":"Example Provider","kind":"openai_compatible","base_url":"https://models.example.com/v1","default_model":"example-model","auth_mode":"api_key","api_key":getpass.getpass("Provider API Key: ")}))' | \
curl --fail --silent -X POST \
  -H 'X-VeriLex-Token: local-dev-token' \
  -H 'Content-Type: application/json' \
  --data-binary @- \
  http://127.0.0.1:8766/v1/providers
```

运行测试：

```bash
runtime/.venv/bin/python -m pytest runtime/tests -q
```

Tauri 桌面壳会在每次启动时生成 64 字符令牌，让 Runtime 自行绑定随机回环端口，并在验证 HMAC readiness 证明后才向 WebView 注入端点。应用退出时会在截止时间内终止 Runtime 进程树；开发者通常无需手动运行上述服务。

生产桌面包通过 `npm --workspace desktop run build:sidecar` 使用 PyInstaller 生成当前平台的独立可执行文件，再由 Tauri 放入应用资源目录。生成物不提交到 Git，且必须在目标操作系统上构建。
