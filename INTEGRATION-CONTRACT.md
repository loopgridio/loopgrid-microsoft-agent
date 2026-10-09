# Integration contract

**Existing LoopGrid Core, SDK, signing and verification:** unchanged. No edits to other framework integrations.

| Host/framework source | LoopGrid evidence | Semantics |
| --- | --- | --- |
| Host `start_decision` | `decision_created` | Scope, agent, authority, model, action spec supplied by host |
| Host policy engine → `record_policy` | `policy_evaluated` | Real policy ID, version, decision; fail closed without it |
| MAF `ChatMiddleware` after nonstream completion | `model_completed` | Hash commitment to observed client result; no fabricated model calls |
| MAF `FunctionMiddleware` before tool | `tool_requested` | Observed name and exact-args commitment |
| MAF `FunctionMiddleware` after dispatch returns | `tool_executed` | Invocation returned, not external business outcome |
| Host verified review → `record_human_review` | `human_approved` / `human_rejected` | Caller is responsible for authenticating reviewer |
| Authoritative host receipt → `record_outcome` | `outcome_observed` | Never inferred from the model or tool return alone |
| Core | Coverage, evidence bundle, verify | Same independent verification contract as existing integrations |

A `human_approval_required` policy is NOT implicitly satisfied by Microsoft tool approval prompts. Host must identify and authenticate reviewer and bind approved action to exact args. Tool requests outside `decision_context` fail closed by default. Cache hits or short-circuited calls in *other* middleware must not be interpreted as real execution; install the LoopGrid function middleware directly adjacent to actual dispatch (innermost) and test your middleware ordering.

`MiddlewareFailure` aborts tool execution if policy, call bounds or evidence-writing checks fail. Any host that catches that failure and executes a tool outside Microsoft Agent Framework breaks the security contract; test host-specific behavior.
