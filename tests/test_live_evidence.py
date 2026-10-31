"""Offline report validation; no account action is provided by this tool."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def validator():
    spec = importlib.util.spec_from_file_location("live_evidence", ROOT / "scripts/live_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def template():
    return json.loads((ROOT / "docs/live-evidence-template.json").read_text(encoding="utf-8"))


def exercised():
    report = template()
    report["consumers"]["proxy"]["versions"] = dict(client="1.0.0", provider="0.1.0", python="3.12.9", host=None, bun=None)
    report["consumers"]["proxy"]["configuration"] = dict(issuer="default", client="default", audience="default", tenant="automatic", callback_port=34211, transport="default", model_selection="account_catalog")
    report["operations"][0].update(status="PASS", reason="observed", observed_at="2026-10-31T12:00:00Z")
    return report


def test_template_is_complete_and_unverified():
    report = validator().validate_report(template())
    assert len(report["operations"]) == 18
    assert {row["status"] for row in report["operations"]} == {"UNVERIFIED"}
    assert report["offline"]["status"] == report["local_startup"]["status"] == "UNVERIFIED"


def test_exercised_report_needs_versions_configuration_and_timestamp():
    module = validator()
    module.validate_report(exercised())
    for field in ("versions", "configuration"):
        report = exercised()
        report["consumers"]["proxy"][field] = None
        with pytest.raises(ValueError):
            module.validate_report(report)
    report = exercised()
    report["operations"][0]["observed_at"] = None
    with pytest.raises(ValueError):
        module.validate_report(report)


@pytest.mark.parametrize("status,reason", [("ENTITLEMENT_LIMITATION", "capability_unavailable"), ("FAIL", "service_error"), ("PASS", "observed")])
def test_outcomes_remain_distinct_and_refresh_stays_unverified(status, reason):
    report = exercised()
    report["operations"][0].update(status=status, reason=reason)
    checked = validator().validate_report(report)
    assert checked["operations"][0]["status"] == status
    assert all(row["status"] == "UNVERIFIED" for row in checked["operations"] if row["operation"] == "refresh")


@pytest.mark.parametrize("mutation", [
    lambda r: r["operations"].pop(),
    lambda r: r["operations"].__setitem__(1, copy.deepcopy(r["operations"][0])),
    lambda r: r["operations"][0].update(status="SKIPPED"),
    lambda r: r["operations"][0].update(reason="not_exercised"),
    lambda r: r["operations"][1].update(status="PASS", reason="observed"),
    lambda r: r.update(schema_version=True),
    lambda r: r["consumers"]["proxy"]["versions"].update(python=None),
    lambda r: r["operations"][0].update(observed_at="invalid"),
])
def test_rejects_incomplete_inconsistent_and_malformed_reports(mutation):
    report = exercised()
    mutation(report)
    with pytest.raises(ValueError):
        validator().validate_report(report)


@pytest.mark.parametrize("secret", [
    "Bearer synthetic-private-token", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwcml2YXRlIn0.synthetic",
    "https://issuer.invalid/authorize?code=synthetic-private-code&state=private",
    "synthetic private prompt and account contents", "synthetic-refresh-token",
])
def test_rejects_private_payloads_in_every_string_slot(secret):
    module = validator()
    report = exercised()

    def paths(value, prefix=()):
        if isinstance(value, dict):
            for key, child in value.items():
                yield from paths(child, prefix + (key,))
        elif isinstance(value, list):
            for key, child in enumerate(value):
                yield from paths(child, prefix + (key,))
        elif isinstance(value, str):
            yield prefix

    for path in paths(report):
        candidate = copy.deepcopy(report)
        parent = candidate
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = secret
        with pytest.raises(ValueError):
            module.validate_report(candidate)


@pytest.mark.parametrize("key", ["access_token", "authorization_code", "auth_url", "prompt", "account", "notes"])
def test_rejects_unknown_fields_at_all_object_boundaries(key):
    module = validator()
    for path in [(), ("offline",), ("local_startup",), ("consumers",), ("consumers", "proxy"), ("consumers", "proxy", "versions"), ("consumers", "proxy", "configuration"), ("operations", 0)]:
        report = exercised()
        node = report
        for part in path:
            node = node[part]
        node[key] = "synthetic-secret"
        with pytest.raises(ValueError):
            module.validate_report(report)


def test_cli_reads_only_and_never_echoes_rejected_material(tmp_path, capsys):
    module = validator()
    path = tmp_path / "report.json"
    path.write_text(json.dumps(template()), encoding="utf-8")
    before = path.read_bytes()
    assert module.main([str(path)]) == 0
    assert path.read_bytes() == before
    assert "not proof" in capsys.readouterr().out
    path.write_text('{"authorization_code":"synthetic-private-code"}', encoding="utf-8")
    assert module.main([str(path)]) == 1
    assert "synthetic-private-code" not in str(capsys.readouterr())
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    assert module.main([str(path)]) == 1
    path.write_text("[" * 1500, encoding="utf-8")
    assert module.main([str(path)]) == 1
    assert module.main(["--live"]) == 1


def test_rejects_oversized_report_without_echo(tmp_path, capsys):
    module = validator()
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (module.MAX_BYTES + 1))
    assert module.main([str(path)]) == 1
    assert "rejected contents omitted" in capsys.readouterr().err


def test_direct_operation_requires_actual_host_and_bun_versions():
    module = validator()
    report = exercised()
    report["consumers"]["direct_provider"] = copy.deepcopy(report["consumers"]["proxy"])
    row = next(row for row in report["operations"] if row["consumer"] == "direct_provider")
    row.update(status="PASS", reason="observed", observed_at="2026-10-31T12:00:00Z")
    with pytest.raises(ValueError):
        module.validate_report(report)
    report["consumers"]["direct_provider"]["versions"].update(host="2.2.5", bun="1.4.4")
    module.validate_report(report)


@pytest.mark.parametrize("status,reason", [("PASS", "capability_unavailable"), ("ENTITLEMENT_LIMITATION", "service_error"), ("FAIL", "observed")])
def test_rejects_relabeling_failure_entitlement_or_pass(status, reason):
    report = exercised()
    report["operations"][0].update(status=status, reason=reason)
    with pytest.raises(ValueError):
        validator().validate_report(report)


def test_documentary_contracts_and_default_runner():
    procedure = (ROOT / "docs/live-verification.md").read_text(encoding="utf-8")
    results = (ROOT / "docs/live-results.md").read_text(encoding="utf-8")
    assert procedure.startswith("# Separate live verification")
    assert results.startswith("# Live verification evidence")
    for term in ("proxy", "direct provider", "browser", "reuse", "tenant", "models", "chat", "streaming", "tool", "embeddings", "refresh", "ENTITLEMENT_LIMITATION", "UNVERIFIED"):
        assert term in procedure
    assert "UNVERIFIED" in results
    runner = (ROOT / "scripts/verify.ps1").read_text(encoding="utf-8")
    assert "proxy.py --login" not in runner
    assert "live_evidence.py --live" not in runner
