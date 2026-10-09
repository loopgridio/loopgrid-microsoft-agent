"""Offline contract testing shims when package downloads are not available.

CI installs the real framework and real LoopGrid SDK via pip; these shims are
not used when those packages exist. Offline unit tests exercise the integration
through real middleware method signatures with fake contexts.
"""
import importlib.util
import sys
import types

if importlib.util.find_spec("agent_framework") is None:
    af = types.ModuleType("agent_framework")
    class ChatMiddleware: pass
    class FunctionMiddleware: pass
    class MiddlewareFailure(RuntimeError): pass
    af.ChatMiddleware = ChatMiddleware
    af.FunctionMiddleware = FunctionMiddleware
    af.MiddlewareFailure = MiddlewareFailure
    sys.modules["agent_framework"] = af

if importlib.util.find_spec("loopgrid") is None:
    lg = types.ModuleType("loopgrid")
    class LoopGrid:
        def __init__(self, **kwargs): self.kwargs = kwargs
    lg.LoopGrid = LoopGrid
    sys.modules["loopgrid"] = lg
