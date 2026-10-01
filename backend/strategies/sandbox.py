"""
Compiling and running user-written strategy code.

**What this is and is not.** Tradeo runs on your own machine, against your own
strategies. The threat being defended against is *accident*, not malice: a
strategy that shells out because an LLM suggested `os.system`, one that
silently reads a file and makes the backtest unreproducible, one that loops
forever and takes the API process with it. Python's dynamic nature means a
determined author can escape any in-process sandbox, and pretending otherwise
would be the dishonest claim. If you paste a stranger's strategy in, read it
first — the same rule as any other code you run.

Within that scope the enforcement is real, and it happens in three layers:

  1. **AST inspection before execution.** Imports, attribute access to dunder
     internals, `exec`/`eval`, and comprehension-free infinite constructs are
     rejected at compile time with a line number, not at runtime with a
     traceback.
  2. **A stripped namespace.** No `__builtins__` beyond an explicit allowlist,
     and no module objects at all — `math` and friends are injected as
     already-bound functions.
  3. **A step budget.** The engine ticks a counter each bar; a strategy that
     will not terminate is stopped with a clear error rather than hanging.
"""

from __future__ import annotations

import ast
import builtins
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

from .sdk import SDK_NAMESPACE, Context

log = logging.getLogger("tradeo.strategies.sandbox")

MAX_SOURCE_BYTES = 64_000
MAX_AST_NODES = 8_000


class SandboxError(Exception):
    """A strategy was rejected, with a reason a human can act on."""

    def __init__(self, message: str, line: int | None = None):
        self.line = line
        super().__init__(f"line {line}: {message}" if line else message)


# ---------------------------------------------------------------------------
# What user code may reach
# ---------------------------------------------------------------------------

# Builtins that cannot touch the outside world. Notably absent: open, input,
# eval, exec, compile, __import__, globals, locals, vars, getattr, setattr,
# delattr, dir, help, breakpoint, memoryview.
SAFE_BUILTINS: dict[str, Any] = {
    name: getattr(builtins, name)
    for name in (
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "int", "isinstance", "issubclass",
        "len", "list", "map", "max", "min", "pow", "print", "range", "repr",
        "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple",
        "zip", "True", "False", "None",
    )
    if hasattr(builtins, name)
}
SAFE_BUILTINS.update({"True": True, "False": False, "None": None})

# `class Strategy:` compiles to a call to __build_class__, so a stripped
# namespace rejects the class form of a strategy with a NameError that points
# at nothing the author wrote. It cannot construct arbitrary types on its own
# — it needs a body and bases the AST audit has already inspected.
SAFE_BUILTINS["__build_class__"] = builtins.__build_class__

# Maths, pre-bound. A strategy gets `sqrt`, not `math`, so there is no module
# object to walk attributes off.
MATH_NAMESPACE: dict[str, Any] = {
    name: getattr(math, name)
    for name in ("sqrt", "log", "log10", "exp", "floor", "ceil", "fabs",
                 "pi", "e", "inf", "nan", "isnan", "isinf", "copysign")
}

# Names that exist only to reach back into the interpreter.
FORBIDDEN_ATTRIBUTES = frozenset({
    "__class__", "__bases__", "__subclasses__", "__mro__", "__globals__",
    "__code__", "__closure__", "__func__", "__self__", "__dict__",
    "__builtins__", "__import__", "__loader__", "__spec__", "__module__",
    "__reduce__", "__reduce_ex__", "__getattribute__", "__base__",
    "gi_frame", "cr_frame", "f_globals", "f_locals", "f_builtins",
})

FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "open", "input", "__import__", "globals",
    "locals", "vars", "getattr", "setattr", "delattr", "hasattr", "dir",
    "help", "exit", "quit", "breakpoint", "memoryview", "object", "type",
    "super", "classmethod", "staticmethod", "property", "id", "hash",
})

