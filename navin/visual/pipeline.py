"""Orchestrate reference capture, generation, render and comparison."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from navin.visual.compare import compare_images, render_html

GenerateFn = Callable[[Path, int, dict[str, Any] | None], str]
RenderFn = Callable[[str, Path], Path]


def run_pipeline(
    reference: str | Path,
    generate: GenerateFn,
    out_dir: str | Path,
    threshold: float = 0.95,
    max_iters: int = 3,
    render: RenderFn | None = None,
) -> dict[str, Any]:
    """Iterate generate -> render -> compare until score >= threshold.

    ``generate(reference, iteration, last_report)`` returns HTML.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    render = render or render_html
    ref = Path(reference)
    history: list[dict[str, Any]] = []
    last: dict[str, Any] | None = None
    best: dict[str, Any] | None = None
    for i in range(1, max_iters + 1):
        html = generate(ref, i, last)
        (out / f"iter{i}.html").write_text(html, encoding="utf-8")
        png = render(html, out / f"iter{i}.png")
        last = compare_images(
            ref, png, diff_path=out / f"iter{i}_diff.png", report_path=out / f"iter{i}_report.json"
        )
        last["iteration"] = i
        history.append(last)
        if best is None or last["score"] > best["score"]:
            best = last
        if last["score"] >= threshold:
            break
    assert best is not None
    return {
        "reached": best["score"] >= threshold,
        "threshold": threshold,
        "best": best,
        "iterations": len(history),
        "history": history,
    }
