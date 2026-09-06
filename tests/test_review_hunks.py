"""Per-hunk accept and reject.

The whole design rests on one identity: the pending change is exactly
``baseline + every hunk``. Accepting a hunk advances the baseline, rejecting one
rewrites the file without it, and both are the same splice. These tests exist to
keep that identity true - especially the convergence properties, since a partial
decision that does not add up to the whole-file decision would leave files in
states no button can clear.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.agent.hunks import (
    HunkConflictError,
    apply_hunks,
    compute_hunks,
    resolve_hunk,
)
from navin.agent.review import PendingReviewStore
from navin.webui.review_api import ReviewApiError, review_action_payload

SESSION = "websocket:chat-1"


class HunkEngineTest(unittest.TestCase):
    """Grouping and splicing, independent of any storage."""

    def test_distant_changes_are_separate_decisions(self) -> None:
        base = "".join(f"l{i}\n" for i in range(20))
        cur = base.replace("l1\n", "L1\n").replace("l15\n", "L15\n")
        self.assertEqual(len(compute_hunks(base, cur)), 2)

    def test_adjacent_changes_are_one_decision(self) -> None:
        """Splitting these would offer a choice that cannot be applied coherently."""
        self.assertEqual(len(compute_hunks("a\nb\nc\n", "a\nX\nY\n")), 1)

    def test_identical_content_has_no_hunks(self) -> None:
        self.assertEqual(compute_hunks("a\n", "a\n"), [])

    def test_selecting_nothing_rebuilds_the_baseline(self) -> None:
        base, cur = "a\nb\nc\n", "a\nB\nc\n"
        self.assertEqual(apply_hunks(base, cur, set()), base)

    def test_selecting_everything_rebuilds_the_current_content(self) -> None:
        base = "".join(f"l{i}\n" for i in range(20))
        cur = base.replace("l1\n", "L1\n").replace("l15\n", "L15\n")
        ids = {hunk.id for hunk in compute_hunks(base, cur)}
        self.assertEqual(apply_hunks(base, cur, ids), cur)

    def test_one_hunk_applies_without_the_other(self) -> None:
        base = "".join(f"l{i}\n" for i in range(20))
        cur = base.replace("l1\n", "L1\n").replace("l15\n", "L15\n")
        hunks = compute_hunks(base, cur)
        out = apply_hunks(base, cur, {hunks[0].id})
        self.assertIn("L1\n", out)
        self.assertIn("l15\n", out)

    def test_crlf_survives_a_partial_apply(self) -> None:
        """Re-joining with "\\n" would rewrite every line ending on Windows."""
        base, cur = "a\r\nb\r\nc\r\n", "a\r\nX\r\nc\r\n"
        ids = {hunk.id for hunk in compute_hunks(base, cur)}
        self.assertEqual(apply_hunks(base, cur, ids), cur)

    def test_a_missing_final_newline_is_not_invented(self) -> None:
        base, cur = "a\nb", "a\nB"
        ids = {hunk.id for hunk in compute_hunks(base, cur)}
        self.assertEqual(apply_hunks(base, cur, ids), cur)

    def test_a_pure_insertion_is_a_hunk(self) -> None:
        base, cur = "a\nc\n", "a\nb\nc\n"
        hunks = compute_hunks(base, cur)
        self.assertEqual(len(hunks), 1)
        self.assertEqual((hunks[0].added, hunks[0].deleted), (1, 0))

    def test_a_pure_deletion_is_a_hunk(self) -> None:
        base, cur = "a\nb\nc\n", "a\nc\n"
        hunks = compute_hunks(base, cur)
        self.assertEqual(len(hunks), 1)
        self.assertEqual((hunks[0].added, hunks[0].deleted), (0, 1))

    def test_an_id_tracks_content_not_just_position(self) -> None:
        """Otherwise a click could revert whatever landed at index 3 meanwhile."""
        base = "a\nb\nc\n"
        first = compute_hunks(base, "a\nX\nc\n")[0].id
        second = compute_hunks(base, "a\nY\nc\n")[0].id
        self.assertNotEqual(first, second)

    def test_an_unknown_id_is_refused_rather_than_ignored(self) -> None:
        hunks = compute_hunks("a\n", "b\n")
        with self.assertRaises(HunkConflictError):
            resolve_hunk(hunks, "0:deadbeef")


class _StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.store = PendingReviewStore(self.root)

    def track(self, rel: str, before: str | None, after: str | None) -> str:
        """Put a file in review with the given before/after state."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        baseline: bytes | None = None
        if before is not None:
            path.write_text(before, encoding="utf-8")
            baseline = path.read_bytes()
        if after is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(after, encoding="utf-8")
        self.store.merge_turn(SESSION, {str(path): baseline})
        return str(path)

    def hunks(self, path: str) -> list[dict]:
        payload = self.store.file_payload(SESSION, path)
        assert payload is not None
        return payload["hunks"]

    def text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")


