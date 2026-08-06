# LaunchScope V0.1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在已冻结的势能引擎架构上，按一个可复验的纵向闭环实现社区版 V0.1：提交产品材料、诊断缺口、主动补问、确认产品画像、执行 1+5 多 Agent 评审、沉淀证据、生成决策报告，并用同一套标准完成 V1/V2 版本复验。

**Architecture:** 采用 Next.js/TypeScript Web 与 Ops、Python/FastAPI 模块化单体控制平面、独立 Orchestrator 和隔离 Worker。PostgreSQL 是业务状态唯一事实源；MinIO/OSS 保存不可公开访问的证据对象；RocketMQ 承载可靠命令和领域事件；AgentTeams/Matrix 只负责协作，不直接写业务状态。所有外部副作用默认关闭，工具调用经双网关、短期凭据、预算、审批和审计控制。

**Tech Stack:** Next.js + TypeScript；Python + FastAPI + Pydantic；PostgreSQL/PolarDB PostgreSQL；MinIO/OSS；RocketMQ；Nacos；Higress；AgentTeams/Matrix；OpenAPI + JSON Schema；OpenTelemetry/LoongSuite + AgentScope Studio；Docker Compose。

---

## 0. 本计划的边界、阅读依据与执行规则

本文件是实施计划，同时承载本轮要求的领域模型、ER 图、状态机、模块边界和安全模型。当前仓库没有业务代码；以下 apps、packages、infra、tests 路径均是后续实现时的准确目标路径，不代表它们已经存在。

当前工作只允许产生本计划文件及其父目录：

- 允许创建：docs/plans/2026-08-05-launchscope-v0.1.md
- 不允许修改：docs/势能引擎技术架构基线_V1.0.md
- 不允许修改：reference/ 下两份材料
- 不允许编写：apps/、packages/、infra/、tests/ 下的业务或基础设施代码
- 不允许执行：Git add、commit、push、分支切换、部署、付费外部调用

领域词汇按本文件第 2 节作为当前上下文；未来若仓库增加独立 CONTEXT.md，应以该词汇表为初始内容，不得在实现中重新发明同义概念。凡是改变冻结边界的决定，都必须先创建 ADR，说明原因、影响、迁移和回滚，不得静默修改基线。

### 0.1 已完整读取的材料

| 材料 | 读取范围 | 本计划中的用途 |
|---|---:|---|
| docs/势能引擎技术架构基线_V1.0.md | 35 个章节，685 行 | 唯一架构基线、领域边界、状态、组件边界、V0.1 验收门 |
| reference/goai-Agent-Infra（新智基座）赛道要求.txt | 324 行 | 赛道必选能力、复赛/决赛工程证据、评审维度 |
| reference/势能引擎_AgentInfra赛道实施方案_V2.0_优化版.docx | 主文档 637 个段落、31 个表格 | 主案例流程、1+5 Agent 角色、Skill 清单、Demo 验收和分工建议 |

本次阅读时的 SHA-256 记录如下，用于本轮结束时确认参考材料未被改写：

| 文件 | SHA-256 |
|---|---|
| docs/势能引擎技术架构基线_V1.0.md | D8676E84B8386CCFE84871CF411ED93C36A55E03545C71FE66CC547D6FB6EA3A |
| reference/goai-Agent-Infra（新智基座）赛道要求.txt | 9EC4DAD658924845B2946E453056780DA49DE9EB98EC3022C83653A5A0EF685E |
| reference/势能引擎_AgentInfra赛道实施方案_V2.0_优化版.docx | 6D81B511552354BDFF56497C39E8F060257694BD4D553A660C303D9E7ED4A78D |

### 0.2 冻结约束到工程证据的追踪表

| 冻结约束 | 来源 | 实施约束 | 必须留下的证据 |
|---|---|---|---|
| PostgreSQL 是业务状态唯一事实源 | 基线 §3、§10、§21，行 44-52、235-250、431-437 | API 控制平面独占状态提交；Matrix、RocketMQ、Worker 只能提交变更请求 | 数据库状态、Outbox、Inbox、状态提交审计 |
| 模块化单体 + 独立 Worker | 基线 §5.2-5.3，行 103-135 | API 模块禁止跨表直写；长任务进入 Worker Pool | 进程拓扑、模块依赖检查、Worker lease/checkout 日志 |
| 租户隔离贯穿全链路 | 基线 §7，行 156-171 | 所有表、对象、消息、向量、Trace、权限带 tenant_id；共享库默认 RLS | 两租户互读/互写失败测试、RLS SQL、对象路径和 Trace 标签 |
| 1+5 Agent 与严格职责 | 基线 §8，行 173-190；赛道要求行 78-95 | Evaluation Manager + 五个专业 Agent；Agent 只能提交 Finding/状态请求 | Agent Identity 清单、Team/Room、结构化交接、权限拒绝日志 |
| 固定阶段门 + 动态 DAG | 基线 §9，行 192-233 | 10 个固定阶段；阶段内任务声明依赖、Agent、Skill、预算、超时、成功条件、证据要求 | Run Manifest、DAG、阶段转移日志、Harness 回归报告 |
| Matrix/RocketMQ/PostgreSQL 三权分离 | 基线 §10，行 235-250 | Matrix 不改状态；RocketMQ 不携带完整聊天记录；事务状态 + Outbox 发布 | 事件 envelope、Consumer Inbox、重复消息去重证据 |
| 证据/结论/审批不可覆盖 | 基线 §13，行 283-318 | 原始 Evidence、Agent 输出、Approval、AuditEvent 追加写；新版本用 supersedes_id | append-only 约束、Finding→Evidence 链、版本差异报告 |
| 记忆先过滤再检索，长期写入受控 | 基线 §14，行 320-331 | 只允许 MemoryCandidate；先过滤租户/项目/版本/地域/时间/权限，再做全文/向量检索 | RAG 查询过滤日志、候选审批、过期策略、跨租户拒绝 |
| Skill 是版本化一等对象 | 基线 §15，行 333-358；赛道要求行 96-114 | 每个 Skill 有 schema、前置条件、权限、失败分类、预算、测试和回滚 | Skill manifest、契约测试、样例、版本哈希 |
| MCP 优先、统一 Tool Contract | 基线 §16，行 360-371 | Agent/Skill 只依赖端口；MCP/内部 Adapter 只替换传输层 | 工具 schema、鉴权/幂等/审计字段、Adapter 迁移测试 |
| 默认只读、风险分级、人工审批 | 基线 §2、§18-20，行 19-40、384-429 | 公开研究/认证研究/外部动作分级；高风险动作先预览再一次性审批 | ApprovalRequest、参数哈希、短期 token、审批/拒绝/过期日志 |
| fail-closed 与 SUBMISSION_UNKNOWN | 基线 §3、§17、§22，行 42-53、373-382、439-449 | 未知提交、有副作用或费用不明立即冻结；不重试、不切换、不补偿 | 故障分类、冻结状态、无二次提交证明 |
| 规则决定等级和阻断，模型负责解释 | 基线 §23，行 451-463 | 规则引擎输出维度等级/阻断；模型只能形成解释性报告 | RuleEvaluation、Finding/Evidence 展开、阻断用例 |
| 社区版交付一个完整纵向闭环 | 基线 §31，行 573-603 | 不扩展行业模板、自动发布、生产写入、复杂计费和多区域 HA | README 启动、样例材料、真实只读工具、V1/V2 E2E 记录 |
| 赛道要求真实 Agent 协同、Skill、至少两项上下文能力 | 赛道要求行 78-126 | 实际展示 AgentTeams、Skill、项目记忆 + 轨迹观测；不能用预制报告冒充工具执行 | Team/Worker 轨迹、Skill 调用、RAG/Memory 检索、Trace/Log/Metrics |

赛道材料中的建议如果与基线冲突，以基线为准。例如 V2.0 方案列出的 user-validation-designer 未进入基线 §15 的六个 P0 Skill 名单；本计划不静默把它提升为第七个 P0，见第 7.2 节的处理门。

## 1. V0.1 纵向闭环与非目标

### 1.1 交付闭环

~~~text
授权与提交
  -> 材料隔离、哈希和类型校验
  -> product-intake-normalizer 形成 ProductProfile
  -> intake-gap-diagnosis 生成缺口和 3—5 个优先问题
  -> 用户补问并确认 ProductProfile
  -> Harness 冻结 RunManifest、预算、权限、Skill、模型和评测标准
  -> 1 个 Manager + 5 个专业 Agent 并行执行固定 DAG
  -> 浏览器/搜索/代码只读工具产生 Evidence
  -> Evidence Auditor 通过、降级、驳回或要求补证
  -> 规则引擎形成四维等级和阻断
  -> Decision/Report 生成并写入 Product Dossier
  -> 产品修改为 ProductVersion V2
  -> version-regression-verification 使用同一标准复验
  -> 报告展示已解决、未解决、新风险和下一轮 1—3 个行动
~~~

### 1.2 四个判断维度

| dimension_code | 判断问题 | 首批主要 Agent | 典型证据 |
|---|---|---|---|
| PRODUCT_IMPLEMENTATION | 产品能否真实可用并稳定交付 | Product Engineering Agent | 产品操作、代码结构、部署说明、错误截图 |
| USER_USAGE | 目标用户是否真实需要并持续使用 | User Evidence Agent | 用户确认材料、真实访谈/试用记录；AI 模拟必须标为模拟 |
| BUSINESS_INVESTMENT | 是否值得继续投入并形成业务 | Business Investment Agent | 价格、成本、渠道、经营数据、竞品来源 |
| GEO_POLICY_TREND | 当前地区和时间窗口是否合适 | Geo Policy Trend Agent | 一手政策、平台规则、发布日期、抓取时间、适用地区 |

维度不做简单平均。硬性合规阻断、核心功能不可用、证据不足等规则可以覆盖其他优势；缺少真实数据时输出 INSUFFICIENT_EVIDENCE 或 HYPOTHESIS，不生成“爆款概率”。

### 1.3 V0.1 明确不做

- 不自动发布页面、联系客户、发送邮件、发送问卷、写入生产数据库或购买付费能力。
- 不执行任意仓库脚本、任意 SQL、任意 Shell 或任意 HTTP。
- 不把 AI 模拟用户、自动浏览、搜索摘要当作真实用户、真实留存或真实付费证据。
- 不实现大量行业模板、复杂企业计费、多区域高可用、全功能 Platform Ops 或 AgentLoop 深度托管。
- 不在普通 CI 中执行付费模型/搜索/浏览器调用；真实只读 E2E 需测试环境、固定预算和明确授权。

## 2. 领域模型

### 2.1 统一词汇表

