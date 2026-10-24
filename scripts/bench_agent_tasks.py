#!/usr/bin/env python3
"""End-to-end agent benchmark: real read-only tasks through `navin agent`.

bench_agent_hotpath.py measures everything around the model. This one times
whole turns, model included, so a prompt or routing change can be judged on
wall time, thinking time and the number of search/read steps.

Each task runs in a fresh session and is told not to modify anything. It uses
the configured model, so it spends real quota.

Usage:
  python scripts/bench_agent_tasks.py                    # every task once
  python scripts/bench_agent_tasks.py --repeat 3 --json out.json
  python scripts/bench_agent_tasks.py --only routing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NAVIN = REPO_ROOT / ".venv" / "bin" / "navin"

NO_EDIT = "Ne modifie aucun fichier. Réponds en 4 lignes maximum avec les fichiers. "
TASKS = {
    "routing": NO_EDIT + "Dans l'interface terminal (TUI), où est défini le formulaire "
    "Task routing et comment l'interrupteur model_routing_enabled y est enregistré ?",
    "picker": NO_EDIT + "Dans le TUI, comment la liste des modèles du sélecteur est-elle "
    "construite et quelles lignes sont masquées ?",
    "wide": NO_EDIT + "Explique comment l'activation de licence fonctionne de bout en bout, "
    "du desktop au site (validation, appareils, clé IA livrée), en citant les fichiers de chaque étape.",
    "cap": NO_EDIT + "Quel est le nombre maximal d'étapes d'outils par tour et où est-il appliqué ?",
}

THOUGHT = re.compile(r"Thought for (\d+)s")
STEP = re.compile(r"[└├]\s+✓\s+(Search|Read|Index|Ran|List)")
TIMING = re.compile(r"turn timing: (.*)")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def run_task(prompt: str, timeout: int, config: Path | None = None) -> dict:
    session = f"cli:bench-{uuid.uuid4().hex[:8]}"
    cmd = [str(NAVIN), "agent", "-s", session, "-m", f"/forge {prompt}", "--logs",
           *(["-c", str(config)] if config else [])]
    started = time.monotonic()
    timed_out = False
    # Output goes to a file as it is produced: a run cut at the timeout still
    # leaves its tool calls and thinking time to read.
    with tempfile.TemporaryFile("w+") as sink:
        proc = subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=sink, stderr=subprocess.STDOUT,
                                text=True, start_new_session=True)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        sink.seek(0)
        out = ANSI.sub("", sink.read())
    timing = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", (TIMING.findall(out) or [""])[-1])}
    steps = max(timing.get("steps", 0), 1)
    tool_calls = len(re.findall(r"Tool call:", out))
    return {
        "prefill_k_per_step": round(timing.get("prefill", 0) / steps / 1000, 1),
        "ttft_avg_s": round(timing.get("ttft_avg", 0) / 1000, 1),
        "model_s": round(timing.get("model", 0) / 1000, 1),
        "wall_s": round(time.monotonic() - started, 1),
        "thinking_s": sum(int(n) for n in THOUGHT.findall(out)),
        "steps": timing.get("steps", len(THOUGHT.findall(out))),
        "tool_calls": tool_calls,
        "answered": not timed_out and bool(out.strip())
        and "The task is unfinished" not in out and "Error:" not in out,
        "timed_out": timed_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--only", choices=sorted(TASKS))
    parser.add_argument("--timeout", type=int, default=300, help="seconds per run")
    parser.add_argument("--json", type=Path, help="also write results here")
    parser.add_argument("--config", type=Path, help="config file to run with (default: yours)")
    args = parser.parse_args()
    if not NAVIN.exists():
        print(f"missing {NAVIN}: create the venv first", file=sys.stderr)
        return 2

    results: dict[str, list[dict]] = {}
    for name, prompt in TASKS.items():
        if args.only and name != args.only:
            continue
        results[name] = [run_task(prompt, args.timeout, args.config) for _ in range(args.repeat)]
        runs = results[name]
        print(f"{name:10} wall p50 {statistics.median(r['wall_s'] for r in runs):6.1f}s  "
              f"thinking {statistics.median(r['thinking_s'] for r in runs):5.1f}s  "
              f"steps {statistics.median(r['steps'] for r in runs):4.1f}  "
              f"prefill/step {statistics.median(r['prefill_k_per_step'] for r in runs):5.1f}k  "
              f"ttft {statistics.median(r['ttft_avg_s'] for r in runs):4.1f}s  "
              f"tool calls {statistics.median(r['tool_calls'] for r in runs):4.1f}  "
              f"answered {sum(r['answered'] for r in runs)}/{len(runs)}  "
              f"cut {sum(r['timed_out'] for r in runs)}")
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
