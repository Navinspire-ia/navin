"""Tests for live agent-edit streaming (checkpoints live hook -> review store)."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from navin.agent import checkpoints as cp
from navin.agent.review import PendingReviewStore


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class RecordReturnsFirstTouchTest(unittest.TestCase):
    def test_first_record_returns_path(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("before", encoding="utf-8")
            recorder = cp.TurnRecorder()
            first = recorder.record(target)
            self.assertIsNotNone(first)
            self.assertIn(first, recorder.files)

    def test_second_record_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("before", encoding="utf-8")
            recorder = cp.TurnRecorder()
            recorder.record(target)
            self.assertIsNone(recorder.record(target))

    def test_missing_file_still_counts_as_first_touch(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "new.txt"
            recorder = cp.TurnRecorder()
            first = recorder.record(target)
            self.assertIsNotNone(first)
            self.assertIsNone(recorder.files[first])


class LiveHookTest(unittest.TestCase):
    def test_hook_fires_once_per_file_after_delay(self) -> None:
        async def scenario() -> None:
            with TemporaryDirectory() as tmp:
                target = Path(tmp) / "a.txt"
                target.write_text("v1", encoding="utf-8")
                calls: list[tuple[str, bytes | None]] = []

                recorder_token = cp.bind_checkpoint_recorder(cp.TurnRecorder())
                hook_token = cp.bind_live_edit_hook(
                    lambda path, before: calls.append((path, before))
                )
                old_delay = cp._LIVE_FLUSH_DELAY_S
                cp._LIVE_FLUSH_DELAY_S = 0.01
                try:
                    cp.record_file_before(target)
                    cp.record_file_before(target)  # second touch: no new call
                    await asyncio.sleep(0.2)
                finally:
                    cp._LIVE_FLUSH_DELAY_S = old_delay
                    cp.reset_live_edit_hook(hook_token)
                    cp.reset_checkpoint_recorder(recorder_token)

                self.assertEqual(len(calls), 1)
                path, before = calls[0]
                self.assertTrue(path.endswith("a.txt"))
                self.assertEqual(before, b"v1")

        run(scenario())

    def test_no_hook_bound_is_a_noop(self) -> None:
        async def scenario() -> None:
            with TemporaryDirectory() as tmp:
                target = Path(tmp) / "a.txt"
                target.write_text("v1", encoding="utf-8")
                recorder_token = cp.bind_checkpoint_recorder(cp.TurnRecorder())
                try:
                    cp.record_file_before(target)
                except Exception as exc:  # pragma: no cover
                    self.fail(f"record_file_before raised: {exc}")
                finally:
                    cp.reset_checkpoint_recorder(recorder_token)

        run(scenario())

    def test_hook_error_does_not_break_the_turn(self) -> None:
        async def scenario() -> None:
            with TemporaryDirectory() as tmp:
                target = Path(tmp) / "a.txt"
                target.write_text("v1", encoding="utf-8")

                def broken(_path: str, _before: bytes | None) -> None:
                    raise RuntimeError("observer bug")

                recorder_token = cp.bind_checkpoint_recorder(cp.TurnRecorder())
                hook_token = cp.bind_live_edit_hook(broken)
                old_delay = cp._LIVE_FLUSH_DELAY_S
                cp._LIVE_FLUSH_DELAY_S = 0.01
                try:
                    cp.record_file_before(target)
                    await asyncio.sleep(0.2)
                finally:
                    cp._LIVE_FLUSH_DELAY_S = old_delay
                    cp.reset_live_edit_hook(hook_token)
                    cp.reset_checkpoint_recorder(recorder_token)

        run(scenario())


class ConcurrentMergeTest(unittest.TestCase):
    def test_parallel_merges_lose_no_baseline(self) -> None:
        """Live flushes (executor threads) and end-of-turn merges must not
        clobber each other's entries in the load-modify-save cycle."""
        import concurrent.futures

        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            store = PendingReviewStore(workspace)
            session_key = "websocket:race-test"

            proj = Path(tmp) / "proj"
            proj.mkdir()
            paths = []
            for i in range(24):
                target = proj / f"f{i}.txt"
                target.write_text(f"after-{i}", encoding="utf-8")
                paths.append(str(target))

            def flush(path: str) -> None:
                # Baseline differs from current so the entry must be kept.
                store.merge_turn(session_key, {path: b"before"})

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(flush, paths))

            changes = store.list_changes(session_key)
            self.assertEqual(len(changes), 24)


class LiveFlushIntoReviewStoreTest(unittest.TestCase):
    def test_edit_appears_in_review_before_end_of_turn(self) -> None:
        """The full streaming path: record -> write -> delayed flush -> pending list."""

        async def scenario() -> None:
            with TemporaryDirectory() as tmp:
                workspace = Path(tmp) / "ws"
                workspace.mkdir()
                store = PendingReviewStore(workspace)
                session_key = "websocket:stream-test"

                target = Path(tmp) / "proj" / "main.py"
                target.parent.mkdir()
                target.write_text("print('v1')\n", encoding="utf-8")

                def live_flush(path: str, before: bytes | None) -> None:
                    store.merge_turn(session_key, {path: before})

                recorder_token = cp.bind_checkpoint_recorder(cp.TurnRecorder())
                hook_token = cp.bind_live_edit_hook(live_flush)
                old_delay = cp._LIVE_FLUSH_DELAY_S
                cp._LIVE_FLUSH_DELAY_S = 0.01
                try:
                    # What an editing tool does: record, then write.
                    cp.record_file_before(target)
                    target.write_text("print('v2')\n", encoding="utf-8")
                    await asyncio.sleep(0.3)
                finally:
                    cp._LIVE_FLUSH_DELAY_S = old_delay
                    cp.reset_live_edit_hook(hook_token)
                    cp.reset_checkpoint_recorder(recorder_token)

                changes = store.list_changes(session_key)
                self.assertEqual(len(changes), 1)
                self.assertEqual(changes[0]["status"], "modified")
                self.assertTrue(changes[0]["path"].endswith("main.py"))

        run(scenario())

    def test_unwritten_file_is_not_listed(self) -> None:
        """A flush landing before any write must not create a phantom entry."""

        async def scenario() -> None:
            with TemporaryDirectory() as tmp:
                workspace = Path(tmp) / "ws"
                workspace.mkdir()
                store = PendingReviewStore(workspace)
                session_key = "websocket:stream-test"

                target = Path(tmp) / "proj" / "main.py"
                target.parent.mkdir()
                target.write_text("print('v1')\n", encoding="utf-8")

                recorder_token = cp.bind_checkpoint_recorder(cp.TurnRecorder())
                hook_token = cp.bind_live_edit_hook(
                    lambda path, before: store.merge_turn(session_key, {path: before})
                )
                old_delay = cp._LIVE_FLUSH_DELAY_S
                cp._LIVE_FLUSH_DELAY_S = 0.01
                try:
                    cp.record_file_before(target)
                    # No write happens (tool failed / rejected).
                    await asyncio.sleep(0.3)
                finally:
                    cp._LIVE_FLUSH_DELAY_S = old_delay
                    cp.reset_live_edit_hook(hook_token)
                    cp.reset_checkpoint_recorder(recorder_token)

                self.assertEqual(store.list_changes(session_key), [])

        run(scenario())


if __name__ == "__main__":
    unittest.main()
