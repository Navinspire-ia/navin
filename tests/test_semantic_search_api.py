"""WebUI semantic search payload (DevSearchPanel)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navin.agent.tools.code_index import SemanticSearchConfig
from navin.index.semantic import SemanticHit
from navin.webui.project_search import ProjectSearchError
from navin.webui.semantic_search_api import semantic_search_payload


def _scope(tmp_path):
    return SimpleNamespace(project_path=str(tmp_path))


@pytest.mark.asyncio
async def test_semantic_payload_maps_hits_to_project_search_shape(tmp_path):
    (tmp_path / "auth.py").write_text("def login():\n    pass\n", encoding="utf-8")
    hit = SemanticHit(
        path="auth.py",
        name="login",
        kind="function",
        start_line=1,
        end_line=2,
        score=0.91,
    )
    semantic = MagicMock()
    semantic.pending_files.return_value = 0
    semantic.search = AsyncMock(return_value=[hit])
    index = MagicMock()
    index.root = tmp_path
    index.search.return_value = []

    with (
        patch(
            "navin.webui.semantic_search_api._load_semantic_config",
            return_value=SemanticSearchConfig(enabled=True, provider="ollama"),
        ),
        patch(
            "navin.index.embeddings.build_client",
            return_value=MagicMock(),
        ),
        patch("navin.index.get_index", return_value=index),
        patch(
            "navin.index.semantic.SemanticIndex",
            return_value=semantic,
        ),
        patch(
            "navin.config.loader.load_config",
            return_value=SimpleNamespace(providers={}),
        ),
    ):
        payload = await semantic_search_payload(_scope(tmp_path), "user login")

    assert payload["mode"] == "semantic"
    assert payload["tool"] == "semantic"
    assert payload["total"] == 1
    assert payload["files"][0]["path"] == "auth.py"
    match = payload["files"][0]["matches"][0]
    assert match["line"] == 1
    assert "login" in match["text"]
    assert "0.91" in match["text"]


@pytest.mark.asyncio
async def test_semantic_disabled_raises(tmp_path):
    with patch(
        "navin.webui.semantic_search_api._load_semantic_config",
        return_value=SemanticSearchConfig(enabled=False),
    ):
        with pytest.raises(ProjectSearchError) as ctx:
            await semantic_search_payload(_scope(tmp_path), "auth")
    assert ctx.value.status == 400
    assert "disabled" in ctx.value.message.lower()


@pytest.mark.asyncio
async def test_empty_query_raises(tmp_path):
    with pytest.raises(ProjectSearchError):
        await semantic_search_payload(_scope(tmp_path), "   ")


@pytest.mark.asyncio
async def test_paid_provider_requires_explicit_enable(tmp_path):
    with patch(
        "navin.webui.semantic_search_api._load_semantic_config",
        return_value=SemanticSearchConfig(enabled=None, provider="openai"),
    ):
        with pytest.raises(ProjectSearchError) as ctx:
            await semantic_search_payload(_scope(tmp_path), "auth")
    assert ctx.value.status == 400
    assert "bill" in ctx.value.message.lower()
