# 样例输出说明

使用 `RUN_AGENTTEAMS_PACKAGE.ps1 -Mode Validate` 时，标准输出应包含：

```text
validated AgentTeams agentteams.io/v1beta1: 1 Team, 5 Workers, 1 Human
AgentTeams v6 package validation and deterministic build succeeded.
```

同时，`infra/agentteams/generated/packages-v6/` 会得到五个 Worker ZIP，`SHA256SUMS.txt` 可核验其摘要。

该输出只说明本地资源、合同和角色包可复现构建。它不表示 AgentTeams、Matrix、RocketMQ、浏览器/搜索工具、模型、计费或真实业务预测已经完成 Live 验收。
