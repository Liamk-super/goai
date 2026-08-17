# Benchmark adapters

Adapters are replaceable execution and observation edges. The canonical Case, Oracle, Rubric, Run Manifest and scorer
remain in the native Python package.

| Tool | V1 role | Why | Boundary |
|---|---|---|---|
| Promptfoo 0.121.19 | local model and single-Agent matrix | mature provider matrix, assertions and repeatable exports | no system DAG or Gold ownership; Node 24; no sharing/cache for formal runs |
| AgentScope Studio | local trace visualization candidate | competition-oriented open-source trace inspection | disabled until a pinned compatible backend proves ingestion and UI display |
| Alibaba Cloud AgentLoop | optional continuous-evaluation upper layer | trace/dataset experiments, regression and cost/latency analysis | disabled by default; no cloud account, upload or charge without approval |
| EvalScope | ADR alternative | broad model/Agent benchmark support | deferred to avoid overlapping V1 runner dependencies |
| AgentScope Evaluation/OpenJudge | ADR alternative | trajectory and grader framework | not used because AgentTeams remains the execution runtime |

Promptfoo is an independent package below this directory. Its Node 24 engine and lockfile do not alter the root
Next.js dependency graph.
