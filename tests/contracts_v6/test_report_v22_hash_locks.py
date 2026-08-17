from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"


def _canonical_hash(document: dict[str, object]) -> str:
    content = {key: value for key, value in document.items() if key != "content_sha256"}
    payload = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_v6_identity_hashes_are_self_verifying() -> None:
    paths = sorted((CONTRACTS / "manager" / "agents").glob("*.v6.yaml"))
    assert len(paths) == 5
    for path in paths:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert document["content_sha256"] == _canonical_hash(document)


def test_report_v22_contract_files_are_hash_locked() -> None:
    expected = {
        "audit/audit-result.v4.json": "4b8b23ebc4e0a7131b585734a4f47c8700aa581954e369288e9a613cbea7ff89",
        "manager/manager-synthesis.v2.json": "07847a5e4ec4e558f3beb817a6e9181709a449b61401c6da6a44c4d8042cb0f5",
        "manager/run-manifest.v6.json": "6423db96a234a8dd3d0f77e414ada071887d2bc7940ef47085bd6d1ca665ce01",
        "reports/citation-source.v1.json": "0a5857d65368f897fcc1fb02f03b824026ca4dd1647f387b1a67abae002bab3f",
        "reports/report-comparison.v1.json": "4e5c0ab91eb15f8766db77e7c3c8b73c664c76b2bf8ff769d407ec1bf75b2238",
        "reports/specialist-report.v2.json": "c4962bab2a7c99a1a94486c698c1be554b66f1d199b58183538e611b611e2c39",
        "reports/supervisor-report.v2.json": "a9e05912dee3982d16000d8116ad8f442f3e4fb40c5b06cd4bf04ba528839f23",
        "score/profiles/full-potential.v2.json": "fc09bc181ba5a1f7050c121b11d2c70f80119031fd5f20225e0407d2a4978ae4",
        "score/score-profile.v2.json": "ab944b565832d1f758192b4c61b12c0b6935416bb310d3271ced2b9c25d1c142",
    }
    actual = {
        relative: hashlib.sha256((CONTRACTS / relative).read_bytes()).hexdigest()
        for relative in expected
    }
    assert actual == expected


def test_report_v22_openapi_files_are_hash_locked() -> None:
    expected = {
        "openapi/agent-reports.v5.yaml": "5b8a2849639c9ee78beea0200bd3e84106ecf3be0ec182f2451da515190e9404",
        "openapi/public-demo-report.v2.yaml": "2a4466e9a5f8b190c6cbeef5c0e4b2c5d26ee7cb5b59656046459dc3c7ce4d0b",
        "openapi/report-experience.v2.yaml": "2b99d3fe80cb92b17ab9bd35c61ec5396fbbf94f15e1d4012154beb3017b9a36",
        "openapi/report-export.v1.yaml": "3f89733964f76f3a8da8a0fddd584a1113bc2bc26303cb9e38f1e1223ff0a425",
    }
    actual = {
        relative: hashlib.sha256((CONTRACTS / relative).read_bytes()).hexdigest()
        for relative in expected
    }
    assert actual == expected
