"""Real Microsoft Agent Framework + LoopGrid Core, local scripted sandbox.

Requires external validation on Windows with agent-framework-core>=1.19, loopgrid==0.8,
and LoopGrid Core 0.8.1-design-partner at localhost:8000. Does NOT use a paid model
or execute a real refund. Run this local example as an end-to-end evidence verification check.

Run: python examples/native_core_e2e.py
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request
import uuid

from agent_framework import (
    Agent, BaseChatClient, ChatResponse, Content, Message, tool,
    FunctionInvocationLayer, ChatMiddlewareLayer,
)
from loopgrid import LoopGrid
from loopgrid_microsoft_agent import LoopGridMicrosoftAgent

BASE = os.getenv("LOOPGRID_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.load(r)


def post(path, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(BASE + path, body, {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


class ScriptedAgentClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Deterministic framework tool call; does not contact a model provider."""
    OTEL_PROVIDER_NAME = "loopgrid-local-scripted"

    def __init__(self):
        super().__init__()
        self.calls = 0

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        async def respond():
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(messages=[Message(
                    role="assistant", contents=[Content.from_function_call(
                        call_id="scripted-1", name="sandbox_refund", arguments={"amount": 25},
                    )],
                )])
            return ChatResponse(messages=[Message(
                role="assistant", contents=[Content.from_text(
                    text="Sandbox tool returned. No real money moved."
                )],
            )])
        if stream:
            raise ValueError("This release fixture intentionally uses only non-streaming calls")
        return respond()


async def main() -> None:
    ready = get("/ready")
    assert ready.get("ready") is True, f"LoopGrid Core not ready: {ready}"
    ws = post("/api/v1/workspaces", {"name": "maf-e2e-" + uuid.uuid4().hex[:8]})
    workspace_id = ws["workspace_id"]
    sdk = LoopGrid(base_url=BASE, workspace_id=workspace_id)
    bridge = LoopGridMicrosoftAgent(client=sdk, workspace_id=workspace_id, agent_id="maf-sandbox")
    ledger = []

    @tool(approval_mode="never_require")
    def sandbox_refund(amount: int) -> str:
        """Create ONLY a local, simulated refund receipt."""
        receipt = {
            "id": "sandbox-" + uuid.uuid4().hex[:10], "amount": amount,
            "status": "succeeded", "sandbox": True, "real_money_moved": False,
        }
        ledger.append(receipt)
        return json.dumps(receipt)

    decision = bridge.start_decision(
        decision_type="sandbox_refund",
        agent={"id": "maf-sandbox", "version": "e2e"},
        authority={"acting_for": "local LoopGrid sandbox", "scope": ["refund:simulate"]},
        model={"provider": "local", "name": "scripted-tool-client"},
        context={"prompt_version": "maf-sandbox-1"},
        proposed_action={"tool": "sandbox_refund", "arguments": {"amount": 25}},
        policy={"policy_id": "sandbox-policy", "version": "1", "decision": "auto_allowed"},
        metadata={"sandbox": True, "real_money_moved": False},
    )
    did = decision["decision_id"]
    client = ScriptedAgentClient()
    agent = Agent(
        client=client,
        name="RefundSandbox",
        instructions="Use sandbox_refund once with amount 25; this is NOT a real refund.",
        tools=[sandbox_refund],
        middleware=bridge.middleware,
    )
    async with agent:
        with bridge.decision_context(did):
            result = await agent.run("Call sandbox_refund for amount 25, then report it was simulated.")
    assert len(ledger) == 1, f"Expected one ACTUAL framework tool call, got {len(ledger)}"
    assert ledger[0]["amount"] == 25
    assert client.calls >= 2, "Scripted client did not finish tool-and-response round trip"

    # Only the host-owned sandbox ledger supplies the observed business outcome.
    bridge.record_outcome(
        did,
        {"status": "succeeded", "amount": ledger[0]["amount"], "sandbox": True,
         "real_money_moved": False, "external_reference": ledger[0]["id"]},
        observer="sandbox-ledger",
    )
    evidence = sdk.get_decision(did)
    events = [e.get("event_type") for e in evidence.get("events", [])]
    summary = evidence.get("summary", {})
    lifecycle = summary.get("lifecycle", {})
    lifecycle_state = lifecycle.get("state") if isinstance(lifecycle, dict) else lifecycle
    coverage = evidence.get("coverage", {})
    verified = evidence.get("verification", {})
    assert events.count("tool_requested") == 1, events
    assert events.count("tool_executed") == 1, events
    assert events.count("outcome_observed") == 1, events
    assert events.count("model_completed") >= 1, events
    assert lifecycle_state == "evidence_complete", evidence
    assert coverage.get("score") == 100 and coverage.get("complete") is True, evidence
    assert verified.get("valid") is True and verified.get("failures", []) == [], evidence
    print(json.dumps({
        "framework": "microsoft-agent-framework",
        "coreVersion": ready.get("version"), "workspaceId": workspace_id,
        "decisionId": did, "modelCalls": client.calls, "sandboxToolExecutions": len(ledger),
        "lifecycle": lifecycle_state, "coverageScore": coverage.get("score"),
        "coverageComplete": coverage.get("complete"), "verifyValid": verified.get("valid"),
        "eventTypes": events, "agentResponse": str(result),
    }, indent=2))
    print("PASS: real MAF -> real sandbox tool -> signed LoopGrid Core verification")


if __name__ == "__main__":
    asyncio.run(main())
