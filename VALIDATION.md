# Local validation record — Microsoft Agent Framework v0.1.0

**Scope:** Functional Windows validation was reported on 2026-10-09 against the versions listed below. This historical record is based on operator-provided PowerShell output, not an independent production deployment or authenticated human-review attestation.

## Environment

- Windows, Python 3.12 virtual environment
- `agent-framework-core==1.21.0`
- `loopgrid==0.8.0`
- LoopGrid Core `0.8.1-design-partner` at `http://127.0.0.1:8000`
- Core ready=true; database=ok; signer=local_ed25519; payload_vault=aes-256-gcm; timestamping=not_configured

## Validated by operator

1. Editable install with real framework and SDK: successful.
2. `pytest -q`: **28 passed** (with real dependencies installed).
3. `compileall -q src examples tests`: passed.
4. `examples/native_core_e2e.py`: real Microsoft agent tool dispatch, one simulated $25 refund, two model calls, one actual sandbox invocation; lifecycle `evidence_complete`, applicable coverage 100%, `verifyValid=true`.
5. `examples/native_core_policy_e2e.py`: all **six** scenarios passed. Blocked, approval-missing, rejected, wrong arguments and injected event-write failure: 0 actual tool executions. Host-approved: 1 actual sandbox execution with complete verified evidence. Blocked policy and review-required states may refuse persistence of a `tool_requested` event; this is not evidence of a tool side effect. Incomplete decisions can have <100% coverage while still having valid cryptographic chains.
6. `examples/native_core_microsoft_approval_e2e.py`: actual Microsoft `@tool(approval_mode="always_require")` and `ToolApprovalMiddleware` paused, with 0 executions until resume in the **same** session. Rejection: 0 executed and signed rejected state with 100% coverage. Approval: 1 executed, host-recorded sandbox outcome, `evidence_complete`, 100% coverage, `verifyValid=true`.

All simulated transactions explicitly reported `realMoneyMoved=false`.

## What these validations do NOT prove

- **No authenticated production human reviewer** was tested. The host-generated approval responses are synthetic (`authenticatedHumanIdentityValidated=false`). Production applications must authenticate the reviewer, validate the pending tool name and exact arguments, correlate the request/decision, record the review only after authentication, and prevent replay or approval substitution.
- Neither the SDK nor middleware executes real payments. Core verification proves integrity of recorded evidence, not the actual correctness of a policy, reviewer identity, or downstream business claim.
- Streaming response finalization, durability of in-process action budgets across crashes, distributed authorization and production service availability were not validated.
- The local runtime test record does not itself establish CI status, installation from a public package index, production identity provenance, or compatibility with other LoopGrid integrations.

## Reproducing the validation

See [WINDOWS-VALIDATION.md](WINDOWS-VALIDATION.md) for the exact checks. Review GitHub Actions and packaging logs for the version you install; this record only describes the local functional tests above.
