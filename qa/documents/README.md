# Word 工作台 QA 语料

本目录只保存 VeriLex 自行生成的合成文档、机器清单与字体来源锁。不得放入客户文件、真实案件材料、Codex Documents 资源或来源不明的文档。

- `fixtures/manifest.json` 记录每个夹具的 SHA-256、来源、用途和结构计数。
- `scripts/documents/generate_fixture_corpus.py` 可确定性重建全部 DOCX 夹具。
- `fonts/fonts.lock.json` 只锁定 Task 1 将随包验证的字体来源与哈希；字体二进制不进入 Git。

引擎门禁先验证固定提交和这些夹具，再分别运行 Docxodus 主路线、Adeu 后备路线和 Adeu 辅助路线，最后生成只含哈希、计数、状态和错误码的报告；报告不得包含正文或绝对路径。
