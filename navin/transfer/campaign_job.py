# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Subprocess entry of the transfer campaign: ``python -m navin.transfer.campaign_job``.

The gateway never runs a campaign in its own process (S5.2: an isolated job,
another machine when possible). The panel and the CLI start this module as a
child and read one JSON line on stdout. The child opens the configured
provider (the reasoning rail) and the sandbox tools; it never opens a
channel, the gateway's settings or the chat sessions, and it never writes an
S4 trajectory.

Exit code 0 with a JSON result, whatever the campaign status; a non-zero exit
means the child itself died, which the parent reports as ``status: error``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="navin.transfer.campaign_job", add_help=False)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--actor", default="human")
    parser.add_argument("--replay", default=None)
    args = parser.parse_args(argv)

    try:
        from loguru import logger

        logger.remove()
        logger.add(sys.stderr, level="WARNING")
    except Exception:  # noqa: BLE001 - logging is not the job
        pass

    from navin.transfer.campaign import replay, run_campaign

    workspace = Path(args.workspace)
    if args.replay:
        result = replay(workspace, args.replay, actor=args.actor)
    else:
        result = run_campaign(workspace, actor=args.actor)
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
