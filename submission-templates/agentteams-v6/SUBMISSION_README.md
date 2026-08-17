# LaunchScope AgentTeams v6 可执行代码包

本包提交的是 LaunchScope 的 AgentTeams v6「主管 + 4 专家」代码、配置和可再生成的 Worker 包。五个角色为：`evaluation-manager`、`user-evidence`、`product-engineering`、`business-investment` 与 `evidence-auditor`。资源定义禁止 Worker 间自由提及；业务状态只由控制平面的 PostgreSQL 提交。

## 包内内容

- `RUN_AGENTTEAMS_PACKAGE.ps1`：唯一运行入口，支持包验证、Recorded 启动和受控 Live 启动。
- `apps/`、`packages/`、`infra/`、`scripts/`、`tests/`：可复核的运行源代码、合同、基础设施和测试。
- `infra/agentteams/resources/launchscope-team-v6.yaml`：五个 Worker、一个 Team、一个 Human 的 AgentTeams v1.2 资源。
- `infra/agentteams/generated/packages-v6/*.zip`：由 `scripts/build-agentteams-packages.py --generation v6` 从同包源代码生成的五个角色包。
- `.env.demo.example`：脱敏配置模板；本包不包含 API Key、Matrix Token、数据库密码、对象存储密钥或真实业务数据。
- `sample-input/`、`sample-output/`：合成的 Recorded 样例及其使用边界。
- `图文使用指南.md`：可离线阅读的中文上手手册，附架构、运行模式和包结构三张 SVG 示意图。
- `SHA256SUMS.txt`：运行入口、资源文件和五个 Worker ZIP 的 SHA-256 校验清单。

首次使用请先阅读 [图文使用指南](图文使用指南.md)。

## 依赖与安装

Windows PowerShell 7、Python 3.11–3.13、Node.js 20.19+、pnpm 11.9 和 Docker Desktop with Compose。

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]" -e packages/domain -e packages/contracts -e packages/skills -e packages/observability -e apps/api -e apps/orchestrator -e apps/worker
pnpm.cmd install --frozen-lockfile
```

从模板建立本机私有配置，填写真实环境所需凭据；不要将该文件重新打包或提交：

```powershell
Copy-Item .env.demo.example .env.demo.local
```

## 运行入口

先在解压后的包根目录执行无外部调用的结构与产物验证：

```powershell
pwsh -File .\RUN_AGENTTEAMS_PACKAGE.ps1 -Mode Validate
```

该命令会重新构建 v6 Worker ZIP，并验证 1 个 Team、5 个 Worker 和 1 个 Human 的配置与合同一致性。

Recorded 模式可启动本地标注演示，不会启动 AgentTeams / Matrix / RocketMQ 桥，也不会调用模型或搜索：

```powershell
pwsh -File .\RUN_AGENTTEAMS_PACKAGE.ps1 -Mode Recorded -Bootstrap -NoBrowser
```

Live 模式需要 `.env.demo.local` 中的 AgentTeams、Matrix、RocketMQ、PostgreSQL、对象存储和模型网关配置，以及一个已授权且具备成本边界的外部案例：

```powershell
pwsh -File .\RUN_AGENTTEAMS_PACKAGE.ps1 -Mode Live -NoBrowser
```

Live 启动会先执行 preflight。没有授权案例时，正确结果是 `BLOCKED_NO_AUTHORIZED_CASE`；出现 `SUBMISSION_UNKNOWN`、未知用量或未知账单时，系统会进入 `NEEDS_ATTENTION`，不会自动重试、切换模型或手工结算。

## 样例与证据边界

`sample-input/recorded-agentteams-package-case.json` 是合成、无个人数据、禁止网络和外部写操作的 Recorded 验证选择器。它用于复现包结构和本地测试边界，并不是生产输入，也不是模型预测准确率或真实 AgentTeams E2E 证据。

本包的 `execution-evidence/` 在打包后保存了本次构建和测试输出。它只能证明代码/资源包可构建且测试通过；真实 Live 验收仍需对每个 Ticket、Matrix 回执、模型用量、账单、对象完整性及报告 SHA-256 作 Run 级核验。
