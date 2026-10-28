"""Each turn names the language to answer in; the user's own words decide, never a paste."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

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
    ("oui vas y de 1 a 6", "French"),
    ("أريد تطبيقا لإدارة المشاريع مع تسجيل الدخول", "Arabic"),
    ("Quiero una aplicación para gestionar proyectos con usuarios", "Spanish"),
    ("Correctie: in mijn vorige antwoord schreef ik dat er nog 6 resources aangemaakt moeten worden.", "Dutch"),
    # French typed fast, with the words it shares with Dutch (de, en, je, met):
    # these used to score Dutch and got the user a Dutch answer.
    ("je met en prod de suite la version de la semaine", "French"),
    ("/forge les gel cli et desktop toujours n oublie pas desktop c est important surtout avec wsl", "French"),
    ("mon colluege a creer un branche merge la avec le main stp", "French"),
])
def test_detects_the_language_of_a_real_message(text, language):
    assert rl.detect_language(text) == language


@pytest.mark.parametrize("text", ["ok", "vasy", "```python\nprint(1)\n```", "", "/forge", "✓ Ran · exit 0"])
def test_too_short_to_tell(text):
    assert rl.detect_language(text) is None


DUTCH_PASTE = (
    "Correctie: in mijn vorige antwoord schreef ik dat er nog 6 resources aangemaakt moeten worden. "
    "Het zijn er 5: 15 gepland, 10 al aangemaakt. Ik heb dat ook in de beschrijving van PR #866 rechtgezet.\n"
    "Wat ik heb gedaan\n"
    "•  Een bericht in negen punten opgesteld, in de volgorde waarin het team moet werken:\n"
    " 1.  rechten van de Terraform-rol (PR #866) en de afwijking in de account-laag;\n"
    " 2.  de infrastructuur voor de telefonie opnieuw uitrollen;\n"
    "•  In de beschrijving van PR #866 het aantal nog aan te maken resources verbeterd: 5 in plaats van 6.\n"
    "Wat ik heb gecontroleerd voordat ik het bericht schreef\n"
    "•  PR #863 en #865 zijn gemerged, #866 staat nog open.\n"
    "•  De beschrijving van PR #866 meldt nu 5 resources."
)

ENGLISH_JOURNAL = (
    "× Ran print(d if isinstance(d,dict) else \"\\n\".join(f\"{p.get('id') or p.get(… · 2 lines  [━━━━━━━━━━] 100%\n"
    "  └ exit 1 · 5 lines\n"
    "- Permission denied: do not use sudo; fix the folder's ownership or install into the project (venv, local node_modules).\n"
    "[Sandboxed: this command ran in the OS sandbox (writes limited to the project and toolchain caches; "
    "reads and network open). The output mentions a p…\n"
    "• Explored\n"
    "└ ✓ Search AGENTS.md  no matches"
)


def test_a_paste_in_another_language_never_outvotes_the_users_own_words():
    # The two real messages behind the bug report: a French remark around a
    # Dutch answer, a French question glued to an English journal. The paste
    # won before, and the user was answered in Dutch, then in English.
    around_dutch = (
        "dans journal il melange anglais francas et change de langue " + DUTCH_PASTE
        + " alors que je lui parle en francais et parfos en anglais ou arabe"
    )
    assert rl.detect_language(around_dutch) == "French"
    assert rl.detect_language(around_dutch, remembered="French") == "French"
    after_journal = "/forge " + ENGLISH_JOURNAL + " les permission sudo a ne pas donnes au cli c est normal ?"
    assert rl.detect_language(after_journal) == "French"
    assert rl.detect_language(after_journal, remembered="French") == "French"
    # The remark before the paste alone, nothing after it: still the user's words.
    before_paste = "regarde cette erreur et dis moi ce que tu en penses:\n" + ENGLISH_JOURNAL
    assert rl.detect_language(before_paste) == "French"
    # A remark after the paste alone, in a new chat (nothing remembered).
    assert rl.detect_language(DUTCH_PASTE + "\nc est quoi ce charabia, reponds moi en francais") == "French"
    # The same pastes with no words of the user's own: the paste is all there is.
    assert rl.detect_language(DUTCH_PASTE) == "Dutch"
    assert rl.detect_language("/forge " + ENGLISH_JOURNAL) == "English"


def test_the_chats_language_breaks_a_tie_between_the_words_around_a_paste():
    bilingual = "Voici le retour complet du build que j'ai eu ce matin.\n" + ENGLISH_JOURNAL + "\nPlease tell me what the fix is here."
    assert rl.detect_language(bilingual, remembered="French") == "French"
    assert rl.detect_language(bilingual, remembered="English") == "English"


def test_a_real_switch_of_language_is_followed():
    # The whole message in another language: the user switched, so does Navin.
    assert rl.detect_language("Can you also check the deploy script and run the tests again?", remembered="French") == "English"
    arabic_around_log = "هذا الخطأ ظهر عند التشغيل، ما السبب؟\n" + ENGLISH_JOURNAL + "\nأرجو أن تشرح لي ما حدث"
    assert rl.detect_language(arabic_around_log, remembered="French") == "Arabic"


def _block(session_key, text):
    context = RequestContext(channel="websocket", chat_id="c", session_key=session_key, original_user_text=text)
    return asyncio.run(rl.reply_language_context_provider(context))


def test_a_short_reply_keeps_the_chats_language():
    first = _block("websocket:a", "il faut enlever les garde fou null comme ca")
    assert first is not None and "Reply language: French" in first.content
    again = _block("websocket:a", "vasy")
    assert again is not None and "French" in again.content
    assert _block("websocket:b", "vasy") is None  # another chat learned nothing


class _Store:
    """A session store with one cached chat, as the loop's SessionManager.peek gives it."""

    def __init__(self, *keys: str) -> None:
        self.sessions = {key: SimpleNamespace(metadata={}) for key in keys}
        self.peeked: list[str] = []

    def peek(self, key: str):
        self.peeked.append(key)
        return self.sessions.get(key)


