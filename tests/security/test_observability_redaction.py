"""T11 proof that general telemetry cannot carry secrets or business bodies."""

from launchscope_observability import REDACTED, TRACE_LEVELS, payload_sha256, redact, safe_trace_attributes


def test_trace_hierarchy_is_complete_and_stable() -> None:
    assert TRACE_LEVELS == (
        "evaluation_run",
        "stage",
        "agent_task",
        "skill_invocation",
        "llm_call",
        "tool_call",
        "rag_retrieval",
        "evidence_write",
    )


def test_recursive_redaction_removes_secrets_prompts_and_reasoning() -> None:
    raw = {
        "authorization": "Bearer secret",
        "nested": {
            "api-key": "secret",
            "prompt": "tenant material body",
            "private_reasoning": "not for telemetry",
            "duration_ms": 25,
        },
    }
    safe = redact(raw)
    assert safe["authorization"] == REDACTED
    assert safe["nested"]["api-key"] == REDACTED
    assert safe["nested"]["prompt"] == REDACTED
    assert safe["nested"]["private_reasoning"] == REDACTED
    assert safe["nested"]["duration_ms"] == 25
    assert payload_sha256(raw) == payload_sha256(raw)


def test_unknown_trace_attributes_are_dropped_fail_closed() -> None:
    attributes = safe_trace_attributes(
        {
            "launchscope.run.id": "run-1",
            "launchscope.duration.ms": 42,
            "material_body": "private text",
            "prompt": "private prompt",
            "unreviewed.vendor.attribute": "must not escape",
        }
    )
    assert attributes == {"launchscope.run.id": "run-1", "launchscope.duration.ms": 42}
