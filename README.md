# tiny-repl

> **Interactive debugging REPL for AI agent pipelines.**
> Replay agent runs step-by-step, diff two executions, inspect state, export traces as JSON or markdown. Zero dependencies.

[![MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-green.svg)](https://python.org)

---

## Why tiny-repl?

When an autonomous agent runs for 20 minutes and fails, you don't want to grep through 500 tool calls in a log file. You want to **step through the execution like a debugger**, jump to the failure, diff it against a successful run, and export a clean trace for the report.

`tiny-repl` is the tool for that. It was built for the autonomous agent workflow — the kind where a script owns an issue end-to-end and you need to understand what it did after the fact.

---

## Quick Start

### Install

```bash
pip install tiny-repl
```

Or just copy the file — it's one Python file, no dependencies:

```bash
curl -O https://raw.githubusercontent.com/hussain-alsaibai/tiny-repl/main/tiny_repl.py
```

### Trace an agent (programmatic)

```python
from tiny_repl import start_trace, record, end_trace

# Start tracing
run_id = start_trace("my-agent", "Fix the auth bug in login.py")

# ... your agent runs tools ...
record("read_file", {"path": "auth.py"}, output={"lines": 42})
record("write_file", {"path": "auth.py"}, output="done")

# End trace
run = end_trace(status="completed", final_output="Bug fixed")
print(f"Run saved: {run.run_id}")
```

### Replay in interactive REPL

```bash
$ tiny-repl
tiny-repl> list runs
  abc12345 — my-agent (14 steps, completed)
  def67890 — my-agent (18 steps, failed)
tiny-repl> replay abc12345
tiny-repl> n           # step forward
tiny-repl> n           # step forward again
tiny-repl> call 5      # inspect step 5 detail
tiny-repl> find error  # find steps with "error"
tiny-repl> stats       # show run statistics
```

### Diff two runs

```python
from tiny_repl import diff_runs

diff = diff_runs("abc12345", "def67890")
print(diff)
```

### Export as markdown

```python
from tiny_repl import export_run

md = export_run("abc12345", format="markdown")
print(md)
```

---

## Concepts

### `AgentRun`
A complete record of one agent execution: the prompt, all tool calls, final output, error, and metadata.

### `ToolCall`
A single step in an agent run: which tool was called, its input, output, duration, and any error.

### `TraceStore`
File-based persistence. Traces are saved as `~/.tiny-repl-traces/{run_id}.json`. An index file tracks all runs.

### `LiveTracer`
Instruments a running agent. Wraps async functions, auto-records duration and errors.

### `ReplayEngine`
Loads runs from the store, diffs two runs step-by-step, exports traces.

### `InteractiveREPL`
Terminal UI for stepping through a run. Commands: `n`/`p` (step forward/back), `goto N`, `call N`, `find`, `stats`, `diff`, `export`.

---

## API Reference

### Tracing

```python
start_trace(agent_name: str, prompt: str) -> str
    # Start a new trace. Returns run_id.

record(name: str, inp: dict, output: Any = None,
       error: str = None, duration_ms: float = 0.0)
    # Record a tool call in the active trace.

async trace_async(name: str, inp: dict, func: Callable, *args, **kwargs) -> Any
    # Wrap an async function call: records entry, duration, and error automatically.

end_trace(status: str = "completed",
          final_output: Any = None, error: str = None) -> AgentRun | None
    # Finalize and save the trace.

@wrap
async def my_tool(input):
    # Decorator that auto-traces any async function.
    return await actual_tool(input)
```

### Replay

```python
replay(run_id: str) -> ReplSession | None
    # Load a run and return a steppable session.

interact(session: ReplSession)
    # Start interactive REPL with a session.

list_runs(limit: int = 20) -> list[dict]
    # List recent runs from the index.

diff_runs(run_id_a: str, run_id_b: str) -> str
    # Step-by-step diff of two runs.

export_run(run_id: str, format: str = "json") -> str
    # Export as JSON or markdown.
```

### Convenience

```python
get_tracer(agent_name: str = "agent") -> LiveTracer
get_engine() -> ReplayEngine
get_store(path: str | None) -> TraceStore
```

---

## Repl Commands

| Command | Alias | Description |
|---------|-------|-------------|
| `next`, `n` | | Step forward one tool call |
| `prev`, `p` | | Step backward one tool call |
| `goto <N>` | | Jump to step N |
| `current`, `c` | | Show current step |
| `list`, `l` | | List all steps with markers |
| `call <N>` | | Show full detail of step N |
| `diff <A> <B>` | | Diff step A vs step B |
| `find <term>` | | Find steps matching term |
| `stats` | | Show run statistics |
| `watch <expr>` | | Watch a variable expression |
| `replay [id]` | | Load a different run |
| `export [fmt]` | | Export as json/markdown |
| `break <N>` | | Set breakpoint at step N |
| `run` | | Run to next breakpoint or end |
| `help` | `?` | Show help |

---

## Trace Storage

By default, traces are stored in `~/.tiny-repl-traces/`:

```
~/.tiny-repl-traces/
├── .index.jsonl          # Run index (one JSON line per run)
└── {run_id}.json        # Full run data
```

Set `TINY_REPL_PATH` env var or pass a path to `TraceStore(path)` to override.

---

## Performance

- Single-file, ~800 LOC, **zero dependencies** (stdlib only)
- ~10 KB — no install overhead
- Sync and async APIs
- Persistent index for fast run lookup

---

## Related

- [tiny-workflow](https://github.com/hussain-alsaibai/tiny-workflow) — DAG orchestrator with retry, persistence, approval gates
- [tiny-trace](https://github.com/hussain-alsaibai/tiny-trace) — Distributed tracing with zero dependencies
- [tiny-otel](https://github.com/hussain-alsaibai/tiny-otel) — OpenTelemetry-compatible spans without the SDK

---

MIT © 2026 Hussain Al-Saibai
