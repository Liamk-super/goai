"""Compatibility guards for the v1 event contract."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "packages" / "contracts"
ENVELOPE_PATH = CONTRACTS / "events" / "envelope.schema.json"
EVENTS_PATH = CONTRACTS / "events" / "evaluation-events.v1.json"

FROZEN_ENVELOPE_FIELDS = (
    "event_id",
    "tenant_id",
    "run_id",
    "task_id",
    "correlation_id",
    "causation_id",
    "idempotency_key",
    "schema_version",
    "occurred_at",
    "payload",
)

EXPECTED_EVENT_TYPES = {
    "project.created",
    "product_version.submitted",
    "intake.gap_identified",
    "profile.confirmed",
    "evaluation.run.started",
    "task.dispatched",
    "evidence.captured",
    "finding.submitted",
    "evidence.audit_completed",
    "approval.requested",
    "approval.resolved",
    "run.needs_attention",
    "decision.synthesized",
    "dossier.committed",
    "version.regression_completed",
    "run.completed",
    "run.failed",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def event_validator(events: dict, envelope: dict) -> Draft202012Validator:
    registry = Registry()
    registry = registry.with_resource(events["$id"], Resource.from_contents(events))
    registry = registry.with_resource(envelope["$id"], Resource.from_contents(envelope))
    return Draft202012Validator(events, registry=registry, format_checker=FormatChecker())


class EventCompatibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.events = read_json(EVENTS_PATH)
        cls.envelope = read_json(ENVELOPE_PATH)
        cls.validator = event_validator(cls.events, cls.envelope)

    def test_event_union_contains_the_frozen_lifecycle(self) -> None:
        actual = {example["event_type"] for example in self.events["examples"]}
        self.assertEqual(EXPECTED_EVENT_TYPES, actual)
        self.assertEqual(len(EXPECTED_EVENT_TYPES), len(self.events["oneOf"]))

    def test_every_current_event_has_the_shared_envelope(self) -> None:
        for example in self.events["examples"]:
            errors = list(self.validator.iter_errors(example))
            self.assertEqual([], errors, (example["event_type"], [error.message for error in errors]))
            self.assertEqual(set(FROZEN_ENVELOPE_FIELDS), set(example).intersection(FROZEN_ENVELOPE_FIELDS))
            self.assertEqual("1.0", example["schema_version"])

    def test_previous_consumer_rule_is_additive_and_ignores_payload_extensions(self) -> None:
        compatibility = self.events["x-compatibility"]
        self.assertEqual("accept-current-and-immediately-previous-released-minor", compatibility["consumer-rule"])
        self.assertTrue(compatibility["published-schema-is-immutable"])

        # This models the minimum behavior of a previous consumer: it reads the
        # frozen envelope and treats event-specific payload additions as opaque.
        for example in self.events["examples"]:
            previous_view = {field: example[field] for field in FROZEN_ENVELOPE_FIELDS}
            self.assertEqual(set(FROZEN_ENVELOPE_FIELDS), set(previous_view))
            self.assertIsInstance(previous_view["payload"], dict)

            future_event = copy.deepcopy(example)
            future_event["payload"]["future_optional_field"] = "ignored-by-old-consumer"
            future_view = {field: future_event[field] for field in FROZEN_ENVELOPE_FIELDS}
            self.assertEqual(previous_view[FROZEN_ENVELOPE_FIELDS[0]], future_view[FROZEN_ENVELOPE_FIELDS[0]])
            self.assertEqual(previous_view["idempotency_key"], future_view["idempotency_key"])

    def test_event_definitions_reference_event_envelope(self) -> None:
        for union_member in self.events["oneOf"]:
            definition_name = union_member["$ref"].rsplit("/", 1)[-1]
            definition = self.events["$defs"][definition_name]
            refs = {part.get("$ref") for part in definition["allOf"]}
            self.assertIn("envelope.schema.json#/$defs/EventEnvelope", refs, definition_name)

    def test_submission_unknown_is_explicitly_non_retryable(self) -> None:
        example = next(example for example in self.events["examples"] if example["event_type"] == "run.failed")
        self.assertEqual("SUBMISSION_UNKNOWN", example["payload"]["failure_class"])
        self.assertFalse(example["payload"]["retry_permitted"])
        self.assertEqual([], list(self.validator.iter_errors(example)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
