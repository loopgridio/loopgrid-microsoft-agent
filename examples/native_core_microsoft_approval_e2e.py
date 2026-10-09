"""RELEASE GATE: Microsoft native always_require approval + LoopGrid Core.

Requires the installed v0.1.0 package, Python 3.12, Microsoft
agent-framework-core 1.21.x, LoopGrid SDK 0.8.0, and local LoopGrid Core.

No paid model and no real refund. Approval decisions are synthetic HOST TEST
responses, not authenticated human attestations. Do not claim this test validates
real reviewer identity, provider-side approval, or production approval UI.

Run in repo root:
    .\\.venv\\Scripts\\python.exe examples\\native_core_microsoft_approval_e2e.py
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request
import uuid
from collections.abc import Mapping

from agent_framework import Agent, Message, ToolApprovalMiddleware, tool
from loopgrid import LoopGrid
from loopgrid_microsoft_agent import LoopGridMicrosoftAgent

# Reuse the deterministic chat client from the sandbox example.
from native_core_e2e import ScriptedAgentClient

BASE = os.getenv("LOOPGRID_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=10) as result:
        return json.load(result)


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        BASE + path,
        json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as result:
        return json.load(result)


def event_types(evidence: dict) -> list[str]:
    return [event.get("event_type", "") for event in evidence.get("events", [])]


def state(evidence: dict) -> str | None:
    lifecycle = evidence.get("summary", {}).get("lifecycle", {})
    return lifecycle.get("state") if isinstance(lifecycle, dict) else lifecycle


def check_verified(evidence: dict, label: str) -> None:
    verified = evidence.get("verification", {})
    assert verified.get("valid") is True and not verified.get("failures", []), (
        f"{label}: Core cryptographic verification failed", verified
    )


def single_native_request(response, label: str):
    requests = list(getattr(response, "user_input_requests", []) or [])
    assert len(requests) == 1, (
        f"{label}: expected exactly one native Microsoft approval request; got "
        f"{len(requests)}. The adapter may be blocking before the framework "
        f"can pause, or approval middleware may be misconfigured."
    )
    request = requests[0]
    assert getattr(request, "type", None) == "function_approval_request", (
        f"{label}: wrong request type", getattr(request, "type", None)
    )
    call = getattr(request, "function_call", None)
    assert call is not None and getattr(call, "name", None) == "sandbox_refund", (
        f"{label}: approval request not bound to the expected function", call
    )
    arguments = call.parse_arguments()
    if hasattr(arguments, "model_dump"):
        arguments = arguments.model_dump(mode="json")
    assert isinstance(arguments, Mapping) and dict(arguments) == {"amount": 25}, (
        f"{label}: framework requested different tool arguments", arguments
    )
    return request


async def check_path(workspace_id: str, *, approve: bool) -> dict:
    label = "native_approved" if approve else "native_rejected"
    sdk = LoopGrid(base_url=BASE, workspace_id=workspace_id)
    bridge = LoopGridMicrosoftAgent(
        client=sdk, workspace_id=workspace_id, agent_id="maf-native-approval-sandbox"
    )
    ledger: list[dict] = []

    # This is Microsoft's own native approval decorator, not our bridge.review flag.
    @tool(approval_mode="always_require")
    def sandbox_refund(amount: int) -> str:
        """Return a simulated sandbox receipt; this never moves money."""
        receipt = {
            "id": "sandbox-" + uuid.uuid4().hex[:10],
            "status": "succeeded", "amount": amount,
            "sandbox": True, "real_money_moved": False,
        }
        ledger.append(receipt)
        return json.dumps(receipt)

    decision = bridge.start_decision(
        decision_type="sandbox_refund_native_approval",
        agent={"id": "maf-native-approval-sandbox", "version": "v0.1.0"},
        authority={"acting_for": "local test host", "scope": ["refund:simulate"]},
        model={"provider": "local", "name": "scripted-tool-client"},
        context={"prompt_version": "native-approval-gate-1"},
        proposed_action={"tool": "sandbox_refund", "arguments": {"amount": 25}},
        policy={"policy_id": "native-approval-policy-" + label,
                "version": "1", "decision": "human_approval_required"},
        metadata={"sandbox": True, "real_money_moved": False,
                  "synthetic_host_approval": True},
    )
    decision_id = decision["decision_id"]
    client = ScriptedAgentClient()
    agent = Agent(
        client=client, name="NativeApprovalSandbox",
        instructions="Call sandbox_refund with amount 25 only; no real money.",
        tools=[sandbox_refund],
        # Real Microsoft middleware: do not add any auto-approval rules.
        middleware=[ToolApprovalMiddleware(), *bridge.middleware],
    )
    session = agent.create_session()

    async with agent:
        # PHASE ONE: request tool invocation; framework MUST pause without executing.
        with bridge.decision_context(decision_id):
            paused = await agent.run(
                "Call sandbox_refund for amount 25, then report sandbox completion.",
                session=session,
            )
        assert not ledger, f"{label}: tool executed BEFORE approval!"
        pending = single_native_request(paused, label)
        before = sdk.get_decision(decision_id)
        assert "tool_executed" not in event_types(before), (
            f"{label}: Core falsely claims execution before approval", event_types(before)
        )
        print(f"PASS: {label} paused for native Microsoft approval; executed=0")

        # SYNTHETIC HOST-TEST REVIEW. A production app must authenticate the
        # reviewer and match the pending call before recording this event.
        bridge.record_human_review(
            decision_id, reviewer="local-synthetic-test-reviewer",
            approved=approve, reason="Synthetic host-only Microsoft approval test",
        )
        approval_response = pending.to_function_approval_response(approved=approve)

        # PHASE TWO: resume the exact pending request in the SAME session.
        with bridge.decision_context(decision_id):
            resumed = await agent.run(
                Message(role="user", contents=[approval_response]), session=session
            )

    evidence = sdk.get_decision(decision_id)
    types = event_types(evidence)
    check_verified(evidence, label)
    expected = 1 if approve else 0
    assert len(ledger) == expected, (
        f"{label}: expected {expected} actual tool executions; got {len(ledger)}", types
    )
    assert types.count("tool_executed") == expected, (
        f"{label}: tool_executed evidence inconsistent with actual invocation", types
    )
    assert types.count("outcome_observed") == 0, (
        f"{label}: observed outcome must only be recorded by the host AFTER execution", types
    )
    if approve:
        assert types.count("human_approved") == 1, (label, types)
        assert types.count("tool_requested") == 1, (label, types)
        assert ledger[0]["amount"] == 25
        # Only the independent host-observed sandbox ledger is an outcome source.
        bridge.record_outcome(
            decision_id,
            {"status": "succeeded", "amount": ledger[0]["amount"],
             "external_reference": ledger[0]["id"], "sandbox": True,
             "real_money_moved": False},
            observer="sandbox-ledger",
        )
        evidence = sdk.get_decision(decision_id)
        types = event_types(evidence)
        check_verified(evidence, label)
        coverage = evidence.get("coverage", {})
        assert state(evidence) == "evidence_complete", (label, state(evidence), types)
        assert coverage.get("score") == 100 and coverage.get("complete") is True, (
            label, coverage, types
        )
        assert types.count("outcome_observed") == 1, (label, types)
    else:
        assert types.count("human_rejected") == 1, (label, types)
        assert state(evidence) == "rejected", (label, state(evidence), types)
        assert "outcome_observed" not in types, (label, types)

    print(f"PASS: {label} resumed via SAME session; executed={len(ledger)}")
    return {
        "case": label, "decisionId": decision_id,
        "nativeApprovalRequest": True, "nativeResume": True,
        "sandboxToolExecutions": len(ledger),
        "modelCalls": client.calls,
        "lifecycle": state(evidence),
        "coverageScore": evidence.get("coverage", {}).get("score"),
        "verifyValid": evidence.get("verification", {}).get("valid"),
        "eventTypes": types,
        "syntheticHostApproval": True,
    }


async def main() -> None:
    ready = get("/ready")
    assert ready.get("ready") is True, ready
    ws = post("/api/v1/workspaces", {"name": "maf-native-approval-" + uuid.uuid4().hex[:8]})
    wid = ws["workspace_id"]
    outputs = []
    for approve in (False, True):
        outputs.append(await check_path(wid, approve=approve))
    print(json.dumps({
        "framework": "microsoft-agent-framework",
        "coreVersion": ready.get("version"),
        "workspaceId": wid, "casesPassed": len(outputs), "cases": outputs,
        "realMoneyMoved": False,
        "authenticatedHumanIdentityValidated": False,
    }, indent=2))
    print("PASS: native Microsoft always_require approval pause/resume + Core evidence")


if __name__ == "__main__":
    asyncio.run(main())
