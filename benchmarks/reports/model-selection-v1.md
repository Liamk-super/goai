# LaunchScope Benchmark V1 model selection

- Date: 2026-08-09
- Decision: `kimi-k3` is the provisional default
- Confidence: provisional until one clean terminal AgentTeams E2E and a preserved three-model formal matrix are available

## Comparable evidence

The existing direct API rule smoke used the same two rule cases for all candidates. The preserved conclusion supplied
for this implementation is 2/2 for `kimi-k3`, 2/2 for `glm-5.2`, and 2/2 for `qwen3.8-max`. Raw provider responses and
Run Manifests from those calls are not present in the repository, so this is historical summary evidence rather than a
new formal Benchmark V1 matrix. No paid calls were repeated merely to recreate it.

| Model | Direct API rule smoke | Runtime-model evidence | Real AgentTeams E2E | Interpretation |
|---|---:|---|---|---|
| `kimi-k3` | 2/2 | verified from six Running AgentTeams Worker resources on 2026-08-09 | real run `5d1e308e-588f-4edc-b7f5-d8bc0540a23d`: Leader and product-engineering succeeded; browser and search tools produced evidence; run then fail-closed as `NEEDS_ATTENTION` on `TOOL_QUOTA_EXHAUSTED` | strongest current runtime evidence; the non-terminal result is not scored as model failure |
| `glm-5.2` | 2/2 | prior raw provider receipt is not preserved | not preserved | API parity only |
| `qwen3.8-max` | 2/2 | prior configuration was confirmed correct; raw live Worker snapshot is not preserved | not preserved | best-supported operational default, not a proven quality winner |

## Recommendation

Use `kimi-k3` as the provisional default because all three candidates tie on the available rule smoke and Kimi is now
the only candidate with current six-Worker runtime identity plus observed real AgentTeams, browser and search
execution. The authorized run was submitted once and was not retried: it reached `NEEDS_ATTENTION` because the
user-evidence Agent exhausted its browser Tool quota after preserving two captures. This is a system/tool-capacity
failure, not evidence that Kimi answered the formal Cases incorrectly. `qwen3.8-max` remains the first fallback
candidate for the next controlled comparison because its LaunchScope configuration is confirmed. The recommendation
is operational and reversible; it is not a formal quality win until the canonical three-repeat matrix and a clean
terminal AgentTeams E2E are preserved.

## Authorized live AgentTeams evidence

- Run: `5d1e308e-588f-4edc-b7f5-d8bc0540a23d`; requested runtime model `kimi-k3`.
- Identity: all six Running Worker resources reported exactly `kimi-k3` before and during the run.
- Observed path: durable dispatch, Leader planning success, parallel specialist execution, one successful
  product-engineering browser audit, one successful business-investment search, and two user-evidence browser
  captures.
- Terminal control state: `NEEDS_ATTENTION`; failure class `TOOL_QUOTA_EXHAUSTED`. Auditor and synthesis remained
  blocked, so this is not a passed System E2E.
- Safety action: no retry, failover, model switch, replacement submission, timeout extension or manual settlement.

## Acceptance boundary

- `MODEL_API`: `MODEL-EVD-01` and `MODEL-EVD-02`, same order, three uncached repeats per candidate; each response must
  report the exact requested model.
- `SINGLE_AGENT`: one Agent contract and its canonical Agent suite; provider API success is insufficient.
- `AGENTTEAMS_E2E`: six Running Worker resources, requested/observed model match, RocketMQ/Matrix handoffs, browser and
  search evidence, Auditor decision, terminal business state and reconciled usage.
- Public-anchor external facts remain `HUMAN_VERIFICATION_REQUIRED` and are judged blind against independent primary
  sources. They never become automated Gold from a model's own output.
