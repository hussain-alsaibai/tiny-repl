"""
tiny-repl CLI entry point.

Usage:
    tiny-repl                        # Interactive REPL, list runs
    tiny-repl list                   # List recent runs
    tiny-repl replay <run_id>        # Replay a specific run
    tiny-repl diff <id_a> <id_b>     # Diff two runs
    tiny-repl export <run_id> [json|md]  # Export a run
    tiny-repl -h                     # Help

Install via entry_points in setuptools, or run directly:
    python -m tiny_repl
"""

from __future__ import annotations

import sys


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        print()
        print("Commands:")
        print("  list                 List recent runs")
        print("  replay <run_id>      Load and replay a run interactively")
        print("  diff <id_a> <id_b>   Diff two runs")
        print("  export <run_id> [fmt] Export as json or markdown")
        print("  -h, --help           Show this help")
        return

    cmd = args[0]

    if cmd == "list":
        from tiny_repl import list_runs
        runs = list_runs(limit=20)
        if not runs:
            print("No runs found. Start tracing an agent first.")
            return
        print(f"{'Run ID':<12} {'Agent':<20} {'Steps':<6} {'Status':<12} {'Prompt (truncated)'}")
        print("-" * 80)
        for r in runs:
            prompt = r.get("prompt", "")[:40]
            print(f"{r['run_id']:<12} {r.get('agent_name',''):<20} "
                  f"{r.get('step_count', 0):<6} {r.get('status',''):<12} {prompt}")

    elif cmd == "replay":
        if len(args) < 2:
            print("Usage: tiny-repl replay <run_id>")
            print("Run 'tiny-repl list' to see available runs.")
            return

        run_id = args[1]
        from tiny_repl import ReplayEngine, InteractiveREPL, ReplSession

        engine = ReplayEngine()
        session = engine.replay(run_id)
        if session is None:
            print(f"Run '{run_id}' not found.")
            print("Run 'tiny-repl list' to see available runs.")
            return

        repl = InteractiveREPL(engine)
        repl.interact(session)

    elif cmd == "diff":
        if len(args) < 3:
            print("Usage: tiny-repl diff <run_id_a> <run_id_b>")
            return

        from tiny_repl import diff_runs
        diff = diff_runs(args[1], args[2])
        if diff:
            print(diff)
        else:
            print("One or both runs not found.")

    elif cmd == "export":
        if len(args) < 2:
            print("Usage: tiny-repl export <run_id> [json|md]")
            return

        fmt = args[2] if len(args) > 2 else "json"
        from tiny_repl import export_run
        output = export_run(args[1], format=fmt)
        if output:
            print(output)
        else:
            print(f"Run '{args[1]}' not found.")

    else:
        print(f"Unknown command: {cmd}")
        print("Run 'tiny-repl --help' for usage.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
