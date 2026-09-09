# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI for Studio Career web search. Prints JSON hits (title, url, snippet)."""

from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    query = " ".join(args).strip()
    if not query:
        print("[]")
        return 0
    from navin.career.collect import _search_ddgs

    print(json.dumps(_search_ddgs(query, 6), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
