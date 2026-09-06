"""Deterministic media intent routing for chat turns.

A request like "genere un chat qui mange une banane" must reach the image model.
Left to the model alone it does not: the media tool schemas are stripped outside
Agent / Montage mode, and a weak fallback model answers with clarifying questions
or hand-drawn SVG instead of calling the tool.

This module decides, from the user text only, which media tools the turn needs.
It is intentionally rule-based rather than model-based: the routing must hold even
when the primary model failed over to a smaller one, which is exactly when the
old behavior broke.

Two confidence levels matter:

- ``explicit``: the user named the medium ("une image", "une video", "une voix
  off"). Safe to force a tool call.
- implicit: a generation verb with a concrete subject and no technical noun
  ("genere un chat qui mange une banane"). Safe to expose the tools and steer,
  and still forced for image because that is what the phrasing means in practice.

The caller intersects :attr:`MediaIntent.tools` with the live registry, so naming
a tool that a build does not ship is harmless.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

IMAGE_TOOL = "generate_image"
VIDEO_TOOL = "generate_video"
MUSIC_TOOL = "generate_music"
SPEECH_TOOL = "generate_speech"
MONTAGE_TOOL = "montage"

KIND_IMAGE = "image"
KIND_VIDEO = "video"
KIND_MUSIC = "music"
KIND_SPEECH = "speech"
KIND_FULL_VIDEO = "full_video"

#: Longest implicit request we still read as "draw me this".
_IMPLICIT_MAX_WORDS = 16

# Verb stems that ask for something to be produced. Accents are stripped before
# matching, so "genere", "génère" and "GÉNÉRER" all reduce to the same stem.
_GENERATION_VERBS = re.compile(
    r"\b("
    r"gener\w*|creer|cree\w*|"
    r"fais|faire|fabrique\w*|"
    r"generate\w*|creat\w*|make|made|produce\w*|"
    r"compose\w*|"
    r"donne moi|montre moi|give me|show me"
    r")\b"
)

# Verbs that are visual by definition: they name the medium implicitly, so even
# "dessine un diagramme d'architecture" is an image request, not a document one.
_DRAWING_VERBS = re.compile(r"\b(dessine\w*|dessiner|illustre\w*|illustrer|peins|peindre|draw\w*|sketch\w*|paint\w*)\b")

_IMAGE_NOUNS = re.compile(
    r"\b("
    r"image|images|photo|photos|photographie|illustration|illustrations|dessin|dessins|"
    r"visuel|visuels|logo|logos|affiche|affiches|poster|posters|avatar|avatars|"
    r"picture|pictures|artwork|wallpaper|fond d ecran|vignette|thumbnail|miniature|"
    r"banniere|bannieres|banner|banners|icone|icones|icon|icons|mockup|mockups|portrait"
    r")\b"
)

_VIDEO_NOUNS = re.compile(
    r"\b("
    r"video|videos|clip|clips|animation|animations|film|films|court metrage|"
    r"reel|reels|short|shorts|teaser|teasers|trailer|trailers|bande annonce"
    r")\b"
)

_MUSIC_NOUNS = re.compile(
    r"\b("
    r"musique|musiques|music|chanson|chansons|song|songs|melodie|melody|"
    r"beat|beats|instrumental|instrumentaux|bande son|bande sonore|soundtrack|"
    r"jingle|jingles|bgm|ambiance sonore"
    r")\b"
)

_SPEECH_NOUNS = re.compile(
    r"\b("
    r"voix|voix off|voice over|voiceover|narration|narrateur|narratrice|"
    r"synthese vocale|tts|podcast|podcasts|doublage|speech|audio"
    r")\b"
)

# Nouns that mean "this is software, a document or plain text", which suppresses
# the implicit image reading. An explicit medium always wins over these.
_TECHNICAL_NOUNS = re.compile(
    r"\b("
    r"fichier|fichiers|code|fonction|fonctions|classe|classes|script|scripts|"
    r"composant|composants|component|components|page|pages|app|appli|application|"
    r"site|api|endpoint|endpoints|route|routes|test|tests|doc|docs|documentation|"
    r"readme|rapport|rapports|tableau|tableaux|table|csv|json|yaml|yml|xml|sql|"
    r"requete|requetes|query|base de donnees|database|projet|projets|dossier|"
    r"repertoire|module|modules|bibliotheque|library|package|packages|commit|"
    r"branche|branch|pr|pull request|migration|migrations|schema|config|"
    r"configuration|variable|variables|boucle|algorithme|regex|cron|workflow|"
    r"pipeline|dockerfile|terminal|commande|commandes|mot de passe|password|"
    r"cle api|token|resume|summary|texte|text|article|articles|email|mail|"
    r"message|messages|lettre|cv|facture|devis|contrat|presentation|slide|slides|"
    r"pptx|docx|xlsx|pdf|plan|roadmap|todo|liste|listes|prompt|prompts"
    r")\b"
)

# Phrasings that explicitly ask for one deliverable mixing footage and sound.
_FULL_VIDEO_PHRASES = re.compile(
    r"("
    r"video complete|video complet|complete video|full video|"
    r"video avec (musique|voix|audio|son)|"
    r"clip avec (musique|voix|audio|son)|"
    r"montage video|video montage|"
    r"avec musique et voix|avec voix et musique"
    r")"
)


def _normalize(text: str | None) -> str:
    """Casefold, strip accents and collapse whitespace for stable matching."""
    lowered = (text or "").casefold()
    decomposed = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # Apostrophes and hyphens split words rather than glue them ("voix-off").
    cleaned = re.sub(r"[’'\-_/]+", " ", stripped)
    return re.sub(r"\s+", " ", cleaned).strip()


@dataclass(frozen=True, slots=True)
class MediaIntent:
    """One turn's media routing decision."""

    kind: str
    tools: tuple[str, ...]
    #: The user named the medium, so forcing a tool call cannot misread them.
    explicit: bool
    #: Which media families were named, for logs and tests.
    matched: tuple[str, ...]

    @property
    def primary_tool(self) -> str:
        return self.tools[0] if self.tools else ""

    def should_force_tool(self) -> bool:
        """Force ``tool_choice`` only when a single medium is unambiguous.

        A multi-asset deliverable needs several calls in sequence, so pinning the
        first one would fight the plan rather than help it.
        """
        return self.kind in {KIND_IMAGE, KIND_VIDEO, KIND_MUSIC, KIND_SPEECH}


