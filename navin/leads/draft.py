"""Outreach drafts: one message per sequence step, in the prospect's language.

Deterministic templates always work (no key, no network). When the desk AI is
routed, the template becomes the brief and the model rewrites it around the
signal we hold on the row (hiring, list mention, registry) and the offer from the
profile. The result is stored on the step so a human can read, edit and send.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from navin.desk_ai import ai_enabled, ask_json
from navin.leads.discover import lang_for

AI_ENV_FLAG = "NAVIN_LEADS_AI"
AI_ROLE = "writer"
AskFn = Callable[..., tuple[Any, str]]

_TEMPLATES: dict[str, dict[int, tuple[str, str]]] = {
    "fr": {
        1: (
            "{company} x {sender}",
            "Bonjour {who},\n\n{hook}\n\n{offer_line}\n\n"
            "Est-ce que 15 minutes cette semaine ou la suivante vous conviendraient pour en parler ?\n\n{signature}",
        ),
        2: (
            "Re: {company} x {sender}",
            "Bonjour {who},\n\nJe me permets de relancer mon message de la semaine derniere.\n\n{offer_line}\n\n"
            "Si le sujet n'est pas prioritaire chez {company}, dites-le moi et je ne vous relancerai pas.\n\n{signature}",
        ),
        3: (
            "Dernier message - {company}",
            "Bonjour {who},\n\nDernier message de ma part : si {company} travaille sur ce sujet dans les prochains mois, "
            "je reste disponible pour un echange court.\n\nBonne continuation.\n\n{signature}",
        ),
    },
    "en": {
        1: (
            "{company} x {sender}",
            "Hello {who},\n\n{hook}\n\n{offer_line}\n\n"
            "Would 15 minutes this week or next work to talk it through?\n\n{signature}",
        ),
        2: (
            "Re: {company} x {sender}",
            "Hello {who},\n\nFollowing up on my note from last week.\n\n{offer_line}\n\n"
            "If this is not a priority at {company} right now, just say so and I will stop here.\n\n{signature}",
        ),
        3: (
            "Last note - {company}",
            "Hello {who},\n\nLast message from me: if {company} picks this up in the coming months, "
            "I am happy to have a short call.\n\nAll the best.\n\n{signature}",
        ),
    },
    "de": {
        1: (
            "{company} x {sender}",
            "Guten Tag {who},\n\n{hook}\n\n{offer_line}\n\n"
            "Passen Ihnen 15 Minuten in dieser oder der naechsten Woche fuer ein kurzes Gespraech?\n\n{signature}",
        ),
        2: (
            "Re: {company} x {sender}",
            "Guten Tag {who},\n\nich moechte an meine Nachricht von letzter Woche anknuepfen.\n\n{offer_line}\n\n"
            "Falls das Thema bei {company} gerade keine Prioritaet hat, sagen Sie es mir kurz.\n\n{signature}",
        ),
        3: (
            "Letzte Nachricht - {company}",
            "Guten Tag {who},\n\nletzte Nachricht von mir: sollte {company} das Thema in den kommenden Monaten angehen, "
            "stehe ich gern fuer ein kurzes Gespraech zur Verfuegung.\n\nAlles Gute.\n\n{signature}",
        ),
    },
    "es": {
        1: (
            "{company} x {sender}",
            "Hola {who},\n\n{hook}\n\n{offer_line}\n\n"
            "Le vendrian bien 15 minutos esta semana o la proxima para hablarlo?\n\n{signature}",
        ),
        2: (
            "Re: {company} x {sender}",
            "Hola {who},\n\nRetomo mi mensaje de la semana pasada.\n\n{offer_line}\n\n"
            "Si no es una prioridad en {company} ahora mismo, digamelo y no insistire.\n\n{signature}",
        ),
        3: (
            "Ultimo mensaje - {company}",
            "Hola {who},\n\nUltimo mensaje por mi parte: si {company} retoma este tema en los proximos meses, "
            "quedo disponible para una llamada corta.\n\nUn saludo.\n\n{signature}",
        ),
    },
    "it": {
        1: (
            "{company} x {sender}",
            "Buongiorno {who},\n\n{hook}\n\n{offer_line}\n\n"
            "Avrebbe 15 minuti questa settimana o la prossima per parlarne?\n\n{signature}",
        ),
        2: (
            "Re: {company} x {sender}",
            "Buongiorno {who},\n\nriprendo il mio messaggio della settimana scorsa.\n\n{offer_line}\n\n"
            "Se non e una priorita per {company} in questo momento, me lo dica e non insistero.\n\n{signature}",
        ),
        3: (
            "Ultimo messaggio - {company}",
            "Buongiorno {who},\n\nultimo messaggio da parte mia: se {company} riprendera il tema nei prossimi mesi, "
            "resto disponibile per una breve chiamata.\n\nCordiali saluti.\n\n{signature}",
        ),
    },
    "nl": {
        1: (
            "{company} x {sender}",
            "Beste {who},\n\n{hook}\n\n{offer_line}\n\n"
            "Zou 15 minuten deze of volgende week passen om het te bespreken?\n\n{signature}",
        ),
        2: (
            "Re: {company} x {sender}",
            "Beste {who},\n\nEven een opvolging van mijn bericht van vorige week.\n\n{offer_line}\n\n"
            "Als dit nu geen prioriteit is bij {company}, laat het me weten en ik stop hier.\n\n{signature}",
        ),
        3: (
            "Laatste bericht - {company}",
            "Beste {who},\n\nLaatste bericht van mij: als {company} dit de komende maanden oppakt, "
            "ben ik beschikbaar voor een kort gesprek.\n\nHet beste.\n\n{signature}",
        ),
    },
    "pt": {
        1: (
            "{company} x {sender}",
            "Ola {who},\n\n{hook}\n\n{offer_line}\n\n"
            "Teria 15 minutos esta semana ou na proxima para falarmos?\n\n{signature}",
        ),
        2: (
            "Re: {company} x {sender}",
            "Ola {who},\n\nRetomo a minha mensagem da semana passada.\n\n{offer_line}\n\n"
            "Se nao for prioridade na {company} neste momento, diga-me e nao insisto.\n\n{signature}",
        ),
        3: (
            "Ultima mensagem - {company}",
            "Ola {who},\n\nUltima mensagem da minha parte: se a {company} retomar este tema nos proximos meses, "
            "fico disponivel para uma chamada curta.\n\nTudo de bom.\n\n{signature}",
        ),
    },
}

_HOOKS = {
    "fr": {
        "hiring": "J'ai vu que {company} recrute ({detail}) : c'est souvent le moment ou le sujet ci-dessous devient concret.",
        "listed": "{company} apparait parmi les acteurs de reference de votre marche ({detail}).",
        "funding": "Felicitations pour le financement de {company} ({detail}).",
        "news": "J'ai suivi l'actualite de {company} ({detail}).",
        "default": "Je suis tombe sur {company} en etudiant les acteurs {sector} de votre region.",
    },
    "en": {
        "hiring": "I noticed {company} is hiring ({detail}): that is usually when the topic below becomes concrete.",
        "listed": "{company} shows up among the reference players in your market ({detail}).",
        "funding": "Congratulations on {company}'s funding ({detail}).",
        "news": "I have been following {company}'s news ({detail}).",
        "default": "I came across {company} while mapping the {sector} players in your area.",
    },
    "de": {
        "hiring": "Mir ist aufgefallen, dass {company} einstellt ({detail}): meist wird das Thema unten dann konkret.",
        "listed": "{company} zaehlt zu den Referenzanbietern in Ihrem Markt ({detail}).",
        "funding": "Herzlichen Glueckwunsch zur Finanzierung von {company} ({detail}).",
        "news": "Ich verfolge die Neuigkeiten von {company} ({detail}).",
        "default": "Ich bin auf {company} gestossen, als ich die {sector}-Anbieter in Ihrer Region analysiert habe.",
    },
    "es": {
        "hiring": "He visto que {company} esta contratando ({detail}): suele ser el momento en que el tema de abajo se vuelve concreto.",
        "listed": "{company} aparece entre los actores de referencia de su mercado ({detail}).",
        "funding": "Enhorabuena por la financiacion de {company} ({detail}).",
        "news": "He seguido las novedades de {company} ({detail}).",
        "default": "Encontre {company} al estudiar los actores de {sector} de su zona.",
    },
    "it": {
        "hiring": "Ho notato che {company} sta assumendo ({detail}): di solito e il momento in cui il tema qui sotto diventa concreto.",
        "listed": "{company} compare tra gli attori di riferimento del vostro mercato ({detail}).",
        "funding": "Congratulazioni per il finanziamento di {company} ({detail}).",
        "news": "Ho seguito le novita di {company} ({detail}).",
        "default": "Ho trovato {company} studiando gli attori {sector} della vostra zona.",
    },
    "nl": {
        "hiring": "Ik zag dat {company} werft ({detail}): meestal wordt het onderwerp hieronder dan concreet.",
        "listed": "{company} staat tussen de referentiespelers in uw markt ({detail}).",
        "funding": "Gefeliciteerd met de financiering van {company} ({detail}).",
        "news": "Ik volg het nieuws van {company} ({detail}).",
        "default": "Ik kwam {company} tegen bij het in kaart brengen van de {sector}-spelers in uw regio.",
    },
    "pt": {
        "hiring": "Reparei que a {company} esta a recrutar ({detail}): e normalmente quando o tema abaixo se torna concreto.",
        "listed": "A {company} aparece entre os atores de referencia do vosso mercado ({detail}).",
        "funding": "Parabens pelo financiamento da {company} ({detail}).",
        "news": "Tenho acompanhado as novidades da {company} ({detail}).",
        "default": "Encontrei a {company} ao mapear os atores de {sector} da vossa regiao.",
    },
}
_OFFER_LINE = {
    "fr": "En une ligne : {offer}",
    "en": "In one line: {offer}",
    "de": "In einem Satz: {offer}",
    "es": "En una linea: {offer}",
    "it": "In una riga: {offer}",
    "nl": "In een zin: {offer}",
    "pt": "Numa linha: {offer}",
}
_OFFER_FALLBACK = {
    "fr": "Nous aidons des entreprises comme la votre sur ce sujet ; je vous explique en deux phrases si utile.",
    "en": "We help companies like yours on this; I can explain in two sentences if useful.",
    "de": "Wir unterstuetzen Unternehmen wie Ihres bei diesem Thema; gern erklaere ich es in zwei Saetzen.",
    "es": "Ayudamos a empresas como la suya en este tema; se lo explico en dos frases si le interesa.",
    "it": "Aiutiamo aziende come la vostra su questo tema; posso spiegarlo in due frasi se utile.",
    "nl": "We helpen bedrijven zoals het uwe hiermee; ik leg het graag in twee zinnen uit.",
    "pt": "Ajudamos empresas como a sua neste tema; explico em duas frases se for util.",
}
_UNSUBSCRIBE = {
    "fr": "Pour ne plus recevoir de message de ma part, repondez simplement STOP.",
    "en": "Reply STOP and you will not hear from me again.",
    "de": "Antworten Sie mit STOP, und Sie erhalten keine weitere Nachricht von mir.",
    "es": "Responda STOP y no volvera a recibir mensajes mios.",
    "it": "Rispondi STOP e non ricevera altri messaggi da parte mia.",
    "nl": "Antwoord STOP en u hoort niets meer van mij.",
    "pt": "Responda STOP e nao voltara a receber mensagens minhas.",
}


def _first_name(row: dict[str, Any]) -> str:
    first = str(row.get("first_name") or "").strip()
    if first:
        return first
    person = str(row.get("person") or "").strip()
    return person.split()[0] if person else ""


def _signal_kind(row: dict[str, Any]) -> tuple[str, str]:
    signals = row.get("signals") if isinstance(row.get("signals"), list) else []
    ranked = ("hiring", "funding", "news", "listed")
    for wanted in ranked:
        for item in signals:
            if isinstance(item, dict) and item.get("kind") == wanted:
                detail = str(item.get("text") or "")
                return wanted, _clean_detail(detail)
    raw = str(row.get("signal") or "")
    lowered = raw.casefold()
    if lowered.startswith("hiring"):
        return "hiring", _clean_detail(raw)
    if lowered.startswith("listed on"):
        return "listed", _clean_detail(raw)
    return "default", ""


def _clean_detail(text: str) -> str:
    body = re.sub(r"^(hiring|listed on|web|osm|registry)\s*:\s*", "", text.strip(), flags=re.I)
    body = body.split(";")[0].strip()
    return body[:90]


def draft_language(row: dict[str, Any], profile: dict[str, Any]) -> str:
    wanted = str(profile.get("language") or "").strip().lower()[:2]
    if wanted in _TEMPLATES:
        return wanted
    return lang_for(str(row.get("country") or (profile.get("countries") or ["FR"])[0]))


def template_draft(row: dict[str, Any], profile: dict[str, Any], step: int) -> dict[str, str]:
    """Deterministic draft for one step (1..3). Never empty, never raises."""
    lang = draft_language(row, profile)
    subject_tpl, body_tpl = (
        _TEMPLATES.get(lang, _TEMPLATES["en"]).get(step) or _TEMPLATES["en"][min(max(step, 1), 3)]
    )
    company = str(row.get("company") or "").strip() or (
        "votre entreprise" if lang == "fr" else "your company"
    )
    sender = (
        str(profile.get("sender_name") or "").strip()
        or str(profile.get("icp_name") or "").strip()
        or "Navin"
    )
    # No first name: the greeting stands alone ("Bonjour," not "Bonjour there,").
    who = _first_name(row)
    kind, detail = _signal_kind(row)
    hooks = _HOOKS.get(lang) or _HOOKS["en"]
    sector = str(profile.get("sector") or row.get("sector") or "").strip() or "-"
    if kind == "default" or not detail:
        hook = hooks["default"].format(company=company, sector=sector)
    else:
        hook = (hooks.get(kind) or hooks["default"]).format(
            company=company, detail=detail, sector=sector
        )
    offer = str(profile.get("offer") or "").strip()
    offer_line = (
        _OFFER_LINE.get(lang, _OFFER_LINE["en"]).format(offer=offer)
        if offer
        else _OFFER_FALLBACK.get(lang, _OFFER_FALLBACK["en"])
    )
    signature = sender + "\n" + _UNSUBSCRIBE.get(lang, _UNSUBSCRIBE["en"])
    body = body_tpl.format(
        who=who, hook=hook, offer_line=offer_line, company=company, signature=signature
    )
    body = re.sub(r"(Bonjour|Hello|Hola|Buongiorno|Beste|Ola|Guten Tag) ,", r"\1,", body)
    subject = subject_tpl.format(company=company, sender=sender)
    return {
        "subject": subject[:120],
        "body": body.strip() + "\n",
        "lang": lang,
        "model": "",
        "kind": kind,
    }


def ai_draft(
    row: dict[str, Any],
    profile: dict[str, Any],
    step: int,
    *,
    ask_fn: AskFn | None = None,
) -> dict[str, str]:
    """Template first; the routed model rewrites it when the desk AI is on. Same shape."""
    base = template_draft(row, profile, step)
    if not ai_enabled(AI_ENV_FLAG, profile):
        return base
    ask = ask_fn or ask_json
    lang = base["lang"]
    facts = {
        "company": row.get("company"),
        "person": row.get("person"),
        "role": row.get("role"),
        "country": row.get("country"),
        "sector": row.get("sector"),
        "signal": row.get("signal"),
        "why": row.get("why"),
        "website": row.get("website"),
    }
    system = (
        "You write short B2B cold outreach for a small vendor. Rules: write in language code "
        f"'{lang}'. Under 120 words. One concrete hook from the facts, one line on the offer, one soft ask. "
        "No hype, no exclamation marks, no invented facts, no placeholders. Keep the unsubscribe line at the end verbatim. "
        'Return {"subject": "...", "body": "..."}.'
    )
    user = (
        f"Sequence step {step} of 3 (1 = first touch, 2 = follow-up, 3 = last note).\n"
        f"Offer: {profile.get('offer') or '-'}\nSender: {profile.get('sender_name') or '-'}\n"
        f"Facts: {facts}\n\nBaseline draft to improve:\nSubject: {base['subject']}\n\n{base['body']}"
    )
    try:
        data, model = ask(
            AI_ROLE,
            system,
            user,
            env_flag=AI_ENV_FLAG,
            profile=profile,
            max_tokens=500,
            temperature=0.4,
        )
    except Exception:
        return base
    if not isinstance(data, dict):
        return base
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    if len(body) < 40 or not subject:
        return base
    unsubscribe = _UNSUBSCRIBE.get(lang, _UNSUBSCRIBE["en"])
    if unsubscribe not in body:
        body = body.rstrip() + "\n\n" + unsubscribe
    return {**base, "subject": subject[:120], "body": body + "\n", "model": model or "ai"}


def draft_sequence(
    row: dict[str, Any],
    profile: dict[str, Any],
    *,
    ask_fn: AskFn | None = None,
    only_missing: bool = True,
) -> dict[str, Any]:
    """Attach a ``draft`` to every step of the row's sequence. Returns the new row."""
    seq = row.get("sequence") if isinstance(row.get("sequence"), dict) else None
    if not seq or not isinstance(seq.get("steps"), list):
        return dict(row)
    steps = []
    for step in seq["steps"]:
        if not isinstance(step, dict):
            continue
        item = dict(step)
        if item.get("status") == "sent" or (
            only_missing and isinstance(item.get("draft"), dict) and item["draft"].get("body")
        ):
            steps.append(item)
            continue
        item["draft"] = ai_draft(row, profile, int(item.get("n") or 1), ask_fn=ask_fn)
        steps.append(item)
    next_row = dict(row)
    next_row["sequence"] = {**seq, "steps": steps}
    return next_row