class HunkPayloadTest(_StoreCase):
    def test_a_modified_file_exposes_its_hunks(self) -> None:
        base = "".join(f"l{i}\n" for i in range(20))
        path = self.track("a.py", base, base.replace("l1\n", "L1\n").replace("l15\n", "L15\n"))
        self.assertEqual(len(self.hunks(path)), 2)

    def test_a_created_file_is_one_hunk(self) -> None:
        path = self.track("new.py", None, "x\ny\n")
        payload = self.store.file_payload(SESSION, path)
        assert payload is not None
        self.assertEqual(payload["status"], "created")
        self.assertEqual(len(payload["hunks"]), 1)

    def test_a_deleted_file_offers_no_hunks(self) -> None:
        """There is no current content to carve up; reject restores the file."""
        path = self.track("gone.py", "a\n", None)
        payload = self.store.file_payload(SESSION, path)
        assert payload is not None
        self.assertEqual(payload["status"], "deleted")
        self.assertEqual(payload["hunks"], [])

    def test_a_binary_file_offers_no_hunks(self) -> None:
        path = self.root / "blob.bin"
        # Invalid UTF-8, not merely unprintable: NUL and 0x01 decode fine and
        # would leave the file text, which is what "binary" actually means here.
        path.write_bytes(b"\xff\xfebefore")
        before = path.read_bytes()
        path.write_bytes(b"\xff\xfeafter")
        self.store.merge_turn(SESSION, {str(path): before})
        payload = self.store.file_payload(SESSION, str(path))
        assert payload is not None
        self.assertTrue(payload["binary"])
        self.assertEqual(payload["hunks"], [])