# Statements with no legitimate use inside a bar handler.
FORBIDDEN_NODES: dict[type, str] = {
    ast.Import: "imports are not available — every indicator you need is on `ctx`",
    ast.ImportFrom: "imports are not available — every indicator you need is on `ctx`",
    ast.Global: "`global` is not allowed; keep state on `self` or in a class attribute",
    ast.Nonlocal: "`nonlocal` is not allowed; keep state on `self`",
    ast.Delete: "`del` is not allowed",
    ast.With: "`with` blocks are not available (there are no resources to manage)",
    ast.AsyncWith: "async is not supported — strategies run synchronously, bar by bar",
    ast.AsyncFor: "async is not supported — strategies run synchronously, bar by bar",
    ast.AsyncFunctionDef: "async is not supported — strategies run synchronously, bar by bar",
    ast.Await: "async is not supported — strategies run synchronously, bar by bar",
    ast.Try: "`try` is not allowed — an error in a strategy should surface, not be swallowed",
    ast.Raise: "raise `StrategyError(...)` is available; bare raise is not",
    ast.Lambda: "lambdas are allowed only in simple expressions — use a def",
}
# Lambdas are actually fine and useful; drop the entry rather than special-case it.
FORBIDDEN_NODES.pop(ast.Lambda)


class _Auditor(ast.NodeVisitor):
    """Walks the parsed strategy and collects every reason to refuse it."""

    def __init__(self) -> None:
        self.errors: list[SandboxError] = []
        self.nodes = 0
        self.defines: set[str] = set()

    def visit(self, node: ast.AST) -> Any:
        self.nodes += 1
        if self.nodes > MAX_AST_NODES:
            raise SandboxError(
                f"strategy is too large ({MAX_AST_NODES}+ syntax nodes) — "
                "split the logic or simplify it"
            )

        forbidden = FORBIDDEN_NODES.get(type(node))
        if forbidden:
            self.errors.append(SandboxError(forbidden, getattr(node, "lineno", None)))

        return super().visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRIBUTES or (
            node.attr.startswith("__") and node.attr.endswith("__")
        ):
            self.errors.append(
                SandboxError(f"attribute `{node.attr}` is not accessible", node.lineno)
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in FORBIDDEN_NAMES:
            self.errors.append(
                SandboxError(f"`{node.id}` is not available in a strategy", node.lineno)
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defines.add(node.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.defines.add(node.name)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        # `while True` without a break is the one loop that reliably hangs the
        # process, and it is always a mistake in a per-bar handler.
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            has_break = any(isinstance(n, ast.Break) for n in ast.walk(node))
            if not has_break:
                self.errors.append(
                    SandboxError("`while True` with no `break` will never return", node.lineno)
                )
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# The compiled result
# ---------------------------------------------------------------------------


@dataclass
class ParamSpec:
    """
    One tunable knob, as declared by the strategy.

    The range matters as much as the default: it is what the optimiser sweeps
    and what the UI renders as a slider. A strategy that declares no ranges
    can still be backtested — it just cannot be optimised, which is a fair
    trade for not having to declare anything.
    """

    name: str
    default: Any
    low: float | None = None
    high: float | None = None
    step: float | None = None
    label: str = ""

    @property
    def is_numeric(self) -> bool:
        return isinstance(self.default, (int, float)) and not isinstance(self.default, bool)

    @property
    def tunable(self) -> bool:
        return self.is_numeric and self.low is not None and self.high is not None

    def values(self) -> list[Any]:
        """Every value the optimiser will try for this parameter."""
        if not self.tunable or self.low is None or self.high is None:
            return [self.default]

        low, high = float(self.low), float(self.high)
        step = float(self.step or (1 if isinstance(self.default, int) else (high - low) / 8))
        if step <= 0:
            return [self.default]

        out: list[Any] = []
        value = low
        # Bounded independently of `step`: a fat-fingered step of 0.0001 over a
        # wide range would otherwise build a list large enough to take the
        # process down before the optimiser ever sees it.
        while value <= high + 1e-9 and len(out) < 64:
            out.append(int(round(value)) if isinstance(self.default, int) else round(value, 6))
            value += step
        return out or [self.default]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "default": self.default,
            "low": self.low,
            "high": self.high,
            "step": self.step,
            "label": self.label or self.name.replace("_", " "),
            "tunable": self.tunable,
        }


@dataclass
class StrategyModule:
    """A validated, executable strategy."""

    name: str
    source: str
    # Return value is ignored — a strategy speaks by calling ctx.buy/ctx.sell,
    # so `Any` here just avoids forcing users to write `-> None`.
    on_bar: Callable[[Context], Any]
    params: list[ParamSpec] = field(default_factory=list)
    description: str = ""
    on_start: Callable[[Context], None] | None = None
    on_finish: Callable[[Context], None] | None = None
    warmup: int = 0
    namespace: dict[str, Any] = field(default_factory=dict)

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}

    def param_dict(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = self.defaults()
        for key, value in (overrides or {}).items():
            if key in merged and merged[key] is not None:
                # Keep the declared type: a slider handing back 20.0 for an
                # int period would make `range(20.0)` explode three files away.
                try:
                    merged[key] = type(merged[key])(value)
                    continue
                except (TypeError, ValueError):
                    pass
            merged[key] = value
        return merged

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "params": [p.as_dict() for p in self.params],
            "warmup": self.warmup,
        }


def _parse_params(raw: Any) -> list[ParamSpec]:
    """
    Read a PARAMS declaration.

    Two shapes are accepted, because the short one covers most strategies:

        PARAMS = {"period": 14}
        PARAMS = {"period": {"default": 14, "low": 5, "high": 40, "step": 1}}
    """
    if not isinstance(raw, dict):
        return []

    specs: list[ParamSpec] = []
    for name, value in raw.items():
        if isinstance(value, dict):
            specs.append(
                ParamSpec(
                    name=str(name),
                    default=value.get("default"),
                    low=value.get("low"),
                    high=value.get("high"),
                    step=value.get("step"),
                    label=str(value.get("label", "")),
                )
            )
        else:
            specs.append(ParamSpec(name=str(name), default=value))
    return specs


def audit(source: str) -> list[SandboxError]:
    """Every problem with this source, without executing any of it."""
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return [SandboxError(f"strategy source exceeds {MAX_SOURCE_BYTES // 1000} kB")]

    try:
        tree = ast.parse(source, filename="<strategy>", mode="exec")
    except SyntaxError as exc:
        return [SandboxError(exc.msg or "syntax error", exc.lineno)]

    auditor = _Auditor()
    try:
        auditor.visit(tree)
    except SandboxError as exc:
        return [exc]

    if not auditor.errors and "on_bar" not in auditor.defines:
        auditor.errors.append(
            SandboxError(
                "no `on_bar` found — a strategy must define `def on_bar(ctx):` "
                "(or a class with an `on_bar` method)"
            )
        )
    return auditor.errors


def compile_strategy(source: str, name: str = "custom") -> StrategyModule:
    """
    Validate, execute the top level once, and hand back a callable strategy.

    The top level runs — that is how `PARAMS` and any helper functions come to
    exist — but it runs in the stripped namespace, so "executes the module" is
    a much smaller claim here than it sounds.
    """
    errors = audit(source)
    if errors:
        raise errors[0]

    namespace: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "__name__": "strategy",
        **MATH_NAMESPACE,
        **SDK_NAMESPACE,
    }

    try:
        exec(compile(source, "<strategy>", "exec"), namespace)  # noqa: S102
    except SandboxError:
        raise
    except Exception as exc:
        raise SandboxError(f"strategy failed while loading: {type(exc).__name__}: {exc}") from exc

    on_bar = namespace.get("on_bar")
    instance = None

    # Class form: `class Strategy:` with an `on_bar` method. Instantiated once
    # per run so a strategy can hold state across bars in the obvious place.
    if not callable(on_bar):
        for candidate in namespace.values():
            if isinstance(candidate, type) and callable(getattr(candidate, "on_bar", None)):
                try:
                    instance = candidate()
                except Exception as exc:
                    raise SandboxError(
                        f"could not construct {candidate.__name__}(): {exc}"
                    ) from exc
                on_bar = instance.on_bar
                break

    if not callable(on_bar):
        raise SandboxError("no callable `on_bar` after loading")

    params = _parse_params(namespace.get("PARAMS"))
    if instance is not None and not params:
        params = _parse_params(getattr(instance, "params", None))

    return StrategyModule(
        name=str(namespace.get("NAME", name)),
        source=source,
        on_bar=on_bar,
        params=params,
        description=str(namespace.get("DESCRIPTION", "")).strip(),
        on_start=namespace.get("on_start") if callable(namespace.get("on_start")) else None,
        on_finish=namespace.get("on_finish") if callable(namespace.get("on_finish")) else None,
        warmup=int(namespace.get("WARMUP", 0) or 0),
        namespace=namespace,
    )
