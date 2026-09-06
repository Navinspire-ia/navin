#!/usr/bin/env python3
"""Benchmark the agent hot path: code index, token estimation, governance.

Measures the primitives that dominate agent-perceived latency outside the
LLM itself, so optimizations (index warmer, lazy consolidation, governance
caching) can be judged against a recorded baseline instead of feelings.

Scenarios:
  - index_cold      first CodeIndex.ensure() on a fresh cache (cold start)
  - index_warm      ensure() when the index is already loaded (revalidate)
  - estimate        tiktoken prompt estimation over a long synthetic history
  - governance      ContextGovernor.prepare_for_model over N iterations

Usage:
  python scripts/bench_agent_hotpath.py                 # small fixture (~200 files)
  python scripts/bench_agent_hotpath.py --files 5000    # medium fixture
  python scripts/bench_agent_hotpath.py --repeat 5 --json out.json
  python scripts/bench_agent_hotpath.py --root /path/to/real/repo

Results (p50/p95 in ms) print to stdout; --json also writes a machine-readable
file for docs/performance.md baselines.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# -- fixture -----------------------------------------------------------------

_MODULE_TEMPLATE = '''"""Synthetic module {n} for the hot-path benchmark."""


class Service{n}:
    """Fake service with enough symbols to exercise the parser."""

    def __init__(self, name: str = "svc{n}") -> None:
        self.name = name
        self.calls = 0

    def handle(self, payload: dict) -> dict:
        self.calls += 1
        return {{"handled_by": self.name, "payload": payload}}

    def reset(self) -> None:
        self.calls = 0


def build_service_{n}() -> Service{n}:
    return Service{n}()


def process_batch_{n}(items: list) -> list:
    service = build_service_{n}()
    return [service.handle({{"item": item}}) for item in items]
'''


def build_fixture(root: Path, files: int) -> None:
    """Create a synthetic Python repo with ``files`` modules."""
    per_dir = 50
    for n in range(files):
        pkg = root / f"pkg{n // per_dir:03d}"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").touch()
        (pkg / f"module_{n:05d}.py").write_text(
            _MODULE_TEMPLATE.format(n=n), encoding="utf-8"
        )


# -- timing ------------------------------------------------------------------


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    mid = statistics.median(ordered)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return {
        "p50_ms": round(mid, 2),
        "p95_ms": round(p95, 2),
        "min_ms": round(ordered[0], 2),
        "max_ms": round(ordered[-1], 2),
        "runs": len(ordered),
    }


def timed(fn, repeat: int) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return _percentiles(samples)


# -- scenarios -----------------------------------------------------------------


def bench_index(root: Path, repeat: int) -> dict[str, dict[str, float]]:
    from navin.index import service as index_service
    from navin.index.store import cache_path

    results: dict[str, dict[str, float]] = {}

    def cold_once() -> None:
        # Reset both the in-process instance and the on-disk cache so each
        # run pays the full first-tool cost an agent would.
        index_service._instances.pop(str(root.resolve()), None)
        cache = cache_path(root)
        shutil.rmtree(cache.parent, ignore_errors=True) if cache.parent.name == "index" else None
        cache.unlink(missing_ok=True)
        index_service.get_index(root).ensure()

    results["index_cold"] = timed(cold_once, max(1, min(repeat, 3)))

    index = index_service.get_index(root)
    index.ensure()
    results["index_warm"] = timed(index.ensure, repeat)

    def lookup() -> None:
        index.search("Service", limit=10)

    results["index_lookup"] = timed(lookup, repeat)
    return results


def _synthetic_history(messages: int, chars_per_message: int) -> list[dict]:
    body = ("The quick brown fox jumps over the lazy dog. " * 200)[:chars_per_message]
    history: list[dict] = [
        {"role": "system", "content": "You are a coding agent." + body[:2000]},
    ]
    for i in range(messages):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({"role": role, "content": f"[msg {i}] {body}"})
    return history


def bench_estimate(repeat: int) -> dict[str, dict[str, float]]:
    from navin.utils.helpers import estimate_prompt_tokens_chain

    history = _synthetic_history(messages=120, chars_per_message=2000)

    def run() -> None:
        estimate_prompt_tokens_chain(None, "gpt-4o", history, None)

    # Warm tiktoken's encoder outside the measurement.
    run()
    return {"estimate_long_history": timed(run, repeat)}


def bench_governance(repeat: int) -> dict[str, dict[str, float]]:
    from navin.agent.context_governance import ContextGovernanceConfig, ContextGovernor

    class _Tools:
        @staticmethod
        def get_definitions() -> list[dict]:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": f"tool_{i}",
                        "description": "synthetic tool " * 10,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
                for i in range(20)
            ]

    governor = ContextGovernor()
    config = ContextGovernanceConfig(
        provider=None,
        model="gpt-4o",
        tools=_Tools(),
        workspace=None,
        session_key="bench",
        max_tool_result_chars=16_000,
        context_window_tokens=200_000,
        context_block_limit=None,
        clearing=None,
        max_tokens=8192,
        inflight_start_index=0,
    )
    history = _synthetic_history(messages=80, chars_per_message=2000)

    def run() -> None:
        compacted: set[str] = set()
        # Simulate a 20-iteration tool loop over a growing message list.
        messages = list(history)
        for i in range(20):
            governor.prepare_for_model(config, messages, compacted)
            messages.append({"role": "assistant", "content": f"step {i} done"})

    run()
    return {"governance_20_iterations": timed(run, max(1, repeat // 2))}


# -- main ----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=200, help="fixture size (files)")
    parser.add_argument("--repeat", type=int, default=5, help="timed repetitions")
    parser.add_argument("--root", type=Path, default=None, help="bench a real repo instead of a fixture")
    parser.add_argument("--json", type=Path, default=None, help="write results to this JSON file")
    parser.add_argument("--skip-index", action="store_true", help="skip the index scenarios")
    args = parser.parse_args()

    results: dict[str, dict[str, float]] = {}
    tmp: tempfile.TemporaryDirectory | None = None

    if not args.skip_index:
        if args.root is not None:
            root = args.root.resolve()
        else:
            tmp = tempfile.TemporaryDirectory(prefix="navin-bench-")
            root = Path(tmp.name)
            print(f"Building fixture: {args.files} files in {root}", flush=True)
            build_fixture(root, args.files)
        results.update(bench_index(root, args.repeat))

    results.update(bench_estimate(args.repeat))
    results.update(bench_governance(args.repeat))

    if tmp is not None:
        tmp.cleanup()

    print()
    print(f"{'scenario':<28} {'p50 ms':>10} {'p95 ms':>10} {'min':>8} {'max':>10}")
    print("-" * 70)
    for name, stats in results.items():
        print(
            f"{name:<28} {stats['p50_ms']:>10.2f} {stats['p95_ms']:>10.2f}"
            f" {stats['min_ms']:>8.2f} {stats['max_ms']:>10.2f}"
        )

    if args.json:
        payload = {
            "fixture_files": args.files if args.root is None else None,
            "root": str(args.root) if args.root else None,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": results,
        }
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nJSON written to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