class AcceptHunkTest(_StoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.base = "".join(f"l{i}\n" for i in range(20))
        self.current = self.base.replace("l1\n", "L1\n").replace("l15\n", "L15\n")
        self.path = self.track("a.py", self.base, self.current)

    def test_the_file_on_disk_is_untouched(self) -> None:
        """Accepting is a bookkeeping move; the content was already applied."""
        self.store.accept_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        self.assertEqual(self.text(self.path), self.current)

    def test_the_baseline_absorbs_the_accepted_hunk(self) -> None:
        self.store.accept_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        payload = self.store.file_payload(SESSION, self.path)
        assert payload is not None
        self.assertIn("L1\n", payload["baseline"])
        self.assertIn("l15\n", payload["baseline"])

    def test_the_other_hunk_stays_pending(self) -> None:
        result = self.store.accept_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        self.assertEqual(result["remaining"], 1)
        self.assertFalse(result["done"])
        self.assertEqual(len(self.hunks(self.path)), 1)

    def test_accepting_every_hunk_clears_the_file(self) -> None:
        """It has to land exactly where "accept this file" lands."""
        self.store.accept_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        result = self.store.accept_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        self.assertTrue(result["done"])
        self.assertEqual(self.store.list_changes(SESSION), [])
        self.assertEqual(self.text(self.path), self.current)

    def test_a_stale_id_changes_nothing(self) -> None:
        with self.assertRaises(HunkConflictError):
            self.store.accept_hunk(SESSION, self.path, "0:deadbeef")
        self.assertEqual(len(self.hunks(self.path)), 2)


class RejectHunkTest(_StoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.base = "".join(f"l{i}\n" for i in range(20))
        self.current = self.base.replace("l1\n", "L1\n").replace("l15\n", "L15\n")
        self.path = self.track("a.py", self.base, self.current)

    def test_only_the_rejected_hunk_is_undone(self) -> None:
        self.store.reject_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        text = self.text(self.path)
        self.assertIn("l1\n", text)
        self.assertIn("L15\n", text)

    def test_the_baseline_is_left_alone(self) -> None:
        self.store.reject_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        payload = self.store.file_payload(SESSION, self.path)
        assert payload is not None
        self.assertEqual(payload["baseline"], self.base)

    def test_the_surviving_hunk_is_still_offered(self) -> None:
        result = self.store.reject_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        self.assertEqual(result["remaining"], 1)
        self.assertFalse(result["done"])

    def test_rejecting_every_hunk_restores_the_baseline(self) -> None:
        """It has to land exactly where "reject this file" lands."""
        self.store.reject_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        result = self.store.reject_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        self.assertTrue(result["done"])
        self.assertEqual(self.text(self.path), self.base)
        self.assertEqual(self.store.list_changes(SESSION), [])

    def test_mixing_a_reject_and_an_accept_keeps_both_decisions(self) -> None:
        self.store.reject_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        result = self.store.accept_hunk(SESSION, self.path, self.hunks(self.path)[0]["id"])
        self.assertTrue(result["done"])
        text = self.text(self.path)
        self.assertIn("l1\n", text)
        self.assertIn("L15\n", text)
        self.assertEqual(self.store.list_changes(SESSION), [])

    def test_undoing_the_last_hunk_of_a_created_file_removes_it(self) -> None:
        """An empty leftover file would match no baseline and never clear."""
        path = self.track("new.py", None, "x\ny\n")
        result = self.store.reject_hunk(SESSION, path, self.hunks(path)[0]["id"])
        self.assertTrue(result["done"])
        self.assertFalse(Path(path).exists())
        self.assertNotIn(path, [row["path"] for row in self.store.list_changes(SESSION)])

    def test_a_created_file_is_a_single_decision_however_long(self) -> None:
        """Its baseline is empty, so the whole body is one contiguous insertion.

        Worth pinning down: it means a brand-new file can never be half-accepted,
        and the per-hunk controls on it are just the file-level buttons.
        """
        body = "".join(f"l{i}\n" for i in range(200))
        path = self.track("new.py", None, body)
        self.assertEqual(len(self.hunks(path)), 1)
        result = self.store.accept_hunk(SESSION, path, self.hunks(path)[0]["id"])
        self.assertTrue(result["done"])
        self.assertEqual(self.text(path), body)


class ChangeListCountsTest(_StoreCase):
    """The review list carries its own +/- counts, for the collapsed panel."""

    def test_a_modification_reports_the_lines_it_moved(self) -> None:
        path = self.track("a.py", "a\nb\nc\n", "a\nB\nc\nd\n")
        row = next(r for r in self.store.list_changes(SESSION) if r["path"] == path)
        self.assertEqual((row["added"], row["deleted"]), (2, 1))

    def test_a_created_file_counts_as_all_additions(self) -> None:
        path = self.track("new.py", None, "x\ny\n")
        row = next(r for r in self.store.list_changes(SESSION) if r["path"] == path)
        self.assertEqual((row["added"], row["deleted"]), (2, 0))

    def test_a_deleted_file_has_no_line_diff(self) -> None:
        """Nothing is left to diff against, so it stays a whole-file decision."""
        path = self.track("gone.py", "x\ny\n", None)
        row = next(r for r in self.store.list_changes(SESSION) if r["path"] == path)
        self.assertEqual((row["added"], row["deleted"]), (0, 0))

    def test_counts_follow_the_file_when_it_changes_again(self) -> None:
        """The counts are cached, so a later edit has to invalidate the cache."""
        path = self.track("a.py", "a\nb\nc\n", "a\nB\nc\n")
        first = next(r for r in self.store.list_changes(SESSION) if r["path"] == path)
        self.assertEqual((first["added"], first["deleted"]), (1, 1))
        Path(path).write_text("a\nB\nc\nd\ne\n", encoding="utf-8")
        second = next(r for r in self.store.list_changes(SESSION) if r["path"] == path)
        self.assertEqual((second["added"], second["deleted"]), (3, 1))

    def test_a_binary_file_reports_no_line_diff(self) -> None:
        """Binary here means "not decodable", same rule as the file payload."""
        path = self.root / "logo.bin"
        path.write_bytes(b"\xff\xfe\x00old")
        baseline = path.read_bytes()
        path.write_bytes(b"\xff\xfe\x00new-and-longer")
        self.store.merge_turn(SESSION, {str(path): baseline})
        row = next(
            r for r in self.store.list_changes(SESSION) if r["path"] == str(path)
        )
        self.assertEqual((row["added"], row["deleted"]), (0, 0))


class HunkApiTest(_StoreCase):
    """The HTTP layer, including the status code the client branches on."""

    def setUp(self) -> None:
        super().setUp()
        self.path = self.track("a.py", "a\nb\nc\n", "a\nB\nc\n")

    def test_a_hunk_action_reports_what_it_did(self) -> None:
        hunk_id = self.hunks(self.path)[0]["id"]
        payload = review_action_payload(
            self.store, SESSION, "accept", self.path, hunk_id
        )
        self.assertEqual(payload["action"], "accept")
        self.assertEqual(payload["hunk"], hunk_id)
        self.assertTrue(payload["done"])

    def test_a_lost_race_is_a_conflict_not_a_bad_request(self) -> None:
        """409 tells the client to reload the diff; 400 would read as a bug."""
        with self.assertRaises(ReviewApiError) as caught:
            review_action_payload(self.store, SESSION, "reject", self.path, "0:deadbeef")
        self.assertEqual(caught.exception.status, 409)

    def test_a_hunk_without_a_path_is_refused(self) -> None:
        with self.assertRaises(ReviewApiError) as caught:
            review_action_payload(self.store, SESSION, "accept", None, "0:abc")
        self.assertEqual(caught.exception.status, 400)

    def test_an_unknown_action_is_refused(self) -> None:
        with self.assertRaises(ReviewApiError) as caught:
            review_action_payload(self.store, SESSION, "maybe", self.path, "0:abc")
        self.assertEqual(caught.exception.status, 400)

    def test_whole_file_actions_still_work(self) -> None:
        """The hunk parameter is additive; omitting it must change nothing."""
        payload = review_action_payload(self.store, SESSION, "accept", self.path, None)
        self.assertEqual(payload, {"action": "accept", "count": 1})


if __name__ == "__main__":
    unittest.main()
