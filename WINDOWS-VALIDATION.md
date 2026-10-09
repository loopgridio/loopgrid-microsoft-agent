# Windows validation — Microsoft Agent Framework v0.1.0

Run commands from this repository root in PowerShell. The end-to-end examples require a running local LoopGrid Core at `http://127.0.0.1:8000`; all transactions are simulated.

## Install and validate the source

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src examples tests
```

## Real framework + local Core examples

```powershell
Invoke-RestMethod http://127.0.0.1:8000/ready
.\.venv\Scripts\python.exe examples\native_core_e2e.py
.\.venv\Scripts\python.exe examples\native_core_policy_e2e.py
.\.venv\Scripts\python.exe examples\native_core_microsoft_approval_e2e.py
```

All examples are sandbox-only; they do not move money. Approval examples supply synthetic host responses, **not** independently authenticated production identities.

## Wheel / sdist smoke check

```powershell
Remove-Item -Recurse -Force .\dist -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m twine check dist\*
py -3.12 -m venv .venv-wheelcheck
$wheel = (Get-ChildItem .\dist -Filter '*.whl' | Select-Object -First 1).FullName
.\.venv-wheelcheck\Scripts\python.exe -m pip install $wheel
.\.venv-wheelcheck\Scripts\python.exe -m pip check
.\.venv-wheelcheck\Scripts\python.exe -c "from loopgrid_microsoft_agent import LoopGridMicrosoftAgent, VERSION; print(VERSION, LoopGridMicrosoftAgent.__name__)"
```

Interpretation: `verifyValid=true` verifies integrity of recorded evidence, not the correctness of a policy, human identity or external outcome. Core may refuse a pre-execution `tool_requested` write for denied or review-pending decisions; the correct invariant is **zero unauthorized tool execution**.
