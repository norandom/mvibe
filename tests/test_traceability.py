"""Acceptance declarations and execution evidence are separate contracts."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def helper():
    spec = importlib.util.spec_from_file_location('verification', ROOT / 'scripts/verification.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mapping():
    return json.loads((ROOT / 'tests/traceability.json').read_text())


def test_every_criterion_has_real_named_evidence():
    helper().validate_mapping(ROOT, mapping())


@pytest.mark.parametrize('mutation', ['unknown', 'missing', 'empty', 'invented'])
def test_invalid_acceptance_declarations_fail(mutation):
    data = mapping()
    if mutation == 'unknown':
        data['criteria']['99.1'] = data['criteria']['1.1']
    elif mutation == 'missing':
        del data['criteria']['1.1']
    elif mutation == 'empty':
        data['criteria']['1.1'] = []
    else:
        data['criteria']['1.1'] = [{'kind': 'test', 'suite': 'python', 'file': 'tests/test_proxy.py', 'name': 'test_invented'}]
    with pytest.raises(ValueError):
        helper().validate_mapping(ROOT, data)


@pytest.mark.parametrize('outcome', ['failed', 'skipped', 'unverified'])
def test_mandatory_nonpass_is_not_compliant(outcome):
    evidence = [{'status': outcome}]
    assert helper().criterion_status(evidence) != 'passed'


def test_collection_is_not_execution_and_missing_report_fails(tmp_path):
    with pytest.raises(ValueError):
        helper().read_junit(tmp_path / 'missing.xml')
    report = tmp_path / 'collected.xml'
    report.write_text('<testsuites><testsuite tests="20"/></testsuites>')
    with pytest.raises(ValueError):
        helper().read_junit(report)


def test_junit_matches_executed_cases_and_preserves_parameter_failures(tmp_path):
    report = tmp_path / 'run.xml'
    report.write_text('<testsuites><testsuite><testcase classname="tests.test_proxy" name="test_case[a]" time="0.1"/><testcase classname="tests.test_proxy" name="test_case[b]" time="0.1"><skipped/></testcase><testcase classname="tests.test_proxy" name="test_other" time="0.1"><failure>private diagnostics</failure></testcase></testsuite></testsuites>')
    rows = helper().read_junit(report)
    assert helper().test_status(rows, 'tests/test_proxy.py', 'test_case') == 'skipped'
    assert helper().test_status(rows, 'tests/test_proxy.py', 'test_other') == 'failed'
    assert helper().test_status(rows, 'tests/test_proxy.py', 'test_invented') == 'unverified'
    assert 'private diagnostics' not in json.dumps(rows)


def test_documentary_declaration_is_required_but_not_pretended_passed(tmp_path):
    item = {'kind': 'document', 'path': 'future.md', 'anchor': 'Evidence', 'required': True}
    assert helper().document_status(tmp_path, item) == 'unverified'
    (tmp_path / 'future.md').write_text('# Different\n')
    assert helper().document_status(tmp_path, item) == 'unverified'
    (tmp_path / 'future.md').write_text('# Evidence\nObserved behavior and limitations.\n')
    assert helper().document_status(tmp_path, item) == 'passed'


def test_native_failure_overrides_passing_evidence():
    assert helper().step_status(1, 'passed') == 'failed'
    assert helper().step_status(0, 'skipped') == 'failed'
    assert helper().step_status(0, 'unverified') == 'failed'


def test_baseline_is_additional_mandatory_gate_and_preserves_parent_mapping():
    assert len(mapping()['criteria']) == 97
    assert set(helper().STEPS) == {'python', 'provider', 'typecheck', 'provider-build',
        'python-build', 'bridge-process', 'bridge-root-exit', 'host-setup', 'host', 'tools', 'tool-baseline'}


def test_offline_runner_has_explicit_prepared_tools_and_no_setup_action():
    script = (ROOT / 'scripts/verify.ps1').read_text()
    assert '[string]$Bun' in script and '[string]$HostBinary' in script
    assert '--locked' in script and '--offline' in script
    assert 'compatibility:current' not in script and 'prepare:host' not in script


def execution_fixture(tmp_path):
    """Synthetic report inputs exercise validation; these are never acceptance outputs."""
    from datetime import datetime, timezone
    import xml.etree.ElementTree as ET
    module = helper()
    data = mapping()
    for suite in ('python', 'provider'):
        root = ET.Element('testsuites')
        tests = ET.SubElement(root, 'testsuite')
        seen = set()
        for evidence in data['criteria'].values():
            for item in evidence:
                if item['kind'] != 'test' or item['suite'] != suite:
                    continue
                key = (item['file'], item['name'])
                if key in seen:
                    continue
                seen.add(key)
                ET.SubElement(tests, 'testcase', file=item['file'], name=item['name'], time='0.01')
        ET.ElementTree(root).write(tmp_path / f'{suite}.xml')
    (tmp_path / 'execution.json').write_text(json.dumps({
        'run_id': tmp_path.name, 'mode': 'offline', 'started': datetime.now(timezone.utc).isoformat(),
        'steps': [dict(name=name, exit_code=0, status='passed') for name in module.STEPS]}))
    for file, kind, checks in [('startup', 'offline-local-startup', module.STARTUP_CHECKS),
                               ('host', 'offline-host-compatibility', module.HOST_CHECKS)]:
        (tmp_path / f'{file}.json').write_text(json.dumps(dict(kind=kind, status='passed', live='unverified', checks=dict.fromkeys(checks, True))))
    (tmp_path / 'tool-baseline').mkdir()
    (tmp_path / 'tool-baseline' / 'baseline.json').write_text(json.dumps(dict(
        kind='offline-tool-baseline', status='passed', mode='offline', live='unverified',
        probes=[dict(name=name, status='passed', exitCode=0) for name in ('query', 'mutation', 'command', 'mcp')],
        compilation=dict(status='passed'), contracts=dict(status='passed'))))
    return module


def test_final_acceptance_reports_pending_documents_without_breaking_declaration_tests(tmp_path, monkeypatch):
    module = execution_fixture(tmp_path)
    monkeypatch.setattr(module, 'document_status', lambda root, item: 'unverified')
    result = module.acceptance(ROOT, tmp_path)
    assert result['offline'] == 'passed'
    assert result['criteria']['12.5']['status'] == 'passed'
    # Required future documents remain explicit. No manufactured future evidence.
    assert result['criteria']['9.7']['status'] == 'unverified'
    assert result['status'] == 'failed' and result['live'] == 'unverified'


@pytest.mark.parametrize('mutation', ['native', 'missing-step', 'missing-case', 'skipped-case', 'host-check', 'stale'])
def test_acceptance_rejects_invalid_or_incomplete_execution(tmp_path, mutation):
    module = execution_fixture(tmp_path)
    if mutation in ('native', 'missing-step'):
        path = tmp_path / 'execution.json'
        manifest = json.loads(path.read_text())
        if mutation == 'native':
            manifest['steps'][0]['exit_code'] = 23
        else:
            manifest['steps'].pop()
        path.write_text(json.dumps(manifest))
    elif mutation == 'host-check':
        path = tmp_path / 'host.json'
        data = json.loads(path.read_text())
        data['checks'].pop('cancellation')
        path.write_text(json.dumps(data))
    elif mutation == 'stale':
        import os
        os.utime(tmp_path / 'python.xml', (0, 0))
    else:
        path = tmp_path / 'python.xml'
        data = path.read_text()
        if mutation == 'missing-case':
            data = data.replace('test_allowed_routes_use_only_current_public_snapshot', 'test_unmapped')
        else:
            data = data.replace(' />', '><skipped/></testcase>', 1)
        path.write_text(data)
    if mutation in ('missing-step', 'stale'):
        with pytest.raises(ValueError):
            module.acceptance(ROOT, tmp_path)
    else:
        result = module.acceptance(ROOT, tmp_path)
        assert result['status'] == 'failed'
        if mutation == 'missing-case':
            assert result['criteria']['1.1']['status'] == 'unverified'
        else:
            assert result['offline'] == 'failed'
