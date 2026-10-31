"""Independent, read-only tool report gate. Synthetic data is not execution proof."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

MAX_BYTES = 16_777_216
REASONS = ('not_exercised', 'missing_capability', 'host_disabled', 'unknown_tool',
           'unsupported_protocol', 'incomplete_evidence', 'permission_denied', 'cancelled',
           'executor_failed', 'cleanup_failed', 'invalid_report', 'scope_changed', 'inventory_gap',
           'timeout', 'redaction_failed', 'hook_unavailable', 'model_declined', 'upstream_rejected')
OBSERVATIONS = ('definition', 'model_call', 'executor_started', 'executor_completed',
                'next_model_request', 'final_response', 'denied', 'error', 'unsupported', 'state_observed')
LIFECYCLE = ('host_cancelled', 'executor_released', 'provider_aborted', 'fixture_cancelled')
CALL_KINDS = ('model_call', 'executor_started', 'executor_completed', 'next_model_request', 'denied', 'error')
PROOF_LINKS = (('definitionEvent', 'definition'), ('modelCallEvent', 'model_call'),
               ('executorStartEvent', 'executor_started'), ('executorEndEvent', 'executor_completed'),
               ('nextRequestEvent', 'next_model_request'), ('finalResponseEvent', 'final_response'))


class InvalidEvidence(ValueError):
    """Only fixed reason codes cross the public boundary."""


def require(value, code='invalid_report'):
    if not value:
        raise InvalidEvidence(code)


def ident(value):
    return (type(value) is str and 0 < len(value.encode('utf-16-le')) // 2 <= 256
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:@/+~-]*', value) is not None
            and '://' not in value and '..' not in value and re.match(r'^[A-Za-z]:/', value) is None)


def hashed(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None


def enum(*values):
    return lambda value: (type(value) in (int, float) if type(values[0]) is int else type(value) is type(values[0])) and value in values


def integer(maximum, minimum=1):
    return lambda value: type(value) in (int, float) and minimum <= value <= maximum and value == int(value)


def array(check, maximum=4096):
    return lambda value: type(value) is list and len(value) <= maximum and all(check(v) for v in value)


def obj(fields, optional=None):
    optional = optional or {}
    def check(value):
        return (type(value) is dict and fields.keys() <= value.keys()
                and value.keys() <= fields.keys() | optional.keys()
                and all((fields.get(k) or optional[k])(v) for k, v in value.items()))
    return check


boolean = lambda value: type(value) is bool
seq = integer(32768)
strings = array(ident)
mode = enum('offline', 'live')
tool_schema = obj({'registryId': ident, 'declaredName': ident, 'origin': enum('native', 'plugin', 'mcp', 'unknown'), 'schemaHash': hashed},
                  {'offeredName': ident, 'namespace': ident, 'originHash': hashed})
scope_schema = obj({**{k: hashed for k in ('hash', 'hostSha256', 'providerBuildHash', 'profileHash')},
                    **{k: ident for k in ('hostVersion', 'sdkVersion', 'agent', 'model', 'protocolProfile')}, 'negotiatedMcpVersions': strings})
limits_schema = obj({**{k: integer(3_600_000) for k in ('startupMs', 'discoveryMs', 'cancellationMs', 'runMs')},
                     'traceBytes': integer(MAX_BYTES), 'eventsPerCase': seq, 'toolCallsPerCase': integer(4096)})
outcome_schema = lambda v: (obj({'status': enum('PASS'), 'proofIds': lambda x: strings(x) and bool(x)})(v)
                            or obj({'status': enum('FAIL'), 'code': enum(*REASONS)})(v)
                            or obj({'status': enum('UNSUPPORTED', 'DISABLED', 'UNVERIFIED'), 'reason': enum(*REASONS)})(v))
row_schema = obj({'tool': tool_schema, 'scopeHash': hashed, 'offline': outcome_schema, 'live': outcome_schema})
inventory_schema = obj({'scope': scope_schema, 'registered': array(tool_schema), 'offered': array(tool_schema), 'classifiedAbsent': array(row_schema)})
call_schema = obj({**{k: ident for k in ('hostMessageId', 'hostCallId', 'upstreamCallId')}, 'inputHash': hashed,
                   'link': enum('host_exposed', 'unique_fixture_nonce')}, {'fixtureNonceHash': hashed})
cancel_schema = obj({'hostEvent': seq, 'stateEvent': seq, 'commitState': enum('absent', 'observed', 'unknown')},
                    {k: seq for k in ('executorEvent', 'providerEvent', 'mcpEvent')})
cancel_expectation = obj({**{k: boolean for k in ('host', 'executor', 'provider', 'mcp')}, 'completionRace': enum('fail_case', 'mark_unverified')})
case_schema = obj({'id': ident, 'toolIds': strings, 'prerequisites': strings,
                  'fixtureKind': enum('filesystem', 'shell', 'web', 'question', 'delegation', 'state', 'mcp', 'plugin', 'media'),
                  'expectedObservationHash': hashed, 'positiveRequired': boolean, 'timeoutMs': integer(3_600_000),
                  'expectedCalls': array(obj({'toolId': ident, 'fixtureNonceHash': hashed, 'inputHash': hashed, 'observationHash': hashed})),
                  'expectedOutcome': enum('success', 'denied', 'error', 'cancelled', 'unsupported'),
                  'requiredObservations': array(enum(*OBSERVATIONS)),
                  'executorExpectation': enum('must_complete', 'must_not_start', 'observe_if_started')}, {'cancellation': cancel_expectation})
trace_base = {**{k: ident for k in ('runId', 'caseId', 'sessionId', 'chainId', 'requestId')}, 'sequence': seq,
              'scopeHash': hashed, 'requestKind': enum('primary', 'title', 'compaction', 'generate'), 'payloadHash': hashed}


def event_schema(value):
    if type(value) is not dict:
        return False
    kind = value.get('kind')
    fields, optional = {**trace_base, 'kind': enum(*OBSERVATIONS, *LIFECYCLE)}, {'observationHash': hashed}
    if kind in ('definition', 'unsupported', 'state_observed', *CALL_KINDS):
        fields.update(toolId=ident, toolSchemaHash=hashed)
        optional['originHash'] = hashed
    if kind in CALL_KINDS:
        fields['call'] = call_schema
    if kind in ('model_call', 'next_model_request'):
        fields['wireCallId'] = ident
    if kind in LIFECYCLE:
        optional['call'] = call_schema
    return obj(fields, optional)(value) and (not value.get('call') or value['call']['link'] != 'unique_fixture_nonce' or 'fixtureNonceHash' in value['call'])


proof_schema = obj({**{k: ident for k in ('id', 'caseId', 'sessionId', 'toolId', 'chainId', 'primaryRequestId', 'nextRequestId',
                                        'hostMessageId', 'upstreamCallId', 'hostCallId', 'continuationWireCallId')},
                    'scopeHash': hashed, 'toolSchemaHash': hashed, 'requestKind': enum('primary'),
                    **{k: seq for k, _ in PROOF_LINKS}}, {'originHash': hashed})
result_schema = obj({**{k: ident for k in ('caseId', 'toolId', 'sessionId', 'chainId')}, 'scopeHash': hashed,
                     'status': enum('PASS', 'FAIL', 'UNVERIFIED'), 'observedOutcome': enum('success', 'denied', 'error', 'cancelled', 'unsupported', 'not_observed'),
                     'eventReferences': array(seq)}, {'proofId': ident, 'cancellation': cancel_schema})
report_schema = obj({'schemaVersion': enum(1), 'feature': enum('opencode-vibe-tool-compatibility'), 'runId': ident, 'mode': mode,
                     'caseManifestHash': hashed, 'criteriaMappingHash': hashed, 'scopes': array(scope_schema), 'inventories': array(inventory_schema),
                     'rows': array(row_schema), 'events': array(event_schema, 32768), 'proofs': array(proof_schema),
                     'caseResults': array(result_schema), 'inventoryComplete': boolean, 'supportedToolIds': strings, 'cleanupComplete': boolean})
provenance_schema = obj({'kind': enum('native', 'plugin', 'mcp', 'unknown'), 'observationId': ident}, {'configurationHash': hashed})
turn_schema = obj({'sessionId': ident, 'requestId': ident, 'snapshot': inventory_schema,
                   'diff': obj({'missingCases': strings, 'changedSchemas': strings}),
                   'gaps': array(obj({'code': enum('unmapped_offering', 'missing_case', 'unknown_origin', 'missing_mcp_discovery', 'new_tool', 'schema_changed', 'scope_changed', 'not_offered')}, {'toolId': ident, 'offeredName': ident})),
                   'inventoryComplete': boolean, 'provenance': array(obj({'registryId': ident, 'observation': provenance_schema})),
                   'negotiatedMcp': array(obj({'serverHash': hashed, 'namespace': ident, 'protocolVersion': ident, 'capabilitiesHash': hashed, 'observationId': ident})),
                   'absenceObservations': array(obj({'registryId': ident, 'scopeHash': hashed, 'kind': enum('policy_disabled', 'missing_capability'), 'observationId': ident})),
                   'effectiveDefinitions': array(obj({'offeredName': ident, 'schemaHash': hashed}, {'registryId': ident, 'namespace': ident}))})
context_schema = obj({'mode': mode, 'scopes': array(scope_schema), 'caseManifestHash': hashed, 'criteriaMappingHash': hashed,
                      'limits': limits_schema, 'turns': array(turn_schema)})


def unique(values):
    require(len(set(values)) == len(values))


def equal(a, b, code='incomplete_evidence'):
    # evidence.ts compares through bounded canonical fixtureHash. Preserve its
    # node/depth/string/encoded-size rejection even though no hashing is needed.
    for value in (a, b):
        nodes = 0

        def bounded(v, depth=0):
            nonlocal nodes
            nodes += 1
            require(nodes <= 4096 and depth <= 32)
            if type(v) is str:
                require(len(v.encode('utf-16-le')) // 2 <= 65536)
            elif type(v) is list:
                for child in v:
                    bounded(child, depth + 1)
            elif type(v) is dict:
                for child in v.values():
                    bounded(child, depth + 1)
        bounded(value)
        require(len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-16-le')) // 2 <= 262144)
    require(a == b, code)


def validate_case(c, limits):
    require(case_schema(c))
    required = c['requiredObservations']
    for values in (c['toolIds'], c['prerequisites'], required):
        unique(values)
    calls = c['expectedCalls']
    unique([v['fixtureNonceHash'] for v in calls])
    require(c['toolIds'] and c['timeoutMs'] <= limits['runMs'] and len(calls) <= limits['toolCallsPerCase'] and len(required) <= limits['eventsPerCase'])
    require(c['positiveRequired'] == (c['expectedOutcome'] == 'success'))
    require(all(v['toolId'] in c['toolIds'] for v in calls) and all(any(v['toolId'] == t for v in calls) for t in c['toolIds']))
    has = lambda *names: all(n in required for n in names)
    for negative in ('denied', 'error', 'unsupported'):
        require(not has(negative) or c['expectedOutcome'] == negative)
    require(not has('executor_completed') or has('executor_started'))
    if c['positiveRequired']:
        require(c['executorExpectation'] == 'must_complete' and has('definition', 'model_call', 'executor_started', 'executor_completed', 'next_model_request', 'final_response'))
    if c['executorExpectation'] == 'must_not_start':
        require(not has('executor_started') and not has('executor_completed'))
    if c['executorExpectation'] == 'must_complete':
        require(has('executor_started', 'executor_completed'))
    if c['expectedOutcome'] == 'denied':
        require(c['executorExpectation'] == 'must_not_start' and has('definition', 'model_call', 'denied', 'state_observed', 'next_model_request', 'final_response'))
    if c['expectedOutcome'] == 'error':
        require(has('definition', 'model_call', 'error', 'next_model_request', 'final_response') and (c['executorExpectation'] != 'must_not_start' or has('state_observed')))
    if c['expectedOutcome'] == 'unsupported':
        require(c['executorExpectation'] == 'must_not_start' and has('unsupported'))
    cancellation = c.get('cancellation')
    if c['expectedOutcome'] == 'cancelled':
        require(cancellation and cancellation['host'] and cancellation['provider'] and has('definition', 'model_call', 'state_observed')
                and c['timeoutMs'] >= limits['cancellationMs'] and c['executorExpectation'] != 'must_complete'
                and (not cancellation['executor'] or has('executor_started'))
                and (not cancellation['mcp'] or c['fixtureKind'] == 'mcp' and cancellation['executor']))
    else:
        require(not cancellation)
    extra = 1 + sum(cancellation[k] for k in ('executor', 'provider', 'mcp')) if cancellation else 0
    per_call = sum(k in CALL_KINDS for k in required)
    require(len(required) + (len(calls) - 1) * per_call + extra <= limits['eventsPerCase'])


def validate_links(r):
    """Independent implementation of contracts.ts structural joins."""
    require(report_schema(r))
    for values in ([s['hash'] for s in r['scopes']], [e['sequence'] for e in r['events']], [p['id'] for p in r['proofs']]):
        unique(values)
    scopes = {s['hash']: s for s in r['scopes']}
    events = {e['sequence']: e for e in r['events']}
    proofs = {p['id']: p for p in r['proofs']}
    tools = {}
    same = lambda a, b: equal(a, b, 'invalid_report')
    for inv in r['inventories']:
        same(scopes.get(inv['scope']['hash']), inv['scope'])
        for t in inv['registered'] + inv['offered']:
            key = (inv['scope']['hash'], t['registryId'])
            prior = tools.get(key)
            if prior:
                same({**prior, 'offeredName': prior.get('offeredName', t.get('offeredName'))},
                     {**t, 'offeredName': t.get('offeredName', prior.get('offeredName'))})
            tools[key] = prior if prior and 'offeredName' in prior and 'offeredName' not in t else t

    def check_tool(owner, schema=False):
        t = tools.get((owner['scopeHash'], owner['toolId']))
        require(t is not None)
        if schema:
            same(t['schemaHash'], owner['toolSchemaHash'])
            same(t.get('originHash'), owner.get('originHash'))
        return t

    def linked(n, owner):
        require(n in events)
        e = events[n]
        for k in ('caseId', 'scopeHash', 'sessionId', 'chainId'):
            same(e[k], owner[k])
        return e

    for e in r['events']:
        same(e['runId'], r['runId'])
        require(e['scopeHash'] in scopes)
        if 'toolId' in e:
            check_tool(e, True)
    for p in r['proofs']:
        check_tool(p, True)
        previous, correlation = 0, None
        for key, kind in PROOF_LINKS:
            n = p[key]
            require(n > previous)
            previous = n
            e = linked(n, p)
            same(e['kind'], kind)
            same(e['requestKind'], p['requestKind'])
            same(e['requestId'], p['primaryRequestId'] if n <= p['executorEndEvent'] else p['nextRequestId'])
            if 'toolId' in e:
                for k in ('toolId', 'toolSchemaHash', 'originHash'):
                    same(e.get(k), p.get(k))
            if 'call' in e:
                for k in ('hostMessageId', 'hostCallId', 'upstreamCallId'):
                    same(e['call'][k], p[k])
                if correlation:
                    for k in ('inputHash', 'link', 'fixtureNonceHash'):
                        same(e['call'].get(k), correlation.get(k))
                correlation = e['call']
            if kind == 'next_model_request':
                same(e['wireCallId'], p['continuationWireCallId'])
    for c in r['caseResults']:
        check_tool(c)
        for n in c['eventReferences']:
            e = linked(n, c)
            if 'toolId' in e:
                same(e['toolId'], c['toolId'])
        if 'proofId' in c:
            require(c['proofId'] in proofs)
            for k in ('caseId', 'scopeHash', 'toolId', 'sessionId', 'chainId'):
                same(proofs[c['proofId']][k], c[k])
        if 'cancellation' in c:
            cp = c['cancellation']
            host = linked(cp['hostEvent'], c)
            correlation = None
            known = [linked(n, c) for n in c['eventReferences'] if events[n]['kind'] in CALL_KINDS]
            for field, kind in (('hostEvent', 'host_cancelled'), ('executorEvent', 'executor_released'), ('providerEvent', 'provider_aborted'), ('mcpEvent', 'fixture_cancelled'), ('stateEvent', 'state_observed')):
                if field not in cp:
                    continue
                e = linked(cp[field], c)
                same(e['kind'], kind)
                if 'toolId' in e:
                    same(e['toolId'], c['toolId'])
                if kind != 'state_observed':
                    same(e['requestId'], host['requestId'])
                    same(e['requestKind'], host['requestKind'])
                if 'call' in e:
                    if correlation:
                        same(correlation, e['call'])
                    require(not known or any(x['call'] == e['call'] and x['requestId'] == e['requestId'] and x['requestKind'] == e['requestKind'] for x in known))
                    correlation = e['call']
    for row in r['rows'] + [row for inv in r['inventories'] for row in inv['classifiedAbsent']]:
        t = check_tool({'scopeHash': row['scopeHash'], 'toolId': row['tool']['registryId'], 'toolSchemaHash': row['tool']['schemaHash'], 'originHash': row['tool'].get('originHash')}, True)
        same(t, row['tool'])
        for m in ('offline', 'live'):
            if row[m]['status'] == 'PASS':
                same(m, r['mode'])
                for pid in row[m]['proofIds']:
                    require(pid in proofs)
                    same(proofs[pid]['scopeHash'], row['scopeHash'])
                    same(proofs[pid]['toolId'], row['tool']['registryId'])
    require(all(any(row['tool']['registryId'] == t for row in r['rows']) for t in r['supportedToolIds']))
    return events, proofs


def validate_negative(refs, spec, result):
    has = lambda kind: [e for e in refs if e['kind'] == kind]
    first = lambda kind: next(iter(has(kind)), None)
    check = lambda value: require(value, 'incomplete_evidence')
    if spec['expectedOutcome'] != 'cancelled':
        check(result['status'] == 'PASS')
        previous = 0
        for kind in ('definition', 'model_call', spec['expectedOutcome'], 'next_model_request', 'final_response'):
            e = first(kind)
            if e:
                check(e['sequence'] > previous)
                previous = e['sequence']
        model, following, final = first('model_call'), first('next_model_request'), first('final_response')
        if model and following:
            check(model['requestId'] != following['requestId'] and final and final['requestId'] == following['requestId'])
        if model:
            check(all(e['requestId'] == model['requestId'] for e in refs if e['kind'] in ('definition', 'executor_started', 'executor_completed', 'denied', 'error')))
        terminal, start, end = first(spec['expectedOutcome']), first('executor_started'), first('executor_completed')
        if terminal:
            check(all(e['sequence'] > terminal['sequence'] for e in has('state_observed')))
        if start and model:
            check(start['sequence'] > model['sequence'])
        if start and terminal:
            check(start['sequence'] < terminal['sequence'])
        if end:
            check(start and end['sequence'] > start['sequence'])
            if following:
                check(end['sequence'] < following['sequence'])
        return
    cp, expected = result.get('cancellation'), spec.get('cancellation')
    check(cp and expected)
    indexed = {e['sequence']: e for e in refs}
    observations = (('hostEvent', 'host_cancelled', 'host'), ('executorEvent', 'executor_released', 'executor'),
                    ('providerEvent', 'provider_aborted', 'provider'), ('mcpEvent', 'fixture_cancelled', 'mcp'))
    for field, kind, needed in observations:
        check(not expected[needed] or field in cp)
        if field in cp:
            check(indexed.get(cp[field], {}).get('kind') == kind)
    host, state = indexed.get(cp['hostEvent']), indexed.get(cp['stateEvent'])
    check(host and state and state['kind'] == 'state_observed' and state['sequence'] > host['sequence'] and state.get('observationHash') == spec['expectedObservationHash'])
    definition, model = first('definition'), first('model_call')
    check(definition and model and definition['sequence'] < model['sequence'] < host['sequence'])
    check(definition['requestId'] == model['requestId'] == host['requestId'])
    check(cp['commitState'] != 'unknown')
    completed = has('executor_completed')
    if cp['commitState'] == 'observed':
        check(completed and all(e['sequence'] < host['sequence'] for e in completed)
              and expected['completionRace'] == 'mark_unverified' and result['status'] == 'UNVERIFIED')
    else:
        check(not completed and result['status'] == 'PASS')
    for field, _, needed in observations:
        if expected[needed] and field != 'hostEvent':
            check(host['sequence'] < cp[field] < state['sequence'])
    check(all(model['sequence'] < e['sequence'] < host['sequence'] and e['requestId'] == host['requestId'] for e in has('executor_started')))


def evaluate(r, context, expected_scopes, cases):
    events, proofs = validate_links(r)
    require(context_schema(context) and array(hashed)(expected_scopes) and array(case_schema)(cases))
    limits = context['limits']
    require(all(limits[k] <= limits['runMs'] for k in ('startupMs', 'discoveryMs', 'cancellationMs')))
    unique(expected_scopes)
    unique([s['hash'] for s in context['scopes']])
    unique([c['id'] for c in cases])
    equal(sorted(s['hash'] for s in r['scopes']), sorted(expected_scopes), 'scope_changed')
    equal(sorted(s['hash'] for s in context['scopes']), sorted(expected_scopes), 'scope_changed')
    require(expected_scopes and r['mode'] == context['mode'], 'scope_changed')
    for k in ('caseManifestHash', 'criteriaMappingHash'):
        equal(r[k], context[k], 'scope_changed')
    for s in context['scopes']:
        equal(next((x for x in r['scopes'] if x['hash'] == s['hash']), None), s, 'scope_changed')
    for c in cases:
        validate_case(c, limits)
    by_case = {c['id']: c for c in cases}
    turns = context['turns']
    unique([(t['snapshot']['scope']['hash'], t['sessionId'], t['requestId']) for t in turns])
    require(turns, 'inventory_gap')
    for t in turns:
        equal(t['snapshot']['scope'], next((s for s in context['scopes'] if s['hash'] == t['snapshot']['scope']['hash']), None), 'scope_changed')

    def turn_for(e):
        turn = next((t for t in turns if t['snapshot']['scope']['hash'] == e['scopeHash'] and t['sessionId'] == e['sessionId'] and t['requestId'] == e['requestId']), None)
        require(turn is not None, 'inventory_gap')
        return turn

    for e in r['events']:
        require(e['caseId'] in by_case and e['requestKind'] == 'primary', 'incomplete_evidence')
        turn = turn_for(e)
        if 'toolId' in e and e['kind'] not in ('unsupported', 'state_observed'):
            t = next((t for t in turn['snapshot']['offered'] if t['registryId'] == e['toolId']), None)
            require(t and t['schemaHash'] == e['toolSchemaHash'] and t.get('originHash') == e.get('originHash'), 'inventory_gap')
    require(r['cleanupComplete'], 'cleanup_failed')
    inventory_complete = all(t['inventoryComplete'] and not t['gaps'] and
                             all(any(tool['registryId'] in c['toolIds'] for c in cases) or any(row['tool']['registryId'] == tool['registryId'] for row in t['snapshot']['classifiedAbsent'])
                                 for tool in t['snapshot']['registered'] + t['snapshot']['offered']) for t in turns)
    equal(r['inventoryComplete'], inventory_complete, 'inventory_gap')
    inventories = []
    for s in context['scopes']:
        scoped = [t for t in turns if t['snapshot']['scope']['hash'] == s['hash']]

        def union(kind):
            by_id = {}
            for t in scoped:
                for tool in t['snapshot'][kind]:
                    if tool['registryId'] in by_id:
                        equal(by_id[tool['registryId']], tool, 'scope_changed')
                    by_id[tool['registryId']] = tool
            return sorted(by_id.values(), key=lambda t: t['registryId'])

        registered, offered = union('registered'), union('offered')
        supplied = next((i for i in r['inventories'] if i['scope']['hash'] == s['hash']), None)
        require(supplied is not None, 'inventory_gap')
        normalize = lambda ts: sorted(({k: v for k, v in t.items() if k != 'offeredName'} for t in ts), key=lambda t: t['registryId'])
        equal(normalize(supplied['registered']), normalize(registered), 'inventory_gap')
        equal(sorted(supplied['offered'], key=lambda t: t['registryId']), offered, 'inventory_gap')
        absent = {row['tool']['registryId']: row for t in scoped for row in t['snapshot']['classifiedAbsent'] if not any(x['registryId'] == row['tool']['registryId'] for x in offered)}
        equal(sorted(supplied['classifiedAbsent'], key=lambda x: x['tool']['registryId']), sorted(absent.values(), key=lambda x: x['tool']['registryId']), 'inventory_gap')
        require(scoped, 'inventory_gap')
        inventories.append({'scope': s, 'registered': registered, 'offered': offered, 'absent': absent})
    unique([i['scope']['hash'] for i in r['inventories']])
    require(len(r['inventories']) == len(inventories), 'inventory_gap')
    counts = {}
    for e in r['events']:
        key = (e['scopeHash'], e['caseId'])
        counts[key] = counts.get(key, 0) + 1
    require(all(n <= limits['eventsPerCase'] for n in counts.values()), 'incomplete_evidence')
    accepted, passed = {}, set()
    claimed_events, used_proofs, used_expectations, referenced_events = set(), set(), set(), set()
    call_ids, wire_ids, continuation_ids = set(), set(), set()
    for index, result in enumerate(r['caseResults']):
        spec = by_case.get(result['caseId'])
        check = lambda value: require(value, 'incomplete_evidence')
        check(spec and result['toolId'] in spec['toolIds'])
        unique(result['eventReferences'])
        refs = sorted((events[n] for n in result['eventReferences']), key=lambda e: e['sequence'])
        referenced_events.update(e['sequence'] for e in refs)
        relevant = [e for e in r['events'] if all(e[k] == result[k] for k in ('caseId', 'scopeHash', 'sessionId', 'chainId')) and ('toolId' not in e or e['toolId'] == result['toolId'])]
        require(result['status'] != 'FAIL', 'executor_failed')
        if result['status'] == 'UNVERIFIED' and result['observedOutcome'] == 'not_observed':
            check(not result.get('proofId') and not result.get('cancellation') and not refs)
            continue
        check(result['observedOutcome'] == spec['expectedOutcome'])
        has = lambda kind: [e for e in refs if e['kind'] == kind]
        check(all(len(has(k)) <= 1 for k in CALL_KINDS))
        check(all(has(k) for k in spec['requiredObservations']))
        if spec['executorExpectation'] == 'must_not_start':
            check(not any(e['kind'] in ('executor_started', 'executor_completed') for e in relevant))
        if spec['executorExpectation'] == 'must_complete':
            check(has('executor_started') and has('executor_completed'))
        if spec['expectedOutcome'] != 'cancelled':
            check(not result.get('cancellation') and not any(e['kind'] == 'host_cancelled' for e in relevant))
        call_events = [e for e in refs if e.get('call')]
        first_call = call_events[0]['call'] if call_events else None
        if first_call:
            check(all(e['call'] == first_call for e in call_events))
        expected_calls = [c for c in spec['expectedCalls'] if c['toolId'] == result['toolId']]
        if first_call:
            matched = next((c for c in expected_calls if c['inputHash'] == first_call['inputHash'] and
                            (first_call['link'] == 'host_exposed' and 'fixtureNonceHash' not in first_call or first_call.get('fixtureNonceHash') == c['fixtureNonceHash'])), None)
        else:
            matched = expected_calls[0] if len(expected_calls) == 1 else None
        check(matched is not None)
        expectation_key = (result['scopeHash'], result['caseId'], result['toolId'], matched['fixtureNonceHash'])
        check(expectation_key not in used_expectations)
        used_expectations.add(expectation_key)
        check(all(e.get('observationHash') == matched['observationHash'] for e in refs if e['kind'] in ('executor_completed', 'next_model_request', 'denied', 'error')))
        check(all(e.get('observationHash') == spec['expectedObservationHash'] for e in has('state_observed')))
        if spec['expectedOutcome'] == 'unsupported':
            check(all(e.get('observationHash') == spec['expectedObservationHash'] for e in has('unsupported')))
        for e in call_events:
            prefix = (e['scopeHash'], e['sessionId'], e['requestId'])
            if e['kind'] == 'model_call':
                call_key, wire_key = (*prefix, e['call']['hostCallId']), (*prefix, e['wireCallId'])
                check(call_key not in call_ids and wire_key not in wire_ids and e['wireCallId'] == e['call']['upstreamCallId'])
                call_ids.add(call_key)
                wire_ids.add(wire_key)
            if e['kind'] == 'next_model_request':
                key = (*prefix, e['wireCallId'])
                check(key not in continuation_ids)
                continuation_ids.add(key)
        if spec['positiveRequired']:
            check(not any(e['kind'] in ('denied', 'error', 'unsupported') for e in relevant))
            check(result['status'] == 'PASS' and result.get('proofId') in proofs)
            proof = proofs[result['proofId']]
            check(proof['id'] not in used_proofs and proof['primaryRequestId'] != proof['nextRequestId'])
            used_proofs.add(proof['id'])
            check(all(proof[k] in result['eventReferences'] for k, _ in PROOF_LINKS))
            for k in ('modelCallEvent', 'executorStartEvent', 'executorEndEvent', 'nextRequestEvent'):
                check(proof[k] not in claimed_events)
                claimed_events.add(proof[k])
            turn = turn_for(events[proof['modelCallEvent']])
            offered = next((t for t in turn['snapshot']['offered'] if t['registryId'] == result['toolId']), None)
            provenance = next((p['observation'] for p in turn['provenance'] if p['registryId'] == result['toolId']), None)
            require(offered and offered['origin'] != 'unknown' and offered.get('originHash') and provenance and provenance['kind'] == offered['origin'] and provenance.get('configurationHash') == offered['originHash'], 'inventory_gap')
            check(events[proof['executorEndEvent']].get('observationHash') == matched['observationHash'] and events[proof['nextRequestEvent']].get('observationHash') == matched['observationHash'])
            accepted.setdefault((result['scopeHash'], result['toolId']), []).append(proof['id'])
        else:
            check(not result.get('proofId'))
            validate_negative(refs, spec, result)
        if result['status'] == 'PASS':
            passed.add(index)
    require(len(used_proofs) == len(r['proofs']) and len(referenced_events) == len(r['events']), 'incomplete_evidence')
    for inv in inventories:
        for spec in cases:
            for tid in spec['toolIds']:
                if not any(t['registryId'] == tid for t in inv['registered'] + inv['offered']):
                    continue
                results = [(i, c) for i, c in enumerate(r['caseResults']) if c['scopeHash'] == inv['scope']['hash'] and c['caseId'] == spec['id'] and c['toolId'] == tid]
                require(len(results) == sum(c['toolId'] == tid for c in spec['expectedCalls']), 'incomplete_evidence')
                if not spec['positiveRequired']:
                    require(all(i in passed or spec.get('cancellation', {}).get('completionRace') == 'mark_unverified' and c['status'] == 'UNVERIFIED' and c.get('cancellation') for i, c in results), 'incomplete_evidence')
    rows, claims = [], []
    unverified = lambda: {'status': 'UNVERIFIED', 'reason': 'not_exercised'}
    for inv in inventories:
        sh = inv['scope']['hash']
        supported = []
        for tool in {t['registryId']: t for t in inv['registered'] + inv['offered']}.values():
            tid = tool['registryId']
            applicable = [c for c in cases if c['positiveRequired'] and tid in c['toolIds']]
            complete = bool(applicable) and all(all(i in passed for i, x in enumerate(r['caseResults']) if x['scopeHash'] == sh and x['caseId'] == c['id'] and x['toolId'] == tid) for c in applicable)
            ids = accepted.get((sh, tid), [])
            unsupported = any(c['scopeHash'] == sh and c['toolId'] == tid and c['observedOutcome'] == 'unsupported' and i in passed for i, c in enumerate(r['caseResults']))
            outcome = ({'status': 'PASS', 'proofIds': sorted(ids)} if complete and ids else inv['absent'].get(tid, {}).get(r['mode']) or
                       ({'status': 'UNSUPPORTED', 'reason': 'unsupported_protocol'} if unsupported else unverified()))
            if outcome['status'] == 'PASS':
                supported.append(tid)
            rows.append({'tool': tool, 'scopeHash': sh, 'offline': outcome if r['mode'] == 'offline' else unverified(), 'live': outcome if r['mode'] == 'live' else unverified()})
        negatives_complete = all(all(i in passed and x['observedOutcome'] != 'unsupported' for i, x in enumerate(r['caseResults']) if x['scopeHash'] == sh and x['caseId'] == c['id']) for c in cases if not c['positiveRequired'])
        scoped_complete = all(t['inventoryComplete'] and not t['gaps'] for t in turns if t['snapshot']['scope']['hash'] == sh)
        claims.append({'scopeHash': sh, 'mode': r['mode'], 'supported': sorted(supported),
                       'complete': bool(scoped_complete and negatives_complete and inv['offered'] and all(t['registryId'] in supported for t in inv['offered']))})
    return rows, claims


def canonical_rows(rows):
    normalized = deepcopy(rows)
    for row in normalized:
        for m in ('offline', 'live'):
            if row[m]['status'] == 'PASS':
                unique(row[m]['proofIds'])
                row[m]['proofIds'].sort()
    return sorted(normalized, key=lambda row: (row['scopeHash'], row['tool']['registryId']))


def validate_report(report, context, expected_scopes, expected_cases):
    """Expectations must come from trusted pre-run context, separately from the report.

    Checks consistency of trusted observations, never authenticity of operator JSON.
    Does not read files, launch processes, import application code, or use the network.
    """
    try:
        rows, claims = evaluate(report, context, expected_scopes, expected_cases)
        equal(canonical_rows(report['rows']), canonical_rows(rows), 'invalid_report')
        unique(report['supportedToolIds'])
        equal(sorted(report['supportedToolIds']), sorted({tid for c in claims for tid in c['supported']}), 'invalid_report')
        return {'claims': claims, 'errors': []}
    except (InvalidEvidence, KeyError, TypeError, ValueError, RecursionError, OverflowError) as error:
        known = ('invalid_report', 'incomplete_evidence', 'scope_changed', 'inventory_gap', 'cleanup_failed', 'executor_failed')
        code = str(error) if isinstance(error, InvalidEvidence) and str(error) in known else 'invalid_report'
        return {'claims': [], 'errors': [code]}


def load_json(path):
    """Bound the actual read, reject duplicate keys/non-JSON numbers and deep inputs."""
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES)
    # Bound nesting before invoking the JSON decoder; braces inside strings do not count.
    depth, quoted, escaped = 0, False, False
    for char in data:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            require(depth <= 64)
        elif char in (93, 125):
            depth -= 1
    return json.loads(data.decode('utf8'), object_pairs_hook=pairs, parse_constant=lambda _: require(False))


class FixedParser(argparse.ArgumentParser):
    def error(self, message):
        raise InvalidEvidence('invalid_report')


def main(argv=None):
    """Usage: tool_verification.py --report REPORT --context TRUSTED_EXPECTATIONS.

    The expectations document has exactly context, expectedScopes, expectedCases.
    It must be supplied by the trusted controller independently of report contents.
    Exit 0 accepts consistency; exit 1 prints a bounded fixed-code rejection.
    """
    try:
        parser = FixedParser(description=__doc__)
        parser.add_argument('--report', required=True)
        parser.add_argument('--context', required=True)
        args = parser.parse_args(argv)
        r, trusted = load_json(args.report), load_json(args.context)
        require(obj({'context': context_schema, 'expectedScopes': array(hashed), 'expectedCases': array(case_schema)})(trusted))
        result = validate_report(r, trusted['context'], trusted['expectedScopes'], trusted['expectedCases'])
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        result = {'claims': [], 'errors': ['invalid_report']}
    print(json.dumps(result, separators=(',', ':')))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
