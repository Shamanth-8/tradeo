"""
The agent runtime — thinking you can watch, and stop.

An agent here is an ordered graph of typed steps, not a conversation. That
choice is the whole architecture, so it is worth defending.

**Why not CrewAI / LangChain.**

Three reasons, in order of how much they matter here:

1. *Cost.* A CrewAI crew delegating between four agents spends 15-30 model
   calls on one analysis once you count task decomposition and inter-agent
   messages. On a 3B model running on this CPU that is 15-25 seconds each —
   five to ten minutes per decision. A terminal that takes ten minutes to
   answer is not a terminal.

2. *Explainability.* Conversational frameworks emit prose. You cannot draw a
   chart of prose. Every claim this system makes has to be traceable to a
   number, which means steps must emit **typed evidence**, not paragraphs. The
   charts in the UI are generated from that evidence — they are the audit
   trail, not decoration on top of one.

3. *Determinism.* Most of what looks like reasoning in finance is arithmetic:
   HHI, ATR, beta, drawdown, a discount rate. Routing arithmetic through a
   language model makes it slow, occasionally wrong, and never reproducible.
   Routing *judgment* through rules makes it brittle. So the split here is
   explicit: deterministic tools compute, the model judges at the joints, and
   the step graph makes both visible.

The result costs about two model calls where a crew costs twenty-five, and
every intermediate value is inspectable.

**Pausing.** Any step can require approval. When one does, the run halts with
its full state intact and waits. Approval is a separate call, which means the
pause survives a page reload, a restart, or a week — the state is data, not a
suspended coroutine.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Iterator

log = logging.getLogger("tradeo.agents")

MAX_RUNS_RETAINED = 60


class StepKind(str, Enum):
    """
    What a step does — which tells you what it costs.

    COMPUTE never calls a model and is therefore instant and reproducible.
    REASON and DECIDE call one. VERIFY may call the cloud verifier. ACT changes
    something in the world and always requires approval.
    """

    COMPUTE = "compute"
    REASON = "reason"
    VERIFY = "verify"
    DECIDE = "decide"
    ACT = "act"


class RunState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class ThoughtLevel(str, Enum):
    PLAN = "plan"           # what I am about to do
    OBSERVE = "observe"     # what the data says
    INFER = "infer"         # what I conclude from it
    CONFLICT = "conflict"   # where the evidence disagrees with itself
    DECIDE = "decide"       # the call
    WARN = "warn"


@dataclass
class Thought:
    """
    One line of visible reasoning.

    Thoughts are emitted as they happen and streamed to the UI, so the user
    watches the agent think rather than waiting for a verdict to appear.
    """

    level: ThoughtLevel
    text: str
    step_id: str = ""
    at: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "text": self.text,
            "step_id": self.step_id,
            "at": self.at,
            "data": self.data,
        }


@dataclass
class Chart:
    """
    A chart specification, produced by the step that computed the numbers.

    The step owns the chart because only the step knows what the numbers mean.
    Building charts later, in the UI, from a blob of results is how you end up
    with a plot that quietly misrepresents what happened.

    `kind` maps to a renderer in the frontend:
      waterfall  — how a score was built up, contribution by contribution
      bridge     — price to fair value, one bar per valuation method
      radar      — multi-factor scoring profile
      series     — a price line, optionally with bands and markers
      bar        — plain comparison
      equity     — a backtest equity curve with drawdown
      gauge      — a single number against thresholds
      table      — evidence with no natural geometry
    """

    kind: str
    title: str
    data: list[dict[str, Any]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    caption: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "data": self.data,
            "config": self.config,
            "caption": self.caption,
        }


@dataclass
class Evidence:
    """
    What a step found — structured, so it can be plotted and re-checked.

    `confidence` is the step's own view of how much weight this deserves, and
    it is separate from the values: a fair value computed from one year of
    lumpy earnings is a real number with low confidence, and flattening those
    into one figure loses the thing that matters.
    """

    label: str
    values: dict[str, Any] = field(default_factory=dict)
    charts: list[Chart] = field(default_factory=list)
    confidence: float = 50.0
    source: str = ""
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "values": self.values,
            "charts": [c.as_dict() for c in self.charts],
            "confidence": round(self.confidence, 1),
            "source": self.source,
            "caveats": self.caveats,
        }


@dataclass
class StepResult:
    """What a step hands back to the runtime."""

    evidence: list[Evidence] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    thoughts: list[Thought] = field(default_factory=list)
    skip_reason: str | None = None


# A step function receives the live context and an emitter for streaming
# thoughts, and returns a StepResult.
StepFn = Callable[["RunContext"], StepResult]


@dataclass
class Step:
    """One unit of work."""

    id: str
    title: str
    kind: StepKind
    run: StepFn
    description: str = ""
    requires_approval: bool = False
    # A step can be skipped when its precondition is unmet; this keeps the
    # graph honest rather than having steps silently no-op.
    precondition: Callable[["RunContext"], bool] | None = None
    # Rough cost hint for the UI, so a user knows why something is slow.
    est_seconds: float = 0.1

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind.value,
            "description": self.description,
            "requires_approval": self.requires_approval,
            "est_seconds": self.est_seconds,
        }


@dataclass
class StepRecord:
    """The executed history of one step."""

    step: Step
    status: str = "pending"  # pending | running | done | skipped | failed | awaiting_approval
    started_at: float = 0.0
    finished_at: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    thoughts: list[Thought] = field(default_factory=list)
    error: str | None = None
    skip_reason: str | None = None

    @property
    def duration_ms(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.time()
        return round((end - self.started_at) * 1000, 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.step.as_dict(),
            "status": self.status,
            "duration_ms": self.duration_ms,
            "evidence": [e.as_dict() for e in self.evidence],
            "output": self.output,
            "thoughts": [t.as_dict() for t in self.thoughts],
            "error": self.error,
            "skip_reason": self.skip_reason,
        }


class RunContext:
    """
    Shared state for one run.

    Steps read what earlier steps produced through `get`, which makes the data
    dependencies between them explicit and inspectable rather than hidden in
    closures.
    """

    def __init__(self, run: "AgentRun") -> None:
        self._run = run
        self.symbol: str = run.symbol
        self.params: dict[str, Any] = dict(run.params)
        self.data: dict[str, Any] = {}

    def emit(self, level: ThoughtLevel, text: str, **data: Any) -> None:
        """Stream a thought immediately — this is what the user watches."""
        self._run.emit(Thought(level=level, text=text,
                               step_id=self._run.current_step_id, data=data))

    def plan(self, text: str, **data: Any) -> None:
        self.emit(ThoughtLevel.PLAN, text, **data)

    def observe(self, text: str, **data: Any) -> None:
        self.emit(ThoughtLevel.OBSERVE, text, **data)

    def infer(self, text: str, **data: Any) -> None:
        self.emit(ThoughtLevel.INFER, text, **data)

    def conflict(self, text: str, **data: Any) -> None:
        self.emit(ThoughtLevel.CONFLICT, text, **data)

    def decide(self, text: str, **data: Any) -> None:
        self.emit(ThoughtLevel.DECIDE, text, **data)

    def warn(self, text: str, **data: Any) -> None:
        self.emit(ThoughtLevel.WARN, text, **data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    @property
    def approval(self) -> dict[str, Any]:
        """Whatever the user sent along with their approval."""
        return self._run.approval_payload


class AgentRun:
    """
    One execution of one agent.

    Holds the whole trace, so a completed run can be replayed in the UI without
    re-running anything, and a paused run can be resumed from data alone.
    """

    def __init__(self, agent: "Agent", symbol: str, params: dict[str, Any] | None = None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.agent = agent
        self.agent_name = agent.name
        self.symbol = (symbol or "").upper()
        self.params = params or {}
        self.state = RunState.PENDING
        self.records: list[StepRecord] = [StepRecord(step=s) for s in agent.steps]
        self.created_at = time.time()
        self.finished_at: float = 0.0
        self.error: str | None = None
        self.summary: str = ""
        self.verdict: dict[str, Any] = {}

        self.current_step_id: str = ""
        self.approval_payload: dict[str, Any] = {}
        self.pending_step_id: str | None = None
        self.rejection_reason: str | None = None

        self.context = RunContext(self)
        self._thoughts: list[Thought] = []
        # Bounded, because a UI that reconnects should get recent thinking, not
        # an unbounded backlog.
        self._stream: deque[Thought] = deque(maxlen=500)
        self._lock = threading.Lock()
        self._resume = threading.Event()

    # ---- thought streaming -------------------------------------------------

    def emit(self, thought: Thought) -> None:
        with self._lock:
            self._thoughts.append(thought)
            self._stream.append(thought)
        log.debug("[%s/%s] %s: %s", self.agent_name, self.symbol,
                  thought.level.value, thought.text)

    def thoughts_since(self, index: int) -> tuple[list[Thought], int]:
        with self._lock:
            return list(self._thoughts[index:]), len(self._thoughts)

    # ---- approval ----------------------------------------------------------

    def approve(self, payload: dict[str, Any] | None = None) -> bool:
        if self.state is not RunState.AWAITING_APPROVAL:
            return False
        self.approval_payload = payload or {}
        self.emit(Thought(ThoughtLevel.DECIDE, "Approved by operator — continuing.",
                          self.pending_step_id or ""))
        self._resume.set()
        return True

    def reject(self, reason: str = "") -> bool:
        if self.state is not RunState.AWAITING_APPROVAL:
            return False
        self.rejection_reason = reason or "rejected by operator"
        self.emit(Thought(ThoughtLevel.DECIDE,
                          f"Rejected by operator: {self.rejection_reason}. Stopping.",
                          self.pending_step_id or ""))
        self.state = RunState.REJECTED
        self._resume.set()
        return True

    # ---- serialisation -----------------------------------------------------

    def as_dict(self, include_trace: bool = True) -> dict[str, Any]:
        # Read the verdict live from the context rather than the snapshot taken
        # at completion. A run paused for approval has already produced its
        # reading, and the approval screen needs to show the numbers it is
        # asking about — serving an empty verdict there left the UI rendering
        # `undefined`.
        verdict = self.context.get("verdict") or self.verdict or {}

        payload: dict[str, Any] = {
            "id": self.id,
            "agent": self.agent_name,
            "agent_title": self.agent.title,
            "symbol": self.symbol,
            "state": self.state.value,
            "created_at": self.created_at,
            "duration_ms": round(((self.finished_at or time.time()) - self.created_at) * 1000, 1),
            "summary": self.summary or self.context.get("summary", ""),
            "verdict": verdict,
            "error": self.error,
            "pending_step": self.pending_step_id,
            "rejection_reason": self.rejection_reason,
            "progress": {
                "done": sum(1 for r in self.records if r.status in {"done", "skipped"}),
                "total": len(self.records),
            },
        }
        if include_trace:
            payload["steps"] = [r.as_dict() for r in self.records]
            payload["thoughts"] = [t.as_dict() for t in self._thoughts]
            payload["charts"] = [
                c.as_dict()
                for record in self.records
                for evidence in record.evidence
                for c in evidence.charts
            ]
        return payload


@dataclass
class Agent:
    """A named sequence of steps."""

    name: str
    title: str
    description: str
    steps: list[Step]
    # Panels this agent backs, so the UI knows which dashboard tile it drives.
    panel: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "panel": self.panel,
            "steps": [s.as_dict() for s in self.steps],
            "requires_approval": any(s.requires_approval for s in self.steps),
            "est_seconds": round(sum(s.est_seconds for s in self.steps), 1),
        }


class Executor:
    """
    Runs agents, keeps their traces, and mediates approvals.

    Runs execute on their own threads so the HTTP request that started one
    returns immediately with a run ID; the UI then streams thoughts. A run that
    takes forty seconds must not hold a connection open for forty seconds.
    """

    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}
        self._order: deque[str] = deque(maxlen=MAX_RUNS_RETAINED)
        self._lock = threading.Lock()
        self.registry: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        self.registry[agent.name] = agent

    def get_agent(self, name: str) -> Agent | None:
        return self.registry.get(name)

    def get_run(self, run_id: str) -> AgentRun | None:
        return self._runs.get(run_id)

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._order)[-limit:][::-1]
        return [self._runs[i].as_dict(include_trace=False) for i in ids if i in self._runs]

    def start(self, agent_name: str, symbol: str = "",
              params: dict[str, Any] | None = None) -> AgentRun:
        agent = self.registry.get(agent_name)
        if agent is None:
            raise KeyError(f"unknown agent '{agent_name}'")

        run = AgentRun(agent, symbol, params)
        with self._lock:
            self._runs[run.id] = run
            self._order.append(run.id)
            # Drop traces that fell out of the ring, or memory grows forever.
            live = set(self._order)
            for stale in [k for k in self._runs if k not in live]:
                self._runs.pop(stale, None)

        thread = threading.Thread(target=self._execute, args=(run,),
                                  name=f"agent-{agent_name}-{run.id}", daemon=True)
        thread.start()
        return run

    def _execute(self, run: AgentRun) -> None:
        run.state = RunState.RUNNING
        context = run.context

        context.emit(
            ThoughtLevel.PLAN,
            f"{run.agent.title}: {len(run.records)} steps"
            + (f" on {run.symbol}" if run.symbol else "")
            + ".",
            steps=[r.step.title for r in run.records],
        )

        try:
            for record in run.records:
                if run.state is RunState.REJECTED:
                    break

                step = record.step
                run.current_step_id = step.id

                # Preconditions are evaluated before the approval gate: there
                # is no point asking a human to approve a step that is about to
                # be skipped.
                if step.precondition is not None:
                    try:
                        if not step.precondition(context):
                            record.status = "skipped"
                            record.skip_reason = "precondition not met"
                            context.emit(ThoughtLevel.OBSERVE,
                                         f"Skipping {step.title} — precondition not met.")
                            continue
                    except Exception as exc:
                        record.status = "skipped"
                        record.skip_reason = f"precondition error: {exc}"
                        continue

                if step.requires_approval:
                    if not self._await_approval(run, record):
                        break

                record.status = "running"
                record.started_at = time.time()
                context.emit(ThoughtLevel.PLAN, step.description or step.title)

                try:
                    result = step.run(context)
                except Exception as exc:
                    record.status = "failed"
                    record.error = str(exc)[:400]
                    record.finished_at = time.time()
                    context.emit(ThoughtLevel.WARN,
                                 f"{step.title} failed: {str(exc)[:200]}")
                    log.warning("step %s failed: %s", step.id, exc)
                    log.debug(traceback.format_exc())
                    # A failed step is not necessarily fatal — a missing
                    # sentiment read should not abort a valuation. Steps that
                    # truly cannot be skipped assert on their inputs instead.
                    continue

                record.finished_at = time.time()
                if result.skip_reason:
                    record.status = "skipped"
                    record.skip_reason = result.skip_reason
                    context.emit(ThoughtLevel.OBSERVE,
                                 f"{step.title}: {result.skip_reason}")
                    continue

                record.status = "done"
                record.evidence = result.evidence
                record.output = result.output
                record.thoughts = result.thoughts
                if result.output:
                    context.data.update(result.output)

            if run.state is not RunState.REJECTED:
                run.state = RunState.COMPLETED
                run.verdict = context.get("verdict", {}) or {}
                run.summary = context.get("summary", "") or ""

        except Exception as exc:
            run.state = RunState.FAILED
            run.error = str(exc)[:400]
            log.error("agent run %s failed: %s", run.id, exc, exc_info=True)
        finally:
            run.finished_at = time.time()
            run.current_step_id = ""
            context.emit(
                ThoughtLevel.DECIDE,
                f"Run {run.state.value} in {run.as_dict(include_trace=False)['duration_ms']:.0f} ms.",
            )

    @staticmethod
    def _await_approval(run: AgentRun, record: StepRecord, timeout: float = 3600.0) -> bool:
        """
        Halt and wait.

        Returns True to proceed. The whole run state is already serialisable at
        this point, so the UI can render exactly what it is being asked to
        approve — including every number that led here.
        """
        run.state = RunState.AWAITING_APPROVAL
        run.pending_step_id = record.step.id
        record.status = "awaiting_approval"
        run._resume.clear()

        run.emit(Thought(
            level=ThoughtLevel.DECIDE,
            text=f"Waiting for your approval before: {record.step.title}.",
            step_id=record.step.id,
            data={"requires_approval": True, "step": record.step.title},
        ))

        granted = run._resume.wait(timeout)
        if not granted:
            run.state = RunState.REJECTED
            run.rejection_reason = "approval timed out"
            record.status = "skipped"
            record.skip_reason = "approval timed out"
            run.emit(Thought(ThoughtLevel.WARN,
                             "Approval timed out after an hour — stopping.", record.step.id))
            return False

        if run.state is RunState.REJECTED:
            record.status = "skipped"
            record.skip_reason = run.rejection_reason
            return False

        run.state = RunState.RUNNING
        run.pending_step_id = None
        return True

    # ---- streaming ---------------------------------------------------------

    def stream(self, run_id: str, poll: float = 0.15) -> Iterator[dict[str, Any]]:
        """
        Thoughts as they are produced, then a final frame.

        Yields dicts ready to be serialised as SSE. The terminal frame carries
        the full trace so the client does not need a second request.
        """
        run = self._runs.get(run_id)
        if run is None:
            yield {"type": "error", "error": "unknown run"}
            return

        cursor = 0
        terminal = {RunState.COMPLETED, RunState.FAILED, RunState.REJECTED}

        while True:
            thoughts, cursor = run.thoughts_since(cursor)
            for thought in thoughts:
                yield {"type": "thought", **thought.as_dict()}

            if run.state is RunState.AWAITING_APPROVAL:
                yield {
                    "type": "approval_required",
                    "run_id": run.id,
                    "step": run.pending_step_id,
                    "run": run.as_dict(),
                }
                # Keep polling: the approval may arrive from another client.
                while run.state is RunState.AWAITING_APPROVAL:
                    time.sleep(poll)
                continue

            if run.state in terminal:
                thoughts, cursor = run.thoughts_since(cursor)
                for thought in thoughts:
                    yield {"type": "thought", **thought.as_dict()}
                yield {"type": "done", "run": run.as_dict()}
                return

            time.sleep(poll)


executor = Executor()
