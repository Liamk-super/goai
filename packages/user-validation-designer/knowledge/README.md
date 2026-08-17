# 知识库绑定索引（user-validation-designer）

> 本目录**不复制知识库正文**。只记录哪些 KB ID 管辖哪个检查，
> 以便 `src/knowledge.mjs` 绑定真实检索器时调用方无需改动。
> 知识库原件：《用户共创Agent 用户研究知识库与行为决策逻辑 V1.0》。

未绑定检索器时，`retrieve()` 返回 `{ status: "retriever_unavailable", kb_ids, passages: [] }`
—— 返回 ID 用于可追溯，**不伪造 passage 文本**。

## concern → KB ID

| concern | KB IDs | 使用单元 |
|---|---|---|
| `target_user_admission` | KB-USR-R04, KB-USR-P02, KB-USR-S1 | S1 |
| `persona_modeling` | KB-USR-F01, KB-USR-G01, KB-USR-G02, KB-USR-G03, KB-USR-S1 | S2 |
| `jtbd` | KB-USR-F02, KB-USR-S2 | S2, S3 |
| `scenario_alternatives` | KB-USR-F02, KB-USR-F03, KB-USR-S2 | S3 |
| `pain_analysis` | KB-USR-F04 | S3, S4b |
| `first_experience` | KB-USR-F03, KB-USR-F06, KB-USR-S3, KB-USR-B01 | S4a |
| `task_test` | KB-USR-F06, KB-USR-F04, KB-USR-S4 | S4b |
| `interview_simulation` | KB-USR-F07, KB-USR-S5, KB-USR-B02 | S5 |
| `feedback_analysis` | KB-USR-F05 | S5 |
| `hypothesis_extraction` | KB-USR-F05, KB-USR-P01 | S5 |
| `validation_design` | KB-USR-V01, KB-USR-V02, KB-USR-V03, KB-USR-V04, KB-USR-S6 | S6 |
| `scoring` | KB-USR-VS01, KB-USR-VS02, KB-USR-VS03 | S7 |
| `behavior_rules` | KB-USR-B01, KB-USR-B02, KB-USR-B03, KB-USR-B04 | S2–S5 |
| `guardrails` | KB-USR-P01, KB-USR-P02, KB-USR-P03 | 全程 |
| `handoff` | KB-USR-R02, KB-USR-VS03 | S7 |
| `report_template` | KB-USR-T01 | S7 |

## 阈值规则的落点

KB 中所有「如果……那么……」规则**不经由 RAG 传递给模型**，而是编译进程序
（`SKILL_SPEC_V0.1.md` 第四节 A-01 ~ A-29）。检索只用于向模型提供方法论说明与判断口径。

原因：阈值走 RAG 意味着"检索没命中就没有约束"。硬规则必须是无条件的。
