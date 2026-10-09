"""Real Microsoft Agent Framework + LoopGrid Core authorization regression gate.

This fixture performs only simulated local refunds and deliberately exercises
unauthorized actions. It does NOT authenticate real human reviewers or validate
Microsoft's native always_require approval/resumption protocol.

Usage (in repository root, with Core running):
    .\\.venv\\Scripts\\python.exe examples\\native_core_policy_e2e.py
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request
import uuid

from agent_framework import Agent, tool
from loopgrid import LoopGrid
from loopgrid_microsoft_agent import LoopGridMicrosoftAgent
from native_core_e2e import ScriptedAgentClient

BASE = os.getenv('LOOPGRID_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.load(response)


def post(path, data):
    req = urllib.request.Request(
        BASE + path, json.dumps(data).encode('utf-8'),
        {'Content-Type': 'application/json'}, method='POST',
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def normalized_arguments(value):
    if hasattr(value, 'model_dump') and callable(value.model_dump):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return dict(value)
    return value


async def scenario(workspace_id, name, *, policy_mode, review=None,
                   bound_amount=25, break_event_write=False):
    sdk = LoopGrid(base_url=BASE, workspace_id=workspace_id)
    bridge = LoopGridMicrosoftAgent(client=sdk, workspace_id=workspace_id,
                                    agent_id='maf-policy-sandbox')
    sandbox_receipts = []
    middleware_invocations = []
    tool_request_writes = []
    middleware_failures = []

    @tool(approval_mode='never_require')
    def sandbox_refund(amount: int) -> str:
        """Return a local synthetic receipt; NEVER contact a payment provider."""
        receipt = {
            'id': 'sandbox-' + uuid.uuid4().hex[:10],
            'amount': amount, 'status': 'succeeded',
            'sandbox': True, 'real_money_moved': False,
        }
        sandbox_receipts.append(receipt)
        return json.dumps(receipt)

    decision = bridge.start_decision(
        decision_type='sandbox_refund_policy_gate',
        agent={'id': 'maf-policy-sandbox', 'version': 'local-security-validation'},
        authority={'acting_for': 'local LoopGrid sandbox', 'scope': ['refund:simulate']},
        model={'provider': 'local', 'name': 'scripted-tool-client'},
        context={'prompt_version': 'maf-policy-security-1'},
        proposed_action={'tool': 'sandbox_refund', 'arguments': {'amount': bound_amount}},
        policy={'policy_id': 'sandbox-policy-' + name, 'version': '1',
                'decision': policy_mode},
        metadata={'sandbox': True, 'real_money_moved': False},
    )
    decision_id = decision['decision_id']

    if review is not None:
        # Only synthetic host-issued review; NOT evidence of real human identity.
        bridge.record_human_review(
            decision_id, reviewer='local-sandbox-reviewer', approved=review,
            reason='Deterministic local gate test',
        )

    # Trace the *actual native Microsoft FunctionMiddleware invocation*.
    # In a denied scenario, Core may refuse to persist tool_requested before
    # the adapter reaches its own authorization check. Do not mistake that for
    # "no tool was attempted" or "agent safely blocked it".
    original_process = bridge.function_middleware.process

    async def traced_process(context, call_next):
        middleware_invocations.append({
            'name': getattr(getattr(context, 'function', None), 'name', None),
            'arguments': normalized_arguments(getattr(context, 'arguments', None)),
        })
        try:
            return await original_process(context, call_next)
        except Exception as exc:
            middleware_failures.append(type(exc).__name__)
            raise

    bridge.function_middleware.process = traced_process

    # Trace each SDK attempt, including writes Core rejects. Simulate failure
    # only for the tool_requested write in the dedicated fault-injection case.
    original_add_event = sdk.add_event

    def traced_add_event(did, event_type, *args, **kwargs):
        record = None
        if event_type == 'tool_requested':
            record = {'eventType': event_type, 'errorType': None}
            tool_request_writes.append(record)
            if break_event_write:
                record['errorType'] = 'InjectedConnectionError'
                raise ConnectionError('simulated transport failure')
        try:
            return original_add_event(did, event_type, *args, **kwargs)
        except Exception as exc:
            if record is not None:
                # Show only exception CLASS (no URLs, secrets or request bodies).
                record['errorType'] = type(exc).__name__
            raise

    sdk.add_event = traced_add_event

    client = ScriptedAgentClient()
    agent = Agent(
        client=client, name='RefundPolicySandbox',
        instructions='Invoke sandbox_refund once with amount 25. Never issue real refunds.',
        tools=[sandbox_refund], middleware=bridge.middleware,
    )
    run_exception = None
    try:
        async with agent:
            with bridge.decision_context(decision_id):
                await agent.run('Call sandbox_refund for amount 25, then say it was simulated.')
    except Exception as exc:
        # Microsoft may surface middleware errors as a tool result instead.
        run_exception = type(exc).__name__

    # Critical assertion: the actual Microsoft dispatcher REACHED the tool
    # middleware. Lack of an event alone is NOT proof of authorization blocking.
    assert client.calls >= 1, f'{name}: scripted client never produced a tool call'
    assert len(middleware_invocations) == 1, (
        f'{name}: expected 1 middleware invocation, got {middleware_invocations}'
    )
    assert middleware_invocations[0] == {
        'name': 'sandbox_refund', 'arguments': {'amount': 25}
    }, (name, middleware_invocations)
    assert len(tool_request_writes) == 1, (
        f'{name}: expected 1 SDK tool_requested write attempt, got {tool_request_writes}'
    )

    allowed = name == 'human_approved'
    expected_executions = int(allowed)
    assert len(sandbox_receipts) == expected_executions, (
        f'{name}: expected {expected_executions} executed tools, '
        f'got {len(sandbox_receipts)}'
    )
    if not allowed:
        assert middleware_failures, (
            f'{name}: no middleware denial/failure was observed; '
            'absence of sandbox side effects alone is insufficient'
        )
    else:
        assert not middleware_failures and run_exception is None, (
            name, middleware_failures, run_exception
        )
        bridge.record_outcome(
            decision_id,
            {'status': 'succeeded', 'amount': sandbox_receipts[0]['amount'],
             'external_reference': sandbox_receipts[0]['id'], 'sandbox': True,
             'real_money_moved': False},
            observer='sandbox-ledger',
        )

    evidence = sdk.get_decision(decision_id)
    event_types = [e.get('event_type') for e in evidence.get('events', [])]
    assert event_types.count('policy_evaluated') == 1, (name, event_types)
    assert event_types.count('tool_executed') == expected_executions, (name, event_types)
    assert event_types.count('outcome_observed') == expected_executions, (name, event_types)

    persisted_requests = event_types.count('tool_requested')
    if allowed:
        assert persisted_requests == 1, (name, event_types)
    elif break_event_write:
        assert persisted_requests == 0, (name, event_types)
        assert tool_request_writes[0]['errorType'] == 'InjectedConnectionError', (
            name, tool_request_writes
        )
    else:
        # Denied Core event writes legitimately leave no tool_requested event;
        # authorized policy with incorrect args normally persists one. Either
        # way require proof of the SDK write ATTEMPT and its outcome.
        assert persisted_requests in (0, 1), (name, event_types)
        if persisted_requests == 0:
            assert tool_request_writes[0]['errorType'] is not None, (
                f'{name}: attempted write vanished without SDK failure', tool_request_writes
            )
        else:
            assert tool_request_writes[0]['errorType'] is None, (
                f'{name}: persisted event despite an SDK write error', tool_request_writes
            )

    if review is True:
        assert event_types.count('human_approved') == 1, (name, event_types)
    if review is False:
        assert event_types.count('human_rejected') == 1, (name, event_types)

    verification = evidence.get('verification', {})
    assert verification.get('valid') is True, (name, verification)
    assert not verification.get('failures'), (name, verification)

    coverage = evidence.get('coverage', {})
    lifecycle = evidence.get('summary', {}).get('lifecycle', {})
    lifecycle_state = lifecycle.get('state') if isinstance(lifecycle, dict) else lifecycle
    if allowed:
        assert lifecycle_state == 'evidence_complete', (name, lifecycle)
        assert coverage.get('score') == 100 and coverage.get('complete') is True, (
            name, coverage
        )

    return {
        'case': name, 'decisionId': decision_id,
        'nativeFunctionInvocations': len(middleware_invocations),
        'requestWriteAttempted': len(tool_request_writes),
        'requestWriteErrorType': tool_request_writes[0]['errorType'],
        'persistedToolRequests': persisted_requests,
        'actualToolExecutions': len(sandbox_receipts),
        'middlewareFailureTypes': middleware_failures,
        'eventTypes': event_types,
        'lifecycle': lifecycle_state,
        'coverageScore': coverage.get('score'),
        'verifyValid': verification.get('valid'),
        'modelCalls': client.calls, 'runExceptionType': run_exception,
        'result': 'PASS',
    }


async def main():
    ready = get('/ready')
    assert ready.get('ready') is True, f'Core is not ready: {ready}'
    ws = post('/api/v1/workspaces', {'name': 'maf-policy-e2e-' + uuid.uuid4().hex[:8]})
    workspace_id = ws['workspace_id']
    cases = [
        ('blocked', dict(policy_mode='blocked')),
        ('missing_approval', dict(policy_mode='human_approval_required')),
        ('human_rejected', dict(policy_mode='human_approval_required', review=False)),
        ('human_approved', dict(policy_mode='human_approval_required', review=True)),
        ('wrong_arguments', dict(policy_mode='auto_allowed', bound_amount=26)),
        ('evidence_write_failure', dict(policy_mode='auto_allowed', break_event_write=True)),
    ]
    results = []
    for name, config in cases:
        results.append(await scenario(workspace_id, name, **config))
        print(f'PASS: {name} (sandbox tools: {results[-1]["actualToolExecutions"]})', flush=True)
    print(json.dumps({
        'framework': 'microsoft-agent-framework',
        'coreVersion': ready['version'], 'workspaceId': workspace_id,
        'casesPassed': len(results), 'cases': results,
        'realMoneyMoved': False,
        'nativeMicrosoftApprovalFlowValidated': False,
    }, indent=2))
    print('PASS: native Microsoft tool dispatch + LoopGrid Core fail-closed evidence validated')
    print('NOTE: Microsoft always_require approval/resumption remains a separate release gate.')


if __name__ == '__main__':
    asyncio.run(main())
