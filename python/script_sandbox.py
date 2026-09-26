from __future__ import annotations

import ast
import hashlib
import importlib
import math
import statistics
import sys
import time
import types
from typing import Any, Callable, Optional

MAX_CODE_BYTES = 128 * 1024
HOOK_NAMES = ("decide", "decide_market", "manage", "decide_signal",
              "supervise", "on_start", "on_fill", "on_settle")
ENTRY_HOOKS = ("decide", "decide_market", "manage", "decide_signal",
               "supervise")

INJECTED_GLOBALS = ("math", "statistics", "state", "log", "ctx")

# ------------------------------------------------------------------ the sandbox
#
# Scripts are pasted in — the Scripts page invites pasting an AI chat's reply —
# and they run inside the backend process, which holds the user's Polymarket US
# credentials. They used to run as full Python: a pasted script could import
# os, read the credential store and post it anywhere. The risk audit only
# warned, a flagged script could be re-enabled, and a static audit cannot see
# through obfuscation.
#
# A strategy needs arithmetic, the market data it is handed, and somewhere to
# keep state. So scripts now get exactly that:
#   * a curated set of builtins (no open, eval, exec, compile, input, vars,
#     globals, breakpoint), with getattr/setattr/delattr that refuse the names
#     below and an __import__ that only admits ALLOWED_MODULES;
#   * modules through a read-only view that hides private names, hides any
#     attribute that is itself a module (statistics.sys was a way out), and
#     hides the few functions that evaluate strings as code;
#   * no dunder attributes or names beyond SAFE_DUNDERS, and none of the frame,
#     traceback and generator internals that lead back to real globals —
#     checked in the source before it compiles, so the author sees why.
# The risk audit still runs on top of this.

ALLOWED_MODULES = frozenset({
    "__future__",  # `from __future__ import annotations` is a compiler directive
    "math", "cmath", "statistics", "random", "decimal", "fractions", "numbers",
    "datetime", "time", "calendar",
    "collections", "collections.abc", "itertools", "functools", "heapq",
    "bisect", "array", "copy", "dataclasses", "enum", "typing",
    "re", "json", "textwrap",
})

# Functions inside allowed modules that evaluate strings or reach attributes by
# computed name. typing.get_type_hints evaluates annotation strings against a
# class's module globals, which for a script class are empty — so eval() fills
# in the REAL builtins.
MODULE_DENY = {
    "typing": frozenset({"get_type_hints", "evaluate_forward_ref"}),
}

# Dunders a script may legitimately write: super().__init__(...),
# type(x).__name__, x.__class__. Everything else that starts and ends with a
# double underscore is refused (__globals__, __self__, __subclasses__,
# __builtins__, __dict__, __code__, __closure__, __traceback__ ...).
SAFE_DUNDERS = frozenset({"__init__", "__name__", "__qualname__", "__doc__",
                          "__class__"})

# Not dunders, but each one hands back a frame, code object or traceback, and
# from a frame f_globals / f_back reach the engine's real module globals.
BLOCKED_ATTRS = frozenset({
    "gi_frame", "gi_code", "gi_yieldfrom", "gi_suspended",
    "cr_frame", "cr_code", "cr_await", "cr_origin",
    "ag_frame", "ag_code", "ag_await",
    "f_back", "f_globals", "f_locals", "f_builtins", "f_code", "f_trace",
    "tb_frame", "tb_next", "func_globals",
})


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def name_allowed(name: str) -> bool:
    """Whether a script may use this attribute or global name."""
    if name in BLOCKED_ATTRS:
        return False
    if _is_dunder(name):
        return name in SAFE_DUNDERS
    return True


def module_allowed(name: str) -> bool:
    return name in ALLOWED_MODULES


