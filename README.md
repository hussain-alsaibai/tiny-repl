# tiny-repl — Interactive Debugging REPL for AI Agent Pipelines

> **Replay steps, diff runs, inspect state, export traces — built for the agent-first debugging workflow.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](tiny_repl.py)
[![Part of the tiny-* ecosystem](https://img.shields.io/badge/tiny--*-ecosystem-purple.svg)](#ecosystem)

When a multi-step agent fails at step 47, you need to understand what happened — replay that step, diff against a working run, inspect the output. Current solution: print statements + manual log grep. tiny-repl: first-class debugging.

## The Problem

Agents do dozens or hundreds of tool calls. When something goes wrong:
- "Which step failed?" → grep logs manually
- "What was the input at step 23?" → search log files
- "How does this run differ from the working one?" → compare log files by eye
- "Can I replay step 23?" → re-run the whole thing

tiny-repl solves this with a debugging-first design: trace, replay, inspect, diff.

## Installation

```bash
# Copy tiny_repl.py into your project — that's it.
# Zero dependencies.
```

## Quick Start

### 1. Trace Your Agent

```python
from tiny_repl import start_trace, record, end_trace, wrap

# Start tracing
run_id = start_trace(agent_name="bounty-hunter", prompt="Find XSS in repo")

# ... run your agent, recording each step:
record("web_search", {"query": "fastapi xss"}, output={"results": [...]})
record("read_file", {"path": "routes.py"}, output="def login(req): ...")
record("write_file", {"path": "fix.py"}, output="✅ Saved")

# ... if something fails:
try:
    result = await agent.run()
    end_trace("completed", final_output=result)
except Exception as exc:
    end_trace("failed", error=str(exc))
```

### 2. Replay Interactively

```python
from tiny_repl import replay, interact

session = replay("abc12345")  # Load a run
interact(session)            # Drop into REPL
```

```
tiny-repl — Run abc12345 (bounty-hunter)
Status: completed | Steps: 23
Current step: 0
============================================================
tiny-repl> n
→ Step 0:
  [  0] ✓ web_search (142.3ms)
       Input: {"query": "fastapi xss"}
       → {"results": [...]}

tiny-repl> n
→ Step 1:
  [  1] ✓ read_file (12.1ms)
       Input: {"path": "routes.py"}
       → def login(req): ...

tiny-repl> call 15
  [ 15] ✗ git_commit (231.4ms)
       ERROR: git: command not found
       Input: {"message": "fix: XSS in login", "files": [...]}

tiny-repl> find "error"
  [ 15] ✗ git_commit (231.4ms)
       ERROR: git: command not found
```

### 3. Diff Two Runs

```python
from tiny_repl import get_engine

engine = get_engine()
diff = engine.diff("run-a1b2c3", "run-d4e5f6")
print(diff)
```

```
============================================================
STEP 15
============================================================
[RUN A] git_commit({"message": "fix: XSS in login", ...})
  → ✅ Committed (sha: abc123)
[RUN B] git_commit({"message": "fix: XSS in login", ...})
  ERROR: git: command not found
  ⚠️  Output differs
```

## Key Features

### Trace Everything
```python
from tiny_repl import get_tracer, wrap, trace_async

tracer = get_tracer("my-agent")
tracer.start_run("my-agent", "Analyze codebase")

# Manual recording
tracer.record_call("read_file", {"path": "main.py"}, output=content)

# Or wrap any async function
@wrap
async def web_search(query: str):
    return await search_api(query)

# Or use trace_async for timing
async def main():
    result = await trace_async(
        "llm_call",
        {"model": "gpt-4o-mini", "prompt": "..."},
        llm.generate,
        prompt="...",
    )
```

### Auto-Trace Your Agent Framework
```python
# Patch your agent's tool execution to auto-trace:
async def execute_tool(name, input):
    tracer = get_tracer()
    start = time.perf_counter()
    try:
        result = await actual_execute(name, input)
        ms = (time.perf_counter() - start) * 1000
        tracer.record_call(name, input, result, duration_ms=ms)
        return result
    except Exception as exc:
        ms = (time.perf_counter() - start) * 1000
        tracer.record_call(name, input, None, error=traceback.format_exc(), duration_ms=ms)
        raise
```

### Export & Share
```python
# JSON (for programmatic use)
json_data = engine.export(run_id, "json")

# Markdown (for human reading / GitHub issues)
md_report = engine.export(run_id, "markdown")
```

### List Recent Runs
```python
from tiny_repl import list_runs

for run in list_runs(limit=20):
    print(f"{run['run_id']} — {run['agent_name']} "
          f"({run['step_count']} steps, {run['status']})")
```

## REPL Commands

| Command | Alias | Description |
|---------|-------|-------------|
| `next`, `n` | | Step forward one tool call |
| `prev`, `p` | | Step backward one tool call |
| `goto <N>` | | Jump to step N |
| `current`, `c` | | Show current step detail |
| `list`, `l` | | List all steps (→ = current, ● = breakpoint) |
| `call <N>` | | Full detail of step N |
| `diff <A> <B>` | | Diff step A vs step B |
| `watch <expr>` | | Watch an expression |
| `find <term>` | | Find steps matching term |
| `replay <id>` | | Load a different run |
| `export [json/md]` | | Export run |
| `break <N>` | | Set breakpoint at step N |
| `run` | | Run to next breakpoint or end |
| `stats` | | Show run statistics |
| `help` | `?` | Show help |

## Architecture

```
tiny-repl has three layers:

1. LiveTracer      → instruments your running agent
2. TraceStore      → persists runs as JSON files
3. ReplayEngine    → loads, replays, diffs, exports

Command-line REPL sits on top:
4. InteractiveREPL  → interactive debugging interface
```

Traces are stored as:
```
.tiny-repl-traces/
  abc12345.json     # Full run data
  def67890.json
  .index.jsonl       # Run metadata index
```

## Use Cases

### Bounty Hunting
Trace every step of your bug bounty agent. When it finds something, export the full trace as a markdown report for the bug submission.

### Code Review
Run your code review agent twice — before and after a fix. Diff the traces to see exactly what changed.

### CI/CD Integration
Run agent traces in CI. On failure, attach the trace JSON to the CI artifact. Review later without re-running.

### Learning
Watch how an agent solves a problem. Step through each tool call. See where it goes wrong.

## Ecosystem

Part of the **tiny-\*** ecosystem — zero-dependency Python utilities:

| Repo | Description |
|------|-------------|
| [tiny-agent](https://github.com/hussain-alsaibai/tiny-agent) | Agent framework in one file |
| [tiny-trace](https://github.com/hussain-alsaibai/tiny-trace) | Distributed tracing |
| [tiny-checkpoint](https://github.com/hussain-alsaibai/tiny-checkpoint) | Agent suspend/resume |
| [tiny-mesh](https://github.com/hussain-alsaibai/tiny-mesh) | Service mesh proxy |
| [tiny-llm-router](https://github.com/hussain-alsaibai/tiny-llm-router) | Multi-provider LLM routing |
| [fast-cache](https://github.com/hussain-alsaibai/fast-cache) | LRU + TTL cache |
| [dev-masterkit](https://github.com/hussain-alsaibai/dev-masterkit) | Curated dev tools |

---

*MIT License. https://github.com/hussain-alsaibai/tiny-repl*
