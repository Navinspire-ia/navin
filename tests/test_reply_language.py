"""Each turn names the language to answer in; short messages keep the chat's language."""

from __future__ import annotations

import asyncio

import pytest

from navin.agent import reply_language as rl
from navin.agent.tools.context import RequestContext


@pytest.fixture(autouse=True)
def _fresh_memory():
    rl._REMEMBERED.clear()
    yield
    rl._REMEMBERED.clear()


@pytest.mark.parametrize("text,language", [
    ("quand je parle avec navin il repond en anglais, normal ?", "French"),
    ("corrige le bug dans `src/app.py` et lance npm run dev", "French"),
    ("Can you fix the login page and add a test for it please", "English"),
    ("I want an app to manage projects", "English"),
    ("run the tests again", "English"),
    ("ok now deploy it to production", "English"),
    ("write a script to parse logs", "English"),
    ("tu peux lancer les tests et me dire", "French"),
    ("أريد تطبيقا لإدارة المشاريع مع تسجيل الدخول", "Arabic"),
    ("Quiero una aplicación para gestionar proyectos con usuarios", "Spanish"),
])
def test_detects_the_language_of_a_real_message(text, language):
    assert rl.detect_language(text) == language


@pytest.mark.parametrize("text", ["ok", "vasy", "oui vas y de 1 a 6", "```python\nprint(1)\n```", ""])
def test_too_short_to_tell(text):
    assert rl.detect_language(text) is None


def _block(session_key, text):
    context = RequestContext(channel="websocket", chat_id="c", session_key=session_key, original_user_text=text)
    return asyncio.run(rl.reply_language_context_provider(context))


def test_a_short_reply_keeps_the_chats_language():
    first = _block("websocket:a", "il faut enlever les garde fou null comme ca")
    assert first is not None and "Reply language: French" in first.content
    again = _block("websocket:a", "vasy")
    assert again is not None and "French" in again.content
    assert _block("websocket:b", "vasy") is None  # another chat learned nothing


def test_rich_interim_messages_are_kept_as_markdown():
    from navin.tui.widgets import is_rich_narration

    assert is_rich_narration("# Titre\n\n| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert is_rich_narration("- un\n- deux")
    assert is_rich_narration("Je lis **app.py** maintenant.")
    assert not is_rich_narration("Je vais lancer les tests.")
