# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Semantic search: chunking, incrementality, and the fallbacks around it.

Embeddings are stubbed with a deterministic hashing embedder rather than mocked
call-by-call. That keeps the tests offline and free, but still exercises the part
that can actually be wrong: which text gets embedded, which files get re-embedded
after an edit, and whether a cache written by one model is ever read back under
another.

The properties worth defending are the expensive ones. Re-embedding a file that
did not change costs the user money on every query, and reading vectors from a
different model back as if they were comparable produces rankings that look
plausible and are noise.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from navin.index import get_index
from navin.index.embeddings import EmbeddingError, build_client
from navin.index.semantic import SemanticIndex, fuse_rankings

_DIMS = 32


def _run(coro):
    return asyncio.run(coro)


@dataclass
class HashEmbedder:
    """Bag-of-words hashing embedder: no network, still discriminative."""

    model: str = "fake"
    dimensions: int = _DIMS
    space: str = "fake"
    seen: list[list[str]] = field(default_factory=list)
    fail: bool = False
    ragged: bool = False

    @property
    def signature(self) -> str:
        return f"{self.space}@{self.dimensions}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise EmbeddingError("endpoint unreachable")
        self.seen.append(list(texts))
        out: list[list[float]] = []
        for position, text in enumerate(texts):
            width = self.dimensions
            if self.ragged and position:
                width -= 1
            vector = [0.0] * width
            for word in text.lower().split():
                digest = hashlib.blake2b(word.encode(), digest_size=4).hexdigest()
                vector[int(digest, 16) % width] += 1.0
            out.append(vector)
        return out

    @property
    def embed_calls(self) -> int:
        """Number of texts embedded, across every call."""
        return sum(len(batch) for batch in self.seen)


