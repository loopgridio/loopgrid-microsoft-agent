# Security considerations

- Never put payment keys, authorization headers or OAuth tokens in evidence payloads.
- Captured arguments/model responses are hashed by default; even hashes can leak information via dictionary attacks for low-entropy inputs. Use sanitized application metadata and minimize sensitive data.
- Core credentials use `LoopGrid` SDK configuration. Do not commit `.env` files or workspace credentials.
- The adapter's policy entries are accepted only from its **trusted host application**; it does not independently assess policy correctness or identity provenance.
- `record_human_review` must only be called after a separate authenticated human-review system confirms approval. The approval must match the preauthorized tool and argument mapping.
- The bridge is fail closed on missing scope, denied policy, exhausted call limits and failure to write pre-execution evidence.
- Streaming model evidence is deliberately incomplete until stream-finalization support exists.
- The adapter cannot prevent out-of-band tool invocation or untrusted middleware which bypasses its enforcement; host must attach it to every consequential execution path.
- The SHA-256 commitments included in event payloads are NOT signatures. Ed25519 signatures and hash-chain verification are produced and checked by LoopGrid Core, not by this package.
- Do not claim Microsoft certification or enterprise compliance just because signing succeeds.

To report an issue, use the project's GitHub security reporting instructions after release. Do not paste credentials or secrets into issues.

- Execution-slot reservations are process-local. A host must use an idempotent downstream action API and should not treat this adapter as a durable distributed authorization gateway.