def test_the_chats_language_survives_a_restart_through_its_session():
    store = _Store("cli:x")
    assert rl.reply_language("cli:x", "corrige le bug dans le fichier de config stp", store) == "French"
    assert store.sessions["cli:x"].metadata[rl.METADATA_KEY] == "French"
    rl._REMEMBERED.clear()  # the process restarted: only the session knows
    assert rl.reply_language("cli:x", "ok", store) == "French"
    assert rl.reply_language("cli:x", None, store) == "French"
    # A chat the store never saw learns nothing and creates no session.
    assert rl.reply_language("cli:new", "ok", store) is None
    assert "cli:new" not in store.sessions
    # The store may be given as a callable resolved at call time (the loop's).
    rl._REMEMBERED.clear()
    provider = rl.reply_language_provider(lambda: store)
    context = RequestContext(channel="cli", chat_id="c", session_key="cli:x", original_user_text="vasy")
    block = asyncio.run(provider(context))
    assert block is not None and "Reply language: French" in block.content
    # A genuine switch updates the session as well.
    assert rl.reply_language("cli:x", "Can you run the tests again and show me the output please", store) == "English"
    assert store.sessions["cli:x"].metadata[rl.METADATA_KEY] == "English"


def test_rich_interim_messages_are_kept_as_markdown():
    from navin.tui.widgets import is_rich_narration

    assert is_rich_narration("# Titre\n\n| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert is_rich_narration("- un\n- deux")
    assert is_rich_narration("Je lis **app.py** maintenant.")
    assert not is_rich_narration("Je vais lancer les tests.")


def test_a_relaunch_navin_writes_mid_turn_carries_the_chats_language():
    # Navin's own relaunches reach the model as English user messages; the
    # reminder is added on a copy for the request only, never in the history.
    rl.reply_language("cli:r", "corrige le bug dans le fichier de config stp")
    nudge = {"role": "user", "content": "Wrap-up check: your latest edit is validated (checks green)."}
    history = [{"role": "system", "content": "s"}, {"role": "assistant", "content": "ok"}, nudge]
    sent = rl.with_reply_language(history, "cli:r")
    assert sent[-1]["content"].startswith(nudge["content"])
    assert "Reply language: French" in sent[-1]["content"]
    assert sent[:-1] == history[:-1]
    assert history[-1] is nudge and "Reply language" not in nudge["content"]
    # The user's own message already carries the line: nothing is added twice.
    own = [{"role": "user", "content": "vasy\n\n[Runtime Context]\nReply language: French.\n[/Runtime Context]"}]
    assert rl.with_reply_language(own, "cli:r") is own
    # Tool results, assistant turns, image messages and unknown chats are left alone.
    for untouched in (
        [{"role": "tool", "content": "exit 0"}],
        [{"role": "assistant", "content": "done"}],
        [{"role": "user", "content": [{"type": "text", "text": "look"}]}],
        [],
    ):
        assert rl.with_reply_language(untouched, "cli:r") is untouched
    assert rl.with_reply_language([nudge], "cli:unknown") == [nudge]