def detect_media_intent(text: str | None) -> MediaIntent | None:
    """Return the media routing decision for *text*, or ``None`` for non-media."""
    normalized = _normalize(text)
    if not normalized:
        return None

    has_image = bool(_IMAGE_NOUNS.search(normalized))
    has_video = bool(_VIDEO_NOUNS.search(normalized))
    has_music = bool(_MUSIC_NOUNS.search(normalized))
    has_speech = bool(_SPEECH_NOUNS.search(normalized))
    drawing = bool(_DRAWING_VERBS.search(normalized))
    generating = drawing or bool(_GENERATION_VERBS.search(normalized))

    if not generating:
        return None

    # One deliverable that mixes footage with sound: the montage pipeline owns it.
    wants_full_video = bool(_FULL_VIDEO_PHRASES.search(normalized)) or (
        has_video and (has_music or has_speech)
    )
    if wants_full_video:
        tools = [VIDEO_TOOL]
        matched = [KIND_VIDEO]
        if has_image:
            tools.append(IMAGE_TOOL)
            matched.append(KIND_IMAGE)
        if has_music or not has_speech:
            tools.append(MUSIC_TOOL)
            matched.append(KIND_MUSIC)
        if has_speech:
            tools.append(SPEECH_TOOL)
            matched.append(KIND_SPEECH)
        tools.append(MONTAGE_TOOL)
        return MediaIntent(
            kind=KIND_FULL_VIDEO,
            tools=tuple(dict.fromkeys(tools)),
            explicit=True,
            matched=tuple(dict.fromkeys(matched)),
        )

    if has_video:
        return MediaIntent(KIND_VIDEO, (VIDEO_TOOL,), True, (KIND_VIDEO,))
    if has_music:
        return MediaIntent(KIND_MUSIC, (MUSIC_TOOL,), True, (KIND_MUSIC,))
    if has_speech:
        return MediaIntent(KIND_SPEECH, (SPEECH_TOOL,), True, (KIND_SPEECH,))
    if has_image or drawing:
        return MediaIntent(KIND_IMAGE, (IMAGE_TOOL,), True, (KIND_IMAGE,))

    # Implicit: "genere un chat qui mange une banane". A technical noun means the
    # user is asking for software, a document or text, so stay out of the way.
    if _TECHNICAL_NOUNS.search(normalized):
        return None
    if len(normalized.split()) > _IMPLICIT_MAX_WORDS:
        return None
    return MediaIntent(KIND_IMAGE, (IMAGE_TOOL,), False, (KIND_IMAGE,))


def media_tools_for_text(text: str | None) -> frozenset[str]:
    """Tool names this turn must keep in the prompt, empty when not a media turn."""
    intent = detect_media_intent(text)
    return frozenset(intent.tools) if intent else frozenset()


def _definition_names(tool_definitions: list[dict] | None) -> set[str]:
    names: set[str] = set()
    for schema in tool_definitions or []:
        if not isinstance(schema, dict):
            continue
        fn = schema.get("function")
        raw = fn.get("name") if isinstance(fn, dict) else schema.get("name")
        if isinstance(raw, str) and raw:
            names.add(raw)
    return names


def _already_called_a_tool(messages: list[dict] | None) -> bool:
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool":
            return True
        if message.get("role") == "assistant" and message.get("tool_calls"):
            return True
    return False


def forced_tool_choice(
    tool_name: str | None,
    tool_definitions: list[dict] | None,
    messages: list[dict] | None,
) -> dict | None:
    """OpenAI-style ``tool_choice`` pinning *tool_name*, for the first call only.

    Pinning past the first call would trap the turn in a loop: the model could
    never write the reply that presents the artifact. It is also skipped when the
    build does not ship the tool, so naming a not-yet-released generator is inert.
    """
    if not tool_name:
        return None
    if tool_name not in _definition_names(tool_definitions):
        return None
    if _already_called_a_tool(messages):
        return None
    return {"type": "function", "function": {"name": tool_name}}
