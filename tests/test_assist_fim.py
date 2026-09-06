"""Unit tests for native FIM detection and request shaping."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch

from navin.webui import assist_fim


class ResolveFimModeTest(unittest.TestCase):
    def test_codestral_uses_mistral_fim_endpoint(self):
        self.assertEqual(
            assist_fim.resolve_fim_mode("codestral-latest"),
            "mistral_fim",
        )
        self.assertEqual(
            assist_fim.resolve_fim_mode("mistral/codestral-2501"),
            "mistral_fim",
        )

    def test_deepseek_coder_uses_completions_suffix(self):
        self.assertEqual(
            assist_fim.resolve_fim_mode("deepseek-coder-v2"),
            "openai_completions",
        )
        self.assertEqual(
            assist_fim.resolve_fim_mode("deepseek/deepseek-coder"),
            "openai_completions",
        )

    def test_local_starcoder_uses_fim_tokens(self):
        self.assertEqual(
            assist_fim.resolve_fim_mode(
                "starcoder2:15b",
                "http://127.0.0.1:11434/v1",
            ),
            "fim_tokens",
        )

    def test_remote_qwen_coder_uses_completions(self):
        self.assertEqual(
            assist_fim.resolve_fim_mode(
                "qwen2.5-coder",
                "https://openrouter.ai/api/v1",
            ),
            "openai_completions",
        )

    def test_chat_models_have_no_native_fim(self):
        self.assertIsNone(assist_fim.resolve_fim_mode("gpt-4o"))
        self.assertIsNone(assist_fim.resolve_fim_mode("claude-sonnet-4"))
        self.assertIsNone(assist_fim.resolve_fim_mode("deepseek/deepseek-v4-flash"))
        self.assertIsNone(assist_fim.resolve_fim_mode(""))


class BuildFimBodyTest(unittest.TestCase):
    def test_mistral_body_has_prompt_and_suffix(self):
        body = assist_fim.build_fim_body(
            mode="mistral_fim",
            model="codestral-latest",
            prefix="def add(",
            suffix="):\n    pass\n",
            max_tokens=64,
        )
        self.assertEqual(body["prompt"], "def add(")
        self.assertEqual(body["suffix"], "):\n    pass\n")
        self.assertEqual(body["model"], "codestral-latest")
        self.assertEqual(body["max_tokens"], 64)
        self.assertEqual(body["temperature"], 0.0)
        self.assertFalse(body["stream"])

    def test_fim_tokens_body_embeds_special_markers(self):
        body = assist_fim.build_fim_body(
            mode="fim_tokens",
            model="starcoder2",
            prefix="PREFIX",
            suffix="SUFFIX",
            max_tokens=32,
        )
        self.assertIn("<fim_prefix>PREFIX", body["prompt"])
        self.assertIn("<fim_suffix>SUFFIX", body["prompt"])
        self.assertTrue(body["prompt"].endswith("<fim_middle>"))
        self.assertNotIn("suffix", body)

    def test_deepseek_token_prompt_uses_deepseek_markers(self):
        prompt = assist_fim.build_fim_token_prompt(
            "pre",
            "suf",
            model="deepseek-coder",
        )
        self.assertIn("<｜fim▁begin｜>pre", prompt)
        self.assertIn("<｜fim▁hole｜>suf", prompt)
        self.assertTrue(prompt.endswith("<｜fim▁end｜>"))


class FimEndpointTest(unittest.TestCase):
    def test_mistral_endpoint(self):
        self.assertEqual(
            assist_fim.fim_endpoint("https://api.mistral.ai/v1/", "mistral_fim"),
            "https://api.mistral.ai/v1/fim/completions",
        )

    def test_completions_endpoint(self):
        self.assertEqual(
            assist_fim.fim_endpoint("http://localhost:11434/v1", "openai_completions"),
            "http://localhost:11434/v1/completions",
        )


class ExtractFimTextTest(unittest.TestCase):
    def test_openai_text_choice(self):
        self.assertEqual(
            assist_fim.extract_fim_text({"choices": [{"text": "hello()"}]}),
            "hello()",
        )

    def test_message_content_choice(self):
        self.assertEqual(
            assist_fim.extract_fim_text(
                {"choices": [{"message": {"content": "x = 1"}}]}
            ),
            "x = 1",
        )

    def test_empty_choices(self):
        self.assertEqual(assist_fim.extract_fim_text({"choices": []}), "")
        self.assertEqual(assist_fim.extract_fim_text({}), "")

    def test_stream_delta_text(self):
        self.assertEqual(
            assist_fim.extract_fim_stream_delta({"choices": [{"text": "ab"}]}),
            "ab",
        )
        self.assertEqual(
            assist_fim.extract_fim_stream_delta(
                {"choices": [{"delta": {"content": "c"}}]}
            ),
            "c",
        )


class ProviderCredentialsTest(unittest.TestCase):
    def test_unwraps_fallback_primary(self):
        primary = Mock(api_base="https://api.mistral.ai/v1", api_key="k")
        wrapper = Mock(_primary=primary, api_base=None, api_key=None)
        self.assertEqual(
            assist_fim.provider_fim_credentials(wrapper),
            ("https://api.mistral.ai/v1", "k"),
        )


class FimCompleteHttpTest(unittest.IsolatedAsyncioTestCase):
    async def test_fim_complete_posts_expected_json(self):
        captured: dict[str, object] = {}

        class _Response:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"text": "value + 1"}]}

        class _Client:
            def __init__(self, *args, **kwargs):
                del args, kwargs

                async def _post(url, headers=None, json=None):
                    captured["url"] = url
                    captured["headers"] = headers
                    captured["json"] = json
                    return _Response()

                self.post = _post

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                del args
                return False

        import httpx

        with patch.object(httpx, "AsyncClient", _Client):
            text = await assist_fim.fim_complete(
                api_base="https://api.mistral.ai/v1",
                api_key="secret",
                model="codestral-latest",
                prefix="return ",
                suffix="\n",
                max_tokens=64,
                mode="mistral_fim",
                timeout_s=3.0,
            )
        self.assertEqual(text, "value + 1")
        self.assertEqual(
            captured["url"],
            "https://api.mistral.ai/v1/fim/completions",
        )
        self.assertEqual(captured["json"]["prompt"], "return ")
        self.assertEqual(captured["json"]["suffix"], "\n")
        self.assertEqual(
            captured["headers"]["Authorization"],
            "Bearer secret",
        )

    async def test_fim_complete_raises_on_http_error(self):
        class _Response:
            status_code = 401
            text = "unauthorized"

            def json(self):
                return {}

        class _Client:
            def __init__(self, *args, **kwargs):
                del args, kwargs
                self.post = AsyncMock(return_value=_Response())

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                del args
                return False

        import httpx

        with patch.object(httpx, "AsyncClient", _Client):
            with self.assertRaises(assist_fim.FimError):
                await assist_fim.fim_complete(
                    api_base="https://api.mistral.ai/v1",
                    api_key="bad",
                    model="codestral-latest",
                    prefix="x",
                    suffix="",
                    max_tokens=16,
                    mode="mistral_fim",
                    timeout_s=3.0,
                )

    async def test_fim_complete_stream_reads_sse_chunks(self):
        lines = [
            'data: {"choices":[{"text":"hel"}]}',
            'data: {"choices":[{"text":"lo"}]}',
            "data: [DONE]",
        ]

        class _StreamResponse:
            status_code = 200

            async def aread(self):
                return b""

            async def aiter_lines(self):
                for line in lines:
                    yield line

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                del args
                return False

        class _Client:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            def stream(self, *args, **kwargs):
                del args, kwargs
                return _StreamResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                del args
                return False

        deltas: list[str] = []

        async def on_delta(text: str) -> None:
            deltas.append(text)

        import httpx

        with patch.object(httpx, "AsyncClient", _Client):
            text = await assist_fim.fim_complete_stream(
                api_base="https://api.mistral.ai/v1",
                api_key="secret",
                model="codestral-latest",
                prefix="print(",
                suffix=")",
                max_tokens=32,
                mode="mistral_fim",
                timeout_s=3.0,
                on_delta=on_delta,
            )
        self.assertEqual(text, "hello")
        self.assertEqual(deltas, ["hel", "lo"])


if __name__ == "__main__":
    unittest.main()
