"""Subprocess entry of the policy trainer: ``python -m navin.policy.train_job``.

The gateway never trains in its own process (S4.2). The job runner and the
CLI start this module as a child, with a CPU-time and address-space ceiling
set before anything runs, and read one JSON line on stdout. The child
imports the agent runner and the fixture tools, runs the battery in
throwaway folders, fits and scores the head, writes the checkpoint; it never
opens a provider, a channel or the gateway's settings.

Exit code 0 with a JSON result, whatever the training status; a non-zero
exit means the child itself died (killed by the ceiling, import error...),
which the parent reports as ``status: error``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def apply_ceilings(*, cpu_seconds: int, address_space_mb: int) -> None:
    """Hard limits for this process only; best effort on platforms without them."""
    try:
        import resource
    except ImportError:  # pragma: no cover - not POSIX
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
        cap = cpu_seconds if hard == resource.RLIM_INFINITY else min(cpu_seconds, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (cap, cap if hard == resource.RLIM_INFINITY else hard))
    except (ValueError, OSError):
        pass
    if address_space_mb > 0:
        try:
            limit = address_space_mb * 1024 * 1024
            soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            cap = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap if hard == resource.RLIM_INFINITY else hard))
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="navin.policy.train_job", add_help=False)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--actor", default="auto")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-collect", action="store_true")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-rss-mb", type=int, default=512)
    parser.add_argument("--address-space-mb", type=int, default=4096)
    parser.add_argument("--max-cases", type=int, default=400)
    args = parser.parse_args(argv)

    apply_ceilings(cpu_seconds=int(args.timeout) + 30, address_space_mb=args.address_space_mb)
    try:
        os.nice(5)
    except (OSError, AttributeError):
        pass
    # The training child talks JSON on stdout; keep the library logs quiet.
    try:
        from loguru import logger

        logger.remove()
        logger.add(sys.stderr, level="WARNING")
    except Exception:  # noqa: BLE001 - logging is not the job
        pass

    from navin.policy.train import TrainBudget, train

    budget = TrainBudget(timeout_s=args.timeout, max_rss_growth_mb=args.max_rss_mb, max_cases=args.max_cases)
    result = train(
        Path(args.workspace),
        actor=args.actor,
        budget=budget,
        force=args.force,
        collect_first=not args.no_collect,
    )
    sys.stdout.write(json.dumps(result.as_dict(), ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
