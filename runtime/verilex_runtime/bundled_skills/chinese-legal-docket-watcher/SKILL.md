---
name: chinese-legal-docket-watcher
description: "为案件期限监控任务提供法律工作流程和上游参考资料。"
verilex-category: 案件管理与期限
---

# 案件期限监控

这是 VeriLex 为公开上游材料编写的入口。使用 skill_read 的 path 参数读取本技能下的配套文本；先读取 upstream/README.md，再按问题需要选择下列材料。外部服务、脚本和连接器须单独配置并遵循现有权限。核对法律依据和事实后再形成结论。

- `upstream/README.md`
- `upstream/agent.yaml`
