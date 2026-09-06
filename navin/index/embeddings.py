"""Embedding client for semantic code search.

Speaks the OpenAI ``/v1/embeddings`` shape, which is what Ollama, LM Studio,
vLLM, Azure OpenAI and OpenAI itself all expose. That is a deliberate narrowing:
Anthropic has no embedding endpoint, so a navin user on Claude needs some second
source anyway, and one wire format that every local runner already implements
means the zero-cost path (``ollama pull nomic-embed-text``) works without a new
dependency or a per-vendor client.

Nothing here is imported unless semantic search is actually configured, so a user
who never enables it pays neither the import nor the network cost.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger

# Providers cap both the number of inputs per call and the tokens per input.
# These are conservative enough for every endpoint above, including the local
# ones, whose default context is far smaller than a hosted model's.
_MAX_BATCH = 64
_MAX_INPUT_CHARS = 8_000
_ATTEMPTS = 3
_BACKOFF_S = 1.5
# Bounds the wait a user can hit mid-turn from a wrong base_url: this budget,
# times _ATTEMPTS, and no more - see the max_retries note in _client.
_TIMEOUT_S = 30.0


class EmbeddingError(RuntimeError):
    """Embedding generation failed; the caller degrades instead of crashing."""


@dataclass(frozen=True, slots=True)
class EmbeddingClient:
    """Configured endpoint for turning text into vectors."""

    model: str
    api_key: str
    base_url: str | None
    dimensions: int | None

    @property
    def signature(self) -> str:
        """Identity of the vector space.

        Vectors from two models are not comparable, and neither are two
        truncations of the same model, so the cache keys on this and rebuilds
        rather than silently mixing spaces and returning nonsense rankings.
        """
        return f"{self.model}@{self.dimensions or 'native'}"

    def _client(self):
        from openai import AsyncOpenAI

        # Local runners accept any non-empty key; requiring a real one would
        # block the free path for no benefit.
        #
        # max_retries=0 because the loop below already retries. Left at the
        # default the two layers multiply, and a refused connection - a typo in
        # base_url, or Ollama not running - costs nine attempts before the agent
        # is told anything.
        return AsyncOpenAI(
            api_key=self.api_key or "local",
            base_url=self.base_url,
            timeout=_TIMEOUT_S,
            max_retries=0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts``, preserving order.

        Raises :class:`EmbeddingError` rather than returning a short list: a
        caller that silently accepted fewer vectors than texts would associate
        every vector with the wrong chunk.
        """
        if not texts:
            return []
        client = self._client()
        out: list[list[float]] = []
        for start in range(0, len(texts), _MAX_BATCH):
            batch = [t[:_MAX_INPUT_CHARS] or " " for t in texts[start : start + _MAX_BATCH]]
            out.extend(await self._embed_batch(client, batch))
        if len(out) != len(texts):
            raise EmbeddingError(
                f"endpoint returned {len(out)} vectors for {len(texts)} inputs"
            )
        return out

    async def _embed_batch(self, client, batch: list[str]) -> list[list[float]]:
        kwargs = {"model": self.model, "input": batch}
        if self.dimensions:
            kwargs["dimensions"] = self.dimensions

        last: Exception | None = None
        for attempt in range(_ATTEMPTS):
            try:
                response = await client.embeddings.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - provider SDK error surface
                last = exc
                message = str(exc).lower()
                # A model or dimension the endpoint refuses will refuse again;
                # only transport and rate limits are worth another round trip.
                if any(w in message for w in ("not found", "unknown model", "invalid")):
                    break
                if _connection_refused(exc):
                    # Nothing is listening - the default config points at a local
                    # Ollama that may simply not be running. That does not heal in
                    # a few seconds, and the user is waiting on this turn.
                    break
                if attempt + 1 < _ATTEMPTS:
                    await asyncio.sleep(_BACKOFF_S * (attempt + 1))
                continue
            data = sorted(response.data, key=lambda item: item.index)
            return [list(item.embedding) for item in data]

        if _connection_refused(last):
            raise EmbeddingError(
                f"nothing is listening at {self.base_url or 'the configured endpoint'} "
                f"- start the embedding server or fix tools.semanticSearch"
            )
        raise EmbeddingError(f"embedding request failed: {last}")


def _connection_refused(exc: Exception | None) -> bool:
    """Whether ``exc`` was caused by nothing listening on the other end.

    The SDK wraps the OS error, so the cause chain is what carries the answer.
    """
    seen = 0
    current: BaseException | None = exc
    while current is not None and seen < 5:
        if isinstance(current, ConnectionRefusedError):
            return True
        current = current.__cause__ or current.__context__
        seen += 1
    return False


def build_client(config, providers) -> EmbeddingClient:
    """Resolve an :class:`EmbeddingClient` from navin config.

    The provider name refers to an entry already in ``providers``, so the key and
    base URL a user configured for chat are reused rather than duplicated.
    """
    name = (config.provider or "").strip()
    if not name:
        raise EmbeddingError(
            "semantic search needs tools.semanticSearch.provider (for example "
            "'ollama' for a local model, or 'openai')"
        )
    provider = getattr(providers, name, None)
    if provider is None:
        raise EmbeddingError(f"unknown provider {name!r} for semantic search")

    base_url = provider.api_base or _default_base(name)
    if not base_url and not provider.api_key:
        raise EmbeddingError(
            f"provider {name!r} has neither an api_key nor an api_base configured"
        )
    logger.debug("Semantic search embeddings via {} model {}", name, config.model)
    return EmbeddingClient(
        model=config.model,
        api_key=provider.api_key or "",
        base_url=base_url,
        dimensions=config.dimensions or None,
    )


def _default_base(name: str) -> str | None:
    """Base URL for local runners, whose ports are conventional."""
    return {
        "ollama": "http://localhost:11434/v1",
        "lm_studio": "http://localhost:1234/v1",
    }.get(name)
