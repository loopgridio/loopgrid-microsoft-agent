# LoopGrid for Microsoft Agent Framework (Python)

[![CI](https://github.com/loopgridio/loopgrid-microsoft-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/loopgridio/loopgrid-microsoft-agent/actions/workflows/tests.yml)

**v0.1.0.** Native [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) middleware for LoopGrid's signed decision-evidence lifecycle. Uses the existing LoopGrid Core and Python SDK: **no new backend, keys, ledger, evidence profile or Microsoft-specific Core required**.

## What it does

- Connects Microsoft's native `ChatMiddleware` and `FunctionMiddleware` to a host-created LoopGrid decision.
- Records model completions, actual tool requests and returned invocation results with per-event idempotency keys.
- Requires an application-supplied, recorded policy decision before calling a tool. For `human_approval_required`, the host must record a verified approval first.
- Binds each authorized call to the expected **tool name and exact argument mapping**, with one call per decision (across reopened scopes) by default.
- Refuses to execute tools when LoopGrid evidence transport fails; does not swallow errors.
- Captures commitments (SHA-256 digests) rather than raw prompts, arguments or responses by default.
- Lets the **host** append an independently observed external outcome and export/verify the signed decision evidence through existing Core.

**Trust distinction:** a signed tool result means the Framework observed an invocation returning. It does **not** by itself prove a refund was processed, an outside service changed state, a policy was correct, or that a named human really approved. These must be supplied as separately sourced evidence. The adapter does not grant Microsoft tool permissions or replace the host's IAM, policy engine, or Microsoft approval workflow.

## Install

```powershell
python -m pip install loopgrid-microsoft-agent
```

For development or validation from source:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e '.[dev]'
.\.venv\Scripts\python -m pytest -q
```

It depends on `agent-framework-core>=1.19.0,<2` and `loopgrid>=0.8.0,<0.9`. Microsoft's **Python** integration only; no .NET support claimed.

## Minimal usage

```python
from agent_framework import Agent, tool
from loopgrid_microsoft_agent import LoopGridMicrosoftAgent

bridge = LoopGridMicrosoftAgent(workspace_id="YOUR_WORKSPACE_ID", agent_id="finance-agent")

@tool(approval_mode="never_require")  # only for this harmless sandbox demo
async def sandbox_refund(amount: int) -> str:
    """Local sandbox simulation; never changes an actual payment system."""
    return "sandbox simulated only"

decision = bridge.start_decision(
    decision_type="sandbox_refund",
    agent={"id": "finance-agent"},
    authority={"acting_for": "sandbox", "scope": ["refund:simulate"]},
    model={"provider": "your-model-provider", "name": "your-model-name"},
    context={"prompt_version": "v1"},
    proposed_action={"tool": "sandbox_refund", "arguments": {"amount": 25}},
    policy={"policy_id": "reviewed-sandbox-policy", "version": "1", "decision": "auto_allowed"},
    metadata={"sandbox": True, "real_money_moved": False},
)
# Supply a Microsoft Agent Framework chat client configured by the host application.
# YOUR_CONFIGURED_CHAT_CLIENT is a placeholder, not part of this package.
agent = Agent(client=YOUR_CONFIGURED_CHAT_CLIENT, tools=[sandbox_refund],
              middleware=bridge.middleware)

# In an async function:
with bridge.decision_context(decision["decision_id"]):
    result = await agent.run("Perform the sandbox simulation using amount 25")
```

**Order of middleware matters:** place LoopGrid's **function middleware innermost** (last among function middlewares) so that its `call_next()` proceeds directly to Microsoft tool dispatch after any argument-repair middleware. Do not allow additional function middleware to alter validated arguments after LoopGrid authorization. For a real consequential action use Microsoft's `approval_mode="always_require"` and capture the **authenticated host's approval response** via `bridge.record_human_review(...)`; merely asking a model for approval is not sufficient.

`start_decision` does **not** itself call external systems. Its `policy` is a real decision returned by **your** application policy authority, not a policy invented by LoopGrid. If a tool is blocked or approval is missing, Core may reject the pre-execution `tool_requested` write; the adapter fails closed, and no `tool_executed` event is recorded. Successful authorization alone must not be interpreted as execution.

### Host-observed outcome

```python
# Only after querying a trusted external system or independent sandbox ledger:
bridge.record_outcome(
    decision["decision_id"],
    {"status": "succeeded", "external_reference": "trusted-receipt-id", "sandbox": True,
     "real_money_moved": False},
    observer="authoritative-sandbox-ledger",
)
record = bridge.get_decision(decision["decision_id"])
print(record["coverage"], record["verification"])
```

## Local release gate (Windows)

```powershell
Invoke-RestMethod http://127.0.0.1:8000/ready
python -m pytest -q
python -m compileall -q src tests examples
python examples/native_core_e2e.py
python examples/native_core_policy_e2e.py
python examples/native_core_microsoft_approval_e2e.py
python -m build
python -m twine check dist\*
```

`examples/native_core_e2e.py` is a *real-framework, real-Core* sandbox test, using a deterministic local chat client. It must demonstrate actual tool execution, a genuine sandbox receipt, policy, evidence completeness and verification — **the Windows validation reports from 2026-10-09 show these passed against Core 0.8.1; repeat against the installed Core when preparing the exact release artifact**. No paid model or payment system should be needed.

## Current limitations

- `stream=True` model responses are **not** declared `model_completed` until explicit stream-finalization support is added. `streamed_responses_not_recorded` exposes the gap. Use non-streaming runs for the 0.1.0 validation path.
- Native Microsoft tool approvals require the host to verify and record the human review; the bridge does not infer an authenticated reviewer from an untrusted string.
- In-memory action/approval bindings are intentionally not persisted; a process restart requires explicit host re-authorization (fail closed).
- Multiple tools per decision require a more complete action authorization policy; the RC defaults to one exact action per decision even across reopened scopes. Action budgets are process-local: restart requires host re-authorization and downstream idempotency.
- The full Core end-to-end test requires a running LoopGrid Core and installed Microsoft Agent Framework; offline unit tests alone cannot establish that end-to-end behavior.
- Agent-runtime and evidence-pipeline behavior must be verified against the installed Agent Framework version, not only mocked contexts.

See [WINDOWS-VALIDATION.md](WINDOWS-VALIDATION.md), [INTEGRATION-CONTRACT.md](INTEGRATION-CONTRACT.md), [VALIDATION.md](VALIDATION.md), and the [project validation record](VALIDATION.md).
