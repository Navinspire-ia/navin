"""WebUI payload for semantic codebase search (DevSearchPanel).

Wraps :class:`navin.index.semantic.SemanticIndex` into the same grouped shape
the lexical project search returns, so the panel can render either mode.
"""

from __future__ import annotations

from typing import Any

from navin.agent.tools.code_index import SemanticSearchConfig, _is_free_provider
from navin.webui.project_search import ProjectSearchError
from navin.webui.workspaces import WorkspaceScope


async def semantic_search_payload(
    scope: WorkspaceScope,
    query: str,
    *,
    limit: int = 40,
) -> dict[str, Any]:
    """Run embedding search and return a ``ProjectSearchPayload``-compatible dict."""
    cleaned = (query or "").strip()
    if not cleaned:
        raise ProjectSearchError("missing query")
    if len(cleaned) > 500:
        raise ProjectSearchError("query is too long (max 500 characters)")

    from navin.webui.project_search import _root

    root = _root(scope)
    config = _load_semantic_config()
    if config.enabled is False:
        raise ProjectSearchError(
            "Semantic search is disabled (tools.semanticSearch.enabled=false)",
            status=400,
        )
    if config.enabled is not True and not _is_free_provider(config.provider):
        raise ProjectSearchError(
            f"Semantic search would bill against {config.provider!r}. "
            "Set tools.semanticSearch.enabled=true or use a free local provider "
            "(ollama + nomic-embed-text).",
            status=400,
        )

    from navin.config.loader import load_config
    from navin.index import get_index
    from navin.index.embeddings import EmbeddingError, build_client
    from navin.index.semantic import SemanticIndex, fuse_rankings

    try:
        providers = load_config().providers
        client = build_client(config, providers)
    except EmbeddingError as exc:
        raise ProjectSearchError(str(exc), status=503) from exc
    except Exception as exc:
        raise ProjectSearchError(
            f"semantic search config error: {exc}",
            status=503,
        ) from exc

    index = get_index(root)
    index.ensure()
    semantic = SemanticIndex(root, client, max_chunks=config.max_chunks)
    try:
        if semantic.pending_files(index):
            await semantic.sync(index)
        hits = await semantic.search(cleaned, max(limit, 1) * 2)
    except EmbeddingError as exc:
        raise ProjectSearchError(
            f"semantic search unavailable: {exc}",
            status=503,
        ) from exc

    lexical_keys = [
        f"{loc.path}:{loc.line}"
        for loc in index.search(cleaned, limit=max(limit, 1) * 2)
    ]
    by_key = {f"{hit.path}:{hit.start_line}": hit for hit in hits}
    order = fuse_rankings(list(by_key), lexical_keys, limit)

    files: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for key in order:
        hit = by_key.get(key)
        if hit is None:
            continue
        text = (
            f"[{hit.kind}] {hit.name}  "
            f"L{hit.start_line}-{hit.end_line}  score {hit.score:.2f}"
        )
        files.setdefault(hit.path, []).append(
            {
                "line": int(hit.start_line),
                "col": 1,
                "text": text,
            }
        )
        total += 1

    return {
        "root": str(root),
        "query": cleaned,
        "tool": "semantic",
        "mode": "semantic",
        "truncated": len(hits) > limit,
        "total": total,
        "files": [
            {"path": path, "matches": matches} for path, matches in files.items()
        ],
    }


def _load_semantic_config() -> SemanticSearchConfig:
    """Read ``tools.semanticSearch`` from the live config, with safe defaults."""
    try:
        from navin.config.loader import load_config

        raw = load_config().tools.semantic_search
        if isinstance(raw, SemanticSearchConfig):
            return raw
        if raw is None:
            return SemanticSearchConfig()
        if isinstance(raw, dict):
            return SemanticSearchConfig.model_validate(raw)
        data = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        return SemanticSearchConfig.model_validate(data)
    except Exception:
        return SemanticSearchConfig()
