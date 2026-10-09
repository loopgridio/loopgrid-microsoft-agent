import asyncio
from types import SimpleNamespace
import pytest

from agent_framework import MiddlewareFailure, ChatMiddleware, FunctionMiddleware
from loopgrid_microsoft_agent import LoopGridMicrosoftAgent
from loopgrid_microsoft_agent.integration import _commitment


class FakeCore:
    def __init__(self):
        self.events = []
        self.decisions = []
        self.broken = False
        self.n = 0

    def record_decision(self, **data):
        if self.broken: raise ConnectionError("SECRET_TOKEN_NEVER_PRINT")
        self.n += 1
        self.decisions.append(data)
        return {"decision_id": f"dec_{self.n}"}

    def policy_evaluated(self, did, policy, actor_id):
        if self.broken: raise ConnectionError("SECRET_TOKEN_NEVER_PRINT")
        self.events.append((did, "policy_evaluated", policy, actor_id, None))
        return {"ok": True}

    def human_approved(self, did, reviewer, reason):
        if self.broken: raise ConnectionError("SECRET_TOKEN_NEVER_PRINT")
        self.events.append((did, "human_approved", {}, reviewer, None))

    def human_rejected(self, did, reviewer, reason):
        self.events.append((did, "human_rejected", {}, reviewer, None))

    def outcome_observed(self, did, outcome, actor_id):
        self.events.append((did, "outcome_observed", outcome, actor_id, None))

    def add_event(self, did, event_type, payload, actor_type, actor_id, idempotency_key):
        if self.broken: raise ConnectionError("SECRET_TOKEN_NEVER_PRINT")
        self.events.append((did, event_type, payload, actor_id, idempotency_key))
        return {"ok": True}

    def get_decision(self, did): return {"decision_id": did}
    def export_evidence(self, did, **kwargs): return b"export"


def setup(policy="auto_allowed", args=None, with_policy=True):
    core = FakeCore()
    bridge = LoopGridMicrosoftAgent(client=core, workspace_id="ws_1", agent_id="ms-agent")
    decision = bridge.start_decision(
        decision_type="sandbox_refund", agent={"id": "ms-agent"},
        authority={"acting_for": "sandbox", "scope": ["refund"]},
        model={"provider": "scripted", "name": "scripted"},
        context={"prompt_version": "v1"},
        proposed_action={"tool": "sandbox_refund", "arguments": args if args is not None else {"amount": 25}},
        policy={"policy_id": "refund-policy", "version": "v1", "decision": policy} if with_policy else None,
        metadata={"sandbox": True},
    )
    return bridge, core, decision["decision_id"]


def tool_context(name="sandbox_refund", args=None):
    return SimpleNamespace(function=SimpleNamespace(name=name),
        arguments=args if args is not None else {"amount": 25}, result=None)


def chat_context(response="the model responded", stream=False):
    return SimpleNamespace(client=SimpleNamespace(model_id="scripted"), stream=stream,
                           result=None)


@pytest.mark.asyncio
async def test_full_native_middleware_flow_order_and_privacy():
    bridge, core, did = setup()
    chat = chat_context()
    ctx = tool_context()
    with bridge.decision_context(did):
        async def model_call(): chat.result = "LLM response"
        await bridge.chat_middleware.process(chat, model_call)
        async def do_tool(): ctx.result = {"receipt": "private-secret", "status": "ok"}
        await bridge.function_middleware.process(ctx, do_tool)
        bridge.record_outcome(did, {"status": "sandbox-success", "external_reference": "sandbox-01"})
    kinds = [e[1] for e in core.events]
    assert kinds == ["policy_evaluated", "model_completed", "tool_requested", "tool_executed", "outcome_observed"]
    assert core.events[3][2]["external_outcome_verified"] is False
    assert "private-secret" not in str(core.events)
    assert len(set(e[4] for e in core.events if e[4])) == 3


@pytest.mark.asyncio
async def test_blocked_policy_stops_execution():
    bridge, core, did = setup("blocked")
    called = False
    async def next_call():
        nonlocal called
        called = True
    with bridge.decision_context(did):
        with pytest.raises(MiddlewareFailure):
            await bridge.function_middleware.process(tool_context(), next_call)
    assert not called
    assert [e[1] for e in core.events] == ["policy_evaluated", "tool_requested"]


