# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for inline editor assistance (ghost text and Cmd+K edits).

The model call is stubbed: what matters here is that the prompt carries the
right context and that model output is sanitized before it can reach a buffer.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch

from navin.webui import assist_api


class StripFencesTest(unittest.TestCase):
    def test_plain_code_is_untouched(self):
        self.assertEqual(assist_api._strip_fences("x = 1\n"), "x = 1\n")

    def test_fenced_block_is_unwrapped(self):
        self.assertEqual(
            assist_api._strip_fences("```python\nx = 1\ny = 2\n```"), "x = 1\ny = 2"
        )

    def test_fence_without_language_is_unwrapped(self):
        self.assertEqual(assist_api._strip_fences("```\nx = 1\n```"), "x = 1")

    def test_unterminated_fence_is_unwrapped(self):
        self.assertEqual(assist_api._strip_fences("```ts\nconst a = 1;"), "const a = 1;")

    def test_lone_fence_yields_nothing(self):
        self.assertEqual(assist_api._strip_fences("```"), "")

    def test_inner_backticks_survive(self):
        source = "```md\nuse `code` here\n```"
        self.assertEqual(assist_api._strip_fences(source), "use `code` here")


class DropOverlapTest(unittest.TestCase):
    """Models often restate the current line, which would duplicate text."""

    def test_restated_prefix_is_dropped(self):
        self.assertEqual(
            assist_api._drop_overlap("    return self.va", "self.value + 1"),
            "lue + 1",
        )

    def test_unrelated_completion_is_kept(self):
        self.assertEqual(assist_api._drop_overlap("x = ", "compute()"), "compute()")

    def test_empty_completion_is_safe(self):
        self.assertEqual(assist_api._drop_overlap("anything", ""), "")

    def test_short_coincidental_overlap_is_kept(self):
        # A 1-3 char overlap is usually genuine code, not a restatement.
        self.assertEqual(assist_api._drop_overlap("a = b", "b + 1"), "b + 1")


class DropSuffixOverlapTest(unittest.TestCase):
    """FIM models often echo the code that already follows the caret."""

    def test_restated_suffix_is_dropped(self):
        self.assertEqual(
            assist_api._drop_suffix_overlap("value + 1\n\nx = 1", "\n\nx = 1\n"),
            "value + 1",
        )

    def test_unrelated_suffix_is_kept(self):
        self.assertEqual(
            assist_api._drop_suffix_overlap("compute()", "\nother()\n"),
            "compute()",
        )

    def test_empty_inputs_are_safe(self):
        self.assertEqual(assist_api._drop_suffix_overlap("", "tail"), "")
        self.assertEqual(assist_api._drop_suffix_overlap("head", ""), "head")


class FinalizeCompletionTest(unittest.TestCase):
    def test_finalize_strips_prefix_and_suffix_overlap(self):
        self.assertEqual(
            assist_api.finalize_completion(
                "return ",
                "\n\n# end\n",
                "```\nvalue\n\n# end\n```",
            ),
            "value",
        )

    def test_partial_keeps_growing_text(self):
        self.assertEqual(
            assist_api.partial_completion("x = ", "x = 1"),
            "1",
        )


class TrimContextTest(unittest.TestCase):
    def test_prefix_keeps_the_end_nearest_the_caret(self):
        text = "a" * 100 + "TAIL"
        with patch.object(assist_api, "_MAX_CONTEXT_CHARS", 10):
            self.assertTrue(assist_api._trim_context(text, keep_end=True).endswith("TAIL"))

    def test_suffix_keeps_the_start_nearest_the_caret(self):
        text = "HEAD" + "a" * 100
        with patch.object(assist_api, "_MAX_CONTEXT_CHARS", 10):
            self.assertTrue(
                assist_api._trim_context(text, keep_end=False).startswith("HEAD")
            )

    def test_short_text_is_untouched(self):
        self.assertEqual(assist_api._trim_context("short", keep_end=True), "short")


