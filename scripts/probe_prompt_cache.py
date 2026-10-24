#!/usr/bin/env python3
"""Measure the prompt cache hit rate of one model through the Navin gateway.

Sends the same long prompt several times and prints, per call, the share of the
prompt served from the provider cache. A model on one deployment hits from the
second call on (about 90 %); a model spread over several deployments by the
gateway hits at random, because each deployment has its own cache.

It spends real tokens (about 22k prompt tokens per call) on your key.

Usage:
  .venv/bin/python scripts/probe_prompt_cache.py
  .venv/bin/python scripts/probe_prompt_cache.py --model moonshotai/kimi-k3 --calls 8
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]


def filler(tokens: int) -> str:
    rng = random.Random(uuid.uuid4().int)
    return uuid.uuid4().hex + " " + " ".join(rng.choice(WORDS) for _ in range(int(tokens * 0.75)))


async def probe(model: str, calls: int, prompt_tokens: int, pause: float) -> list[float | None]:
    import openai

    from navin.config.loader import load_config

    provider = load_config().providers.navin
    client = openai.AsyncOpenAI(api_key=provider.api_key, base_url=provider.api_base, timeout=120)
    prompt = filler(prompt_tokens) + "\n\nReply with exactly: ok"
    ratios: list[float | None] = []
    for _ in range(calls):
        try:
            # max_tokens >= 16: Bedrock refuses a smaller output limit.
            reply = await client.chat.completions.create(
                model=model, max_tokens=16, messages=[{"role": "user", "content": prompt}],
            )
            usage = reply.usage
            details = getattr(usage, "prompt_tokens_details", None)
            cached = (getattr(details, "cached_tokens", 0) if details else 0) or 0
            ratios.append(cached / usage.prompt_tokens if usage.prompt_tokens else 0.0)
        except openai.APIError as error:
            print(f"  call failed: {str(error)[:140]}", file=sys.stderr)
            ratios.append(None)
        await asyncio.sleep(pause)
    return ratios


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default="moonshotai/kimi-k3")
    parser.add_argument("--calls", type=int, default=6)
    parser.add_argument("--prompt-tokens", type=int, default=20_000)
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between calls")
    args = parser.parse_args()
    ratios = asyncio.run(probe(args.model, args.calls, args.prompt_tokens, args.pause))
    shown = ["ERR" if r is None else f"{r:.0%}" for r in ratios]
    print(f"{args.model}: cache per call {shown}")
    later = [r for r in ratios[1:] if r is not None]
    if later:
        hits = sum(1 for r in later if r >= 0.5)
        print(f"calls 2..{len(ratios)}: {hits}/{len(later)} served from cache "
              f"(one deployment: nearly all; several deployments: about 1 in 2 or 3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
