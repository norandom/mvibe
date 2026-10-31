"""Offline acceptance evidence validator. Never launches account checks or installs tools."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

SPEC = '.kiro/specs/vibe-oidc-proxy/requirements.md'
STEPS = ('python', 'provider', 'typecheck', 'provider-build', 'python-build',
         'bridge-process', 'bridge-root-exit', 'host-setup', 'host', 'tools')
HOST_CHECKS = ('markerOnly', 'modelDiscovery', 'currentCredentials', 'incrementalStream',
               'toolCallPayload', 'cancellation', 'noReplay401', 'noSessionReplay401',
               'noReplay503', 'noSessionReplay503', 'customSDKCalled', 'cleanup')
STARTUP_CHECKS = ('default_loopback', 'injected_lifespan', 'local_health',
                  'tool_payload', 'rejected_route', 'closed')


def requirement_ids(text):
    section = None
    result = set()
    for line in text.splitlines():
        match = re.match(r'### Requirement (\d+):', line)
        if match:
            section = match[1]
        criterion = re.match(r'^(\d+)\. ', line)
        if criterion and section:
            result.add(f'{section}.{criterion[1]}')
    return result


def safe_path(root, relative):
    if not isinstance(relative, str) or not relative:
        raise ValueError('Empty evidence path')
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError('Evidence path must stay within project')
    return path


def validate_mapping(root, data):
    if data.get('version') != 1 or set(data.get('criteria', {})) != requirement_ids((root / SPEC).read_text(encoding='utf8')):
        raise ValueError('Missing or unknown requirement IDs')
    cache = {}
    for criterion, evidence in data['criteria'].items():
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f'Empty evidence for {criterion}')
        for item in evidence:
            kind = item.get('kind')
            if kind == 'test':
                path = safe_path(root, item['file'])
                name = item.get('name')
                key = (item.get('suite'), path)
                if key in cache:
                    names = cache[key]
                elif item.get('suite') == 'python' and path.suffix == '.py':
                    source = path.read_text(encoding='utf8')
                    names = {n.name for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test_')}
                elif item.get('suite') == 'provider' and path.name.endswith('.test.ts'):
                    source = path.read_text(encoding='utf8')
                    names = set(re.findall(r'\b(?:test|it)\(\s*"([^"\n]+)"', source))
                else:
                    raise ValueError('Unknown test suite')
                if not name or name not in names:
                    raise ValueError(f'Invented test for {criterion}: {name}')
                cache[key] = names
            elif kind == 'document':
                safe_path(root, item.get('path'))
                if item.get('required') is not True or not isinstance(item.get('anchor'), str) or not item['anchor'].strip():
                    raise ValueError('Document evidence must declare required path and review anchor')
            elif kind == 'step':
                if item.get('name') not in STEPS:
                    raise ValueError('Unknown mandatory execution step')
            else:
                raise ValueError('Unknown evidence kind')


def criterion_status(evidence):
    statuses = [item['status'] for item in evidence]
    for status in ('failed', 'unverified', 'skipped'):
        if status in statuses:
            return status
    return 'passed' if statuses and all(s == 'passed' for s in statuses) else 'unverified'


def step_status(exit_code, evidence):
    return 'passed' if exit_code == 0 and evidence == 'passed' else 'failed'


def read_junit(path):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise ValueError('Missing or invalid execution report') from error
    result = []
    for case in root.iter('testcase'):
        name, classname = case.get('name'), case.get('classname', '')
        # A test collection listing is not an executed JUnit case.
        if not name or case.get('time') is None:
            raise ValueError('JUnit case lacks execution duration/name')
        duration = float(case.get('time'))
        if not math.isfinite(duration) or duration < 0:
            raise ValueError('Invalid execution duration')
        status = 'failed' if case.find('failure') is not None or case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
        result.append({'name': name, 'class': classname, 'file': case.get('file', ''), 'status': status})
    if not result:
        raise ValueError('No executed test cases')
    if root.get('tests') is not None and int(root.get('tests')) != len(result):
        raise ValueError('Execution count does not match cases')
    return result


def test_status(rows, file, name):
    module = file.removesuffix('.py').replace('/', '.')
    matches = [r for r in rows if (r['name'] == name or r['name'].startswith(name + '[')) and
               (r['class'] == module or r['class'].startswith(module + '.') or
                r['file'].replace('\\', '/').endswith(file.removeprefix('packages/opencode-vibe-oidc/')) or
                r['class'].replace('\\', '/').endswith(file.removeprefix('packages/opencode-vibe-oidc/')))]
    return criterion_status(matches)


def document_status(root, item):
    path = safe_path(root, item['path'])
    if not path.is_file():
        return 'unverified'
    return 'passed' if item['anchor'] in path.read_text(encoding='utf8') else 'unverified'


def json_checks(path, kind, checks):
    try:
        data = json.loads(path.read_text(encoding='utf8'))
        result = data.get('result', data)
        return 'passed' if data.get('kind') == kind and result.get('status') == 'passed' and all(result.get('checks', {}).get(c) is True for c in checks) and data.get('live') == 'unverified' else 'failed'
    except (OSError, ValueError):
        return 'unverified'


def acceptance(root, directory):
    data = json.loads((root / 'tests/traceability.json').read_text(encoding='utf8'))
    validate_mapping(root, data)
    manifest = json.loads((directory / 'execution.json').read_text(encoding='utf8'))
    if manifest.get('run_id') != directory.name or manifest.get('mode') != 'offline':
        raise ValueError('Report does not belong to this offline execution')
    started = datetime.fromisoformat(manifest['started'])
    if started.tzinfo is None:
        raise ValueError('Execution timestamp must include timezone')
    for path in directory.iterdir():
        if path.stat().st_mtime + 1 < started.timestamp():
            raise ValueError('Stale execution evidence')
    recorded = manifest.get('steps', [])
    if len(recorded) != len(STEPS) or {s['name'] for s in recorded} != set(STEPS):
        raise ValueError('Missing or duplicate mandatory steps')
    steps = {s['name']: step_status(s.get('exit_code'), s.get('status')) for s in recorded}
    suites = {}
    for suite in ('python', 'provider'):
        try:
            suites[suite] = read_junit(directory / f'{suite}.xml')
            if criterion_status(suites[suite]) != 'passed':
                steps[suite] = 'failed'
        except ValueError:
            suites[suite] = []
            steps[suite] = 'failed'
    steps['host'] = step_status(0 if steps['host'] == 'passed' else 1,
        json_checks(directory / 'host.json', 'offline-host-compatibility', HOST_CHECKS))
    startup = json_checks(directory / 'startup.json', 'offline-local-startup', STARTUP_CHECKS)
    criteria = {}
    for criterion, items in data['criteria'].items():
        evidence = []
        for item in items:
            kind = item['kind']
            if kind == 'test':
                status = test_status(suites[item['suite']], item['file'], item['name'])
            elif kind == 'document':
                status = document_status(root, item)
            else:
                status = steps[item['name']]
            evidence.append({**item, 'status': status})
        criteria[criterion] = {'status': criterion_status(evidence), 'evidence': evidence}
    offline = 'passed' if all(s == 'passed' for s in steps.values()) and startup == 'passed' else 'failed'
    result = {'kind': 'offline-acceptance', 'run_id': directory.name,
              'status': 'passed' if offline == 'passed' and all(c['status'] == 'passed' for c in criteria.values()) else 'failed',
              'offline': offline, 'local_startup': startup, 'live': 'unverified',
              'steps': steps, 'criteria': criteria,
              'documentary_scope': 'Required file and anchor presence; content needs operator review. Live execution is never inferred.'}
    (directory / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        result = acceptance(root, args.reports.resolve())
    except (ValueError, OSError, KeyError, TypeError):
        # Never copy native logs, exception text, credentials or raw test failures.
        result = {'kind': 'offline-acceptance', 'status': 'failed', 'reason': 'Invalid or missing acceptance execution evidence', 'live': 'unverified'}
        args.reports.mkdir(parents=True, exist_ok=True)
        (args.reports / 'acceptance.json').write_text(json.dumps(result, indent=2), encoding='utf8')
    print(f"Offline acceptance: {result['status']}; live: unverified; report: {args.reports / 'acceptance.json'}")
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