| 术语 | Canonical 定义 | 不应使用的含义 | 主要拥有者 |
|---|---|---|---|
| Tenant | 数据、对象、消息、向量、Trace 和权限的隔离边界 | 仅用于登录的组织名 | Identity & Tenant |
| Workspace | Tenant 内的协作空间和成员权限范围 | 业务项目本身 | Identity & Tenant |
| Project | 一个持续验证的产品档案根；V1/V2/V3 共用 project_id | 一次运行或一份报告 | Project Dossier |
| ProductVersion | Project 的一次明确产品状态和材料集合 | 可覆盖的当前版本字段 | Project Dossier |
| ProductProfile | 根据材料和用户确认得到的产品画像快照 | 模型临时猜测 | Project Dossier |
| EvaluationRun | 针对一个 ProductVersion、目标和标准的一次可复验评审 | 后台进程或聊天房间 | Evaluation |
| RunManifest | Run 启动时冻结的材料、标准、Agent、Skill、Prompt、模型、工具、预算、安全策略及哈希 | 可在运行中随意修改的配置 | Evaluation |
| Stage | 固定阶段门中的一个阶段 | 任意 Agent 的临时状态 | Evaluation |
| Task | Stage 内有依赖、执行者、Skill、预算、超时、成功条件和证据要求的可审计工作单元 | 一段提示词 | Evaluation |
| Agent Identity | Agent 的身份、职责、输入/输出、权限、工具白名单和协作关系 | 模型名称 | Agent Orchestration |
| Skill | 可复用、版本化、可测试的任务能力包 | 一次性 Prompt | Skill Registry |
| Tool | 通过 MCP 或 Adapter 暴露的外部能力端口 | Agent 任意拼接的 HTTP/SQL/Shell | Tool Gateway |
| Evidence | 经过来源、范围、时间、哈希和信任等级记录的原始或派生证据 | 搜索结果摘要或无来源断言 | Evidence |
| Hypothesis | 尚未由足够证据支持的可验证假设 | 被包装成事实的模型猜测 | Evidence / Decision |
| Finding | Agent 基于 Evidence 对一个维度或假设提交的追加式发现 | 最终状态或最终报告 | Evidence |
| ConflictRecord | 记录不同 Finding、来源或版本之间冲突的事实 | 用后来的结论覆盖旧结论 | Evidence |
| Evidence Auditor | 独立校验证据、冲突、时效和越权的 Agent | 可以改写原始 Finding 的管理员 | Evidence |
| Decision | 规则和经审计 Finding 形成的阶段建议及阻断集合 | 模型自由给出的分数 | Decision & Report |
| Report | 面向用户的可读解释和证据展开视图 | 业务事实唯一来源 | Decision & Report |
| Product Dossier | Project 的当前投影视图和可追溯历史入口 | 只保留最后一次运行的快照 | Project Dossier |
| MemoryCandidate | Agent 提议写入长期项目记忆的候选内容 | Agent 直接写入长期记忆 | Memory & RAG |
| MemoryItem | 经过用户确认或证据校准后可检索的项目事实/结论 | 未经授权的跨租户知识 | Memory & RAG |
| ApprovalRequest | 绑定 Run、Tool、参数哈希、影响范围、费用、过期时间的一次性审批请求 | 永久通行证 | Policy & Approval |
| UsageRecord | Run/Task/Skill/Tool/模型的 Token、耗时、费用和预算消耗记录 | 计费订单 | Usage & Quota |
| AuditEvent | 操作者、主体、资源、动作、结果、原因、关联 ID 和哈希的不可变安全记录 | 普通调试日志 | Audit & Compliance |

### 2.2 聚合、写入所有者和不变量

| 聚合根 | 所有的核心对象 | 单表单写入所有者 | 必须保持的不变量 |
|---|---|---|---|
| TenantAccess | Tenant、Workspace、Membership、RoleBinding | Identity & Tenant | 每个资源访问都解析到 tenant_id；Platform Operator 默认不能读租户正文 |
| ProjectDossier | Project、ProductVersion、Material 元数据、ProductProfile、Dossier projection | Project Dossier | Project 在 V1/V2 间稳定；ProductVersion 和用户确认画像可追溯；对象正文不存 PostgreSQL |
| Evaluation | EvaluationRun、RunManifest、Stage、Task、TaskDependency | Evaluation | Run 启动前 Manifest、预算和权限必须冻结；固定阶段不能跳过；状态只有控制平面提交 |
| AgentExecution | AgentIdentity、AgentAssignment、Lease、SkillInvocation、ToolInvocation | Agent Orchestration / Worker 通过命令请求 | Worker 不能改变最终 Run 状态；同一幂等键不得重复执行有副作用调用 |
| EvidenceReview | Evidence、Finding、FindingEvidence、ConflictRecord | Evidence | Evidence/Finding 追加写；Finding 无证据只能是 HYPOTHESIS；Auditor 不能改写原始结果 |
| DecisionReport | Decision、DecisionFinding、Report | Decision & Report | 规则产生等级/阻断，模型只解释；报告关键判断可展开到 Finding/Evidence |
| Memory | MemoryCandidate、MemoryItem、RAGRetrieval | Memory & RAG | 先做租户/项目/版本/地域/时间/权限过滤，再做相似度排序；模拟意见保留模拟标签 |
| PolicyUsageAudit | ApprovalRequest、UsageRecord、AuditEvent | Policy & Approval / Usage & Quota / Audit & Compliance | 审批令牌一次性且绑定参数哈希；预算不足暂停；安全事件不可覆盖 |
| IntegrationDelivery | OutboxMessage、InboxMessage、EventDeliveryAttempt | Integration | 状态事务与 Outbox 同事务；Consumer Inbox 唯一去重；事件携带完整关联和幂等字段 |

### 2.3 值对象、枚举和事件包络

以下值对象放入 packages/domain/src/launchscope_domain/value_objects/，不得以裸字符串散落在 API 和 Worker 中：

- TenantScope(tenant_id, workspace_id, project_id, product_version_id, run_id)
- CorrelationContext(correlation_id, causation_id, idempotency_key, schema_version)
- EvidenceRef(evidence_id, object_key, sha256, mime_type, source_type, trust_level)
- BudgetReservation(run_id, category, limit, reserved, consumed, currency)
- ApprovalBinding(run_id, tool_id, parameters_sha256, expires_at, one_time_token_id)
- TimeScope(published_at, fetched_at, valid_from, valid_until, region)

主要枚举：

- RunStatus：DRAFT、INTAKE、WAITING_FOR_USER、PLANNED、RUNNING、EVIDENCE_REVIEW、WAITING_FOR_APPROVAL、WAITING_FOR_BUDGET、SYNTHESIZING、COMPLETED、FAILED、CANCELLED、EXPIRED、NEEDS_ATTENTION。
- StageCode：INTAKE、GAP_ANALYSIS、PROFILE_CONFIRMATION、PLANNING、PARALLEL_EVALUATION、EVIDENCE_REVIEW、REMEDIATION、SYNTHESIS、DOSSIER_COMMIT、VERSION_REGRESSION。
- FindingGrade：STRONG、MODERATE、WEAK、INSUFFICIENT_EVIDENCE。
- Recommendation：PROCEED、VALIDATE_FURTHER、ADJUST、PAUSE。
- EvidenceLevel：E0、E1、E2、E3、E4、E5。
- RiskTier：LOW、MEDIUM、HIGH；NetworkLevel：PUBLIC_RESEARCH、AUTHENTICATED_RESEARCH、EXTERNAL_ACTION。
- FailureClass：TRANSIENT、VALIDATION、AUTHORIZATION、DEPENDENCY、BUDGET、POLICY、SUBMISSION_UNKNOWN、BUSINESS。

所有领域事件使用 packages/contracts/events/envelope.schema.json 定义的包络：

~~~json
{
  "event_type": "evaluation.run.started",
  "schema_version": "1.0",
  "event_id": "uuid",
  "tenant_id": "uuid",
  "run_id": "uuid",
  "task_id": "uuid-or-null",
  "correlation_id": "uuid",
  "causation_id": "uuid-or-null",
  "idempotency_key": "string",
  "occurred_at": "RFC3339",
  "payload": {}
}
~~~

首批事件至少包括：project.created、product_version.submitted、intake.gap_identified、profile.confirmed、evaluation.run.started、task.dispatched、evidence.captured、finding.submitted、evidence.audit_completed、approval.requested、approval.resolved、run.needs_attention、decision.synthesized、dossier.committed、version.regression_completed、run.completed、run.failed。

### 2.4 “点—线—面—环”在领域模型中的落点

~~~mermaid
flowchart LR
    P["点：Evidence"] --> L["线：Hypothesis + Finding + 反证 + 缺口"]
    L --> F["面：四维 Decision"]
    F --> R["报告：Report + 1-3 个行动"]
    R --> V["环：ProductVersion V2"]
    V -->|"同一 project_id、标准和测试任务"| P2["新一轮 Evidence"]
    P2 --> C["版本变化与 ProductDossier"]
~~~

这条链必须能从报告首页逐层展开到 Decision、Finding、Evidence、来源对象和原始工具摘要；任何无法展开的关键结论只能标为缺口或假设。

## 3. ER 图与存储边界

PostgreSQL 保存以下结构化元数据、状态、权限、预算、审计和哈希。原始文档、截图、录像、网页快照和正式报告正文保存在不可公开访问的 MinIO/OSS；对象键必须包含 tenant、project、product_version、run 和 evidence 标识。文本解析、全文索引、向量均是派生数据，删除源对象时一并清理。