def restrictions(tree: ast.AST) -> list[str]:
    """Why this source cannot run as a script, or [] if it can."""
    found: list[str] = []

    def note(node: ast.AST, msg: str) -> None:
        found.append(f"line {getattr(node, 'lineno', '?')}: {msg}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not module_allowed(alias.name):
                    note(node, f"import {alias.name} is not available to "
                               "scripts (allowed: " + ", ".join(sorted(ALLOWED_MODULES)) + ")")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                note(node, "relative imports are not available to scripts")
            elif not module_allowed(node.module or ""):
                note(node, f"import from {node.module} is not available to scripts")
            for alias in node.names:
                if alias.name == "*":
                    note(node, f"'from {node.module} import *' is not supported; "
                               "import the names you use")
                elif not name_allowed(alias.name) or alias.name in MODULE_DENY.get(node.module or "", ()):
                    note(node, f"{node.module}.{alias.name} is not available to scripts")
        elif isinstance(node, ast.Attribute) and not name_allowed(node.attr):
            note(node, f".{node.attr} is not available to scripts")
        elif isinstance(node, ast.Name) and not name_allowed(node.id):
            note(node, f"{node.id} is not available to scripts")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for n in node.names:
                if not name_allowed(n):
                    note(node, f"{n} is not available to scripts")
    return found


class _ModuleView:
    """A read-only view of an allowed module.

    The real module is never an attribute of the view — any attribute name,
    even a private one, would be a handle a script could follow — so it is
    looked up by the view's identity in a table the script cannot see.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        mod = _VIEW_TARGET[id(self)]
        if name.startswith("_") or name in MODULE_DENY.get(mod.__name__, ()):
            raise AttributeError(f"{mod.__name__}.{name} is not available to scripts")
        value = getattr(mod, name)
        if isinstance(value, types.ModuleType):
            if module_allowed(value.__name__):
                return _module_view(value)
            raise AttributeError(f"{mod.__name__}.{name} is not available to scripts")
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("modules are read-only in scripts")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("modules are read-only in scripts")

    def __dir__(self) -> list[str]:
        mod = _VIEW_TARGET[id(self)]
        return [n for n in dir(mod) if not n.startswith("_")]

    def __repr__(self) -> str:
        return f"<module '{_VIEW_TARGET[id(self)].__name__}' (script view)>"


_VIEW_TARGET: dict[int, types.ModuleType] = {}
_VIEWS: dict[str, _ModuleView] = {}


def _module_view(mod: types.ModuleType) -> _ModuleView:
    view = _VIEWS.get(mod.__name__)
    if view is None:
        view = _ModuleView()
        _VIEW_TARGET[id(view)] = mod
        _VIEWS[mod.__name__] = view   # views live for the process; ids stay unique
    return view


def _safe_import(name: str, globals: Any = None, locals: Any = None,
                 fromlist: Any = (), level: int = 0) -> _ModuleView:
    if level or not module_allowed(name):
        raise ImportError(f"import {name} is not available to scripts")
    importlib.import_module(name)
    if fromlist:
        return _module_view(sys.modules[name])
    # `import collections.abc` binds the top-level package; the view reaches
    # the submodule because it is allowed too.
    return _module_view(sys.modules[name.partition(".")[0]])


def _checked(name: Any) -> str:
    if not isinstance(name, str):
        raise TypeError("attribute name must be a string")
    if not name_allowed(name):
        raise AttributeError(f"{name} is not available to scripts")
    return name


def _safe_getattr(obj: Any, name: Any, *default: Any) -> Any:
    return getattr(obj, _checked(name), *default)


def _safe_setattr(obj: Any, name: Any, value: Any) -> None:
    setattr(obj, _checked(name), value)


def _safe_delattr(obj: Any, name: Any) -> None:
    delattr(obj, _checked(name))


_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
    "callable", "chr", "classmethod", "complex", "dict", "dir", "divmod",
    "enumerate", "filter", "float", "format", "frozenset", "hasattr", "hash",
    "hex", "id", "int", "isinstance", "issubclass", "iter", "len", "list",
    "map", "max", "min", "next", "object", "oct", "ord", "pow", "property",
    "range", "repr", "reversed", "round", "set", "slice", "sorted",
    "staticmethod", "str", "sum", "super", "tuple", "type", "zip",
    "NotImplemented", "Ellipsis", "__build_class__",
)


SCRIPT_MODULE = "rom_script"


def _script_module() -> types.ModuleType:
    """The module classes defined by scripts claim to live in.

    dataclasses (and anything else that resolves a class's module) looks the
    class's __module__ up in sys.modules. The stand-in is empty and carries the
    script builtins, so nothing evaluated against its namespace reaches the
    real ones.
    """
    mod = sys.modules.get(SCRIPT_MODULE)
    if mod is None:
        mod = types.ModuleType(SCRIPT_MODULE, "Stand-in for classes defined by user scripts.")
        mod.__dict__["__builtins__"] = _safe_builtins(lambda *parts: None)
        sys.modules[SCRIPT_MODULE] = mod
    return mod


def _safe_builtins(log: Callable[..., None]) -> dict[str, Any]:
    import builtins as _builtins
    safe = {n: getattr(_builtins, n) for n in _SAFE_BUILTIN_NAMES}
    for n, v in vars(_builtins).items():
        # SystemExit and KeyboardInterrupt are left out: the engine catches
        # Exception around every hook, and those two would sail past it.
        if (isinstance(v, type) and issubclass(v, BaseException)
                and v not in (SystemExit, KeyboardInterrupt)):
            safe[n] = v
    safe.update({
        "print": log,
        "getattr": _safe_getattr,
        "setattr": _safe_setattr,
        "delattr": _safe_delattr,
        "__import__": _safe_import,
    })
    return safe


class ScriptError(Exception):
    pass


class ScriptBudgetExceeded(BaseException):
    pass


SCRIPT_FAILURES = (ScriptError, ScriptBudgetExceeded)


def parse_header(code: str) -> dict:
    meta = {"version": None, "name": "", "description": ""}
    for line in code.splitlines()[:15]:
        s = line.strip()
        if not s.startswith("#"):
            if s:
                break
            continue
        body = s.lstrip("#").strip()
        low = body.lower()
        if low.startswith("rom-script"):
            meta["version"] = body.split()[-1] if len(body.split()) > 1 else "v1"
        elif low.startswith("name:"):
            meta["name"] = body[5:].strip()
        elif low.startswith("description:"):
            meta["description"] = body[12:].strip()
    return meta


def find_ctx_fields(code: str) -> list[str]:
    fields: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name) and node.value.id == "ctx"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            fields.add(node.slice.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "ctx"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            fields.add(node.args[0].value)
    return sorted(fields)


def validate(code: str) -> list[str]:
    if len(code.encode("utf-8", "replace")) > MAX_CODE_BYTES:
        return [f"script exceeds {MAX_CODE_BYTES // 1024}KB"]
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error line {e.lineno}: {e.msg}"]
    defined = {n.name for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if not defined & set(ENTRY_HOOKS):
        return ["script must define at least one of: decide(ctx), "
                "decide_market(market), manage(position, ctx), "
                "decide_signal(signal), supervise(app)"]
    async_hooks = sorted(
        n.name for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name in HOOK_NAMES)
    if async_hooks:
        return [f"hook(s) {', '.join(async_hooks)} are 'async def' — the engine "
                "calls hooks synchronously, so an async hook would never run. "
                "Use a plain 'def'."]
    return restrictions(tree)


class _Tracer:

    __slots__ = ("count", "deadline", "tripped", "why")

    def __init__(self, deadline: float) -> None:
        self.count = 0
        self.deadline = deadline
        self.tripped = False
        self.why = ""

    def __call__(self, frame, event, arg):  # noqa: ANN001 - trace signature
        self.count += 1
        if self.count % 64 == 0 and time.perf_counter() > self.deadline:
            self.tripped = True
            self.why = "script exceeded its per-call time budget"
            raise ScriptBudgetExceeded(self.why)
        return self


def call_budgeted(fn: Callable, *args: Any, budget_ms: float = 250.0,
                  traced: bool = False) -> Any:
    if not traced:
        return fn(*args)
    deadline = time.perf_counter() + budget_ms / 1000.0
    tracer = _Tracer(deadline)
    old = sys.gettrace()
    sys.settrace(tracer)
    lost = False
    try:
        out = fn(*args)
    finally:
        lost = sys.gettrace() is not tracer
        sys.settrace(old)
    if tracer.tripped:
        raise ScriptBudgetExceeded(tracer.why or "script exceeded its per-call budget")
    if lost:
        raise ScriptBudgetExceeded(
            "script disabled its own time budget (the trace function was lost "
            "mid-call — usually a swallowed budget error)"
        )
    return out


def code_hash(code: str) -> str:
    h = hashlib.sha256()
    h.update(code.encode("utf-8", "replace"))
    return h.hexdigest()


class CompiledScript:

    def __init__(self, script_id: str, code: str, *,
                 log_sink: Optional[Callable[[str], None]] = None,
                 state: Optional[dict] = None, traced: bool = False):
        self.script_id = script_id
        self.traced = bool(traced)
        self.hash = code_hash(code)
        self.state: dict = state if isinstance(state, dict) else {}
        self._log_lines: list[str] = []
        self._log_sink = log_sink
        self._log_count = 0

        errors = validate(code)
        if errors:
            raise ScriptError("; ".join(errors[:5]))

        def _log(*parts: Any) -> None:
            self._log_count += 1
            if self._log_count > 2000:
                return
            msg = " ".join(str(p) for p in parts)[:400]
            self._log_lines.append(msg)
            if len(self._log_lines) > 200:
                del self._log_lines[:100]
            if self._log_sink:
                try:
                    self._log_sink(msg)
                except Exception:
                    pass

        g: dict[str, Any] = {
            "__builtins__": _safe_builtins(_log),
            "__name__": _script_module().__name__,
            "math": _module_view(math), "statistics": _module_view(statistics),
            "state": self.state, "log": _log,
        }
        self.globals = g

        # dont_inherit: this file's own `from __future__ import annotations`
        # would otherwise turn every script's annotations into strings.
        code_obj = compile(code, f"<script:{script_id[:8]}>", "exec", dont_inherit=True)
        call_budgeted(eval, code_obj, g, budget_ms=2000.0, traced=self.traced)
        self.hooks: dict[str, Callable] = {}
        for name in HOOK_NAMES:
            fn = g.get(name)
            if callable(fn):
                self.hooks[name] = fn
        if not set(self.hooks) & set(ENTRY_HOOKS):
            raise ScriptError(
                "script must define at least one of: decide, decide_market, "
                "manage, decide_signal, supervise")

    def drain_logs(self) -> list[str]:
        out = self._log_lines[:]
        self._log_lines.clear()
        return out

    def call(self, hook: str, *args: Any, budget_ms: float = 250.0) -> Any:
        fn = self.hooks.get(hook)
        if fn is None:
            return None
        try:
            return call_budgeted(fn, *args, budget_ms=budget_ms,
                                 traced=self.traced)
        except ScriptBudgetExceeded:
            raise
        except Exception as e:
            raise ScriptError(f"{hook}() raised {type(e).__name__}: {e}") from e
