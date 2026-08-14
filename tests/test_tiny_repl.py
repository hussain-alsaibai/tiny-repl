"""
Tests for tiny-repl — AI agent pipeline REPL and tracer.
Run: python -m pytest tests/ -v
"""

import json
import os
import tempfile
import time
import uuid
from pathlib import Path

import pytest

import tiny_repl
from tiny_repl import (
    AgentRun,
    LiveTracer,
    ReplSession,
    ReplayEngine,
    ToolCall,
    TraceStore,
    end_trace,
    get_store,
    list_runs,
    record,
    replay,
    start_trace,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_store():
    """Temporary trace store."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = TraceStore(tmpdir)
        yield store
        # cleanup handled by context manager


@pytest.fixture
def sample_run():
    """A sample AgentRun for testing."""
    run = AgentRun(
        run_id="test-run-001",
        agent_name="test-agent",
        prompt="Fix the authentication bug",
        started_at=time.time(),
        ended_at=time.time() + 5.0,
        status="completed",
        tool_calls=[
            ToolCall(
                step=0,
                name="read_file",
                input={"path": "auth.py"},
                output={"lines": 42, "content": "..."},
                duration_ms=120.5,
            ),
            ToolCall(
                step=1,
                name="search",
                input={"query": "auth token"},
                output=["auth.py:42", "login.py:18"],
                duration_ms=80.3,
            ),
            ToolCall(
                step=2,
                name="write_file",
                input={"path": "auth.py", "content": "fixed"},
                output="written",
                error=None,
                duration_ms=45.2,
            ),
        ],
        final_output="Bug fixed in auth.py",
    )
    return run


# ─────────────────────────────────────────────────────────────
# ToolCall Tests
# ─────────────────────────────────────────────────────────────

class TestToolCall:
    def test_to_dict_roundtrip(self):
        call = ToolCall(
            step=0,
            name="read_file",
            input={"path": "a.py"},
            output={"lines": 10},
            error=None,
            duration_ms=50.0,
        )
        d = call.to_dict()
        restored = ToolCall.from_dict(d)
        assert restored.name == call.name
        assert restored.input == call.input
        assert restored.output == call.output
        assert restored.duration_ms == call.duration_ms

    def test_with_error(self):
        call = ToolCall(
            step=0,
            name="read_file",
            input={"path": "missing.py"},
            output=None,
            error="FileNotFoundError",
            duration_ms=10.0,
        )
        assert call.error is not None
        assert "not found" in call.error.lower()


# ─────────────────────────────────────────────────────────────
# AgentRun Tests
# ─────────────────────────────────────────────────────────────

class TestAgentRun:
    def test_add_call(self):
        run = AgentRun(
            run_id="r1",
            agent_name="a1",
            prompt="test",
            started_at=time.time(),
        )
        call = ToolCall(
            step=0, name="tool1", input={}, output="ok", duration_ms=10
        )
        run.add_call(call)
        assert len(run.tool_calls) == 1
        assert run.tool_calls[0].trace_id == "r1"

    def test_to_dict_roundtrip(self, sample_run):
        d = sample_run.to_dict()
        restored = AgentRun.from_dict(d)
        assert restored.run_id == sample_run.run_id
        assert restored.agent_name == sample_run.agent_name
        assert len(restored.tool_calls) == len(sample_run.tool_calls)
        assert restored.final_output == sample_run.final_output


# ─────────────────────────────────────────────────────────────
# ReplSession Tests
# ─────────────────────────────────────────────────────────────

class TestReplSession:
    def test_step_forward_back(self, sample_run):
        session = ReplSession(run=sample_run)
        assert session.current_step == 0

        call = session.step_forward()
        assert call is not None
        assert call.name == "read_file"
        assert session.current_step == 1

        call = session.step_back()
        assert call is not None
        assert session.current_step == 0

    def test_goto_out_of_bounds(self, sample_run):
        session = ReplSession(run=sample_run)
        assert session.goto(99) is None
        assert session.goto(-1) is None

    def test_goto_valid(self, sample_run):
        session = ReplSession(run=sample_run)
        call = session.goto(2)
        assert call is not None
        assert call.name == "write_file"
        assert session.current_step == 2

    def test_current(self, sample_run):
        session = ReplSession(run=sample_run)
        assert session.current() is None  # before stepping
        session.step_forward()
        assert session.current() is not None


# ─────────────────────────────────────────────────────────────
# TraceStore Tests
# ─────────────────────────────────────────────────────────────

class TestTraceStore:
    def test_save_and_load(self, tmp_store, sample_run):
        saved_id = tmp_store.save(sample_run)
        assert saved_id == "test-run-001"

        loaded = tmp_store.load("test-run-001")
        assert loaded is not None
        assert loaded.run_id == sample_run.run_id
        assert len(loaded.tool_calls) == len(sample_run.tool_calls)

    def test_load_nonexistent(self, tmp_store):
        assert tmp_store.load("does-not-exist") is None

    def test_list_runs(self, tmp_store, sample_run):
        tmp_store.save(sample_run)
        runs = tmp_store.list_runs()
        assert len(runs) == 1
        assert runs[0]["run_id"] == "test-run-001"

    def test_delete(self, tmp_store, sample_run):
        tmp_store.save(sample_run)
        assert tmp_store.delete("test-run-001") is True
        assert tmp_store.load("test-run-001") is None

    def test_delete_nonexistent(self, tmp_store):
        assert tmp_store.delete("does-not-exist") is False


# ─────────────────────────────────────────────────────────────
# LiveTracer Tests
# ─────────────────────────────────────────────────────────────

class TestLiveTracer:
    def test_start_and_end_run(self, tmp_store):
        tracer = LiveTracer(store=tmp_store)
        run_id = tracer.start_run("agent-x", "test prompt")
        assert run_id is not None

        tracer.record_call("tool1", {"k": "v"}, "output", None, 10.0)
        tracer.record_call("tool2", {"k": "v2"}, None, "error!", 20.0)

        run = tracer.end_run(status="completed", final_output="done")
        assert run is not None
        assert run.agent_name == "agent-x"
        assert len(run.tool_calls) == 2
        assert run.tool_calls[1].error == "error!"

    def test_record_without_start_raises(self, tmp_store):
        tracer = LiveTracer(store=tmp_store)
        with pytest.raises(RuntimeError, match="No active run"):
            tracer.record_call("tool1", {}, None)

    def test_callback(self, tmp_store):
        tracer = LiveTracer(store=tmp_store)
        calls_seen = []

        def callback(call):
            calls_seen.append(call.name)

        tracer.on_call(callback)
        tracer.start_run("a", "p")
        tracer.record_call("exec", {}, "ok")
        tracer.record_call("read", {}, "data")
        tracer.end_run()

        assert calls_seen == ["exec", "read"]


# ─────────────────────────────────────────────────────────────
# ReplayEngine Tests
# ─────────────────────────────────────────────────────────────

class TestReplayEngine:
    def test_replay_existing_run(self, tmp_store, sample_run):
        tmp_store.save(sample_run)
        engine = ReplayEngine(tmp_store)
        session = engine.replay("test-run-001")
        assert session is not None
        assert session.run.run_id == "test-run-001"

    def test_replay_missing_run(self, tmp_store):
        engine = ReplayEngine(tmp_store)
        session = engine.replay("missing-id")
        assert session is None

    def test_diff_identical_runs(self, tmp_store, sample_run):
        # Save two identical runs
        run_a = sample_run
        run_b = AgentRun(
            run_id="test-run-002",
            agent_name="test-agent",
            prompt="Fix the auth bug",
            started_at=time.time(),
            ended_at=time.time() + 5.0,
            status="completed",
            tool_calls=[
                ToolCall(step=0, name="read_file", input={"path": "auth.py"},
                         output={"lines": 42, "content": "..."}, duration_ms=120.5),
                ToolCall(step=1, name="search", input={"query": "auth token"},
                         output=["auth.py:42", "login.py:18"], duration_ms=80.3),
                ToolCall(step=2, name="write_file", input={"path": "auth.py", "content": "fixed"},
                         output="written", duration_ms=45.2),
            ],
            final_output="Bug fixed in auth.py",
        )
        tmp_store.save(run_a)
        tmp_store.save(run_b)

        engine = ReplayEngine(tmp_store)
        diff = engine.diff("test-run-001", "test-run-002")
        assert "STEP 0" in diff
        assert "STEP 2" in diff

    def test_diff_different_tool_sequence(self, tmp_store, sample_run):
        run_a = sample_run
        run_b = AgentRun(
            run_id="test-run-003",
            agent_name="test-agent",
            prompt="Fix the auth bug",
            started_at=time.time(),
            tool_calls=[
                ToolCall(step=0, name="grep", input={"pattern": "token"},
                         output=["auth.py:42"], duration_ms=30.0),
            ],
        )
        tmp_store.save(run_a)
        tmp_store.save(run_b)

        engine = ReplayEngine(tmp_store)
        diff = engine.diff("test-run-001", "test-run-003")
        assert "Tool differs" in diff

    def test_export_json(self, tmp_store, sample_run):
        tmp_store.save(sample_run)
        engine = ReplayEngine(tmp_store)
        output = engine.export("test-run-001", format="json")
        data = json.loads(output)
        assert data["run_id"] == "test-run-001"
        assert len(data["tool_calls"]) == 3

    def test_export_markdown(self, tmp_store, sample_run):
        tmp_store.save(sample_run)
        engine = ReplayEngine(tmp_store)
        output = engine.export("test-run-001", format="markdown")
        assert "# Agent Run:" in output
        assert "## Step 0: read_file" in output
        assert "test-run-001" in output


# ─────────────────────────────────────────────────────────────
# Convenience API Tests
# ─────────────────────────────────────────────────────────────

class TestConvenienceAPI:
    def test_start_end_trace_cycle(self):
        """Integration test: full trace cycle via convenience functions."""
        # Use a temp store for this test
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch the global store
            tiny_repl._store = TraceStore(tmpdir)
            tiny_repl._tracer = None

            run_id = start_trace("ci-agent", "CI pipeline")
            record("checkout", {"branch": "main"}, "cloned")
            record("install", {"packages": ["pytest"]}, "installed")
            record("test", {"files": ["test_a.py"]}, {"passed": 10, "failed": 0})

            run = end_trace(status="completed", final_output="All tests passed")

            assert run is not None
            assert run.agent_name == "ci-agent"
            assert len(run.tool_calls) == 3
            assert run.status == "completed"

    def test_list_runs(self, tmp_store, sample_run):
        tmp_store.save(sample_run)
        tiny_repl._store = tmp_store
        tiny_repl._engine = None

        runs = list_runs()
        assert len(runs) == 1
        assert runs[0]["run_id"] == "test-run-001"

    def test_replay_function(self, tmp_store, sample_run):
        tmp_store.save(sample_run)
        tiny_repl._store = tmp_store
        tiny_repl._engine = None

        session = replay("test-run-001")
        assert session is not None
        assert session.run.agent_name == "test-agent"