class CompletionPromptTest(unittest.TestCase):
    def test_cursor_marker_is_placed_between_prefix_and_suffix(self):
        user, before, after = assist_api._completion_prompt(
            path="app/calc.py",
            prefix="def add():\n    return ",
            suffix="\n\nx = 1\n",
        )
        self.assertIn("<CURSOR>", user)
        self.assertIn("app/calc.py", user)
        self.assertTrue(before.rstrip().endswith("return"))
        self.assertIn("x = 1", after)

    def test_language_falls_back_to_the_file_suffix(self):
        user, _before, _after = assist_api._completion_prompt(
            path="c/App.tsx", prefix="const ", suffix=""
        )
        self.assertIn("Language: tsx", user)

    def test_related_files_are_injected_into_the_prompt(self):
        user, _before, _after = assist_api._completion_prompt(
            path="app.py",
            prefix="def run():\n    ",
            suffix="\n",
            related_files=[
                {"path": "helpers.py", "content": "def next():\n    return 1\n"},
            ],
        )
        self.assertIn("Related files", user)
        self.assertIn("helpers.py", user)
        self.assertIn("def next()", user)


class CompletionPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_fences_and_trailing_newlines_are_removed(self):
        ask = AsyncMock(return_value=("```python\nvalue + 1\n```\n", "m", "fast", "chat"))
        with patch.object(assist_api, "_ask_completion_native_or_chat", ask):
            payload = await assist_api.completion_payload(
                path="a.py", prefix="return ", suffix=""
            )
        self.assertEqual(payload["completion"], "value + 1")
        self.assertEqual(payload["mode"], "chat")

    async def test_empty_file_makes_no_model_call(self):
        ask = AsyncMock()
        with patch.object(assist_api, "_ask_completion_native_or_chat", ask):
            payload = await assist_api.completion_payload(path="a.py", prefix="  ", suffix="")
        ask.assert_not_awaited()
        self.assertEqual(payload["completion"], "")
        self.assertEqual(payload["reason"], "empty file")

    async def test_no_suggestion_is_reported_not_faked(self):
        with patch.object(
            assist_api,
            "_ask_completion_native_or_chat",
            AsyncMock(return_value=("", "m", "fast", "chat")),
        ):
            payload = await assist_api.completion_payload(
                path="a.py", prefix="x = 1", suffix=""
            )
        self.assertEqual(payload["completion"], "")
        self.assertEqual(payload["reason"], "no suggestion")

    async def test_suffix_overlap_is_stripped_from_model_output(self):
        ask = AsyncMock(return_value=("value\n\n# end", "m", "fast", "fim"))
        with patch.object(assist_api, "_ask_completion_native_or_chat", ask):
            payload = await assist_api.completion_payload(
                path="a.py",
                prefix="x = ",
                suffix="\n\n# end\n",
            )
        self.assertEqual(payload["completion"], "value")
        self.assertEqual(payload["mode"], "fim")

    async def test_completion_uses_three_second_timeout_on_chat_fallback(self):
        snapshot = Mock()
        snapshot.model = "gpt-4o-mini"
        snapshot.provider = Mock(api_base=None, api_key=None)
        ask = AsyncMock(return_value=("x", "gpt-4o-mini", "code"))
        with (
            patch.object(assist_api, "_assist_preset", return_value="code"),
            patch.object(assist_api, "_load_snapshot", return_value=snapshot),
            patch.object(assist_api, "_ask", ask),
        ):
            await assist_api.completion_payload(
                path="a.py", prefix="return ", suffix=""
            )
        self.assertEqual(ask.await_args.kwargs["timeout_s"], assist_api._COMPLETION_TIMEOUT_S)
        self.assertEqual(assist_api._COMPLETION_TIMEOUT_S, 3.0)

    async def test_stream_completion_emits_partials_then_final(self):
        partials: list[str] = []

        async def fake_native(*, before, after, user_prompt, on_delta, stream, **_kw):
            del before, after, user_prompt, stream
            assert on_delta is not None
            await on_delta("hel")
            await on_delta("lo()")
            return "hello()", "m", "fast", "chat"

        async def on_partial(text: str) -> None:
            partials.append(text)

        with patch.object(assist_api, "_ask_completion_native_or_chat", fake_native):
            payload = await assist_api.stream_completion(
                path="a.py",
                prefix="print(",
                suffix="\n",
                on_partial=on_partial,
            )
        self.assertEqual(payload["completion"], "hello()")
        self.assertTrue(partials)
        self.assertEqual(partials[-1], "hello()")

    async def test_native_fim_preferred_over_chat(self):
        snapshot = Mock()
        snapshot.model = "codestral-latest"
        snapshot.provider = Mock(
            api_base="https://api.mistral.ai/v1",
            api_key="secret",
        )
        ask = AsyncMock()
        with (
            patch.object(assist_api, "_assist_preset", return_value="code"),
            patch.object(assist_api, "_load_snapshot", return_value=snapshot),
            patch.object(assist_api, "fim_complete", AsyncMock(return_value="value + 1")),
            patch.object(assist_api, "_ask", ask),
        ):
            payload = await assist_api.completion_payload(
                path="a.py",
                prefix="return ",
                suffix="\n",
            )
        ask.assert_not_awaited()
        self.assertEqual(payload["completion"], "value + 1")
        self.assertEqual(payload["mode"], "fim")

    async def test_fim_failure_falls_back_to_chat(self):
        from navin.webui.assist_fim import FimError

        snapshot = Mock()
        snapshot.model = "codestral-latest"
        snapshot.provider = Mock(
            api_base="https://api.mistral.ai/v1",
            api_key="secret",
        )
        ask = AsyncMock(return_value=("chat_ok", "codestral-latest", "code"))
        with (
            patch.object(assist_api, "_assist_preset", return_value="code"),
            patch.object(assist_api, "_load_snapshot", return_value=snapshot),
            patch.object(
                assist_api,
                "fim_complete",
                AsyncMock(side_effect=FimError("boom")),
            ),
            patch.object(assist_api, "_ask", ask),
        ):
            payload = await assist_api.completion_payload(
                path="a.py",
                prefix="return ",
                suffix="\n",
            )
        ask.assert_awaited()
        self.assertEqual(payload["completion"], "chat_ok")
        self.assertEqual(payload["mode"], "chat")


class EditPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_instruction_and_surrounding_context_reach_the_prompt(self):
        ask = AsyncMock(return_value=("def add(a, b):\n    return a + b", "m", "fast"))
        with patch.object(assist_api, "_ask", ask):
            payload = await assist_api.edit_payload(
                path="calc.py",
                selection="def add(a, b):\n    return a - b",
                instruction="fix the operator",
                prefix="import math\n",
                suffix="\nresult = add(1, 2)\n",
            )
        prompt = ask.await_args.args[1]
        self.assertIn("Instruction: fix the operator", prompt)
        self.assertIn("import math", prompt)
        self.assertIn("result = add(1, 2)", prompt)
        self.assertIn("return a - b", prompt)
        self.assertEqual(payload["replacement"], "def add(a, b):\n    return a + b")
        self.assertEqual(payload["original_length"], len("def add(a, b):\n    return a - b"))

    async def test_stream_edit_emits_partials_then_final(self):
        async def fake_stream(system, user, *, max_tokens, on_delta, timeout_s=20.0):
            del system, user, max_tokens, timeout_s
            await on_delta("hello")
            await on_delta("()")
            return "hello()", "m", "fast"

        partials: list[str] = []

        async def on_partial(text: str) -> None:
            partials.append(text)

        with patch.object(assist_api, "_ask_stream", fake_stream):
            payload = await assist_api.stream_edit(
                path="a.py",
                selection="x",
                instruction="rename",
                on_partial=on_partial,
            )
        self.assertEqual(payload["replacement"], "hello()")
        self.assertTrue(partials)
        self.assertEqual(partials[-1], "hello()")

    def test_partial_edit_strips_opening_fence(self):
        self.assertEqual(
            assist_api.partial_edit("```python\ndef x():\n    pass"),
            "def x():\n    pass",
        )
        self.assertEqual(assist_api.partial_edit("plain"), "plain")

    async def test_missing_instruction_is_rejected_before_any_model_call(self):
        ask = AsyncMock()
        with patch.object(assist_api, "_ask", ask):
            with self.assertRaises(assist_api.AssistError) as caught:
                await assist_api.edit_payload(path="a.py", selection="x", instruction="  ")
        ask.assert_not_awaited()
        self.assertEqual(caught.exception.status, 400)

    async def test_oversized_selection_is_rejected(self):
        with self.assertRaises(assist_api.AssistError) as caught:
            await assist_api.edit_payload(
                path="a.py",
                selection="x" * (assist_api._MAX_SELECTION_CHARS + 1),
                instruction="shorten",
            )
        self.assertIn("too large", caught.exception.message)

    async def test_empty_selection_is_treated_as_an_insertion(self):
        ask = AsyncMock(return_value=("new_code()", "m", "fast"))
        with patch.object(assist_api, "_ask", ask):
            payload = await assist_api.edit_payload(
                path="a.py", selection="", instruction="add a helper"
            )
        self.assertIn("empty selection", ask.await_args.args[1])
        self.assertEqual(payload["replacement"], "new_code()")

    async def test_blank_model_output_is_an_error_not_a_wipe(self):
        """An empty replacement would silently delete the user's selection."""
        with patch.object(assist_api, "_ask", AsyncMock(return_value=("   \n", "m", "fast"))):
            with self.assertRaises(assist_api.AssistError) as caught:
                await assist_api.edit_payload(
                    path="a.py", selection="keep = 1", instruction="tidy"
                )
        self.assertEqual(caught.exception.status, 502)


