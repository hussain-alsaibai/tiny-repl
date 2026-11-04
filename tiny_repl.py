"""
tiny-repl — Interactive debugging REPL for AI agent pipelines.

Replay steps, diff runs, inspect state, export traces — all in one file.
Built for the agent-first debugging workflow.

MIT License. https://github.com/hussain-alsaibai/tiny-repl
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable
from pathlib import Path


# ─────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    step: int
    name: str
    input: dict
    output: Any
    error: str | None = None
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    trace_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ToolCall":
        return cls(**d)


@dataclass
class AgentRun:
    run_id: str
    agent_name: str
    prompt: str
    started_at: float
    ended_at: float | None = None
    status: str = "running"  # running, completed, failed, interrupted
    tool_calls: list[ToolCall] = field(default_factory=list)
    final_output: Any = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def add_call(self, call: ToolCall):
        call.trace_id = self.run_id
        self.tool_calls.append(call)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tool_calls"] = [c.to_dict() for c in self.tool_calls]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AgentRun":
        d["tool_calls"] = [ToolCall.from_dict(c) for c in d["tool_calls"]]
        return cls(**d)


@dataclass
class ReplSession:
    run: AgentRun
    current_step: int = 0
    breakpoints: set[int] = field(default_factory=set)
    watch_vars: dict[str, Any] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)

    def step_forward(self) -> ToolCall | None:
        if self.current_step < len(self.run.tool_calls):
            call = self.run.tool_calls[self.current_step]
            self.current_step += 1
            return call
        return None

    def step_back(self) -> ToolCall | None:
        if self.current_step > 0:
            self.current_step -= 1
            return self.run.tool_calls[self.current_step]
        return None

    def goto(self, step: int) -> ToolCall | None:
        if 0 <= step < len(self.run.tool_calls):
            self.current_step = step
            return self.run.tool_calls[self.current_step]
        return None

    def current(self) -> ToolCall | None:
        """The call the session is sitting on, or None when there is none.

        current_step is the index of the *next* call to run, so the call the
        user is looking at is the one before it. Before any stepping has
        happened there is no current call, which is what the CLI prints as
        "No current step."

        The old implementation returned tool_calls[current_step], which made
        current() return the FIRST call before anything had been stepped. The
        REPL then showed a call the user had not reached yet, and the
        'No current step.' branch was unreachable.
        """
        if 0 < self.current_step <= len(self.run.tool_calls):
            return self.run.tool_calls[self.current_step - 1]
        return None


# ─────────────────────────────────────────────────────────────
# Trace Store (File-based)
# ─────────────────────────────────────────────────────────────

class TraceStore:
    """File-based trace storage with JSON lines format."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(os.getcwd(), ".tiny-repl-traces")
        Path(self.path).mkdir(parents=True, exist_ok=True)

    def _run_file(self, run_id: str) -> str:
        return os.path.join(self.path, f"{run_id}.json")

    def save(self, run: AgentRun) -> str:
        """Save a run to disk. Returns the run_id."""
        path = self._run_file(run.run_id)
        with open(path, "w") as f:
            json.dump(run.to_dict(), f, indent=2, default=str)
        self._append_index(run)
        return run.run_id

    def _append_index(self, run: AgentRun):
        idx_path = os.path.join(self.path, ".index.jsonl")
        with open(idx_path, "a") as f:
            f.write(json.dumps({
                "run_id": run.run_id,
                "agent_name": run.agent_name,
                "prompt": run.prompt[:100],
                "started_at": run.started_at,
                "ended_at": run.ended_at,
                "status": run.status,
                "step_count": len(run.tool_calls),
                "file": f"{run.run_id}.json",
            }) + "\n")

    def load(self, run_id: str) -> AgentRun | None:
        path = self._run_file(run_id)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return AgentRun.from_dict(json.load(f))

    def list_runs(self, limit: int = 50) -> list[dict]:
        idx_path = os.path.join(self.path, ".index.jsonl")
        if not os.path.exists(idx_path):
            return []
        runs = []
        with open(idx_path) as f:
            for line in f:
                runs.append(json.loads(line))
        runs.sort(key=lambda x: x["started_at"], reverse=True)
        return runs[:limit]

    def delete(self, run_id: str) -> bool:
        path = self._run_file(run_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False


# ─────────────────────────────────────────────────────────────
# Live Tracer (for instrumenting agents)
# ─────────────────────────────────────────────────────────────

class LiveTracer:
    """Instrument an agent with step tracking and automatic trace saving."""

    def __init__(self, agent_name: str = "agent", store: TraceStore | None = None):
        self.store = store or TraceStore()
        self._active_run: AgentRun | None = None
        self._callbacks: list[Callable[[ToolCall], None]] = []

    def on_call(self, cb: Callable[[ToolCall], None]):
        self._callbacks.append(cb)

    def start_run(self, agent_name: str, prompt: str) -> str:
        run_id = str(uuid.uuid4())[:8]
        self._active_run = AgentRun(
            run_id=run_id,
            agent_name=agent_name,
            prompt=prompt,
            started_at=time.time(),
        )
        return run_id

    def record_call(
        self,
        name: str,
        inp: dict,
        output: Any,
        error: str | None = None,
        duration_ms: float = 0.0,
    ) -> ToolCall:
        if self._active_run is None:
            raise RuntimeError("No active run — call start_run() first")

        call = ToolCall(
            step=len(self._active_run.tool_calls),
            name=name,
            input=inp,
            output=output,
            error=error,
            duration_ms=duration_ms,
            trace_id=self._active_run.run_id,
        )
        self._active_run.add_call(call)

        for cb in self._callbacks:
            cb(call)

        return call

    async def record_async(
        self,
        name: str,
        inp: dict,
        func: Callable,
        *args,
        **kwargs,
    ) -> Any:
        start = time.perf_counter()
        error = None
        output = None
        try:
            output = await func(*args, **kwargs)
            return output
        except Exception as exc:
            error = traceback.format_exc()
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            self.record_call(name, inp, output, error, duration_ms)

    def end_run(self, status: str = "completed", final_output: Any = None, error: str | None = None):
        if self._active_run is None:
            return None
        self._active_run.ended_at = time.time()
        self._active_run.status = status
        self._active_run.final_output = final_output
        self._active_run.error = error
        saved_id = self.store.save(self._active_run)
        run = self._active_run
        self._active_run = None
        return run

    def active_run(self) -> AgentRun | None:
        return self._active_run


# ─────────────────────────────────────────────────────────────
# Replay Engine
# ─────────────────────────────────────────────────────────────

class ReplayEngine:
    """
    Interactive REPL for replaying agent runs.

    Usage:
        repl = ReplayEngine(store)
        session = repl.replay(run_id="abc12345")
        repl.interact(session)
    """

    def __init__(self, store: TraceStore | None = None):
        self.store = store or TraceStore()
        self._command_handlers: dict[str, Callable] = {}

    def replay(self, run_id: str) -> ReplSession | None:
        """Load a run and create a replay session."""
        run = self.store.load(run_id)
        if run is None:
            print(f"Run '{run_id}' not found.")
            return None
        return ReplSession(run=run)

    def list(self, limit: int = 20) -> list[dict]:
        """List recent runs."""
        return self.store.list_runs(limit=limit)

    def diff(self, run_id_a: str, run_id_b: str) -> str:
        """Diff two runs step-by-step."""
        run_a = self.store.load(run_id_a)
        run_b = self.store.load(run_id_b)

        if not run_a or not run_b:
            missing = []
            if not run_a:
                missing.append(run_id_a)
            if not run_b:
                missing.append(run_id_b)
            print(f"Run(s) not found: {', '.join(missing)}")
            return ""

        lines = []
        max_steps = max(len(run_a.tool_calls), len(run_b.tool_calls))

        for i in range(max_steps):
            lines.append(f"\n{'='*60}")
            lines.append(f"STEP {i}")
            lines.append('=' * 60)

            if i < len(run_a.tool_calls):
                ca = run_a.tool_calls[i]
                lines.append(f"[RUN A] {ca.name}({json.dumps(ca.input, default=str)[:200]})")
                if ca.error:
                    lines.append(f"  ERROR: {ca.error[:200]}")
                else:
                    lines.append(f"  → {str(ca.output)[:200]}")

            if i < len(run_b.tool_calls):
                cb = run_b.tool_calls[i]
                lines.append(f"[RUN B] {cb.name}({json.dumps(cb.input, default=str)[:200]})")
                if cb.error:
                    lines.append(f"  ERROR: {cb.error[:200]}")
                else:
                    lines.append(f"  → {str(cb.output)[:200]}")

            # Highlight differences
            if i < len(run_a.tool_calls) and i < len(run_b.tool_calls):
                ca, cb = run_a.tool_calls[i], run_b.tool_calls[i]
                if ca.name != cb.name:
                    lines.append(f"  ⚠️  Tool differs: {ca.name} vs {cb.name}")
                elif ca.output != cb.output:
                    lines.append(f"  ⚠️  Output differs")

        return "\n".join(lines)

    def export(self, run_id: str, format: str = "json") -> str:
        """Export a run as JSON or markdown."""
        run = self.store.load(run_id)
        if not run:
            return ""

        if format == "json":
            return json.dumps(run.to_dict(), indent=2, default=str)

        lines = [
            f"# Agent Run: {run.agent_name}",
            f"**Run ID:** `{run.run_id}`",
            f"**Status:** {run.status}",
            f"**Steps:** {len(run.tool_calls)}",
            f"**Started:** {datetime.fromtimestamp(run.started_at).isoformat()}",
        ]

        if run.ended_at:
            duration = run.ended_at - run.started_at
            lines.append(f"**Duration:** {duration:.2f}s")
            lines.append(f"**Ended:** {datetime.fromtimestamp(run.ended_at).isoformat()}")

        lines.append(f"\n## Prompt\n\n```\n{run.prompt}\n```")

        for call in run.tool_calls:
            lines.append(f"\n## Step {call.step}: {call.name}")

            if call.error:
                lines.append(f"**ERROR:**\n```\n{call.error}\n```")
            else:
                lines.append(f"**Input:** ```python\n{json.dumps(call.input, indent=2, default=str)}\n```")
                output_str = str(call.output)
                if len(output_str) > 500:
                    output_str = output_str[:500] + "..."
                lines.append(f"**Output:**\n```\n{output_str}\n```")

            lines.append(f"*Duration: {call.duration_ms:.2f}ms*")

        if run.final_output:
            lines.append(f"\n## Final Output\n\n{str(run.final_output)[:500]}")

        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Interactive REPL
# ─────────────────────────────────────────────────────────────

class InteractiveREPL:
    """Text-based interactive REPL for replaying agent runs."""

    PROMPT = "tiny-repl> "
    HELP = """
    Commands:
      next, n       — Step forward one tool call
      prev, p       — Step backward one tool call
      goto <N>      — Jump to step N
      current, c    — Show current step
      list, l       — List all steps
      call <N>      — Show full detail of step N
      diff <A> <B>  — Diff step A vs step B in current run
      watch <expr>  — Watch a variable/expression
      replay <id>   — Load and replay a different run
      export [fmt]  — Export run as json/markdown
      break <N>     — Set breakpoint at step N
      run           — Run to next breakpoint or end
      find <term>   — Find steps matching term
      stats         — Show run statistics
      quit, q       — Exit REPL

    Examples:
      tiny-repl> n           # step forward
      tiny-repl> n           # step forward again
      tiny-repl> call 5      # inspect step 5 in detail
      tiny-repl> find "error"# find steps with "error"
      tiny-repl> export md   # export as markdown
    """

    def __init__(self, engine: ReplayEngine):
        self.engine = engine
        self.session: ReplSession | None = None
        self.running = False

    def interact(self, session: ReplSession):
        """Start interactive REPL with a session."""
        self.session = session
        self.running = True
        self._print_header()

        while self.running:
            try:
                cmd = input(self.PROMPT).strip()
                if not cmd:
                    continue
                self._handle(cmd)
            except (EOFError, KeyboardInterrupt):
                print("\nExiting REPL.")
                break

    def _print_header(self):
        run = self.session.run
        print(f"\n{'='*60}")
        print(f"tiny-repl — Run {run.run_id} ({run.agent_name})")
        print(f"Status: {run.status} | Steps: {len(run.tool_calls)}")
        if run.ended_at:
            print(f"Duration: {run.ended_at - run.started_at:.2f}s")
        print(f"Current step: {self.session.current_step}")
        print('=' * 60)

    def _handle(self, cmd: str):
        parts = cmd.split()
        base = parts[0].lower()

        if base in ("next", "n"):
            self._cmd_next()
        elif base in ("prev", "p"):
            self._cmd_prev()
        elif base == "goto":
            self._cmd_goto(parts)
        elif base in ("current", "c"):
            self._cmd_current()
        elif base in ("list", "l"):
            self._cmd_list()
        elif base == "call":
            self._cmd_call(parts)
        elif base == "diff":
            self._cmd_diff(parts)
        elif base == "watch":
            self._cmd_watch(parts)
        elif base == "replay":
            self._cmd_replay(parts)
        elif base == "export":
            self._cmd_export(parts)
        elif base == "break":
            self._cmd_break(parts)
        elif base in ("run", "continue", "cont"):
            self._cmd_run()
        elif base == "find":
            self._cmd_find(parts)
        elif base == "stats":
            self._cmd_stats()
        elif base in ("help", "?"):
            print(self.HELP)
        elif base in ("quit", "q", "exit"):
            self.running = False
        else:
            print(f"Unknown command: {base}. Type 'help' for commands.")

    def _ensure_session(self) -> bool:
        if self.session is None:
            print("No active session. Use replay(run_id) first.")
            return False
        return True

    def _print_call(self, call: ToolCall, detail: bool = False):
        status = "✓" if not call.error else "✗"
        print(f"  [{call.step:3d}] {status} {call.name} ({call.duration_ms:.1f}ms)")
        if call.error:
            print(f"       ERROR: {call.error[:150]}")
        else:
            output_str = str(call.output)
            if len(output_str) > 200:
                output_str = output_str[:200] + "..."
            print(f"       → {output_str}")

        if detail:
            print(f"       Input: {json.dumps(call.input, default=str)[:500]}")
            if call.error:
                print(f"       Full error:")
                for line in call.error.split("\n"):
                    print(f"         {line}")

    def _cmd_next(self):
        if not self._ensure_session():
            return
        call = self.session.step_forward()
        if call:
            print(f"\n→ Step {call.step}:")
            self._print_call(call, detail=True)
        else:
            print("End of run.")

    def _cmd_prev(self):
        if not self._ensure_session():
            return
        call = self.session.step_back()
        if call:
            print(f"\n← Step {call.step}:")
            self._print_call(call, detail=True)
        else:
            print("Beginning of run.")

    def _cmd_goto(self, parts: list[str]):
        if not self._ensure_session():
            return
        if len(parts) < 2:
            print("Usage: goto <step>")
            return
        try:
            step = int(parts[1])
            call = self.session.goto(step)
            if call:
                print(f"\nJumped to step {step}:")
                self._print_call(call, detail=True)
            else:
                print(f"Step {step} out of range.")
        except ValueError:
            print("Step must be a number.")

    def _cmd_current(self):
        if not self._ensure_session():
            return
        call = self.session.current()
        if call:
            self._print_call(call, detail=True)
        else:
            print("No current step.")

    def _cmd_list(self):
        if not self._ensure_session():
            return
        for call in self.session.run.tool_calls:
            marker = "→" if call.step == self.session.current_step else " "
            bp = "●" if call.step in self.session.breakpoints else " "
            print(f"  {marker}{bp} [{call.step:3d}] {call.name} ({call.duration_ms:.1f}ms)")

    def _cmd_call(self, parts: list[str]):
        if not self._ensure_session():
            return
        if len(parts) < 2:
            print("Usage: call <step>")
            return
        try:
            step = int(parts[1])
            if 0 <= step < len(self.session.run.tool_calls):
                self._print_call(self.session.run.tool_calls[step], detail=True)
            else:
                print(f"Step {step} out of range.")
        except ValueError:
            print("Step must be a number.")

    def _cmd_diff(self, parts: list[str]):
        if not self._ensure_session():
            return
        if len(parts) < 3:
            print("Usage: diff <step_a> <step_b>")
            return
        try:
            a, b = int(parts[1]), int(parts[2])
            run = self.session.run
            if a >= len(run.tool_calls) or b >= len(run.tool_calls):
                print("Step out of range.")
                return

            ca, cb = run.tool_calls[a], run.tool_calls[b]

            print(f"\nDiff step {a} vs step {b}:")
            print(f"  Tool:    {ca.name} vs {cb.name}")
            print(f"  Duration: {ca.duration_ms:.1f}ms vs {cb.duration_ms:.1f}ms")

            if ca.output != cb.output:
                diff = list(difflib.unified_diff(
                    str(ca.output).splitlines()[:30],
                    str(cb.output).splitlines()[:30],
                    fromfile=f"step_{a}",
                    tofile=f"step_{b}",
                    lineterm="",
                ))
                if diff:
                    print("  Output diff:")
                    for line in diff[:20]:
                        print(f"    {line}")
                else:
                    print("  ⚠️  Output differs (not text-differentiable)")
            else:
                print("  Output: identical")

        except ValueError:
            print("Steps must be numbers.")

    def _cmd_watch(self, parts: list[str]):
        if not self._ensure_session():
            return
        if len(parts) < 2:
            print("Watch expressions:", list(self.session.watch_vars.keys()))
            return
        expr = " ".join(parts[1:])
        call = self.session.current()
        if call:
            self.session.watch_vars[expr] = call.output
            print(f"Watching '{expr}' = {str(call.output)[:100]}")

    def _cmd_replay(self, parts: list[str]):
        if len(parts) < 2:
            print("Available runs:")
            for run in self.engine.list(10):
                print(f"  {run['run_id']} — {run['agent_name']} "
                      f"({run['step_count']} steps, {run['status']})")
            return
        run_id = parts[1]
        new_session = self.engine.replay(run_id)
        if new_session:
            self.session = new_session
            self._print_header()

    def _cmd_export(self, parts: list[str]):
        if not self._ensure_session():
            return
        fmt = parts[1] if len(parts) > 1 else "json"
        output = self.engine.export(self.session.run.run_id, format=fmt)
        print(output)

    def _cmd_break(self, parts: list[str]):
        if not self._ensure_session():
            return
        if len(parts) < 2:
            print("Breakpoints:", sorted(self.session.breakpoints))
            return
        try:
            step = int(parts[1])
            self.session.breakpoints.add(step)
            print(f"Breakpoint set at step {step}.")
        except ValueError:
            print("Step must be a number.")

    def _cmd_run(self):
        if not self._ensure_session():
            return
        while self.session.current_step < len(self.session.run.tool_calls):
            if self.session.current_step in self.session.breakpoints:
                print(f"Breakpoint at step {self.session.current_step}.")
                break
            call = self.session.step_forward()
            self._print_call(call)
        else:
            print("End of run.")

    def _cmd_find(self, parts: list[str]):
        if not self._ensure_session():
            return
        if len(parts) < 2:
            print("Usage: find <term>")
            return
        term = " ".join(parts[1:]).lower()
        for call in self.session.run.tool_calls:
            if (term in call.name.lower() or
                term in str(call.input).lower() or
                term in str(call.output).lower() or
                (call.error and term in call.error.lower())):
                self._print_call(call)

    def _cmd_stats(self):
        if not self._ensure_session():
            return
        run = self.session.run
        errors = sum(1 for c in run.tool_calls if c.error)
        total_ms = sum(c.duration_ms for c in run.tool_calls)
        tool_counts: dict[str, int] = {}
        for c in run.tool_calls:
            tool_counts[c.name] = tool_counts.get(c.name, 0) + 1

        print(f"\nRun Statistics:")
        print(f"  Run ID:     {run.run_id}")
        print(f"  Agent:      {run.agent_name}")
        print(f"  Status:     {run.status}")
        print(f"  Total steps: {len(run.tool_calls)}")
        print(f"  Errors:      {errors}")
        print(f"  Total time:  {total_ms:.1f}ms")
        print(f"  Avg step:    {total_ms/len(run.tool_calls):.1f}ms" if run.tool_calls else "")
        print(f"  Tool usage:")
        for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
            print(f"    {tool}: {count}")


# ─────────────────────────────────────────────────────────────
# Convenience API
# ─────────────────────────────────────────────────────────────

_store: TraceStore | None = None
_engine: ReplayEngine | None = None
_tracer: LiveTracer | None = None


def get_store(path: str | None = None) -> TraceStore:
    global _store
    if _store is None:
        _store = TraceStore(path)
    return _store


def get_engine() -> ReplayEngine:
    global _engine
    if _engine is None:
        _engine = ReplayEngine(get_store())
    return _engine


def get_tracer(agent_name: str = "agent") -> LiveTracer:
    global _tracer
    if _tracer is None:
        _tracer = LiveTracer(agent_name, get_store())
    return _tracer


def start_trace(agent_name: str, prompt: str) -> str:
    """Start tracing an agent run. Returns run_id."""
    tracer = get_tracer(agent_name)
    return tracer.start_run(agent_name, prompt)


def record(name: str, inp: dict, output: Any = None, error: str | None = None, duration_ms: float = 0.0):
    """Record a tool call."""
    tracer = get_tracer()
    if tracer._active_run is None:
        return
    tracer.record_call(name, inp, output, error, duration_ms)


async def trace_async(name: str, inp: dict, func: Callable, *args, **kwargs) -> Any:
    """Decorator-like async trace: wraps a call and records duration."""
    tracer = get_tracer()
    return await tracer.record_async(name, inp, func, *args, **kwargs)


def end_trace(status: str = "completed", final_output: Any = None, error: str | None = None) -> AgentRun | None:
    """End the current trace and save to disk."""
    tracer = get_tracer()
    return tracer.end_run(status, final_output, error)


def replay(run_id: str) -> ReplSession | None:
    """Load a run and return a replay session."""
    return get_engine().replay(run_id)


def interact(session: ReplSession):
    """Start interactive REPL."""
    repl = InteractiveREPL(get_engine())
    repl.interact(session)


def list_runs(limit: int = 20) -> list[dict]:
    """List recent runs."""
    return get_engine().list(limit=limit)


def diff_runs(run_id_a: str, run_id_b: str) -> str:
    """Diff two runs."""
    return get_engine().diff(run_id_a, run_id_b)


def export_run(run_id: str, format: str = "json") -> str:
    """Export a run."""
    return get_engine().export(run_id, format)


def wrap(func: Callable) -> Callable:
    """
    Decorator: wrap any function to auto-trace it.
    Usage:
        @wrap
        async def my_tool(input):
            return await actual_tool(input)
    """
    import functools

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        tracer = get_tracer()
        start = time.perf_counter()
        error = None
        output = None
        try:
            output = await func(*args, **kwargs)
            return output
        except Exception as exc:
            error = traceback.format_exc()
            raise
        finally:
            ms = (time.perf_counter() - start) * 1000
            inp = {"args": str(args)[:200], "kwargs": str(kwargs)[:200]}
            if tracer._active_run:
                tracer.record_call(func.__name__, inp, output, error, ms)

    return wrapper
