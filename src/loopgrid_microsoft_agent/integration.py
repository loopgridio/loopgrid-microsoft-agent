"""Microsoft Agent Framework native middleware -> LoopGrid signed decision evidence.

This adapter records only observed framework events and host-supplied policy,
approval, and downstream outcome evidence. It never executes external actions.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import threading
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_framework import ChatMiddleware, FunctionMiddleware, MiddlewareFailure
from loopgrid import LoopGrid

VERSION = "0.1.0"
FRAMEWORK = "microsoft-agent-framework"


def _required(label: str, value: Any) -> None:
    if value is None or value == "" or value == {} or value == []:
        raise ValueError(f"{label} is required")


def _bytes(value: Any) -> bytes:
    # Microsoft arguments can be Pydantic models, not only dictionaries.
    # Normalize both shapes before verifying exact authorized arguments.
    if hasattr(value, "model_dump") and callable(value.model_dump):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str).encode("utf-8")


def _commitment(value: Any) -> dict[str, Any]:
    raw = _bytes(value)
    return {"algorithm": "sha256", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _key(*parts: Any) -> str:
    return "lg-ms-agent-" + hashlib.sha256(_bytes(parts)).hexdigest()


def _approval_placeholder(value: Any) -> bool:
    """Framework request for approval is NOT an executed tool."""
    if getattr(value, "type", "") == "function_approval_request":
        return True
    if isinstance(value, (list, tuple)):
        return any(_approval_placeholder(item) for item in value)
    return False


@dataclass
class _Scope:
    decision_id: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ordinal: int = 0
    policy_decision: str | None = None
    approved: bool = False
    expected_tool: str | None = None
    expected_arguments: dict[str, Any] | None = None
    max_tool_calls: int = 1
    tool_calls: int = 0


class _LoopGridChatMiddleware(ChatMiddleware):
    def __init__(self, owner: "LoopGridMicrosoftAgent") -> None:
        self.owner = owner

    async def process(self, context: Any, call_next: Callable[..., Any]) -> None:
        scope = self.owner._scope.get()
        if scope is None:
            if self.owner.require_scope:
                raise MiddlewareFailure("LoopGrid decision_context is required for model calls")
            await call_next()
            return
        await call_next()
        # Microsoft streams require separate finalization hooks. Do not create
        # model_completed before the entire stream is consumed.
        if getattr(context, "stream", False):
            self.owner._stream_skipped += 1
            return
        response = getattr(context, "result", None)
        if response is None or getattr(response, "is_error", False):
            return
        model = getattr(getattr(context, "client", None), "model_id", None)
        payload = {
            "framework": FRAMEWORK,
            "source": "agent_framework.ChatMiddleware",
            "model": str(model) if model else None,
            "status": "completed",
            "response_commitment": _commitment(response),
        }
        await self.owner._event(scope, "model_completed", payload, "agent", self.owner.agent_id)


class _LoopGridFunctionMiddleware(FunctionMiddleware):
    def __init__(self, owner: "LoopGridMicrosoftAgent") -> None:
        self.owner = owner

    async def process(self, context: Any, call_next: Callable[..., Any]) -> None:
        scope = self.owner._scope.get()
        if scope is None:
            if self.owner.require_scope:
                raise MiddlewareFailure("LoopGrid decision_context is required for tool calls")
            await call_next()
            return
        name = getattr(getattr(context, "function", None), "name", None)
        if not isinstance(name, str) or not name.strip():
            raise MiddlewareFailure("Missing Microsoft function name; refusing unidentifiable tool invocation")
        args = getattr(context, "arguments", None)
        scope.ordinal += 1
        occurrence = scope.ordinal
        common = {
            "framework": FRAMEWORK,
            "source": "agent_framework.FunctionMiddleware",
            "tool_name": name,
            "occurrence": occurrence,
            "arguments_commitment": _commitment(args),
        }
        # Written before the actual tool invocation; if Core is unavailable,
        # fail closed instead of executing an action without evidence.
        await self.owner._event(scope, "tool_requested", common, "tool", name, occurrence)
        self.owner._assert_authorized(scope, name, args)
        # An exception, rejection, or later middleware short-circuit is NOT proof
        # a tool executed. Leave only the authentic requested event in Core.
        await call_next()
        result = getattr(context, "result", None)
        if _approval_placeholder(result):
            return
        executed = dict(common)
        executed.update({"status": "returned", "result_commitment": _commitment(result),
                         "external_outcome_verified": False})
        await self.owner._event(scope, "tool_executed", executed, "tool", name, occurrence)


class LoopGridMicrosoftAgent:
    """Application-owned decision bridge for Microsoft's native middleware.

    Attach bridge.middleware to Agent(middleware=...). Wrap agent.run(...)
    in bridge.decision_context(decision_id), with an authenticated host-supplied
    policy decision registered before any consequence-bearing tool can run.

    require_scope=True + fail-closed event writes are secure defaults. Other
    middleware ordering and application authentication remain host concerns.
    """

    def __init__(
        self, *, client: LoopGrid | None = None,
        base_url: str = "http://127.0.0.1:8000", api_key: str | None = None,
        workspace_id: str = "default", agent_id: str = "microsoft-agent",
        require_scope: bool = True,
    ) -> None:
        _required("workspace_id", workspace_id)
        _required("agent_id", agent_id)
        self.client = client if client is not None else LoopGrid(
            base_url=base_url, api_key=api_key, workspace_id=workspace_id
        )
        self.workspace_id = workspace_id
        self.agent_id = agent_id
        self.require_scope = require_scope
        self._scope: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar(
            f"loopgrid_ms_scope_{id(self)}", default=None
        )
        self._policy: dict[str, str] = {}
        self._approved: set[str] = set()
        self._action_spec: dict[str, tuple[str, dict[str, Any], int]] = {}
        self._remaining_calls: dict[str, int] = {}
        self._lock = threading.RLock()
        self._stream_skipped = 0
        self.chat_middleware = _LoopGridChatMiddleware(self)
        self.function_middleware = _LoopGridFunctionMiddleware(self)

    @property
    def middleware(self) -> list[Any]:
        """Pass to Agent(..., middleware=bridge.middleware)."""
        return [self.chat_middleware, self.function_middleware]

    @property
    def current_decision_id(self) -> str | None:
        scope = self._scope.get()
        return scope.decision_id if scope else None

    @property
    def streamed_responses_not_recorded(self) -> int:
        """Nonzero means streaming evidence coverage is incomplete."""
        return self._stream_skipped

    @contextlib.contextmanager
    def decision_context(self, decision_id: str) -> Iterator["LoopGridMicrosoftAgent"]:
        _required("decision_id", decision_id)
        with self._lock:
            spec = self._action_spec.get(decision_id)
            scope = _Scope(
                decision_id, policy_decision=self._policy.get(decision_id),
                approved=decision_id in self._approved,
                expected_tool=spec[0] if spec else None,
                expected_arguments=spec[1] if spec else None,
                max_tool_calls=spec[2] if spec else 1,
            )
        token = self._scope.set(scope)
        try:
            yield self
        finally:
            self._scope.reset(token)

    def start_decision(
        self, *, decision_type: str, agent: Mapping[str, Any],
        authority: Mapping[str, Any], model: Mapping[str, Any],
        context: Mapping[str, Any], proposed_action: Mapping[str, Any],
        policy: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        privacy_mode: str = "redacted", idempotency_key: str | None = None,
        max_tool_calls: int = 1,
    ) -> dict[str, Any]:
        _required("decision_type", decision_type)
        _required("agent.id", agent.get("id"))
        _required("authority", authority)
        _required("model.name", model.get("name"))
        _required("context", context)
        _required("proposed_action", proposed_action)
        tool_name = proposed_action.get("tool")
        tool_args = proposed_action.get("arguments")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("proposed_action.tool must name the permitted tool")
        if not isinstance(tool_args, Mapping):
            raise ValueError("proposed_action.arguments must be an explicit mapping, including {}")
        if not isinstance(max_tool_calls, int) or isinstance(max_tool_calls, bool) or max_tool_calls < 1:
            raise ValueError("max_tool_calls must be a positive integer")
        md = dict(metadata or {})
        md.update({"framework": FRAMEWORK, "integration": "loopgrid-microsoft-agent",
                   "integration_version": VERSION})
        recorded = self.client.record_decision(
            decision_type=decision_type, agent=dict(agent), authority=dict(authority),
            model=dict(model), context=dict(context), proposed_action=dict(proposed_action),
            metadata=md, privacy_mode=privacy_mode, idempotency_key=idempotency_key,
        )
        decision_id = recorded["decision_id"]
        with self._lock:
            spec = (tool_name, dict(tool_args), max_tool_calls)
            previous = self._action_spec.get(decision_id)
            if previous is not None and previous != spec:
                raise ValueError("Decision already bound to a different action specification")
            if previous is None:
                self._action_spec[decision_id] = spec
                self._remaining_calls[decision_id] = max_tool_calls
        if policy is not None:
            self.record_policy(decision_id, policy)
        return recorded

    def record_policy(self, decision_id: str, policy: Mapping[str, Any]) -> Any:
        """Record a real application-evaluated policy; NEVER generate one."""
        _required("decision_id", decision_id)
        p = dict(policy)
        mode = p.get("decision")
        if mode not in {"auto_allowed", "human_approval_required", "blocked", "block"}:
            raise ValueError("policy.decision must be auto_allowed, human_approval_required, or blocked")
        if not (p.get("policy_id") and p.get("version")):
            raise ValueError("policy provenance requires policy_id AND version")
        result = self.client.policy_evaluated(decision_id, p, actor_id=str(p["policy_id"]))
        # Do not unlock if Core refused to commit the policy record.
        with self._lock:
            self._policy[decision_id] = str(mode)
            self._approved.discard(decision_id)
        scope = self._scope.get()
        if scope and scope.decision_id == decision_id:
            scope.policy_decision, scope.approved = str(mode), False
        return result

    def record_human_review(self, decision_id: str, *, reviewer: str, approved: bool,
                            reason: str = "") -> Any:
        """Call only AFTER the host verified an authentic human response."""
        _required("decision_id", decision_id)
        _required("reviewer", reviewer)
        with self._lock:
            policy = self._policy.get(decision_id)
        if policy != "human_approval_required":
            raise ValueError("human review expected only after human_approval_required policy")
        if approved:
            result = self.client.human_approved(decision_id, reviewer, reason)
            with self._lock:
                self._approved.add(decision_id)
        else:
            result = self.client.human_rejected(decision_id, reviewer, reason)
            with self._lock:
                self._approved.discard(decision_id)
        scope = self._scope.get()
        if scope and scope.decision_id == decision_id:
            scope.approved = approved
        return result

    def record_outcome(self, decision_id: str, outcome: Mapping[str, Any], *,
                       observer: str = "application") -> Any:
        """Only an independent application/external receipt can support outcomes."""
        _required("decision_id", decision_id)
        _required("outcome", outcome)
        _required("observer", observer)
        return self.client.outcome_observed(decision_id, dict(outcome), actor_id=observer)

    def _assert_authorized(self, scope: _Scope, name: str, args: Any) -> None:
        if scope.expected_tool is None or scope.expected_arguments is None:
            raise MiddlewareFailure("Tool execution blocked: action binding missing")
        if name != scope.expected_tool or _bytes(args) != _bytes(scope.expected_arguments):
            raise MiddlewareFailure("Tool execution blocked: function or arguments differ from authorized decision")
        if not (
            scope.policy_decision == "auto_allowed" or
            (scope.policy_decision == "human_approval_required" and scope.approved)
        ):
            raise MiddlewareFailure("Tool execution blocked: LoopGrid policy authorization or approval missing")
        with self._lock:
            available = self._remaining_calls.get(scope.decision_id, 0)
            if available < 1:
                raise MiddlewareFailure("Tool execution blocked: maximum authorized calls exceeded")
            # Reserve BEFORE yielding to the framework; prevents parallel and
            # repeated decision_context scopes from replaying an action.
            self._remaining_calls[scope.decision_id] = available - 1
            scope.tool_calls += 1

    async def _event(self, scope: _Scope, kind: str, payload: dict[str, Any],
                     actor_type: str, actor_id: str, occurrence: int | None = None) -> None:
        identity = occurrence if occurrence is not None else None
        if kind == "model_completed":
            scope.ordinal += 1
            identity = scope.ordinal
        try:
            await asyncio.to_thread(
                self.client.add_event, scope.decision_id, kind, payload,
                actor_type=actor_type, actor_id=actor_id,
                idempotency_key=_key(scope.decision_id, scope.run_id, kind, identity),
            )
        except Exception as exc:
            # Avoid echoing auth headers, request bodies or URLs to the caller.
            raise MiddlewareFailure(
                f"LoopGrid evidence write failed ({type(exc).__name__}); refusing unrecorded tool continuation"
            ) from None

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        return self.client.get_decision(decision_id)

    def export_evidence(self, decision_id: str, include_payloads: bool = True) -> bytes:
        return self.client.export_evidence(decision_id, include_payloads=include_payloads)

__all__ = ["LoopGridMicrosoftAgent", "FRAMEWORK", "VERSION"]