@pytest.mark.asyncio
async def test_no_policy_stops_execution():
    bridge, core, did = setup(with_policy=False)
    with bridge.decision_context(did):
        with pytest.raises(MiddlewareFailure):
            await bridge.function_middleware.process(tool_context(), lambda: asyncio.sleep(0))
    assert [e[1] for e in core.events] == ["tool_requested"]


@pytest.mark.asyncio
async def test_human_approval_rejected_stops_tool():
    bridge, core, did = setup("human_approval_required")
    bridge.record_human_review(did, reviewer="host-authenticated-reviewer", approved=False)
    with bridge.decision_context(did):
        with pytest.raises(MiddlewareFailure):
            await bridge.function_middleware.process(tool_context(), lambda: asyncio.sleep(0))
    assert "tool_executed" not in [e[1] for e in core.events]


@pytest.mark.asyncio
async def test_human_approval_authentic_host_only():
    bridge, core, did = setup("human_approval_required")
    bridge.record_human_review(did, reviewer="verified-reviewer", approved=True)
    ctx = tool_context()
    async def next_call(): ctx.result = "sandbox OK"
    with bridge.decision_context(did):
        await bridge.function_middleware.process(ctx, next_call)
    assert "human_approved" in [e[1] for e in core.events]
    assert "tool_executed" in [e[1] for e in core.events]


@pytest.mark.asyncio
async def test_unscoped_run_fails_closed():
    bridge, core, did = setup()
    with pytest.raises(MiddlewareFailure):
        await bridge.function_middleware.process(tool_context(), lambda: asyncio.sleep(0))
    with pytest.raises(MiddlewareFailure):
        await bridge.chat_middleware.process(chat_context(), lambda: asyncio.sleep(0))


@pytest.mark.asyncio
async def test_core_error_prevents_execution_without_leaking_secret():
    bridge, core, did = setup()
    called = False
    async def next_call():
        nonlocal called
        called = True
    core.broken = True
    with bridge.decision_context(did):
        with pytest.raises(MiddlewareFailure) as exc:
            await bridge.function_middleware.process(tool_context(), next_call)
    assert "SECRET_TOKEN_NEVER_PRINT" not in str(exc.value)
    assert not called


@pytest.mark.asyncio
async def test_exceptions_do_not_invent_execution():
    bridge, core, did = setup()
    async def broken(): raise RuntimeError("bad tool")
    with bridge.decision_context(did):
        with pytest.raises(RuntimeError):
            await bridge.function_middleware.process(tool_context(), broken)
    assert "tool_executed" not in [e[1] for e in core.events]


@pytest.mark.asyncio
async def test_framework_approval_placeholder_not_execution():
    bridge, core, did = setup()
    ctx = tool_context()
    async def request(): ctx.result = [SimpleNamespace(type="function_approval_request")]
    with bridge.decision_context(did):
        await bridge.function_middleware.process(ctx, request)
    assert "tool_executed" not in [e[1] for e in core.events]


@pytest.mark.asyncio
async def test_wrong_name_and_arguments_fail_closed():
    bridge, core, did = setup()
    with bridge.decision_context(did):
        with pytest.raises(MiddlewareFailure):
            await bridge.function_middleware.process(tool_context(name="different_tool"), lambda: asyncio.sleep(0))
        with pytest.raises(MiddlewareFailure):
            await bridge.function_middleware.process(tool_context(args={"amount": 26000}), lambda: asyncio.sleep(0))
    assert "tool_executed" not in [e[1] for e in core.events]


@pytest.mark.asyncio
async def test_second_action_blocked():
    bridge, core, did = setup()
    with bridge.decision_context(did):
        ctx = tool_context()
        async def act(): ctx.result = "done"
        await bridge.function_middleware.process(ctx, act)
        with pytest.raises(MiddlewareFailure):
            await bridge.function_middleware.process(tool_context(), lambda: asyncio.sleep(0))
    assert [e[1] for e in core.events].count("tool_executed") == 1


@pytest.mark.asyncio
async def test_model_error_and_streaming_not_falsely_completed():
    bridge, core, did = setup()
    with bridge.decision_context(did):
        ctx = chat_context(stream=True)
        async def done(): ctx.result = "stream is not finalized"
        await bridge.chat_middleware.process(ctx, done)
        err = chat_context()
        async def fail(): err.result = SimpleNamespace(is_error=True)
        await bridge.chat_middleware.process(err, fail)
    assert bridge.streamed_responses_not_recorded == 1
    assert "model_completed" not in [e[1] for e in core.events]