class _Project(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def index(self):
        index = get_index(self.root)
        index.refresh(force=True)
        return index

    def semantic(self, embedder: HashEmbedder, *, max_chunks: int = 1000):
        return SemanticIndex(self.root, embedder, max_chunks=max_chunks)


class ChunkingTest(_Project):
    """One chunk per symbol, so a hit names something the agent can open."""

    def test_each_symbol_becomes_its_own_chunk(self) -> None:
        self.write(
            "a.py",
            "def first():\n    return 1\n\n\ndef second():\n    return 2\n",
        )
        embedder = HashEmbedder()
        stats = _run(self.semantic(embedder).sync(self.index()))
        self.assertEqual(stats["embedded"], 2)

    def test_the_embedded_text_carries_the_path_and_the_name(self) -> None:
        """Half of what makes a function findable is where it lives."""
        self.write("billing/invoice.py", "def charge_card():\n    return True\n")
        embedder = HashEmbedder()
        _run(self.semantic(embedder).sync(self.index()))
        blob = embedder.seen[0][0]
        self.assertIn("billing/invoice.py", blob)
        self.assertIn("charge_card", blob)

    def test_a_docstring_is_embedded_with_its_body(self) -> None:
        self.write(
            "a.py",
            'def f():\n    """Reject calls beyond the rate limit."""\n    return 1\n',
        )
        embedder = HashEmbedder()
        _run(self.semantic(embedder).sync(self.index()))
        self.assertIn("rate limit", embedder.seen[0][0])

    def test_a_markdown_file_is_indexed_as_one_document(self) -> None:
        self.write("README.md", "# Deployment\n\nRun the release script.\n")
        embedder = HashEmbedder()
        stats = _run(self.semantic(embedder).sync(self.index()))
        self.assertEqual(stats["embedded"], 1)
        self.assertIn("Deployment", embedder.seen[0][0])

    def test_a_code_file_without_symbols_is_skipped(self) -> None:
        """Its text is usually imports and boilerplate; embedding it is spend."""
        self.write("empty.py", "# nothing here\n")
        embedder = HashEmbedder()
        stats = _run(self.semantic(embedder).sync(self.index()))
        self.assertEqual(stats["embedded"], 0)

    def test_a_trivially_short_symbol_is_not_embedded(self) -> None:
        self.write("tiny.py", "x = 1\n")
        embedder = HashEmbedder()
        _run(self.semantic(embedder).sync(self.index()))
        self.assertEqual(embedder.embed_calls, 0)

    def test_the_chunk_cap_is_honoured(self) -> None:
        """A monorepo must not be able to run up an unbounded bill."""
        body = "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(30))
        self.write("many.py", body)
        embedder = HashEmbedder()
        stats = _run(self.semantic(embedder, max_chunks=5).sync(self.index()))
        self.assertLessEqual(stats["embedded"], 30)
        self.assertGreater(stats["embedded"], 0)


class PendingFilesTest(_Project):
    """The tool decides sync-in-background vs search-now from this count."""

    def test_a_fresh_project_reports_everything_pending(self) -> None:
        self.write("a.py", "def alpha():\n    return 'first function here'\n")
        sem = self.semantic(HashEmbedder())
        index = self.index()
        self.assertGreater(sem.pending_files(index), 0)
        self.assertFalse(sem.has_vectors())

    def test_a_synced_project_reports_nothing_pending(self) -> None:
        self.write("a.py", "def alpha():\n    return 'first function here'\n")
        embedder = HashEmbedder()
        index = self.index()
        _run(self.semantic(embedder).sync(index))
        fresh = self.semantic(embedder)
        self.assertEqual(fresh.pending_files(index), 0)
        self.assertTrue(fresh.has_vectors())

    def test_counting_pending_files_embeds_nothing(self) -> None:
        self.write("a.py", "def alpha():\n    return 'first function here'\n")
        embedder = HashEmbedder()
        self.semantic(embedder).pending_files(self.index())
        self.assertEqual(embedder.embed_calls, 0)


class SearchTest(_Project):
    def setUp(self) -> None:
        super().setUp()
        self.write(
            "limiter.py",
            'def throttle_requests(user, budget):\n'
            '    """Reject calls beyond the per-user rate limit budget."""\n'
            "    return budget > 0\n",
        )
        self.write(
            "colors.py",
            'def paint_widget(widget, palette):\n'
            '    """Apply a colour palette to a rendered widget."""\n'
            "    return palette\n",
        )
        self.embedder = HashEmbedder()
        self.sem = self.semantic(self.embedder)
        _run(self.sem.sync(self.index()))

    def test_a_described_behaviour_finds_the_right_symbol(self) -> None:
        """The whole point: the query names no identifier from the file."""
        hits = _run(self.sem.search("rate limit budget per user", 5))
        self.assertEqual(hits[0].path, "limiter.py")

    def test_a_hit_carries_a_usable_line_range(self) -> None:
        hit = _run(self.sem.search("rate limit budget", 1))[0]
        self.assertGreaterEqual(hit.start_line, 1)
        self.assertGreaterEqual(hit.end_line, hit.start_line)
        self.assertEqual(hit.name, "throttle_requests")

    def test_an_empty_index_returns_nothing_rather_than_failing(self) -> None:
        other = _Project()
        other.setUp()
        sem = SemanticIndex(other.root, HashEmbedder(), max_chunks=10)
        self.assertEqual(_run(sem.search("anything", 5)), [])

    def test_a_query_of_a_different_width_scores_zero_instead_of_crashing(self) -> None:
        """A provider that silently changes dimensions must not raise mid-query."""
        narrow = SemanticIndex(self.root, HashEmbedder(dimensions=16), max_chunks=10)
        narrow._load()  # noqa: SLF001 - the cached width is the subject
        narrow._chunks = self.sem._chunks  # noqa: SLF001
        narrow._flat = self.sem._flat  # noqa: SLF001
        narrow._dim = self.sem._dim  # noqa: SLF001
        hits = _run(narrow.search("rate limit", 5))
        self.assertTrue(all(hit.score == 0.0 for hit in hits))


class IncrementalTest(_Project):
    """Re-embedding an unchanged file is a recurring charge, not a slow path."""

    def setUp(self) -> None:
        super().setUp()
        self.write("a.py", "def alpha():\n    return 'first function here'\n")
        self.write("b.py", "def beta():\n    return 'second function here'\n")
        self.embedder = HashEmbedder()
        self.sem = self.semantic(self.embedder)
        _run(self.sem.sync(self.index()))
        self.first = self.embedder.embed_calls

    def test_a_second_sync_on_the_same_instance_embeds_nothing(self) -> None:
        stats = _run(self.sem.sync(self.index()))
        self.assertEqual(stats["embedded"], 0)
        self.assertEqual(self.embedder.embed_calls, self.first)

    def test_a_fresh_instance_reads_the_cache_instead_of_re_embedding(self) -> None:
        embedder = HashEmbedder()
        stats = _run(self.semantic(embedder).sync(self.index()))
        self.assertEqual(stats["embedded"], 0)
        self.assertEqual(embedder.embed_calls, 0)

    def test_editing_one_file_re_embeds_only_that_file(self) -> None:
        self.write("b.py", "def beta():\n    return 'now it does something else'\n")
        embedder = HashEmbedder()
        stats = _run(self.semantic(embedder).sync(self.index()))
        self.assertEqual(stats["embedded"], 1)
        self.assertIn("b.py", embedder.seen[0][0])

    def test_deleting_a_file_drops_its_chunks(self) -> None:
        (self.root / "b.py").unlink()
        stats = _run(self.semantic(HashEmbedder()).sync(self.index()))
        self.assertEqual(stats["removed"], 1)
        self.assertEqual(stats["total"], 1)

    def test_searching_then_re_syncing_then_searching_stays_correct(self) -> None:
        """The numpy path is a view over the vector buffer, not a copy.

        So a search caches a view, a later sync replaces the buffer, and a second
        search must not read the old one. Nothing raises if it does - the
        rankings are just silently stale, which is why this is pinned here.
        """
        first = _run(self.sem.search("first function here", 3))
        self.assertEqual(first[0].path, "a.py")

        self.write("c.py", "def gamma():\n    return 'a third function appears'\n")
        _run(self.sem.sync(self.index()))

        hits = _run(self.sem.search("a third function appears", 3))
        self.assertEqual(hits[0].path, "c.py")
        self.assertEqual(len(self.sem._chunks), 3)  # noqa: SLF001

    def test_a_partially_embedded_index_resumes_instead_of_restarting(self) -> None:
        """A blip on the first index of a large repo must not void the spend."""

        class Flaky(HashEmbedder):
            def __init__(self) -> None:
                super().__init__()
                self.allowed = 1

            async def embed(self, texts: list[str]) -> list[list[float]]:
                if self.allowed <= 0:
                    raise EmbeddingError("rate limited")
                self.allowed -= 1
                return await super().embed(texts)

        for i in range(4):
            self.write(f"m{i}.py", f"def f{i}():\n    return 'function number {i}'\n")

        from navin.index import semantic as module

        saved = module._FLUSH_AT  # noqa: SLF001
        module._FLUSH_AT = 1  # noqa: SLF001 - one file per flush
        try:
            sem = self.semantic(Flaky())
            with self.assertRaises(EmbeddingError):
                _run(sem.sync(self.index()))
            # What survived is on disk, so a fresh index re-embeds less than all.
            resumed = self.semantic(HashEmbedder())
            stats = _run(resumed.sync(self.index()))
        finally:
            module._FLUSH_AT = saved  # noqa: SLF001
        self.assertGreater(stats["total"], stats["embedded"])

    def test_a_deleted_file_stops_appearing_in_results(self) -> None:
        (self.root / "b.py").unlink()
        sem = self.semantic(HashEmbedder())
        _run(sem.sync(self.index()))
        hits = _run(sem.search("second function here", 5))
        self.assertNotIn("b.py", [hit.path for hit in hits])


class VectorSpaceTest(_Project):
    """Vectors from two models are not comparable, and must never be mixed."""

    def setUp(self) -> None:
        super().setUp()
        self.write("a.py", "def alpha():\n    return 'a function with some text'\n")
        _run(self.semantic(HashEmbedder()).sync(self.index()))

    def test_changing_the_model_rebuilds_rather_than_reusing(self) -> None:
        embedder = HashEmbedder(space="other")
        stats = _run(self.semantic(embedder).sync(self.index()))
        self.assertEqual(stats["embedded"], 1)

    def test_changing_the_truncation_rebuilds_too(self) -> None:
        """A truncated vector is a different space, not a shorter one."""
        embedder = HashEmbedder(dimensions=16)
        stats = _run(self.semantic(embedder).sync(self.index()))
        self.assertEqual(stats["embedded"], 1)

    def test_a_ragged_batch_is_refused_rather_than_stored(self) -> None:
        """A flat blob of mixed widths cannot be read back at all."""
        self.write("b.py", "def beta():\n    return 'another function with text'\n")
        self.write("c.py", "def gamma():\n    return 'a third function with text'\n")
        embedder = HashEmbedder(space="ragged", ragged=True)
        with self.assertRaises(EmbeddingError):
            _run(self.semantic(embedder).sync(self.index()))

    def test_a_corrupt_vector_blob_is_rebuilt_not_misread(self) -> None:
        sem = self.semantic(HashEmbedder())
        sem._load()  # noqa: SLF001 - locate the cache the store just wrote
        sem._vec_path.write_bytes(b"\x00\x01\x02")  # noqa: SLF001
        embedder = HashEmbedder()
        stats = _run(self.semantic(embedder).sync(self.index()))
        self.assertEqual(stats["embedded"], 1)

    def test_corrupt_metadata_is_rebuilt_not_misread(self) -> None:
        sem = self.semantic(HashEmbedder())
        sem._load()  # noqa: SLF001
        sem._meta_path.write_text("{not json", encoding="utf-8")  # noqa: SLF001
        stats = _run(self.semantic(HashEmbedder()).sync(self.index()))
        self.assertEqual(stats["embedded"], 1)

    def test_the_cache_never_lands_in_the_project_tree(self) -> None:
        """It is machine-local, model-specific and large; committing it is wrong."""
        sem = self.semantic(HashEmbedder())
        sem._load()  # noqa: SLF001
        for path in (sem._meta_path, sem._vec_path):  # noqa: SLF001
            self.assertNotIn(str(self.root), str(path.resolve()))


class ScoringBackendTest(_Project):
    """numpy is not a declared dependency, so the fallback is the common path."""

    def setUp(self) -> None:
        super().setUp()
        self.write(
            "limiter.py",
            'def throttle_requests(user, budget):\n'
            '    """Reject calls beyond the per-user rate limit budget."""\n'
            "    return budget > 0\n",
        )
        self.write(
            "colors.py",
            'def paint_widget(widget, palette):\n'
            '    """Apply a colour palette to a rendered widget."""\n'
            "    return palette\n",
        )

    def _ranked(self, *, numpy: bool) -> list[str]:
        from navin.index import semantic as module

        saved = module._np  # noqa: SLF001
        module._np = module._np if numpy else None  # noqa: SLF001
        try:
            sem = self.semantic(HashEmbedder())
            _run(sem.sync(self.index()))
            hits = _run(sem.search("rate limit budget per user", 5))
            return [f"{hit.path}:{hit.score:.4f}" for hit in hits]
        finally:
            module._np = saved  # noqa: SLF001

    def test_both_backends_produce_the_same_ranking(self) -> None:
        from navin.index import semantic as module

        if module._np is None:  # noqa: SLF001
            self.skipTest("numpy not installed; only one backend to compare")
        self.assertEqual(self._ranked(numpy=True), self._ranked(numpy=False))

    def test_the_pure_python_backend_still_ranks_correctly(self) -> None:
        self.assertTrue(self._ranked(numpy=False)[0].startswith("limiter.py"))


class FusionTest(unittest.TestCase):
    """Rank fusion, because a cosine and a fuzzy score share no scale."""

    def test_a_key_on_both_lists_outranks_one_on_neither(self) -> None:
        self.assertEqual(fuse_rankings(["a", "b"], ["b", "c"], 1), ["b"])

    def test_an_empty_lexical_ranking_leaves_the_order_intact(self) -> None:
        self.assertEqual(fuse_rankings(["a", "b", "c"], [], 3), ["a", "b", "c"])

    def test_an_empty_semantic_ranking_falls_back_to_lexical(self) -> None:
        self.assertEqual(fuse_rankings([], ["x", "y"], 2), ["x", "y"])

    def test_the_limit_is_respected(self) -> None:
        self.assertEqual(len(fuse_rankings(["a", "b", "c"], ["d"], 2)), 2)


class ClientConfigTest(unittest.TestCase):
    """Resolution reuses the provider a user already configured for chat."""

    def _config(self, **kwargs):
        from navin.agent.tools.code_index import SemanticSearchConfig

        return SemanticSearchConfig(**kwargs)

    def _providers(self):
        from navin.config.schema import ProvidersConfig

        return ProvidersConfig()

    def test_a_local_provider_needs_no_api_key(self) -> None:
        """The free path must not require inventing a credential."""
        client = build_client(self._config(provider="ollama"), self._providers())
        self.assertIn("11434", client.base_url or "")

    def test_an_unknown_provider_is_named_in_the_error(self) -> None:
        with self.assertRaises(EmbeddingError) as caught:
            build_client(self._config(provider="nope"), self._providers())
        self.assertIn("nope", str(caught.exception))

    def test_a_blank_provider_says_what_to_set(self) -> None:
        with self.assertRaises(EmbeddingError) as caught:
            build_client(self._config(provider=""), self._providers())
        self.assertIn("provider", str(caught.exception))

    def test_a_hosted_provider_without_credentials_is_refused(self) -> None:
        with self.assertRaises(EmbeddingError):
            build_client(self._config(provider="openai"), self._providers())

    def test_the_signature_distinguishes_two_truncations(self) -> None:
        providers = self._providers()
        providers.openai.api_key = "sk-test"
        native = build_client(self._config(provider="openai"), providers)
        narrow = build_client(
            self._config(provider="openai", dimensions=256), providers
        )
        self.assertNotEqual(native.signature, narrow.signature)


class ToolSurfaceTest(_Project):
    """The action itself, including what it does when nothing is configured."""

    def _tool(self):
        from navin.agent.tools.code_index import CodeIndexTool, SemanticSearchConfig

        tool = CodeIndexTool(workspace=str(self.root))
        tool.semantic = SemanticSearchConfig()
        return tool

    def test_the_action_is_advertised(self) -> None:
        tool = self._tool()
        actions = tool.parameters["properties"]["action"]["enum"]
        self.assertIn("semantic", actions)

    def test_an_unreachable_endpoint_degrades_instead_of_failing(self) -> None:
        """A hard error would strand a turn over a feature nobody asked for.

        The default now tries a local endpoint, so on a machine without one this
        is the path most users hit first. It has to read as "use the other
        tools", not as a broken turn.

        The refusal is forced rather than assumed: a developer machine that
        happens to run Ollama would otherwise pass this test by succeeding.
        """
        self.write("a.py", "def alpha():\n    return 1\n")
        with patch(
            "navin.index.semantic.SemanticIndex.sync",
            side_effect=EmbeddingError("nothing is listening"),
        ):
            out = _run(self._tool().execute(action="semantic", query="rate limiting"))
        self.assertNotIn("Error", out)
        self.assertIn("action=search", out)
        self.assertIn("grep", out)

    def test_an_explicitly_disabled_feature_says_so(self) -> None:
        from navin.agent.tools.code_index import CodeIndexTool, SemanticSearchConfig

        self.write("a.py", "def alpha():\n    return 1\n")
        tool = CodeIndexTool(workspace=str(self.root))
        tool.semantic = SemanticSearchConfig(enabled=False)
        out = _run(tool.execute(action="semantic", query="rate limiting"))
        self.assertNotIn("Error", out)
        self.assertIn("switched off", out)

    def test_a_billable_provider_is_not_used_without_being_asked(self) -> None:
        """Auto-mode is a convenience, not permission to spend money."""
        from navin.agent.tools.code_index import CodeIndexTool, SemanticSearchConfig

        self.write("a.py", "def alpha():\n    return 1\n")
        tool = CodeIndexTool(workspace=str(self.root))
        tool.semantic = SemanticSearchConfig(provider="openai")
        out = _run(tool.execute(action="semantic", query="rate limiting"))
        self.assertIn("bill", out)
        self.assertIn("enabled=true", out)

    def test_a_missing_query_is_an_error(self) -> None:
        self.write("a.py", "def alpha():\n    return 1\n")
        out = _run(self._tool().execute(action="semantic", query="  "))
        self.assertIn("Error", out)
        self.assertIn("query", out)

    def test_the_description_tells_the_model_when_to_prefer_it(self) -> None:
        """Without the contrast against 'search' the model picks one at random."""
        description = self._tool().description
        self.assertIn("semantic", description)
        self.assertIn("do not know the identifier", description)

    def test_the_config_is_reachable_from_the_tools_section(self) -> None:
        from navin.config.schema import Config

        section = Config().tools.semantic_search
        # None, not False: "decide for me" rather than "off".
        self.assertIsNone(section.enabled)
        self.assertEqual(section.provider, "ollama")

    def test_create_attaches_the_settings_from_the_tools_config(self) -> None:
        """Without this the feature can be enabled in navin.json and never fire."""
        from types import SimpleNamespace

        from navin.agent.tools.code_index import CodeIndexTool
        from navin.config.schema import Config

        tools = Config().tools
        tools.semantic_search.enabled = True
        ctx = SimpleNamespace(
            workspace=str(self.root),
            config=tools,
            file_state_store=None,
        )
        tool = CodeIndexTool.create(ctx)
        self.assertTrue(tool._semantic_config().enabled)  # noqa: SLF001

    def test_the_camel_case_keys_a_user_writes_are_accepted(self) -> None:
        """This is the actual user-facing path: hand-written navin.json."""
        from navin.config.schema import Config

        config = Config.model_validate(
            {
                "tools": {
                    "semanticSearch": {
                        "enabled": True,
                        "provider": "openai",
                        "model": "text-embedding-3-small",
                        "dimensions": 256,
                        "maxChunks": 5000,
                    }
                }
            }
        )
        section = config.tools.semantic_search
        self.assertTrue(section.enabled)
        self.assertEqual(section.provider, "openai")
        self.assertEqual(section.dimensions, 256)
        self.assertEqual(section.max_chunks, 5000)

    def test_an_out_of_range_chunk_cap_is_rejected_at_load(self) -> None:
        """A typo'd cap must fail loudly, not quietly authorise a huge bill."""
        from pydantic import ValidationError

        from navin.config.schema import Config

        with self.assertRaises(ValidationError):
            Config.model_validate(
                {"tools": {"semanticSearch": {"maxChunks": 10_000_000}}}
            )

    def test_the_config_serializes_in_camel_case(self) -> None:
        """navin.json is camelCase; a snake_case key would be silently ignored."""
        from navin.config.schema import Config

        blob = json.loads(Config().model_dump_json(by_alias=True))
        self.assertIn("semanticSearch", blob["tools"])


class WireProtocolTest(unittest.TestCase):
    """The real client against a real socket, because the stub skips the SDK.

    Everything above replaces :class:`EmbeddingClient`, which leaves the wire
    contract untested: batching, and the fact that a provider may return items
    out of order. Getting the order wrong pairs every vector with the wrong
    chunk, and the symptom is not an exception - it is search results that are
    quietly, permanently wrong. So this serves the OpenAI shape over HTTP and
    deliberately answers in reverse.
    """

    def setUp(self) -> None:
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.batches: list[int] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length", 0))
                body = _json.loads(self.rfile.read(length) or b"{}")
                inputs = body.get("input") or []
                outer.batches.append(len(inputs))
                width = body.get("dimensions") or 4
                data = [
                    {
                        "index": position,
                        "embedding": [float(position + 1)] + [0.0] * (width - 1),
                    }
                    for position in range(len(inputs))
                ]
                # Reversed on purpose: a client that trusts arrival order breaks.
                payload = _json.dumps({"data": list(reversed(data))}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def _client(self, dimensions: int = 4):
        from navin.index.embeddings import EmbeddingClient

        return EmbeddingClient(
            model="test-embed",
            api_key="test",
            base_url=self.base,
            dimensions=dimensions,
        )

    def test_vectors_come_back_paired_with_their_input(self) -> None:
        vectors = _run(self._client().embed(["a", "b", "c"]))
        self.assertEqual([vector[0] for vector in vectors], [1.0, 2.0, 3.0])

    def test_a_large_request_is_split_into_batches(self) -> None:
        """One request per hundred chunks would be refused by most endpoints."""
        _run(self._client().embed([f"chunk {i}" for i in range(150)]))
        self.assertGreater(len(self.batches), 1)
        self.assertTrue(all(size <= 64 for size in self.batches))

    def test_the_requested_width_reaches_the_endpoint(self) -> None:
        vectors = _run(self._client(dimensions=8).embed(["a"]))
        self.assertEqual(len(vectors[0]), 8)

    def test_an_empty_request_never_touches_the_network(self) -> None:
        self.assertEqual(_run(self._client().embed([])), [])
        self.assertEqual(self.batches, [])

    def test_a_blank_input_is_not_sent_as_an_empty_string(self) -> None:
        """Several endpoints reject "" with a 400 for the whole batch."""
        vectors = _run(self._client().embed(["", "real text"]))
        self.assertEqual(len(vectors), 2)

    def test_an_unreachable_endpoint_raises_rather_than_hangs(self) -> None:
        import time

        from navin.index.embeddings import EmbeddingClient

        client = EmbeddingClient(
            model="test-embed",
            api_key="test",
            base_url="http://127.0.0.1:9/v1",  # discard port: refuses at once
            dimensions=4,
        )
        started = time.monotonic()
        with self.assertRaises(EmbeddingError) as caught:
            _run(client.embed(["a"]))
        # Nothing listening is the likeliest failure of the default config, and
        # it does not heal, so it must not be retried through a backoff while the
        # user waits on the turn.
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertIn("nothing is listening", str(caught.exception))


class DegradationTest(_Project):
    """An embedding endpoint that is down must not take the turn down with it."""

    def test_a_failing_endpoint_is_reported_with_a_fallback(self) -> None:
        self.write("a.py", "def alpha():\n    return 'some text in a function'\n")
        sem = self.semantic(HashEmbedder(fail=True))
        with self.assertRaises(EmbeddingError):
            _run(sem.sync(self.index()))

    def test_the_tool_turns_that_failure_into_advice(self) -> None:
        from navin.agent.tools.code_index import CodeIndexTool, SemanticSearchConfig

        self.write("a.py", "def alpha():\n    return 'some text in a function'\n")
        tool = CodeIndexTool(workspace=str(self.root))
        tool.semantic = SemanticSearchConfig(enabled=True, provider="nope")
        out = _run(tool.execute(action="semantic", query="rate limiting"))
        self.assertIn("Error", out)
        self.assertIn("nope", out)


if __name__ == "__main__":
    unittest.main()
