"""Semantic memory over notes: chunking, incremental sync, hybrid ask."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.notes import memory, store
from navin.notes.memory import NotesMemoryIndex, ask_notes, chunk_note

_DIM = 32


class FakeEmbeddingClient:
    """Deterministic bag-of-words embeddings: shared words = closer vectors."""

    model = "fake"
    dimensions = None

    def __init__(self, signature: str = "fake@native") -> None:
        self.signature = signature
        self.embed_calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        out = [0.0] * _DIM
        for word in text.lower().split():
            digest = hashlib.sha1(word.encode()).digest()
            out[digest[0] % _DIM] += 1.0
        return out


class MemoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "notes"
        self.root.mkdir()
        self.cache = base / "cache"
        self.cache.mkdir()
        patcher = mock.patch(
            "navin.index.store.cache_path",
            side_effect=lambda root: self.cache / "notes.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def note(self, title: str, markdown: str, folder: str = "") -> str:
        created = store.create_note(
            self.root, title=title, folder=folder, markdown=markdown
        )
        return created["note"]["id"]


class ChunkingTest(unittest.TestCase):
    def test_sections_become_chunks_with_headings_and_lines(self) -> None:
        body = "intro\n\n# Pricing\n\ndecision de juillet\n\n# Roadmap\n\nQ3 focus\n"
        chunks = chunk_note("Strategie", body)
        self.assertEqual([c["heading"] for c in chunks], ["", "Pricing", "Roadmap"])
        pricing = chunks[1]
        self.assertIn("decision de juillet", pricing["text"])
        self.assertGreater(pricing["line"], 1)

    def test_hash_inside_code_fence_is_not_a_heading(self) -> None:
        body = "```bash\n# not a heading\necho hi\n```\ntexte\n"
        chunks = chunk_note("Code", body)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["heading"], "")

    def test_empty_body_still_indexes_the_title(self) -> None:
        chunks = chunk_note("Juste un titre", "")
        self.assertEqual(chunks[0]["text"], "Juste un titre")

    def test_long_sections_are_split(self) -> None:
        body = "# Long\n\n" + ("paragraphe important\n\n" * 300)
        chunks = chunk_note("Long", body)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk["text"]), 1600)


class SyncTest(MemoryTestCase):
    def test_first_sync_embeds_then_second_is_a_noop(self) -> None:
        self.note("Pricing", "# Decision\n\nprix fixe a 29 euros\n")
        client = FakeEmbeddingClient()
        index = NotesMemoryIndex(self.root, client)
        stats = asyncio.run(index.sync())
        self.assertGreater(stats["embedded"], 0)
        self.assertEqual(asyncio.run(index.sync())["embedded"], 0)
        self.assertEqual(index.pending(), 0)

    def test_only_changed_notes_are_reembedded(self) -> None:
        changing = self.note("Meeting", "premiere version")
        self.note("Stable", "ne bouge pas")
        client = FakeEmbeddingClient()
        index = NotesMemoryIndex(self.root, client)
        asyncio.run(index.sync())

        store.update_note(self.root, changing, markdown="deuxieme version")
        fresh = NotesMemoryIndex(self.root, client)
        stats = asyncio.run(fresh.sync())
        self.assertEqual(stats["embedded"], 1)
        last_batch = client.embed_calls[-1]
        self.assertTrue(any("deuxieme" in text for text in last_batch))
        self.assertFalse(any("bouge" in text for text in last_batch))

    def test_deleted_notes_leave_the_index(self) -> None:
        doomed = self.note("Temporaire", "contenu")
        self.note("Gardee", "contenu")
        client = FakeEmbeddingClient()
        index = NotesMemoryIndex(self.root, client)
        asyncio.run(index.sync())
        store.delete_note(self.root, doomed)
        stats = asyncio.run(NotesMemoryIndex(self.root, client).sync())
        self.assertEqual(stats["removed"], 1)
        hits = asyncio.run(NotesMemoryIndex(self.root, client).search("Temporaire"))
        self.assertTrue(all(hit["title"] != "Temporaire" for hit in hits))

    def test_model_change_rebuilds_from_scratch(self) -> None:
        self.note("Note", "contenu")
        asyncio.run(NotesMemoryIndex(self.root, FakeEmbeddingClient()).sync())
        other = NotesMemoryIndex(self.root, FakeEmbeddingClient(signature="other@256"))
        self.assertGreater(other.pending(), 0)


class SearchTest(MemoryTestCase):
    def test_semantically_matching_chunk_ranks_first(self) -> None:
        self.note("Pricing", "# Decision\n\nabonnement mensuel vingt-neuf euros\n")
        self.note("Recettes", "# Cuisine\n\npates au pesto et basilic\n")
        client = FakeEmbeddingClient()
        index = NotesMemoryIndex(self.root, client)
        asyncio.run(index.sync())
        hits = asyncio.run(index.search("abonnement mensuel euros", limit=2))
        self.assertEqual(hits[0]["title"], "Pricing")
        self.assertGreater(hits[0]["score"], hits[-1]["score"])


class AskNotesTest(MemoryTestCase):
    def test_hybrid_ask_returns_semantic_passages(self) -> None:
        self.note("Pricing", "# Decision\n\nabonnement mensuel vingt-neuf euros\n")
        payload = asyncio.run(
            ask_notes(self.root, "abonnement mensuel", client=FakeEmbeddingClient())
        )
        self.assertTrue(payload["semantic"])
        self.assertEqual(payload["passages"][0]["title"], "Pricing")

    def test_without_embedding_endpoint_falls_back_to_lexical(self) -> None:
        self.note("Pricing", "decision de juillet sur le pricing")
        with mock.patch.object(memory, "_resolve_client", return_value=None):
            payload = asyncio.run(ask_notes(self.root, "juillet"))
        self.assertFalse(payload["semantic"])
        self.assertEqual(payload["passages"][0]["title"], "Pricing")

    def test_embedding_failure_mid_ask_degrades_to_lexical(self) -> None:
        from navin.index.embeddings import EmbeddingError

        class BrokenClient(FakeEmbeddingClient):
            async def embed(self, texts: list[str]) -> list[list[float]]:
                raise EmbeddingError("nothing is listening")

        self.note("Pricing", "decision de juillet sur le pricing")
        payload = asyncio.run(
            ask_notes(self.root, "juillet", client=BrokenClient())
        )
        self.assertFalse(payload["semantic"])
        self.assertEqual(payload["passages"][0]["title"], "Pricing")

    def test_empty_question_returns_nothing(self) -> None:
        payload = asyncio.run(ask_notes(self.root, "  "))
        self.assertEqual(payload["passages"], [])


if __name__ == "__main__":
    unittest.main()