@pytest.mark.asyncio
async def test_nested_context_isolation_and_idempotency():
    bridge, core, did1 = setup()
    data = core.decisions[0].copy(); data["proposed_action"] = {"tool":"sandbox_refund", "arguments":{"amount":25}}
    did2 = bridge.client.record_decision(**data)["decision_id"]
    bridge.record_policy(did2, {"policy_id":"p2", "version":"2", "decision":"auto_allowed"})
    bridge._action_spec[did2] = ("sandbox_refund", {"amount":25}, 1)
    with bridge.decision_context(did1):
        assert bridge.current_decision_id == did1
        with bridge.decision_context(did2):
            assert bridge.current_decision_id == did2
        assert bridge.current_decision_id == did1
    assert bridge.current_decision_id is None


@pytest.mark.parametrize("policy", [{"decision":"auto_allowed"},{"policy_id":"p", "decision":"auto_allowed"},{"policy_id":"p", "version":"1", "decision":"invalid"}])
def test_policy_provenance_validation(policy):
    bridge, _, did = setup(with_policy=False)
    with pytest.raises(ValueError): bridge.record_policy(did, policy)


@pytest.mark.parametrize("action", [{"tool":"do"}, {"tool":"" ,"arguments":{}},{"tool":"do", "arguments":"hi"}])
def test_action_binding_validation(action):
    core = FakeCore(); bridge = LoopGridMicrosoftAgent(client=core)
    with pytest.raises(ValueError): bridge.start_decision(
      decision_type="x", agent={"id":"a"}, authority={"scope":["x"]},
      model={"name":"m"}, context={"v":1}, proposed_action=action)
    assert not core.decisions


def test_middleware_native_types_and_default_privacy():
    bridge, core, did = setup()
    assert isinstance(bridge.middleware[0], ChatMiddleware)
    assert isinstance(bridge.middleware[1], FunctionMiddleware)
    assert bridge.get_decision(did)["decision_id"] == did
    assert bridge.export_evidence(did) == b"export"
    assert _commitment({"secret":"x"})["sha256"] == _commitment({"secret":"x"})["sha256"]


def test_api_errors_do_not_authorize():
    bridge, core, did = setup(with_policy=False)
    core.broken = True
    with pytest.raises(ConnectionError):
        bridge.record_policy(did, {"policy_id":"p", "version":"1", "decision":"auto_allowed"})
    assert did not in bridge._policy


def test_approval_errors_do_not_unlock():
    bridge, core, did = setup("human_approval_required")
    core.broken = True
    with pytest.raises(ConnectionError):
        bridge.record_human_review(did, reviewer="verified", approved=True)
    assert did not in bridge._approved

@pytest.mark.asyncio
async def test_reopening_decision_scope_does_not_reauthorize_tool():
    bridge, core, did = setup()
    with bridge.decision_context(did):
        first = tool_context()
        async def execute(): first.result = "sandbox done"
        await bridge.function_middleware.process(first, execute)
    with bridge.decision_context(did):
        with pytest.raises(MiddlewareFailure, match="maximum authorized"):
            await bridge.function_middleware.process(tool_context(), lambda: asyncio.sleep(0))
    assert [e[1] for e in core.events].count("tool_executed") == 1


def test_idempotent_decision_reprepare_does_not_refill_action_budget():
    bridge, core, did = setup()
    bridge._remaining_calls[did] = 0
    # Same returned decision id is possible with the host's idempotency key.
    core.n -= 1
    bridge.start_decision(
        decision_type="sandbox_refund", agent={"id":"ms-agent"},
        authority={"scope":["refund"]}, model={"name":"scripted"},
        context={"version":"v1"},
        proposed_action={"tool":"sandbox_refund", "arguments":{"amount":25}},
    )
    assert bridge._remaining_calls[did] == 0


def test_old_decision_without_memory_binding_fails_closed():
    bridge, core, did = setup()
    other = LoopGridMicrosoftAgent(client=core)
    with other.decision_context(did):
        async def fake(): pass
        with pytest.raises(MiddlewareFailure):
            asyncio.run(other.function_middleware.process(tool_context(), fake))

@pytest.mark.asyncio
async def test_pydantic_arguments_are_compared_canonically():
    from pydantic import BaseModel
    class Arguments(BaseModel):
        amount: int
    bridge, core, did = setup()
    ctx = tool_context(args=Arguments(amount=25))
    async def call(): ctx.result = "approved sandbox invocation"
    with bridge.decision_context(did):
        await bridge.function_middleware.process(ctx, call)
    assert [e[1] for e in core.events].count("tool_executed") == 1
