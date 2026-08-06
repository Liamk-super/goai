"""Executable T1 schema checks.

The module intentionally uses unittest so the checks remain runnable in a
minimal Python environment. pytest can discover the same TestCase classes when
it is available.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "packages" / "contracts"
ENVELOPE_PATH = CONTRACTS / "events" / "envelope.schema.json"
EVENTS_PATH = CONTRACTS / "events" / "evaluation-events.v1.json"
COMMANDS_PATH = CONTRACTS / "commands" / "run-commands.v1.json"
UNIFIED_MODEL_PATH = CONTRACTS / "unified-model" / "launchscope-unified-model.v1.json"
OPENAPI_PATH = CONTRACTS / "openapi" / "control-plane.v1.yaml"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validator_for(schema: dict, *referenced_schemas: dict) -> Draft202012Validator:
    registry = Registry()
    for resource in (schema, *referenced_schemas):
        registry = registry.with_resource(resource["$id"], Resource.from_contents(resource))
    return Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )


def assert_valid(test_case: unittest.TestCase, validator: Draft202012Validator, instance: dict) -> None:
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    test_case.assertEqual([], errors, "\n".join(error.message for error in errors))


class JsonSchemaContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.envelope = read_json(ENVELOPE_PATH)
        cls.events = read_json(EVENTS_PATH)
        cls.commands = read_json(COMMANDS_PATH)
        cls.unified_model = read_json(UNIFIED_MODEL_PATH)

    def test_all_json_documents_are_valid_draft_2020_12_schemas(self) -> None:
        for path in CONTRACTS.rglob("*.json"):
            document = read_json(path)
            Draft202012Validator.check_schema(document)

    def test_event_examples_validate_against_event_union(self) -> None:
        validator = validator_for(self.events, self.envelope)
        for example in self.events["examples"]:
            assert_valid(self, validator, example)

    def test_command_examples_validate_against_command_union(self) -> None:
        validator = validator_for(self.commands, self.envelope)
        for example in self.commands["examples"]:
            assert_valid(self, validator, example)

    def test_unified_model_examples_validate(self) -> None:
        validator = validator_for(self.unified_model)
        for example in self.unified_model["examples"]:
            assert_valid(self, validator, example)

    def test_required_envelope_fields_are_rejected_when_missing(self) -> None:
        required = [
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
        ]
        event_validator = validator_for(self.events, self.envelope)
        command_validator = validator_for(self.commands, self.envelope)
        for field in required:
            event = copy.deepcopy(self.events["examples"][0])
            command = copy.deepcopy(self.commands["examples"][0])
            del event[field]
            del command[field]
            self.assertTrue(list(event_validator.iter_errors(event)), field)
            self.assertTrue(list(command_validator.iter_errors(command)), field)

    def test_p0_skill_set_is_constrained_by_the_unified_model_schema(self) -> None:
        p0_names = {
            "product-intake-normalizer",
            "intake-gap-diagnosis",
            "browser-product-audit",
            "business-investment-assessment",
            "evidence-grounding-audit",
            "version-regression-verification",
        }
        example = self.unified_model["examples"][0]
        skills = example["entities"]["skills"]
        self.assertEqual(p0_names, {skill["skill_ref"] for skill in skills if skill["tier"] == "P0"})
        reference_skill = next(skill for skill in skills if skill["skill_ref"] == "user-validation-designer")
        self.assertEqual("REFERENCE", reference_skill["tier"])

        invalid = copy.deepcopy(example)
        invalid_reference = next(
            skill for skill in invalid["entities"]["skills"] if skill["skill_ref"] == "user-validation-designer"
        )
        invalid_reference["tier"] = "P0"
        validator = validator_for(self.unified_model)
        self.assertTrue(list(validator.iter_errors(invalid)))

    def test_openapi_declares_write_headers_errors_pagination_and_sse(self) -> None:
        document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
        self.assertEqual("3.1.0", document["openapi"])
        self.assertEqual("1.0", document["info"]["x-contract-version"])

        write_operations = []
        for path, path_item in document["paths"].items():
            for method, operation in path_item.items():
                if method in {"post", "put", "patch", "delete"}:
                    write_operations.append((path, method, operation))
        self.assertGreaterEqual(len(write_operations), 3)
        for path, method, operation in write_operations:
            refs = {parameter.get("$ref") for parameter in operation.get("parameters", [])}
            self.assertIn("#/components/parameters/IdempotencyKey", refs, (path, method))
            self.assertIn("#/components/parameters/WriteCorrelationId", refs, (path, method))

        error_codes = set(document["components"]["schemas"]["ErrorResponse"]["properties"]["error_code"]["enum"])
        self.assertTrue({"IDEMPOTENCY_CONFLICT", "CURSOR_INVALID", "SUBMISSION_UNKNOWN"}.issubset(error_codes))
        self.assertEqual("#/components/parameters/Cursor", document["paths"]["/projects"]["get"]["parameters"][0]["$ref"])
        self.assertIn("Last-Event-ID", document["paths"]["/runs/{runId}/events"]["get"]["description"])
        self.assertIn("strictly after", document["paths"]["/runs/{runId}/events"]["get"]["description"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
