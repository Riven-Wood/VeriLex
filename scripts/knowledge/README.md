# 知识库离线基线

在 Source 运行：

```sh
runtime/.venv/bin/python scripts/knowledge/baseline.py
runtime/.venv/bin/python -m pytest runtime/tests/knowledge -q
```

只生成内存中的100篇合成资料和60个预标问题，不读用户知识目录，不使用应用数据库、模型或网络。80篇属于所选库，20篇是未选库干扰。输出含每题预期/实际文件ID、召回比例、耗时、数据分组；前半调试题、后半保留题。合成内容不是真实法条/案例，也不验证法律分析能力。所有题目现可见，保留题仅表示评估分组，不能宣称盲测或独立人工标注。

当前搜索为整段字面匹配：三字以上走FTS5候选并逐字复核，一至二字走字面扫描。范围在排序和限量前过滤，空范围不搜索；输出 truncated 标志。按文档ID稳定排序，不宣称实现相关性排序。所有扫描都仅用于固定小语料；未设生产时间/扫描预算，不能直接注册为Agent工具。

退出0只表示精确题全部命中、不存在词题均无命中，并完成原字节回放断言；不表示自然语言或多资料题达标。当前后两组均零命中，后续模型检索须使用相同问题和范围，并另作人工相关性复核，不可将预标答案反馈给模型扩词来制造高分。

`runtime/verilex_runtime/knowledge/source.py` 是独立基础组件：严格UTF-8不可变快照、内容版本、已读范围和连续引文。偏移为从0开始的Unicode码点、右边界不包含，行号从1开始，CRLF为一个换行；BOM保留并计入偏移。前端未来不得将UTF-16偏移直接传入。无效编码和改写文字不会被自动修复。

ReadReceipt 是受信任服务内部值，不是公开身份凭证；未实现路径授权、选库授权、读取撤销、API、持久化或前端。不得把模型提交的JSON直接反序列化为receipt。后续服务需通过服务器持有的随机ID解析已读记录并核验本轮权限，再调用基础组件。历史quote只保留片段和版本，不包含可重建整篇旧文的承诺。


容量探测：`runtime/.venv/bin/python scripts/knowledge/capacity.py` 使用临时合成100/1000/5000篇Markdown，不调用模型。结果与限制见docs/research/2026-09-11-knowledge-links-capacity.md。

## 已授权的真实 DeepSeek 验收

`deepseek_eval.py PROVIDER_ID NEW_OUTPUT_DIRECTORY BUDGET_LEDGER --questions natural-12` 使用应用中已配置的DeepSeek连接，只发送上述合成资料。该命令会产生真实费用，须有明确授权；不是离线测试。ProviderService内部获取凭据，不在脚本或报告中输出密钥。每批最多60次HTTP和1美元保守预留；同一授权批次必须串行运行并共用账本，不得换路径重置额度。报告的completed仅指模型结束，零quote不算精确引用成功。当前真实结果未通过，见docs/research/2026-09-11-deepseek-ima-acceptance.md。

## 2026-09-12：版本化模型验收与离线复核

以上 LiteralIndex 是独立历史基线；生产知识库已经实现选库授权、服务器持有的读取凭据、撤销、API 和原文卡，不能把基线脚本的能力限制当作当前产品状态。最新真实模型记录见 `docs/research/2026-09-12-deepseek-acceptance.md`，ima 真实账号验收已暂停。

`deepseek_eval.py` 增加 `--suite v1|v2`（默认 v1）。v1 保留原题和标签；v2 题号加 `v2-`，多资料题明确只比较笔记，精确/自然语言题明确分别引用 case/law/book/note 四类。v2 与旧题不构成相同提示的 A/B，不得据此声称提示或检索改动独立改善质量。指定题号时应与 suite 一致。预标文件 ID 不注入模型；原文卡在模型完成后独立评分。

新增 `evidence_score` 分列实际读取、有效引文、缺少/多余文件、版本/范围/文字保真、文档召回和精度；无结果题只有零卡且回合完成才通过。没有卡不算精确引用成功，完成不等于证据通过。旧 `all_required_quoted` 兼容字段仅表示集合包含关系，不再作为验收通过标志。自由回答中的引文和分析仍需另外检查，不能用卡片分数代替。

复核历史报告无需网络、模型、密钥或应用数据库：

```sh
runtime/.venv/bin/python scripts/knowledge/audit_evidence.py docs/research/2026-09-12-deepseek-evidence/round1.json
```

可加 `--output NEW_FILE`，已有文件拒绝覆盖。只复核冻结 v1 合成语料，保留原标签和原报告哈希，不根据模型选择更换答案。三组复核证据已保存为 `docs/research/2026-09-12-deepseek-evidence/offline-audit-v2.json`。本轮未运行 v2 真实模型；任何后续真实调用仍受原授权额度及共同账本约束。

全面验收增加 `--suite long --questions long-1 long-2`：4 MiB以上合成书籍、目录与正文重名，按目标完整句核对 `required_passage_quoted`，不能只看文件命中。`long-0` 为书首、`long-1` 为中部、`long-2` 为末尾。`--max-requests`、`--max-usd` 可进一步缩小授权上限，不能超过默认60次/1美元保守预留；仍须先获用户授权。预算阻断单独写入 `evaluation_stop_reason`，避免把SDK包装出的连接错误误判成供应商网络故障。不同授权批次可有独立账本，同批次所有题必须共用，未用完/失败也不能重置。


合成真实模型验收报告新增model_attempts：记录每次响应的文本与工具调用，用于区分展示前门禁误拦与真实改写，包含被产品拒绝的草稿。仅适用于本脚本固定合成语料，不记录密钥、headers或推理内容。历史report不补写、不覆盖。



旧的强制二次审阅与对应固定矩阵脚本已移除。历史报告保留审计用途，不能作为当前执行流程。
