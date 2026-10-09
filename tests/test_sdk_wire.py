"""Real LoopGrid 0.8 SDK request-shape smoke (skips only when unavailable)."""
import pytest


def test_real_sdk_wire_contract(monkeypatch):
    from loopgrid import LoopGrid
    if not hasattr(LoopGrid, "add_event"):
        pytest.skip("LoopGrid SDK missing in offline shim; run again with real dependencies")
    import httpx
    captured = []
    class Response:
        headers = {"content-type": "application/json"}
        def raise_for_status(self): pass
        def json(self):
            if captured[-1][1].endswith("/api/v1/decisions"):
                return {"decision_id":"dec_sdk_wire"}
            return {"ok": True}

    def fake_request(method, url, **kwargs):
        captured.append((method, url, kwargs))
        return Response()
    monkeypatch.setattr(httpx, "request", fake_request)
    sdk = LoopGrid(base_url="http://127.0.0.1:8000", workspace_id="ws_wire")
    did = sdk.record_decision(decision_type="sandbox", agent={"id":"a"},
          authority={"acting_for":"local"}, model={"name":"scripted"},
          context={"version":"1"}, proposed_action={"tool":"noop", "arguments":{}})["decision_id"]
    sdk.add_event(did, "tool_requested", {"source":"maf"},
                  actor_type="tool", actor_id="noop", idempotency_key="unique-1")
    assert captured[0][0] == 'POST'
    assert captured[0][2]['json']['workspace_id'] == 'ws_wire'
    assert captured[1][1].endswith('/api/v1/decisions/dec_sdk_wire/events')
    assert captured[1][2]['json']['event_type'] == 'tool_requested'
    assert captured[1][2]['json']['idempotency_key'] == 'unique-1'