~~~mermaid
erDiagram
    TENANT ||--o{ WORKSPACE : contains
    WORKSPACE ||--o{ PROJECT : owns
    PROJECT ||--o{ PRODUCT_VERSION : has
    PRODUCT_VERSION ||--o{ MATERIAL : includes
    PRODUCT_VERSION ||--o| PRODUCT_PROFILE : confirms
    PRODUCT_VERSION ||--o{ EVALUATION_RUN : reviewed_by
    EVALUATION_RUN ||--|| RUN_MANIFEST : freezes
    EVALUATION_RUN ||--o{ STAGE : gates
    STAGE ||--o{ TASK : contains
    AGENT_IDENTITY ||--o{ TASK : assigned
    SKILL_VERSION ||--o{ TASK : uses
    TASK ||--o{ SKILL_INVOCATION : invokes
    SKILL_INVOCATION ||--o{ TOOL_INVOCATION : calls
    MATERIAL ||--o{ EVIDENCE : sources
    TASK ||--o{ EVIDENCE : produces
    EVIDENCE ||--o{ FINDING_EVIDENCE : supports
    FINDING ||--o{ FINDING_EVIDENCE : cites
    FINDING ||--o{ CONFLICT_RECORD : conflicts
    FINDING ||--o{ DECISION_FINDING : informs
    DECISION ||--o{ DECISION_FINDING : contains
    EVALUATION_RUN ||--o{ DECISION : yields
    DECISION ||--o| REPORT : rendered_as
    PROJECT ||--o{ MEMORY_CANDIDATE : proposes
    PROJECT ||--o{ MEMORY_ITEM : remembers
    EVALUATION_RUN ||--o{ APPROVAL_REQUEST : requests
    EVALUATION_RUN ||--o{ USAGE_RECORD : consumes
    EVALUATION_RUN ||--o{ AUDIT_EVENT : audits
    EVALUATION_RUN ||--o{ OUTBOX_MESSAGE : emits
    OUTBOX_MESSAGE ||--o{ INBOX_MESSAGE : delivered

    TENANT {
        uuid id PK
        string slug UK
        string status
        timestamptz created_at
    }
    WORKSPACE {
        uuid id PK
        uuid tenant_id FK
        string name
        string status
    }
    PROJECT {
        uuid id PK
        uuid tenant_id FK
        uuid workspace_id FK
        string name
        string dossier_status
    }
    PRODUCT_VERSION {
        uuid id PK
        uuid tenant_id FK
        uuid project_id FK
        int version_number
        string stage
        string source_version
        timestamptz submitted_at
    }
    MATERIAL {
        uuid id PK
        uuid tenant_id FK
        uuid product_version_id FK
        string source_type
        string object_key
        string sha256
        string trust_level
        string ingest_status
    }
    PRODUCT_PROFILE {
        uuid id PK
        uuid tenant_id FK
        uuid product_version_id FK
        jsonb confirmed_fields
        string confirmation_status
        timestamptz confirmed_at
    }
    EVALUATION_RUN {
        uuid id PK
        uuid tenant_id FK
        uuid project_id FK
        uuid product_version_id FK
        string status
        string standard_version
        string correlation_id
        string idempotency_key UK
    }
    RUN_MANIFEST {
        uuid run_id PK, FK
        uuid tenant_id FK
        string manifest_sha256
        jsonb frozen_config
        jsonb budget
        jsonb security_policy
    }
    STAGE {
        uuid id PK
        uuid tenant_id FK
        uuid run_id FK
        string code
        int ordinal
        string status
    }
    TASK {
        uuid id PK
        uuid tenant_id FK
        uuid stage_id FK
        uuid agent_identity_id FK
        uuid skill_version_id FK
        string status
        string lease_token
        string idempotency_key UK
        jsonb success_condition
    }
    AGENT_IDENTITY {
        uuid id PK
        string code UK
        string version
        jsonb capabilities
        jsonb allowed_actions
    }
    SKILL_VERSION {
        uuid id PK
        string skill_code
        string version
        string manifest_sha256
        jsonb input_schema
        jsonb output_schema
    }
    SKILL_INVOCATION {
        uuid id PK
        uuid tenant_id FK
        uuid task_id FK
        uuid skill_version_id FK
        string status
        string idempotency_key UK
        decimal estimated_cost
    }
    TOOL_INVOCATION {
        uuid id PK
        uuid tenant_id FK
        uuid skill_invocation_id FK
        string tool_code
        string risk_tier
        string status
        string parameters_sha256
    }
    EVIDENCE {
        uuid id PK
        uuid tenant_id FK
        uuid run_id FK
        uuid task_id FK
        uuid material_id FK
        string source_type
        string object_key
        string sha256
        string evidence_level
        string trust_level
        timestamptz fetched_at
    }
    FINDING {
        uuid id PK
        uuid tenant_id FK
        uuid run_id FK
        uuid task_id FK
        string dimension_code
        string grade
        string claim_type
        uuid supersedes_id FK
        jsonb structured_result
    }
    FINDING_EVIDENCE {
        uuid finding_id FK
        uuid evidence_id FK
        string relation_type
    }
    CONFLICT_RECORD {
        uuid id PK
        uuid tenant_id FK
        uuid run_id FK
        uuid finding_id FK
        jsonb conflicting_refs
        string resolution_status
    }
    DECISION {
        uuid id PK
        uuid tenant_id FK
        uuid run_id FK
        string recommendation
        jsonb dimension_grades
        jsonb hard_blocks
        uuid supersedes_id FK
    }
    DECISION_FINDING {
        uuid decision_id FK
        uuid finding_id FK
        string role
    }
    REPORT {
        uuid id PK
        uuid tenant_id FK
        uuid decision_id FK
        string object_key
        string sha256
        string status
    }
    MEMORY_CANDIDATE {
        uuid id PK
        uuid tenant_id FK
        uuid project_id FK
        uuid source_finding_id FK
        string status
        jsonb candidate
    }
    MEMORY_ITEM {
        uuid id PK
        uuid tenant_id FK
        uuid project_id FK
        string item_type
        string validity_status
        timestamptz valid_until
        jsonb content
    }
    APPROVAL_REQUEST {
        uuid id PK
        uuid tenant_id FK
        uuid run_id FK
        string tool_code
        string parameters_sha256
        string status
        timestamptz expires_at
    }
    USAGE_RECORD {
        uuid id PK
        uuid tenant_id FK
        uuid run_id FK
        uuid task_id FK
        string category
        decimal quantity
        decimal cost
    }
    AUDIT_EVENT {
        uuid id PK
        uuid tenant_id FK
        uuid run_id FK
        string actor_type
        string action
        string outcome
        string payload_sha256
        timestamptz occurred_at
    }
    OUTBOX_MESSAGE {
        uuid id PK
        uuid tenant_id FK
        uuid aggregate_id
        string event_type
        string idempotency_key UK
        string publish_status
    }
    INBOX_MESSAGE {
        uuid id PK
        uuid tenant_id FK
        uuid outbox_message_id FK
        string consumer_name
        string dedupe_key UK
        string processing_status
    }
~~~

ER 实施规则：

1. 所有业务表都带 tenant_id；外键约束不能允许跨租户引用。共享数据库启用 RLS，应用连接设置当前租户上下文，后台迁移/维护连接不暴露给租户请求。
2. Evidence 的 object_key、sha256、大小、MIME、来源、发布时间、抓取时间、地区和信任等级是元数据；正文和二进制不进入通用日志。
3. Finding、Decision、Report 的历史只追加，旧记录由 supersedes_id 连接；当前 Product Dossier 是投影，不是历史替代品。
4. FindingEvidence 和 DecisionFinding 使用复合唯一键，避免重复引用；Outbox/Inbox 使用幂等键和唯一约束做至少一次消费去重。
5. 任何表的写入只能从其模块应用服务进入；跨模块通过端口或事件，不允许 Repository 直接写另一个模块的表。

## 4. 状态机规格

### 4.1 RunStatus 状态机

控制平面是唯一状态提交者。Agent、Worker、Matrix 和 RocketMQ Consumer 只能提出 Command/StateChangeRequest；控制平面校验当前版本、租约、权限、幂等键和前置证据后才落库。

~~~mermaid
stateDiagram-v2
    [*] --> DRAFT
    DRAFT --> INTAKE: authorized submit + idempotency
    INTAKE --> WAITING_FOR_USER: gap exists
    INTAKE --> PLANNED: material/profile complete
    WAITING_FOR_USER --> PLANNED: user confirms profile
    WAITING_FOR_USER --> EXPIRED: response deadline passed
    PLANNED --> WAITING_FOR_BUDGET: reservation unavailable
    WAITING_FOR_BUDGET --> PLANNED: budget reserved
    PLANNED --> RUNNING: manifest frozen + harness accepted
    RUNNING --> EVIDENCE_REVIEW: required tasks terminal
    EVIDENCE_REVIEW --> WAITING_FOR_APPROVAL: protected action or internal data
    EVIDENCE_REVIEW --> SYNTHESIZING: audit accepted/degraded
    WAITING_FOR_APPROVAL --> SYNTHESIZING: one-time approval valid
    WAITING_FOR_APPROVAL --> NEEDS_ATTENTION: rejected/expired
    SYNTHESIZING --> COMPLETED: decision/report/dossier committed
    RUNNING --> NEEDS_ATTENTION: budget/policy/unknown state
    EVIDENCE_REVIEW --> NEEDS_ATTENTION: unresolved conflict or missing evidence
    NEEDS_ATTENTION --> RUNNING: human resumes with new command
    DRAFT --> CANCELLED: user cancels
    WAITING_FOR_USER --> CANCELLED: user cancels
    PLANNED --> CANCELLED: user cancels
    RUNNING --> CANCELLED: controlled stop
    INTAKE --> FAILED: validation/dependency failure
    RUNNING --> FAILED: known terminal failure
    SYNTHESIZING --> FAILED: report persistence failure
    FAILED --> [*]
    CANCELLED --> [*]
    EXPIRED --> [*]
    COMPLETED --> [*]
~~~

Transition guards and evidence:

| Transition | Guard | State writer | Evidence |
|---|---|---|---|
| DRAFT → INTAKE | 授权材料、tenant scope、幂等键有效 | Evaluation application service | Run created audit + ProductVersion snapshot |
| INTAKE → WAITING_FOR_USER | 缺口诊断已生成，问题不超过本轮 3—5 个优先项 | Evaluation application service | Gap report + question set |
| WAITING_FOR_USER → PLANNED | 用户提交回答并确认 ProductProfile | Project Dossier application service | Profile confirmation event + versioned profile |
| PLANNED → RUNNING | RunManifest hash、标准版本、权限、预算、超时和 Skill/Tool 版本全部冻结 | Evaluation application service | immutable manifest + budget reservation |
| RUNNING → EVIDENCE_REVIEW | 所有 required Task 成功、明确降级或受控失败 | Evaluation application service | task terminal summary + evidence index |
| EVIDENCE_REVIEW → SYNTHESIZING | Auditor 已对关键 Finding 通过/降级，未解决冲突已显式记录 | Evidence application service | auditor result + conflict records |
| EVIDENCE_REVIEW → WAITING_FOR_APPROVAL | 需要认证研究、内部数据或外部动作 | Policy application service | ApprovalRequest + action preview |
| SYNTHESIZING → COMPLETED | 规则判断、报告、Dossier 投影、审计和 Outbox 同事务提交 | Decision application service | decision/report hashes + dossier commit event |
| 任意可运行态 → NEEDS_ATTENTION | SUBMISSION_UNKNOWN、费用不明、策略拒绝、预算超额或无法安全恢复 | Evaluation application service | failure class + freeze audit；不得自动重试 |

SUBMISSION_UNKNOWN 不是可重试状态。若 Provider/Tool 返回“可能已提交但结果未知”，Run 进入 NEEDS_ATTENTION，冻结相同动作的重试、切模、切工具、切 Runtime、人工结算和原始 SQL 改状态；后续只能通过带版本/CAS 的受控 reconciliation 解除。

### 4.2 固定阶段门

RunStatus 与固定阶段分开存储。每次 Run 必须依次经过下列 StageCode；阶段内可有动态 DAG，不能越过阶段门：

~~~text
INTAKE
  -> GAP_ANALYSIS
  -> PROFILE_CONFIRMATION
  -> PLANNING
  -> PARALLEL_EVALUATION
  -> EVIDENCE_REVIEW
  -> REMEDIATION
  -> SYNTHESIS
  -> DOSSIER_COMMIT
  -> VERSION_REGRESSION
~~~

允许的非线性仅限于：

- GAP_ANALYSIS 发现缺口后等待用户；用户回答后继续 PROFILE_CONFIRMATION。
- EVIDENCE_REVIEW 发现缺证时进入 REMEDIATION；补证任务完成后回到同一 Run 的受控审查分支，不重建 RunManifest。
- 高风险或认证访问在阶段门前进入 WAITING_FOR_APPROVAL；审批拒绝进入 NEEDS_ATTENTION。
- 失败恢复只能从持久化 checkpoint 继续；已完成工具调用不得重复执行。

### 4.3 Task 状态机

~~~mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> BLOCKED: dependency not satisfied
    BLOCKED --> PENDING: dependency satisfied
    PENDING --> LEASED: worker lease acquired
    LEASED --> RUNNING: runtime started
    RUNNING --> SUCCEEDED: schema + success condition pass
    RUNNING --> FAILED: known terminal error
    RUNNING --> NEEDS_ATTENTION: SUBMISSION_UNKNOWN/budget/policy
    RUNNING --> WAITING_FOR_APPROVAL: approval required
    WAITING_FOR_APPROVAL --> RUNNING: one-time approval accepted
    WAITING_FOR_APPROVAL --> NEEDS_ATTENTION: rejected/expired
    LEASED --> EXPIRED: lease timeout
    EXPIRED --> PENDING: only no-side-effect and known status
    FAILED --> PENDING: at most one schema correction or controlled transient retry
    RUNNING --> CANCELLED: controlled stop
    SUCCEEDED --> [*]
    NEEDS_ATTENTION --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
~~~

Task 必须持久化 dependency list、agent_identity、skill_version、tool allowlist、budget slice、timeout、retry policy、success condition、evidence requirement、lease token 和 idempotency_key。SUBMISSION_UNKNOWN、AUTHORIZATION、POLICY、BUDGET 不进入自动 retry 分支。

## 5. 模块边界

### 5.1 规划中的仓库布局

~~~text
apps/
  web/                 # 租户用户工作台
  ops/                 # 平台运维/评委审计入口，独立身份边界
  api/                 # FastAPI 模块化单体控制平面
  orchestrator/        # AgentTeams/Matrix Adapter 与 Harness
  worker/              # Skill/Tool 隔离执行器
packages/
  contracts/           # OpenAPI、JSON Schema、事件、UnifiedModel
  domain/              # 纯领域模型、规则、状态机和端口
  skills/              # 版本化 Skill 包
  observability/       # OpenTelemetry 公共语义和脱敏封装
infra/
  compose/             # Local/Demo 组件编排
  kubernetes/          # 生产目标样例
  higress/ nacos/ rocketmq/ polardb/ observability/
tests/
  contract/ integration/ security/ e2e/
~~~

Python 包使用 src 布局：packages/domain/src/launchscope_domain/、apps/api/src/launchscope_api/、apps/orchestrator/src/launchscope_orchestrator/、apps/worker/src/launchscope_worker/。Web 使用 apps/web/src/，契约只在 packages/contracts/ 定义，禁止在前端或 Worker 复制一份未版本化 DTO。

### 5.2 模块职责、所有权与禁区

| 模块 | 目标路径 | 拥有的事实/表 | 入站端口与出站事件 | 明确禁区 |
|---|---|---|---|---|
| Identity & Tenant | apps/api/src/launchscope_api/modules/identity_tenant/ | Tenant、Workspace、Membership、RoleBinding、AccessPolicy | OIDC claims、RBAC checks；tenant.created、membership.changed | 不读取材料正文，不替 Ops 绕过 break-glass |
| Project Dossier | apps/api/src/launchscope_api/modules/project_dossier/ | Project、ProductVersion、Material 元数据、ProductProfile、Dossier projection | upload/initiate、profile.confirm；project.created、profile.confirmed、dossier.updated | 不运行 Agent，不直接写 Evidence/Report |
| Evaluation | apps/api/src/launchscope_api/modules/evaluation/ | EvaluationRun、RunManifest、Stage、Task、TaskDependency | start/resume/cancel、state proposal；run.started、stage.changed、task.dispatched | 不调用外部工具，不由 Matrix 直接更新状态 |
| Agent Orchestration | apps/orchestrator/src/launchscope_orchestrator/ | Agent assignment、Team/Room mapping、lease request、handoff metadata | AgentTeams/Matrix Adapter、Harness；task.handoff、state.change.requested | 不拥有业务最终状态，不传完整聊天记录/思维链 |
| Skill Registry | packages/skills/ 与 apps/api/src/launchscope_api/modules/skill_registry/ | Skill、SkillVersion、schema、allowlist、quality status | resolve approved version；skill.resolved | 不持有长期密钥，不直接扣预算 |
| Tool Gateway | apps/worker/src/launchscope_worker/tool_gateway/ | Tool contract、ToolInvocation metadata、endpoint policy | MCP/Adapter、Higress egress；tool.started/completed/failed | 不允许任意 HTTP/SQL/Shell，不绕过域名/风险/预算策略 |
| Evidence | apps/api/src/launchscope_api/modules/evidence/ | Evidence、Finding、FindingEvidence、ConflictRecord、audit result | object metadata、structured finding、auditor result；evidence.captured、finding.submitted | 不覆盖原始 Finding，不把搜索摘要当来源 |
| Memory & RAG | apps/api/src/launchscope_api/modules/memory_rag/ | MemoryCandidate、MemoryItem、retrieval metadata | candidate submit、filtered retrieval；memory.promoted/invalidated | 不跨租户学习；不先向量检索后做权限过滤 |
| Policy & Approval | apps/api/src/launchscope_api/modules/policy_approval/ | ApprovalRequest、PolicyDecision、one-time token | action preview、approve/reject/expire；approval.requested/resolved | 不把审批 token 变成长效凭据，不接受参数变更复用 |
| Decision & Report | apps/api/src/launchscope_api/modules/decision_report/ | Decision、DecisionFinding、Report | rule evaluation、render/download；decision.synthesized、report.published | 不自行制造证据，不做简单四维平均 |
| Usage & Quota | apps/api/src/launchscope_api/modules/usage_quota/ | BudgetReservation、UsageRecord、QuotaPolicy | reserve/consume/release；budget.changed、usage.recorded | 不允许预算透支；不把未知费用标记为已结算 |
| Audit & Compliance | apps/api/src/launchscope_api/modules/audit_compliance/ | AuditEvent、RetentionJob、DeletionTombstone | domain event subscriber、break-glass；audit.recorded、retention.executed | 不记录敏感正文、私密思维链或长期密钥 |
| Integration | apps/api/src/launchscope_api/infrastructure/integration/ | OutboxMessage、InboxMessage、delivery attempts | PostgreSQL transaction、RocketMQ publisher/consumer、Nacos/Higress adapters | 不让基础设施 SDK 渗透领域包，不在消息里放完整对话 |

### 5.3 依赖方向

~~~mermaid
flowchart LR
    ID["Identity & Tenant"] --> PD["Project Dossier"]
    ID --> POL["Policy & Approval"]
    PD --> EV["Evaluation"]
    EV --> AO["Agent Orchestration"]
    AO --> SR["Skill Registry"]
    SR --> TG["Tool Gateway"]
    EV --> EVD["Evidence"]
    EVD --> DR["Decision & Report"]
    PD --> MR["Memory & RAG"]
    EVD --> MR
    DR --> MR
    POL --> TG
    UQ["Usage & Quota"] --> EV
    AC["Audit & Compliance"] -. subscribes .-> EV
    AC -. subscribes .-> EVD
    INT["Integration Adapters"] -. ports .-> EV
    INT -. ports .-> TG
~~~

图中的实线是应用服务可调用的方向；虚线是事件订阅或端口适配。任何新依赖都必须通过 packages/domain/src/launchscope_domain/ports/ 或 packages/contracts/ 的契约，不能通过 ORM 直接跨模块写表。

## 6. 安全模型

### 6.1 信任边界与数据流

~~~mermaid
flowchart TB
    USER["租户浏览器 / 用户工作台"] --> IN["Higress Entry Gateway<br/>TLS OIDC WAF tenant rate limit SSE"]
    OPS["Platform Ops / Element 审计入口"] --> INOPS["独立 Ops Gateway<br/>独立域名 OAuth Client 会话"]
    IN --> API["FastAPI Control Plane<br/>状态提交者"]
    INOPS --> OPSAPI["Ops API<br/>不默认读取租户正文"]
    API --> PG["PostgreSQL<br/>RLS 业务事实"]
    API --> OBJ["MinIO/OSS<br/>private objects"]
    API --> MQ["RocketMQ<br/>commands/events"]
    API --> ORCH["Orchestrator + Team Harness"]
    ORCH --> MATRIX["AgentTeams / Matrix<br/>structured handoff only"]
    MQ --> WORKER["Isolated Worker Runtime<br/>no default network/credentials"]
    WORKER --> EGRESS["Higress AI/Tool Egress<br/>allowlist budget short token redaction"]
    EGRESS --> PUBLIC["Public HTTPS Research"]
    EGRESS --> AUTH["Approved Authenticated Research"]
    EGRESS -. blocked by default .-> ACTION["External Action"]
    SECRET["Secret Manager / K8s Secret"] -. short-lived token .-> EGRESS
    CONTENT["Untrusted upload / webpage / repo"] --> QUAR["Quarantine + scan + sandbox parse"]
    QUAR --> OBJ
    QUAR -->|"Untrusted Evidence, no instruction authority"| API
    API --> OTEL["OTel/LoongSuite<br/>hashes/summaries/metrics only"]
~~~

### 6.2 威胁、控制和测试证据

| 威胁面 | 控制要求 | 计划中的测试/证据 |
|---|---|---|
| 跨租户读写 | tenant_id 全链路、RLS、对象键和消息带租户、RAG 先过滤 | tests/security/test_tenant_isolation.py；两租户互读/互写均返回 403/空结果 |
| 上传文件、网页、仓库中的恶意内容 | 隔离区、类型/大小/压缩层级/哈希校验、恶意文件/宏/脚本扫描、无凭据沙箱解析 | tests/security/test_untrusted_ingestion.py；隔离日志、扫描结果、Untrusted Evidence 标签 |
| Prompt injection / 越权指令 | 外部内容永远是数据；不能修改 Agent Identity、Skill 白名单、模型、预算、审批和系统规则 | tests/security/test_prompt_injection_boundary.py；被注入内容不能改变 Harness 配置 |
| SSRF、DNS rebinding、内网/元数据访问 | Research Gateway 只允许公开 HTTPS GET/HEAD；阻断 loopback、私网、云元数据、未校验重定向 | tests/security/test_ssrf_policy.py；每个拒绝包含 policy code 和 audit event |
| 长期密钥泄露 | Worker 只拿短期能力 token；长期凭据在 Secret Manager；日志和 Matrix 不存明文 | secret scan、log redaction fixture、token expiry proof |
| 高风险动作误执行 | 风险分级、action preview、人工审批、参数 SHA-256、Run 绑定、一次性 token | ApprovalRequest 集成测试；参数任意变化必须重新审批 |
| 重放、重复扣费、重复副作用 | Outbox/Inbox、idempotency_key、唯一约束、预算预留、lease | tests/integration/test_idempotency.py；同一消息/命令只形成一条事实 |
| 未知提交/费用未知 | SUBMISSION_UNKNOWN 立即冻结，不 retry/failover/switch/resubmit/人工结算 | tests/security/test_fail_closed_unknown_submission.py；状态和日志显示零二次提交 |
| RAG 越权或过期 | 先按 tenant/project/version/region/time/permission 过滤；政策/价格有 valid_until | tests/security/test_rag_scope_and_expiry.py；越权和过期检索无命中 |
| 观测泄露正文 | 通用 Trace 只存哈希、版本、摘要、统计；正文留在受控证据库 | tests/security/test_observability_redaction.py；Trace 中无 prompt/密钥/材料正文 |
| 删除不彻底 | 删除 DB 正文、对象、向量、缓存和派生索引；审计只留哈希、操作者、原因、结果 | tests/integration/test_retention_delete.py；删除清单和 tombstone |
| 代码供应链和任意执行 | 代码仓库默认只读，不执行仓库脚本；依赖、密钥、许可证和镜像扫描 | CI security report、worker network policy、SBOM |

### 6.3 数据分类与保留

| 数据 | 默认存储 | 默认保留 | 处理要求 |
|---|---|---:|---|
| 临时上传/中间文件 | private object store | 7 天 | 终态清理，删除派生解析和向量 |
| 网页快照/截图/工具证据 | private object store + metadata | 90 天 | 租户可调，保存来源/时间/地区/哈希 |
| Product Dossier/正式报告 | PostgreSQL metadata + private object | 项目删除或合同到期 | 版本化，不覆盖 |
| Trace 正文/模型敏感输出 | 受控证据库 | 30 天或策略 | 通用 OTel 只留摘要/哈希 |
| 聚合 Metrics | OTel 后端 | 1 年 | 不含业务正文 |
| 审批与安全审计 | PostgreSQL append-only | 1 年或企业配置 | 删除时只保留 tombstone 摘要 |
| 长期凭据、OAuth secret | Secret Manager | 由密钥策略决定 | 不入库、不入日志、不入 Matrix |

## 7. 详细实施顺序

### 7.1 依赖总图

~~~text
T1 契约与 ADR 约束
  -> T2 Monorepo/Compose 骨架
  -> T3 领域内核与状态机
  -> T4 PostgreSQL/RLS/Outbox/Inbox
  -> T5 Identity + Project + Material + Intake
  -> T6 Evidence/Object Store + Skill Registry
  -> T7 AgentTeams/Matrix + Harness + Task DAG
  -> T8 Worker + Tool Contract + Research Gateway
  -> T9 Auditor + Memory/RAG + Decision/Report + Dossier
  -> T10 Web/OPS + REST/SSE/S3 flows
  -> T11 Observability/Quota/Retention/Deployment
  -> T12 真实只读 E2E、V1/V2 复验和发布验收
~~~

T4 与 T5 在 T3 完成后可由不同人并行；T6 必须先于 T7；T7 和 T8 可并行开发但必须在 T9 前通过契约测试；T10 只能消费已经版本化的 API/SSE 契约；T12 是发布前的唯一合并门。下列任务中的测试命令是未来实现时执行的命令，本轮不执行。

### 7.2 T1：冻结契约、ADR 入口和版本策略

**依赖：** 无。
**目的：** 把本文件的领域语言、事件包络、版本兼容和变更规则落成可检查的契约。

**文件：**

- Create: packages/contracts/events/envelope.schema.json
- Create: packages/contracts/events/evaluation-events.v1.json
- Create: packages/contracts/commands/run-commands.v1.json
- Create: packages/contracts/openapi/control-plane.v1.yaml
- Create: packages/contracts/unified-model/launchscope-unified-model.v1.json
- Create: packages/contracts/README.md
- Create: docs/adr/0001-frozen-boundaries-and-change-policy.md
- Create: docs/adr/0002-p0-skill-set-reconciliation.md
- Test: packages/contracts/tests/test_json_schema_contracts.py
- Test: packages/contracts/tests/test_event_compatibility.py

**步骤：**

1. 将所有事件和命令包络固定为 event_id、tenant_id、run_id、task_id、correlation_id、causation_id、idempotency_key、schema_version、occurred_at、payload。
2. 为 REST 写操作定义幂等键、统一错误码、游标分页和 correlation_id；为 SSE 定义事件游标恢复语义。
3. 为 UnifiedModel 定义语义映射，但明确 PostgreSQL 事务表仍负责状态、审批、预算和幂等。
4. 在 0002 ADR 中记录“基线六个 P0 Skill”与 V2.0 方案额外列出的 user-validation-designer 之间的差异：V0.1 首发注册基线六个；若要把额外能力升为独立 P0，必须先批准 ADR 并补齐预算、契约、测试和回滚。
5. 规定 API、事件、Skill、Prompt、Agent Identity、规则、UnifiedModel 和 RunManifest 各自独立版本；事件消费者至少兼容当前和上一版。

**测试命令：**

~~~powershell
python -m pytest packages/contracts/tests/test_json_schema_contracts.py -q
python -m pytest packages/contracts/tests/test_event_compatibility.py -q
python -m jsonschema packages/contracts/events/envelope.schema.json packages/contracts/events/evaluation-events.v1.json
~~~

**预期验收证据：**

- 所有样例 payload 通过 JSON Schema；缺 tenant_id、schema_version、idempotency_key 或 correlation_id 的 payload 被拒绝。
- 当前/上一版事件兼容报告为 PASS；版本变更没有直接修改已发布 schema。
- ADR 明确记录 P0 Skill 差异，没有静默扩张架构。

### 7.3 T2：建立 Monorepo 和本地基础设施骨架

**依赖：** T1。
**目的：** 建立与基线 §5.2 一致的目录、Python/TypeScript 工具链和 Docker Compose 配置，不实现业务流程。

**文件：**

- Create: package.json
- Create: pnpm-workspace.yaml
- Create: pyproject.toml
- Create: apps/web/package.json
- Create: apps/ops/package.json
- Create: apps/api/pyproject.toml
- Create: apps/orchestrator/pyproject.toml
- Create: apps/worker/pyproject.toml
- Create: packages/domain/pyproject.toml
- Create: packages/contracts/pyproject.toml
- Create: packages/skills/README.md
- Create: packages/observability/pyproject.toml
- Create: infra/compose/docker-compose.yml
- Create: infra/compose/docker-compose.test.yml
- Create: infra/compose/.env.example
- Create: .gitignore
- Test: infra/compose/tests/test_compose_config.ps1

**步骤：**

1. 创建 web、ops、api、orchestrator、worker、domain、contracts、skills、observability 的空包入口和统一 lint/test 命令。
2. Compose 只声明 PostgreSQL、MinIO、RocketMQ、Nacos、Higress、OTel Collector 和可选 Matrix/AgentTeams Adapter；不在镜像中写入真实凭据。
3. Local、Demo、Production 使用同一配置 Schema；只改变基础设施规模和 Secret 引用，不复制业务逻辑。
4. .env.example 只能包含空值或环境变量引用，禁止出现看似真实的密码、Token、OAuth code 或数据库密钥。

**测试命令：**

~~~powershell
pnpm install --frozen-lockfile
docker compose -f infra/compose/docker-compose.yml config
docker compose -f infra/compose/docker-compose.test.yml config
pwsh -File infra/compose/tests/test_compose_config.ps1
~~~

**预期验收证据：**

- Compose config 无 unresolved service、重复端口或明文 secret；所有服务使用固定镜像 digest，不使用 latest。
- docker compose up -d 后健康检查只证明基础设施可启动，不被当作业务 E2E。
- 根目录 Python/TypeScript lint 和 test 命令可被 CI 调用。

### 7.4 T3：实现纯领域内核、聚合不变量和状态机

**依赖：** T1、T2。
**目的：** 在没有数据库、Agent SDK 或云 SDK 的情况下锁定领域规则，确保状态机和 fail-closed 语义可单测。

**文件：**

- Create: packages/domain/src/launchscope_domain/value_objects.py
- Create: packages/domain/src/launchscope_domain/enums.py
- Create: packages/domain/src/launchscope_domain/events.py
- Create: packages/domain/src/launchscope_domain/aggregates/project_dossier.py
- Create: packages/domain/src/launchscope_domain/aggregates/evaluation_run.py
- Create: packages/domain/src/launchscope_domain/aggregates/evidence_review.py
- Create: packages/domain/src/launchscope_domain/aggregates/decision_report.py
- Create: packages/domain/src/launchscope_domain/services/run_state_machine.py
- Create: packages/domain/src/launchscope_domain/services/task_dag.py
- Create: packages/domain/src/launchscope_domain/services/rule_evaluator.py
- Create: packages/domain/src/launchscope_domain/ports/repositories.py
- Create: packages/domain/src/launchscope_domain/ports/integrations.py
- Test: packages/domain/tests/test_run_state_machine.py
- Test: packages/domain/tests/test_stage_gate.py
- Test: packages/domain/tests/test_task_dag.py
- Test: packages/domain/tests/test_evidence_finding_invariants.py
- Test: packages/domain/tests/test_fail_closed_policy.py
- Test: packages/domain/tests/test_rule_evaluator.py

**步骤：**

1. 将第 2 节的值对象和枚举实现为不可变/受校验类型。
2. 实现固定十阶段、RunStatus、TaskStatus 和每个合法转移的 guard；非法转移必须返回结构化错误，不改变聚合。
3. 实现动态 DAG 的依赖拓扑、循环检测、required evidence 和 success condition 校验。
4. 实现 Finding 无证据只能是 HYPOTHESIS、Decision 不可覆盖、SUBMISSION_UNKNOWN 冻结和一次 schema correction 限制。
5. 实现规则层的四维等级、硬阻断和 INSUFFICIENT_EVIDENCE；不得计算简单四维平均。

**测试命令：**

~~~powershell
python -m pytest packages/domain/tests -q
python -m ruff check packages/domain
python -m mypy packages/domain/src
~~~

**预期验收证据：**

- 状态机正向/逆向/非法路径、预算不足、审批过期、未知提交和取消路径均有测试。
- 相同输入的规则评估产生确定性结果；Finding/Decision 历史只追加。
- 领域包不导入 FastAPI、SQLAlchemy、RocketMQ、Matrix、Higress 或任何厂商 SDK。

### 7.5 T4：实现 PostgreSQL、RLS、迁移和可靠消息边界

**依赖：** T3。
**目的：** 将领域模型持久化为可隔离、可恢复、可去重的事务事实源。

**文件：**

- Create: apps/api/migrations/env.py
- Create: apps/api/migrations/versions/0001_identity_dossier.py
- Create: apps/api/migrations/versions/0002_evaluation_manifest.py
- Create: apps/api/migrations/versions/0003_evidence_decision.py
- Create: apps/api/migrations/versions/0004_policy_usage_audit.py
- Create: apps/api/src/launchscope_api/infrastructure/db/session.py
- Create: apps/api/src/launchscope_api/infrastructure/db/rls.py
- Create: apps/api/src/launchscope_api/infrastructure/messaging/outbox.py
- Create: apps/api/src/launchscope_api/infrastructure/messaging/inbox.py
- Create: apps/api/src/launchscope_api/infrastructure/repositories/
- Test: apps/api/tests/integration/test_migrations.py
- Test: apps/api/tests/integration/test_rls_isolation.py
- Test: apps/api/tests/integration/test_outbox_inbox.py
- Test: apps/api/tests/integration/test_append_only_history.py

**步骤：**

1. 按第 3 节 ER 图创建表、外键、复合唯一键、tenant_id 索引、时间索引和 append-only 触发/应用约束。
2. 为每张租户表启用 RLS；请求事务显式设置租户上下文；跨租户外键、查询和对象键测试必须失败。
3. 在更新业务状态的同一事务内写 Outbox；Publisher 只发布已提交消息；Consumer 先写 Inbox 再处理业务。
4. 为每个迁移增加 forward/rollback 说明，但不得修改已发布 migration；兼容升级遵循 Expand-Migrate-Contract。
5. 将原始正文和二进制只写对象存储，数据库保存 object_key、hash、metadata 和权限标识。

**测试命令：**

~~~powershell
docker compose -f infra/compose/docker-compose.test.yml up -d postgres minio rocketmq
python -m pytest apps/api/tests/integration/test_migrations.py -q
python -m pytest apps/api/tests/integration/test_rls_isolation.py -q
python -m pytest apps/api/tests/integration/test_outbox_inbox.py -q
python -m pytest apps/api/tests/integration/test_append_only_history.py -q
~~~

**预期验收证据：**

- 从空库按顺序应用所有迁移；迁移重复运行无副作用；没有编辑旧 migration。
- Tenant A 的连接/Token 不能读取或写入 Tenant B 的 Project、Evidence、Memory、Trace metadata。
- 同一 Outbox/命令重复投递只产生一个状态变化和一个 UsageRecord；消息内容不含完整聊天记录或私密思维链。

### 7.6 T5：实现身份、项目、版本、材料和主动补问

**依赖：** T3、T4。
**目的：** 形成闭环的前半段：用户能创建 Project，提交 ProductVersion，隔离解析材料，收到缺口问题并确认 ProductProfile。

**文件：**

- Create: apps/api/src/launchscope_api/modules/identity_tenant/application.py
- Create: apps/api/src/launchscope_api/modules/identity_tenant/api.py
- Create: apps/api/src/launchscope_api/modules/project_dossier/application.py
- Create: apps/api/src/launchscope_api/modules/project_dossier/api.py
- Create: apps/api/src/launchscope_api/modules/project_dossier/material_ingestion.py
- Create: apps/api/src/launchscope_api/modules/project_dossier/profile_confirmation.py
- Create: apps/api/src/launchscope_api/modules/evaluation/intake_application.py
- Modify: packages/contracts/openapi/control-plane.v1.yaml
- Test: apps/api/tests/api/test_project_version_flow.py
- Test: apps/api/tests/api/test_gap_question_flow.py
- Test: apps/api/tests/integration/test_material_quarantine.py
- Test: apps/api/tests/security/test_material_authorization.py

**步骤：**

1. 实现 Tenant → Workspace → Project → ProductVersion 的创建和授权校验；Project ID 在 V1/V2 间稳定。
2. 实现 S3-compatible 直传初始化、MIME/大小/哈希校验、隔离对象键和完成回调；完成前不允许进入评审。
3. 实现 product-intake-normalizer 的输入/输出边界，生成 ProductProfile 草稿而不是事实。
4. 实现 intake-gap-diagnosis：缺口排序、最多 3—5 个本轮优先问题、目标用户/付费者/阶段/地区/验证目标必问校验。
5. 只有用户确认 ProductProfile 后才能从 WAITING_FOR_USER 进入 PLANNED；未确认不能被 Agent 或 API 绕过。

**测试命令：**

~~~powershell
python -m pytest apps/api/tests/api/test_project_version_flow.py -q
python -m pytest apps/api/tests/api/test_gap_question_flow.py -q
python -m pytest apps/api/tests/integration/test_material_quarantine.py -q
python -m pytest apps/api/tests/security/test_material_authorization.py -q
~~~

**预期验收证据：**

- API transcript 展示上传 → 隔离 → 哈希校验 → 缺口问题 → 用户回答 → 画像确认的 correlation_id 链。
- 未确认画像的 Run 启动请求返回明确的 validation error，不产生预算预留或 Worker 任务。
- 两个租户上传同名对象仍使用不同 tenant/project/version object_key，未授权下载返回 403。

### 7.7 T6：实现 Evidence/Object Store 和六个基线 P0 Skill 契约

**依赖：** T4、T5。
**目的：** 将“点”变成可引用的证据卡，并把基线明确冻结的 Skill 作为可测试能力包。

**文件：**

- Create: packages/skills/product-intake-normalizer/skill.yaml
- Create: packages/skills/product-intake-normalizer/input.schema.json
- Create: packages/skills/product-intake-normalizer/output.schema.json
- Create: packages/skills/intake-gap-diagnosis/skill.yaml
- Create: packages/skills/intake-gap-diagnosis/input.schema.json
- Create: packages/skills/intake-gap-diagnosis/output.schema.json
- Create: packages/skills/browser-product-audit/skill.yaml
- Create: packages/skills/browser-product-audit/input.schema.json
- Create: packages/skills/browser-product-audit/output.schema.json
- Create: packages/skills/business-investment-assessment/skill.yaml
- Create: packages/skills/business-investment-assessment/input.schema.json
- Create: packages/skills/business-investment-assessment/output.schema.json
- Create: packages/skills/evidence-grounding-audit/skill.yaml
- Create: packages/skills/evidence-grounding-audit/input.schema.json
- Create: packages/skills/evidence-grounding-audit/output.schema.json
- Create: packages/skills/version-regression-verification/skill.yaml
- Create: packages/skills/version-regression-verification/input.schema.json
- Create: packages/skills/version-regression-verification/output.schema.json
- Create: apps/api/src/launchscope_api/modules/evidence/evidence_application.py
- Create: apps/api/src/launchscope_api/modules/evidence/object_store.py
- Create: packages/skills/tests/test_skill_manifests.py
- Test: packages/skills/tests/test_skill_contracts.py
- Test: apps/api/tests/integration/test_evidence_lineage.py

**步骤：**

1. 每个 skill.yaml 写用途、适用场景、输入/输出 schema、前置条件、工具/域名/数据权限、预算、超时、重试、幂等、失败分类、证据要求、测试、回滚和弃用策略。
2. 将 Evidence 卡片固定为发现内容、来源、支持/反对假设、适用用户/地区/版本、E0—E5、可信度、发布时间、抓取时间、有效期、对象 hash 和采集 ToolInvocation。
3. 让 Evidence Auditor 只能提交 pass、degrade、reject、request_more_evidence，不允许修改其他 Agent 原始 Finding。
4. 为 browser-product-audit 设置只读浏览器能力和白名单域名；为商业/趋势材料要求来源、发布时间、地区和抓取时间。
5. 将 user-validation-designer、geo-policy-trend-radar、market-evidence-research 记录为参考方案能力，默认不进基线六个 P0；如主案例证明需要独立 Skill，走 T1 的 ADR。

**测试命令：**

~~~powershell
python -m pytest packages/skills/tests -q
python -m pytest apps/api/tests/integration/test_evidence_lineage.py -q
python -m ruff check packages/skills apps/api/src/launchscope_api/modules/evidence
~~~

**预期验收证据：**

- 六个 P0 manifest 和 schema 全部可加载、可校验、可引用版本 hash。
- 一条 Finding 能展开到至少一条 Evidence；无 Evidence 的结论自动降级为 HYPOTHESIS/INSUFFICIENT_EVIDENCE。
- Audited reject/degrade/request-more-evidence 的原始 Agent 结果保持不变，且产生独立 AuditEvent。

### 7.8 T7：实现 1+5 Agent Identity、AgentTeams/Matrix 和 Harness

**依赖：** T4、T6。
**目的：** 将赛道要求中的真实协作映射到固定 Run、结构化交接和可复验 Harness。

**文件：**

- Create: packages/contracts/agents/evaluation-manager.v1.yaml
- Create: packages/contracts/agents/product-engineering.v1.yaml
- Create: packages/contracts/agents/user-evidence.v1.yaml
- Create: packages/contracts/agents/business-investment.v1.yaml
- Create: packages/contracts/agents/geo-policy-trend.v1.yaml
- Create: packages/contracts/agents/evidence-auditor.v1.yaml
- Create: apps/orchestrator/src/launchscope_orchestrator/harness.py
- Create: apps/orchestrator/src/launchscope_orchestrator/manifest_loader.py
- Create: apps/orchestrator/src/launchscope_orchestrator/agentteams_adapter.py
- Create: apps/orchestrator/src/launchscope_orchestrator/matrix_adapter.py
- Create: apps/orchestrator/src/launchscope_orchestrator/handoff.py
- Create: apps/api/src/launchscope_api/modules/evaluation/planning_application.py
- Create: apps/api/src/launchscope_api/modules/evaluation/state_change_requests.py
- Test: apps/orchestrator/tests/test_agent_identity_contract.py
- Test: apps/orchestrator/tests/test_harness_manifest.py
- Test: apps/orchestrator/tests/test_matrix_handoff.py
- Test: apps/orchestrator/tests/test_dynamic_dag_dispatch.py

**步骤：**

1. 为 Manager、Product Engineering、User Evidence、Business Investment、Geo Policy Trend、Evidence Auditor 固定身份、职责、输入、输出、允许 Skill、工具和风险边界。
2. Manager 负责资料完整性、追问、任务拆解、冲突协调、审批请求和综合，不制造专业结论；Human 是独立审批主体，不算第七个 Agent。
3. Harness 在运行前校验 ProductVersion、材料 hash、standard version、Agent/Skill/Prompt/Model/Tool version、地区/时效、预算、超时、审批点、失败策略和证据要求，并写入 RunManifest。
4. Matrix 只传 task_id、结构化结果、Evidence 地址、风险、可信度、审批需求、失败分类和状态变化，不传完整报告/聊天记录/私密思维链。
5. Adapter 接收 Matrix/Worker 的 state-change request，转为控制平面命令；不直接更新 Run、Stage、Task 的业务状态。
6. 动态 DAG 每个节点在 dispatch 前经过依赖、权限、预算、超时、成功条件和 evidence requirement 校验。

**测试命令：**

~~~powershell
python -m pytest apps/orchestrator/tests/test_agent_identity_contract.py -q
python -m pytest apps/orchestrator/tests/test_harness_manifest.py -q
python -m pytest apps/orchestrator/tests/test_matrix_handoff.py -q
python -m pytest apps/orchestrator/tests/test_dynamic_dag_dispatch.py -q
~~~

**预期验收证据：**

- Team/Room 映射、Leader/Worker/Human、五个专业 Agent 的身份清单和任务进度可从一次 Run 展开。
- 同一输入、同一 Manifest hash 和同一标准得到可复验的 DAG；Manifest 运行中不可变。
- 尝试让 Agent 直接写 Run status、长期 Memory 或正式 Report 被拒绝并记录审计。

### 7.9 T8：实现隔离 Worker、统一 Tool Contract 和分级联网

**依赖：** T6、T7。
**目的：** 让浏览器、公开研究、代码读取等真实只读能力可安全执行，同时为未来 MCP 迁移保留端口。

**文件：**

- Create: packages/contracts/tools/browser.read.v1.json
- Create: packages/contracts/tools/public-research.get.v1.json
- Create: packages/contracts/tools/repository.read.v1.json
- Create: apps/worker/src/launchscope_worker/runtime/sandbox.py
- Create: apps/worker/src/launchscope_worker/runtime/lease.py
- Create: apps/worker/src/launchscope_worker/tool_gateway/contract.py
- Create: apps/worker/src/launchscope_worker/tool_gateway/mcp_adapter.py
- Create: apps/worker/src/launchscope_worker/tool_gateway/internal_adapter.py
- Create: apps/worker/src/launchscope_worker/tools/browser_product_audit.py
- Create: apps/worker/src/launchscope_worker/tools/public_research.py
- Create: apps/worker/src/launchscope_worker/tools/repository_read.py
- Create: infra/higress/entry-gateway.yaml
- Create: infra/higress/egress-gateway.yaml
- Create: infra/higress/domain-allowlist.yaml
- Create: apps/worker/tests/test_tool_contract.py
- Test: apps/worker/tests/test_runtime_isolation.py
- Test: apps/worker/tests/test_ssrf_policy.py
- Test: apps/worker/tests/test_tool_idempotency.py

**步骤：**

1. Tool Contract 统一声明版本、输入/输出、风险、权限、域名、超时、重试、幂等、费用、证据要求和数据出境规则。
2. Worker 默认无长期密钥、无任意网络、无仓库脚本执行；通过 Higress Egress 获取短期能力 token。
3. Public Research 只允许公开 HTTPS GET/HEAD、白名单/校验重定向、有限流量和预算；搜索结果只用于来源发现，source-fetch 必须读取原文并保存来源元数据。
4. Authenticated Research 需要指定域名、授权账号、时长和审批；External Action 在 V0.1 默认禁用。
5. 只有明确“未执行”且状态已知的无副作用瞬时失败可以有限重试；未知提交、费用或副作用状态直接 NEEDS_ATTENTION。

**测试命令：**

~~~powershell
python -m pytest apps/worker/tests -q
python -m pytest tests/security/test_ssrf_policy.py -q
docker compose -f infra/compose/docker-compose.test.yml up -d higress
~~~

**预期验收证据：**

- 一次真实测试环境 Run 通过浏览器和搜索工具完成只读调用，输出截图/来源/抓取时间/摘要和 ToolInvocation。
- loopback、私网、云元数据、DNS rebinding、未校验重定向均被拒绝；代码 Worker 不能访问网络。
- Adapter 替换 MCP Server 时只改变传输适配，不改变 Skill/Agent/领域契约。

### 7.10 T9：实现审计、记忆/RAG、规则决策、报告和版本复验

**依赖：** T3、T4、T6、T7、T8。
**目的：** 形成“线—面—环”后半段，生成可展开报告并把 V1/V2 变化写回 Product Dossier。

**文件：**

- Create: apps/api/src/launchscope_api/modules/evidence/auditor_application.py
- Create: apps/api/src/launchscope_api/modules/memory_rag/candidate_application.py
- Create: apps/api/src/launchscope_api/modules/memory_rag/retrieval_policy.py
- Create: apps/api/src/launchscope_api/modules/memory_rag/indexing.py
- Create: apps/api/src/launchscope_api/modules/decision_report/rule_application.py
- Create: apps/api/src/launchscope_api/modules/decision_report/synthesis_application.py
- Create: apps/api/src/launchscope_api/modules/decision_report/report_renderer.py
- Create: apps/api/src/launchscope_api/modules/decision_report/regression_application.py
- Modify: packages/contracts/openapi/control-plane.v1.yaml
- Create: packages/contracts/events/decision-events.v1.json
- Test: apps/api/tests/integration/test_evidence_auditor.py
- Test: apps/api/tests/integration/test_memory_promotion.py
- Test: apps/api/tests/integration/test_rag_scope.py
- Test: apps/api/tests/integration/test_decision_blocking.py
- Test: apps/api/tests/integration/test_version_regression.py

**步骤：**

1. Auditor 校验 Finding 的 Evidence 引用、来源可信度、冲突、有效期、越权和模拟标签，结果只能 pass/degrade/reject/request_more_evidence。
2. MemoryCandidate 只有用户确认事实或通过校准且绑定 Evidence 的 Finding 才能 promote；模拟意见保留 simulated 标签；过期政策、价格和趋势自动失效。
3. RAG 查询先过滤 tenant/project/version/region/time/permission，再做全文与向量排序；检索结果记录过滤条件、命中 Evidence 和结果 hash。
4. 规则引擎计算四维等级和 hard blocks；Synthesis 只把规则结果、审计 Finding 和证据链编排成可读解释。
5. Report 首页输出当前阶段、四维画像、关键矛盾、最大机会/风险、信息缺口、1—3 个行动和版本变化。
6. V2 复验复用同一 project_id、核心测试任务、standard_version 和可比较的 Manifest 组件；若标准改变，分开记录同标准结果与新标准补充结果。

**测试命令：**

~~~powershell
python -m pytest apps/api/tests/integration/test_evidence_auditor.py -q
python -m pytest apps/api/tests/integration/test_memory_promotion.py -q
python -m pytest apps/api/tests/integration/test_rag_scope.py -q
python -m pytest apps/api/tests/integration/test_decision_blocking.py -q
python -m pytest apps/api/tests/integration/test_version_regression.py -q
~~~

**预期验收证据：**

- 一条报告关键结论可通过 report_id → decision_id → finding_id → evidence_id → object hash/source metadata 展开。
- 无证据、过期或越权结论被降级/驳回；硬阻断能覆盖其他维度优势。
- V1/V2 报告明确回答：已解决问题、仍失败问题、被证实/推翻假设、证据等级变化、新风险和建议变化。

### 7.11 T10：实现 Web/OPS、REST、SSE 和对象直传体验

**依赖：** T5、T7、T9。
**目的：** 提供普通用户可理解的提交/追问/运行/报告界面，同时把评委和管理员观测入口与租户工作台隔离。

**文件：**

- Create: apps/web/src/app/(workspace)/projects/page.tsx
- Create: apps/web/src/app/(workspace)/projects/[projectId]/page.tsx
- Create: apps/web/src/app/(workspace)/projects/[projectId]/versions/[versionId]/page.tsx
- Create: apps/web/src/app/(workspace)/projects/[projectId]/new-evaluation/page.tsx
- Create: apps/web/src/app/(workspace)/runs/[runId]/page.tsx
- Create: apps/web/src/app/(workspace)/reports/[reportId]/page.tsx
- Create: apps/web/src/app/(workspace)/projects/[projectId]/compare/[runId]/page.tsx
- Create: apps/web/src/lib/api-client.ts
- Create: apps/web/src/lib/sse-client.ts
- Create: apps/web/src/components/evidence/EvidenceChain.tsx
- Create: apps/web/src/components/runs/RunTimeline.tsx
- Create: apps/web/src/components/profile/ProfileConfirmation.tsx
- Create: apps/ops/src/app/audit/runs/[runId]/page.tsx
- Create: apps/ops/src/app/audit/events/page.tsx
- Create: apps/web/tests/unit/sse-reconnect.test.ts
- Test: apps/web/tests/e2e/launchscope-v01.spec.ts
- Test: apps/ops/tests/e2e/ops-tenant-boundary.spec.ts

**步骤：**

1. 实现项目工作台、新建验证、补充问题、画像确认、Agent 运行页、Product Dossier、报告首页和 V1/V2 对比页。
2. 文件使用 S3-compatible signed URL；前端只拿短期 URL，不在浏览器保存长期凭据。
3. SSE 推送任务进度、Agent 状态、工具摘要、补证和审批请求；断线后按数据库状态和事件 cursor 恢复，不能只依赖内存。
4. 普通用户不展示私密推理链；评委/Ops 只能在独立身份域查看脱敏后的 Agent 状态、工具调用、失败记录和 Evidence。
5. 所有写操作展示 correlation_id/幂等结果和可解释错误，不以“成功”文案替代后端事实状态。

**测试命令：**

~~~powershell
pnpm --filter web lint
pnpm --filter web test
pnpm exec playwright test apps/web/tests/e2e/launchscope-v01.spec.ts
pnpm exec playwright test apps/ops/tests/e2e/ops-tenant-boundary.spec.ts
~~~

**预期验收证据：**

- 浏览器录屏/截图显示材料提交、补问、画像确认、运行时间线、Evidence 展开、报告和版本对比。
- SSE 断线重连后没有重复任务/重复状态，页面恢复到数据库事实；Ops 不可越界读取租户正文。
- UI 只展示真实 API/事件结果；没有预制报告、假成功或 mock 代替主流程。

### 7.12 T11：实现观测、预算、保留、部署和运维门

**依赖：** T4、T7、T8、T9、T10。
**目的：** 让运行可观测、费用可控、数据可删除、Local/Demo 可复现。

**文件：**

- Create: packages/observability/src/launchscope_observability/semconv.py
- Create: packages/observability/src/launchscope_observability/redaction.py
- Create: apps/api/src/launchscope_api/modules/usage_quota/budget_application.py
- Create: apps/api/src/launchscope_api/modules/audit_compliance/retention_application.py
- Create: infra/observability/otel-collector.yaml
- Create: infra/observability/agentscope-studio.yaml
- Create: infra/nacos/config-schema.json
- Create: infra/rocketmq/topics.yaml
- Create: infra/polardb/backup-policy.yaml
- Create: infra/compose/README.md
- Create: docs/runbooks/retention-and-delete.md
- Create: docs/runbooks/unknown-submission.md
- Create: apps/api/tests/integration/test_budget_reservation.py
- Test: tests/security/test_observability_redaction.py
- Test: apps/api/tests/integration/test_retention_delete.py

**步骤：**

1. 按 EvaluationRun → Stage → AgentTask → SkillInvocation → LLM/Tool/RAG/EvidenceWrite 建立 Trace 语义；敏感正文只存受控 Evidence 库。
2. Run 启动前预留 Token、工具、搜索、浏览器时间和费用预算；超额进入 WAITING_FOR_BUDGET 或 NEEDS_ATTENTION。
3. 实现 7 天临时文件、90 天网页/截图、30 天 Trace 正文、1 年 Metrics、1 年审计的默认策略及租户覆盖入口。
4. 删除 Project/Run 时清理正文、对象、向量、缓存、派生索引；审计只留下删除哈希、操作者、原因和结果。
5. 固定 Local/Demo 配置 schema、镜像 digest、迁移版本、Nacos 配置 hash 和 Secret 引用；生产部署不在本任务范围内。

**测试命令：**

~~~powershell
python -m pytest apps/api/tests/integration/test_budget_reservation.py -q
python -m pytest tests/security/test_observability_redaction.py -q
python -m pytest apps/api/tests/integration/test_retention_delete.py -q
docker compose -f infra/compose/docker-compose.yml config
~~~

**预期验收证据：**

- Trace/Log/Metrics 显示完整调用链但不含 prompt、密钥、材料正文或私密思维链。
- 预算预留、消费、释放和未知费用状态均可审计；预算超额不继续调用外部工具。
- 删除验证报告列出 DB/object/vector/cache/derived index 的结果；跨租户和过期对象均无残留业务正文。

### 7.13 T12：执行真实只读 E2E、V1/V2 复验和发布验收

**依赖：** T1—T11 全部通过。
**目的：** 生成符合基线和赛道要求的可观察、可复验、可审计证据包；这一步不能由健康检查、单测、Mock 或录像单独替代。

**文件：**

- Create: tests/e2e/fixtures/v1/product-materials/
- Create: tests/e2e/fixtures/v2/product-materials/
- Create: tests/e2e/fixtures/expected/standard-v1.json
- Create: tests/e2e/test_vertical_slice.py
- Create: tests/e2e/test_v1_v2_regression.py
- Create: tests/integration/test_real_readonly_tools.py
- Create: tests/security/test_full_security_gate.py
- Create: scripts/verify-v01.ps1
- Create: docs/runbooks/v01-demo.md
- Create: README.md
- Create: LICENSE

**步骤：**

1. 选择一个能公开演示、实际访问和修改的主案例；案例选择是业务决策，不在本计划中替用户假定。
2. 以测试环境、固定预算、白名单账号和明确授权执行一条真实只读 Run：提交材料 → 追问 → 画像确认 → Manager + 至少 3 个不同职能 Agent 实际交接 → 浏览器/搜索调用 → Evidence 审查 → Report。
3. 保存脱敏的 RunManifest、Agent Identity、Task DAG、Matrix handoff、RocketMQ event、API transcript、ToolInvocation、Evidence index、截图、Trace summary 和 Report。
4. 修改主案例材料为 V2；复用同一 project_id、核心任务脚本和 standard_version，重新执行并生成版本差异报告。
5. 单独触发已知失败、审批拒绝、预算不足、提示注入、SSRF、跨租户和 SUBMISSION_UNKNOWN 测试；未知提交只证明冻结和无重复提交，不做重试。
6. 运行质量门、整理提交材料；真实付费调用不进入普通 CI。

**测试命令：**

~~~powershell
pwsh -File scripts/verify-v01.ps1 -Environment Test -BudgetLimit 200
python -m pytest tests/e2e/test_vertical_slice.py -q
python -m pytest tests/e2e/test_v1_v2_regression.py -q
python -m pytest tests/integration/test_real_readonly_tools.py -q
python -m pytest tests/security/test_full_security_gate.py -q
pnpm exec playwright test apps/web/tests/e2e/launchscope-v01.spec.ts
~~~

**预期验收证据：**

验收包建议写入 artifacts/acceptance/<run-id>/，至少包含：

~~~text
run-manifest.json
agent-identities/
task-dag.json
api-transcript.redacted.ndjson
matrix-handoff.redacted.ndjson
rocketmq-events.redacted.ndjson
tool-invocations.redacted.ndjson
evidence-index.json
screenshots/
trace-summary.json
report-v1.html
report-v2.html
version-regression.json
security-gate.txt
retention-delete-report.json
hashes.txt
~~~

必须能从证据包证明：

- 真实多 Agent 协作不是顺序拼接 Prompt；至少 3 个不同职能 Agent 实际交接，建议展示完整 1+5。
- 浏览器和搜索工具真实调用并保存截图、来源、发布时间/抓取时间、适用地区和 Evidence hash。
- 缺资料会主动追问；用户确认画像后才进入正式评审。
- Auditor 至少驳回/降级一项无依据或冲突结论；原始 Finding 未被覆盖。
- 高风险/认证访问出现人工审批或明确被拒绝；无授权动作没有外部副作用。
- V1/V2 报告回答已解决、未解决、证实/推翻假设、证据等级、新风险和建议变化。
- RLS、SSRF、恶意文件、提示注入、密钥脱敏、预算、重放、保留删除和 SUBMISSION_UNKNOWN 全部有可重放的测试输出。
- README 能让新用户启动 Local，导入样例材料并完成闭环；健康检查、Mock、单元测试和录像不单独算 E2E 通过。

### 7.14 T13：全链路收尾与演示前端（2026-08-06 追补定义）

原始计划正式任务只定义到 T12；本节按后续执行授权补充，不改写冻结架构基线。T13 的边界是把已实现的持久化能力收口成可实际操作的 Web/Ops 演示，而不是扩张 Agent、Provider 或付费能力。

1. Web 覆盖 Project、材料直传、缺口补问、人工确认、Plan、Run/SSE、Evidence、Report 和同标准 V1/V2 Compare；所有状态来自本地 FastAPI + PostgreSQL/RLS，禁止静态假数据。
2. Ops 使用独立身份和数据库角色，只消费运行状态、阶段、标准、时间和事件元数据；禁止租户材料、报告正文、Evidence、Prompt 或私密推理进入投影。
3. Evidence 正文只能在完成租户授权后获得 60—900 秒私有签名 GET；浏览器上传继续使用受内容长度、MIME、ACL 和 SHA-256 约束的签名 PUT。
4. 真实浏览器闭环必须保存脱敏截图和请求状态；外部研究、在线 AgentTeams/Matrix、LLM/搜索 Provider 仍需另行授权，不得由本地确定性切片冒充。

### 7.15 T14：测试、文档与验收收口（2026-08-06 追补定义）

T14 是发布前本地质量门，不改变领域状态机或冻结边界。

1. 运行全量 pytest、Ruff、Mypy、前端类型检查/测试/生产构建、Alembic head 与 Compose config；失败先定位修复再生成最终证据。
2. 导出 body-free 的 RunManifest、Agent hash、Task DAG、结构化 handoff、ToolInvocation、Evidence/Audit/Decision/Report 索引、Outbox、SSE 历史、预算、版本对比和哈希清单。
3. 明确区分“本地真实 PostgreSQL/MinIO/API/Web 闭环”“确定性本地只读工具”与“真实外部 E2E”；缺少授权主案例或 Provider 时后者必须记录为 BLOCKED。
4. 完成整体代码审查、启动手册和次日演示路径；不提交、不推送、不部署、不写入线上资源。

## 8. 质量门、命令矩阵和完成定义

### 8.1 分层质量门

| 层级 | 命令 | 通过标准 | 证据 |
|---|---|---|---|
| 静态/依赖/密钥/许可证 | python -m ruff check apps packages；pnpm lint；secret/license/SBOM scanner | 零阻断问题；无 secret | CI report、SBOM、license report |
| 领域规则/状态机 | python -m pytest packages/domain/tests -q | 全绿，含非法转移和 fail-closed | pytest XML + coverage |
| API/事件/Skill/Tool 契约 | python -m pytest packages/contracts/tests apps/api/tests -q | schema、幂等、上一版兼容全绿 | contract report |
| 基础设施集成 | docker compose ...；python -m pytest apps/api/tests/integration -q | PostgreSQL/MinIO/RocketMQ/Nacos/Higress 边界通过 | compose logs、integration report |
| 安全评测 | python -m pytest tests/security -q | tenant/SSRF/injection/secrets/approval/unknown 全绿 | security-gate.txt |
| Web/浏览器 | pnpm exec playwright test ... | 提交、补问、运行、报告、对比、断线恢复通过 | screenshots/video/trace |
| 真实 E2E | pwsh -File scripts/verify-v01.ps1 ... | 真实只读工具、证据、审计、V1/V2 可重放 | acceptance artifact bundle |

### 8.2 命令执行顺序

~~~powershell
pnpm install --frozen-lockfile
python -m ruff check apps packages
python -m mypy packages/domain/src apps/api/src apps/orchestrator/src apps/worker/src
python -m pytest packages/domain/tests packages/contracts/tests -q
docker compose -f infra/compose/docker-compose.test.yml up -d
python -m pytest apps/api/tests apps/orchestrator/tests apps/worker/tests -q
python -m pytest tests/security -q
pnpm --filter web lint
pnpm --filter web test
pnpm exec playwright test apps/web/tests/e2e/launchscope-v01.spec.ts
pwsh -File scripts/verify-v01.ps1 -Environment Test -BudgetLimit 200
~~~

除最后一条真实测试环境命令外，普通 CI 不得产生付费外部调用。若任何 Provider/外部系统返回状态未知，立即停止该 Run 的重试、切换和补偿，保留红色证据，不得把“不确定”转成 PASS。

### 8.3 完成定义

只有以下条件全部满足，V0.1 才能宣称完成：

1. 一个新用户按 README 能启动 Local，导入样例材料，完成补问、画像确认、至少三类 Agent 协作、真实只读工具调用、证据审查、报告和 V1/V2 复验。
2. 所有关键报告结论能展开到 Finding 和 Evidence；无证据的内容明确为假设/缺口。
3. RunManifest、标准、Agent、Skill、Tool、Prompt、模型、预算、权限和测试脚本版本可追溯。
4. PostgreSQL/RLS、Object Store、RocketMQ Outbox/Inbox、Higress 双网关、短期凭据、审批和审计边界通过测试。
5. 失败分类、预算、lease、幂等、SUBMISSION_UNKNOWN、保留删除和恢复行为有自动化证据。
6. 未实现的自动发布、联系客户、生产写入、复杂计费、多区域 HA 和任意代码执行仍被明确阻断。

## 9. 本轮计划产出与交接边界

本轮只落盘本文件。第 2—6 节是可以直接交给实现人员和评审人员的领域模型、ER 图、状态机、模块边界及安全模型；第 7—8 节是按依赖顺序拆解的实现计划、测试命令和验收证据。

本轮不声称以下任何事实已经发生：

- 没有创建 apps、packages、infra 或 tests 代码。
- 没有启动 Compose、数据库、消息队列、AgentTeams、Matrix、浏览器或外部工具。
- 没有进行真实模型/搜索/浏览器/付费调用，因此没有真实 E2E 通过证据。
- 没有选择最终比赛样例产品。
- 没有修改基线或 reference/ 文件。
- 没有执行任何 Git 提交、推送或部署。

未来实施必须从 T1 开始，按依赖顺序推进；每完成一个任务，先保存该任务列出的测试输出和验收证据，再进入下一个任务。任何需要改变冻结架构的需求，先停在对应 ADR 门，不得通过“临时配置”绕过。