class ModelRouteTest(unittest.TestCase):
    def test_code_route_is_preferred_over_fast(self):
        class FakeConfig:
            model_routes = {
                "code": "code-preset",
                "code-fast": "code-fast-preset",
                "fast": "haiku-preset",
            }

        with patch("navin.config.loader.load_config", return_value=FakeConfig()):
            self.assertEqual(assist_api._assist_preset(), "code-preset")
            self.assertEqual(assist_api._fast_preset(), "code-preset")

    def test_code_fast_preferred_when_code_missing(self):
        class FakeConfig:
            model_routes = {"code-fast": "code-fast-preset", "fast": "haiku-preset"}

        with patch("navin.config.loader.load_config", return_value=FakeConfig()):
            self.assertEqual(assist_api._assist_preset(), "code-fast-preset")

    def test_fast_route_is_used_when_code_routes_missing(self):
        class FakeConfig:
            model_routes = {"fast": "haiku-preset"}

        with patch("navin.config.loader.load_config", return_value=FakeConfig()):
            self.assertEqual(assist_api._fast_preset(), "haiku-preset")

    def test_no_route_falls_back_to_the_default_preset(self):
        class FakeConfig:
            model_routes: dict[str, str] = {}

        with patch("navin.config.loader.load_config", return_value=FakeConfig()):
            self.assertIsNone(assist_api._fast_preset())

    def test_broken_config_does_not_raise(self):
        with patch("navin.config.loader.load_config", side_effect=RuntimeError("boom")):
            self.assertIsNone(assist_api._fast_preset())


class AskFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_provider_reports_503(self):
        with patch.object(
            assist_api, "_load_snapshot", side_effect=RuntimeError("no api key")
        ):
            with self.assertRaises(assist_api.AssistError) as caught:
                await assist_api._ask("s", "u", max_tokens=10)
        self.assertEqual(caught.exception.status, 503)
        self.assertIn("no model configured", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
