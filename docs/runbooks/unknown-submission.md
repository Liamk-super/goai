# Unknown submission and unknown billing runbook

`SUBMISSION_UNKNOWN` means an external operation or its charge may have
occurred, but the provider has not supplied a conclusive result. LaunchScope
must immediately persist `NEEDS_ATTENTION`, the failure class and a body-free
audit record.

While frozen, do not retry, fail over, switch model/provider/tool/runtime,
resubmit, manually settle, or edit state with raw SQL. Preserve the original
idempotency key, provider request reference, timestamps and hashes. A future
reconciliation command may release the freeze only after authoritative
provider evidence is obtained and a version/CAS check proves the Run has not
changed. No such reconciliation is exposed by V0.1.

Unknown cost follows the same rule: it is not zero and is not “settled”. Budget
consumption remains unrecorded until authoritative cost evidence exists, and
the Run remains frozen.
