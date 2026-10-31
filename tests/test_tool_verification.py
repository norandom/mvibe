"""Independent gate tests; synthetic graphs never demonstrate host execution."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import tool_verification as gate

ROOT = Path(__file__).resolve().parents[1]
VECTOR = ROOT / 'packages/opencode-vibe-oidc/test/tool-compatibility/vectors/report-v1.json'


def sample():
    vector = json.loads(VECTOR.read_text(encoding='utf8'))
    report = vector['report']
    origin = 'b' * 64
    definition = {'description': 'Owned synthetic read', 'inputSchema': {'type': 'object'}}
    schema = hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    for tool in [report['rows'][0]['tool'], *report['inventories'][0]['registered'], *report['inventories'][0]['offered']]:
        tool.update(schemaHash=schema, originHash=origin)
    for item in [*report['events'], *report['proofs']]:
        if 'toolId' in item:
            item.update(toolSchemaHash=schema, originHash=origin)
    turns = []
    for request in ('request-1', 'request-2'):
        snapshot = deepcopy(report['inventories'][0])
        snapshot['registered'][0].pop('offeredName')
        turns.append({'sessionId': 'session-1', 'requestId': request, 'snapshot': snapshot,
                      'diff': {'missingCases': [], 'changedSchemas': []}, 'gaps': [], 'inventoryComplete': True,
                      'provenance': [{'registryId': 'read', 'observation': {'kind': 'native', 'observationId': 'registry-1', 'configurationHash': origin}}],
                      'negotiatedMcp': [], 'absenceObservations': [],
                      'effectiveDefinitions': [{'offeredName': 'read', 'registryId': 'read', 'schemaHash': schema}]})
    context = {'mode': 'offline', 'scopes': deepcopy(report['scopes']), 'caseManifestHash': report['caseManifestHash'],
               'criteriaMappingHash': report['criteriaMappingHash'], 'limits': vector['runSpec']['limits'], 'turns': turns}
    return report, context, [report['scopes'][0]['hash']], [vector['caseSpec']]


def test_valid_scoped_positive():
    report, context, scopes, cases = sample()
    assert gate.validate_report(report, context, scopes, cases) == {
        'claims': [{'scopeHash': scopes[0], 'mode': 'offline', 'supported': ['read'], 'complete': True}], 'errors': []}


@pytest.mark.parametrize('mutation', [
    lambda r, c, s, cases: r.update(extra='private'),
    lambda r, c, s, cases: r['events'][0].update(payload='private'),
    lambda r, c, s, cases: r['events'].pop(3),
    lambda r, c, s, cases: r['events'].pop(4),
    lambda r, c, s, cases: r['events'][3]['call'].update(hostCallId='crossed'),
    lambda r, c, s, cases: r['events'][3].update(observationHash='c' * 64),
    lambda r, c, s, cases: r['events'][4].update(observationHash='c' * 64),
    lambda r, c, s, cases: r.update(caseManifestHash='c' * 64),
    lambda r, c, s, cases: r.update(criteriaMappingHash='c' * 64),
    lambda r, c, s, cases: c.update(mode='live'),
    lambda r, c, s, cases: c['scopes'][0].update(model='stale'),
    lambda r, c, s, cases: r.update(cleanupComplete=False),
    lambda r, c, s, cases: r.update(supportedToolIds=[]),
    lambda r, c, s, cases: r['proofs'].append(deepcopy(r['proofs'][0])),
    lambda r, c, s, cases: r['caseResults'].append(deepcopy(r['caseResults'][0])),
    lambda r, c, s, cases: r['caseResults'].clear(),
    lambda r, c, s, cases: r['rows'][0]['offline']['proofIds'].append('proof-1'),
    lambda r, c, s, cases: c['turns'].pop(),
    lambda r, c, s, cases: cases.clear(),
])
def test_rejects_forged_or_incomplete(mutation):
    values = sample()
    mutation(*values)
    result = gate.validate_report(*values)
    assert result['claims'] == []
    assert len(result['errors']) == 1


def test_cli_read_only_fixed_output(tmp_path, capsys):
    report, context, scopes, cases = sample()
    path, expectations = tmp_path / 'report.json', tmp_path / 'context.json'
    path.write_text(json.dumps(report), encoding='utf8')
    expectations.write_text(json.dumps({'context': context, 'expectedScopes': scopes, 'expectedCases': cases}), encoding='utf8')
    before = {p.name: p.read_bytes() for p in (path, expectations)}
    assert gate.main(['--report', str(path), '--context', str(expectations)]) == 0
    assert json.loads(capsys.readouterr().out)['errors'] == []
    assert {p.name: p.read_bytes() for p in (path, expectations)} == before
    path.write_text('{"private":"credential"}', encoding='utf8')
    assert gate.main(['--report', str(path), '--context', str(expectations)]) == 1
    assert capsys.readouterr().out == '{"claims":[],"errors":["invalid_report"]}\n'


CONFORMANCE = json.loads((VECTOR.parent / 'evidence-v1.json').read_text(encoding='utf8'))


@pytest.mark.parametrize('vector', CONFORMANCE['vectors'], ids=lambda v: v['name'])
def test_shared_writer_gate_conformance(vector):
    assert CONFORMANCE['synthetic'] is True
    assert gate.validate_report(vector['report'], vector['context'], vector['expectedScopes'], vector['expectedCases']) == vector['expectedResult']


@pytest.mark.parametrize('contents', [
    b'{"private":"credential","private":"other"}',
    b'{"private":NaN}', b'{"private":Infinity}', b'\xff',
    b'[' * 65 + b']' * 65,
])
def test_loader_rejects_non_json_or_unbounded_nesting(tmp_path, contents):
    path = tmp_path / 'untrusted.json'
    path.write_bytes(contents)
    with pytest.raises((ValueError, UnicodeError)):
        gate.load_json(path)


def test_loader_bounds_actual_bytes(tmp_path, monkeypatch):
    path = tmp_path / 'untrusted.json'
    path.write_bytes(b' ' * 129)
    monkeypatch.setattr(gate, 'MAX_BYTES', 128)
    with pytest.raises(ValueError, match='invalid_report'):
        gate.load_json(path)


def test_cli_sanitizes_missing_paths_and_arguments(tmp_path, capsys):
    private = str(tmp_path / 'private-secret-path.json')
    for argv in ([], ['--report', private, '--context', private], ['--private-secret-flag']):
        assert gate.main(argv) == 1
        output = capsys.readouterr()
        assert output.err == ''
        assert output.out == '{"claims":[],"errors":["invalid_report"]}\n'


def test_module_imports_only_standard_library_and_has_no_execution_clients():
    import ast
    import sys
    source = ast.parse((ROOT / 'scripts/tool_verification.py').read_text(encoding='utf8'))
    imports = []
    for node in ast.walk(source):
        if isinstance(node, ast.Import):
            imports.extend(n.name.split('.')[0] for n in node.names)
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module.split('.')[0])
    assert set(imports) <= sys.stdlib_module_names | {'__future__'}
    assert set(imports).isdisjoint({'subprocess', 'socket', 'urllib', 'http', 'webbrowser', 'keyring', 'mvibe_auth', 'mvibe_proxy'})


@pytest.mark.parametrize('mutate', [
    lambda r, c, s, cases: c.update(raw='private'),
    lambda r, c, s, cases: c['turns'][0].update(raw='private'),
    lambda r, c, s, cases: c['limits'].update(eventsPerCase=True),
    lambda r, c, s, cases: r.update(schemaVersion=True),
    lambda r, c, s, cases: r['events'][0].update(sequence=1.5),
    lambda r, c, s, cases: r['events'][0].update(runId='https://private'),
    lambda r, c, s, cases: r['events'][0].update(runId='C:/private'),
    lambda r, c, s, cases: r['events'][0].update(payloadHash='A' * 64),
])
def test_exact_shapes_scalar_types_and_redaction(mutate):
    values = sample()
    mutate(*values)
    assert gate.validate_report(*values) == {'claims': [], 'errors': ['invalid_report']}
