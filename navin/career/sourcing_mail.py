# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Company outreach using Career SMTP, threaded IMAP replies and genuine CVs."""

from __future__ import annotations

import base64
import hashlib
import io
import re
import time
import zipfile
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path
from typing import Any

from filelock import FileLock

from navin.career.errors import CareerError
from navin.career.mail import (
    _account_identity,
    _close_smtp,
    _day_key,
    _error_code,
    _smtp_connection,
    load_mail_state,
    mail_lock,
    queue_mail_notification,
    save_mail_state,
)
from navin.career.mail_settings import (
    email_address,
    normalize_mailbox,
    published_recipient,
    validate_mailbox,
)
from navin.career.prospecting import _save, _state
from navin.career.store import CareerStore

MAX_CV_BYTES = 8 * 1024 * 1024


def _ref(oid: str, cid: str) -> str:
    return "N" + hashlib.sha256(f"{oid}:{cid}".encode()).hexdigest()[:12].upper()


def _advance(match: dict[str, Any], kind: str) -> None:
    if kind == "candidate" and match["stage"] == "discovered":
        match.update({"stage": "contacted", "next_action": "Awaiting candidate response."})
    elif kind == "client" and match["stage"] in {"discovered", "contacted", "qualified"}:
        match.update({"stage": "submitted", "next_action": "Awaiting client response."})
    elif kind == "candidate_followup":
        match["next_action"] = "Additional information requested from the candidate."
    elif kind not in {"candidate", "client"}:
        states = {"candidate_interview": ("client_accepted", "candidate_pending", "Awaiting candidate confirmation."),
                  "client_slots": ("candidate_proposed", "client_pending", "Awaiting a time slot from the client."),
                  "client_confirmation": ("ready_to_confirm", "client_confirmed", "Send interview preparation to the candidate."),
                  "candidate_preparation": ("client_confirmed", "confirmed", "Interview confirmed. Preparation sent; awaiting follow-up.")}
        before, after, action = states[kind]
        if match.get("interview", {}).get("status") == before:
            match["interview"]["status"], match["next_action"] = after, action


def proposal_rate(criteria: dict[str, Any], match: dict[str, Any], offer: dict[str, Any] | None = None) -> float:
    from navin.career.scope import offer_work_mode, selling_floor

    sale = selling_floor(criteria, offer_work_mode(offer or {}))
    buy = float(match.get("purchase_rate") or 0)
    margin = float(criteria.get("margin_percent") or 0)
    return round(max(sale, buy / (1 - margin / 100) if buy else 0), 2)


def client_recipient(offer: dict[str, Any]) -> str:
    recipient, _ = published_recipient(offer)
    if recipient:
        return recipient
    # Explicit recruitment contact in the mission, not guessed from its company.
    addresses = re.findall(r"(?:contact|responsable|recruteur|recruiter)\s*:?[^\n@]{0,70}?\b([A-Z0-9_.+%-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
                           str(offer.get("description") or ""), re.I)
    unique = {email_address(address) for address in addresses}
    return next(iter(unique)) if len(unique) == 1 else ""


def read_candidate_cv(store: CareerStore, candidate: dict[str, Any], document: str = "cv") -> dict[str, str]:
    cv = candidate.get(document if document in {"cv", "dossier"} else "cv") or {}
    filename = str(cv.get("file") or "")
    if not re.fullmatch(r"[a-f0-9]{64}\.(pdf|docx)", filename):
        raise CareerError("Candidate CV is unavailable.", status=404)
    path = store.root / "talent-files" / filename
    if not path.is_file() or path.is_symlink():
        raise CareerError("Candidate CV was not found.", status=404)
    return {"name": cv["name"], "data_b64": base64.b64encode(path.read_bytes()).decode(), "mime": cv["mime"]}


def _cv_attachment(store: CareerStore, message: EmailMessage, document: str = "cv") -> dict[str, Any] | None:
    choices = []
    for part in message.iter_attachments():
        name = Path(str(part.get_filename() or "")).name
        ext = Path(name).suffix.lower()
        data = part.get_payload(decode=True) or b""
        if ext not in {".pdf", ".docx"} or not 0 < len(data) <= MAX_CV_BYTES:
            continue
        if ext == ".pdf" and not data.startswith(b"%PDF-"):
            continue
        if ext == ".docx":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    if "word/document.xml" not in archive.namelist() or sum(i.file_size for i in archive.infolist()) > 32 * 1024 * 1024:
                        continue
            except zipfile.BadZipFile:
                continue
        choices.append((name, ext, data))
    pattern = r"competenc|dossier|portfolio|skills" if document == "dossier" else r"\bcv\b|resume|curriculum"
    named = [item for item in choices if re.search(pattern, item[0].replace("_", " "), re.I)]
    selected = named[0] if len(named) == 1 else None
    if not selected and document == "cv" and len(choices) == 1 and not re.search(r"competenc|dossier|portfolio|skills", choices[0][0], re.I):
        selected = choices[0]
    if not selected:
        return None
    name, ext, data = selected
    filename = hashlib.sha256(data).hexdigest() + ext
    folder = store.root / "talent-files"
    folder.mkdir(exist_ok=True)
    (folder / filename).write_bytes(data)
    from navin.utils.document import extract_text

    extracted = extract_text(folder / filename) or ""
    if extracted.startswith("[error:"):
        extracted = ""
    return {"file": filename, "name": name, "size": len(data), "received_at": time.time(), "text": extracted[:12000],
            "mime": "application/pdf" if ext == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def record_sourcing_reply(store: CareerStore, mail_state: dict[str, Any], message: EmailMessage,
                          record: dict[str, Any], text: str, identity: str) -> bool:
    sender = parseaddr(str(message.get("From") or ""))[1].casefold()
    if sender != str(record.get("recipient") or "").casefold():
        return False
    if str(message.get("Auto-Submitted") or "no").lower() != "no" or message.get_content_type() == "multipart/report":
        return False
    with FileLock(str(store.root / "prospecting.lock"), timeout=0):
        state = _state(store)
        results = state["matches"].get(record["opportunity_id"], {}).get("results", [])
        match = next((r for r in results if r["candidate"]["id"] == record["candidate_id"]), None)
        if not match:
            return False
        if identity in match.get("reply_ids", []):
            return False
        match.setdefault("reply_ids", []).append(identity)
        match["last_reply"] = {"at": time.time(), "text": text[:8000], "sender": sender,
                               "kind": record["sourcing_kind"]}
        from navin.career.sourcing_replies import extract_reply, preparation

        kind = record["sourcing_kind"]
        facts = extract_reply(text, kind, record["application_id"],
                              ai=state["criteria"]["auto_contact"] or state["criteria"]["auto_present"])
        match["reply_analysis"] = facts
        if kind.startswith("candidate"):
            for doc in ("cv", "dossier"):
                attachment = _cv_attachment(store, message, doc)
                if attachment:
                    match["candidate"][doc] = attachment
                    for candidate in state["candidates"]:
                        if candidate["id"] == record["candidate_id"]:
                            candidate[doc] = attachment
            documented = "\n".join(str((match["candidate"].get(doc) or {}).get("text") or "") for doc in ("cv", "dossier")).strip()
            if documented:
                from navin.career.prospecting import score_candidate

                match["candidate"]["document_text"] = documented[:24000]
                for candidate in state["candidates"]:
                    if candidate["id"] == record["candidate_id"]:
                        candidate["document_text"] = documented[:24000]
                match.update(score_candidate(match["candidate"], store.get_opportunity(record["opportunity_id"]), state["criteria"]))
            if facts["declined"]:
                match.update({"interest": "declined", "availability": "declined", "stage": "rejected",
                              "sharing_consent": False})
                match["next_action"] = "Candidate declined. Other candidates remain active."
            elif kind in {"candidate", "candidate_followup"}:
                if facts["interest"]:
                    match["interest"] = "confirmed"
                if facts["available"]:
                    match["availability"] = "confirmed"
                if facts["availability"]:
                    match["availability_detail"] = facts["availability"]
                if facts["consent"]:
                    match.update({"sharing_consent": True, "consent_message_id": identity})
                if match["interest"] == match["availability"] == "confirmed" and match["stage"] in {"discovered", "contacted", "qualified"}:
                    match.update({"confirmed_at": time.time(), "stage": "qualified"})
                rate = re.search(r"\bTJM\s*(?::|de|est de)?\s*(\d+(?:[.,]\d+)?)\s+([A-Z]{3})\b", facts["text"], re.I)
                if rate and rate[2].upper() == state["criteria"]["currency"]:
                    value = float(rate[1].replace(",", "."))
                    if 0 < value <= 100000:
                        match["purchase_rate"] = value
                salary = re.search(r"(?:salaire|salary)\s*:\s*(\d+(?:[.,]\d+)?)\s+([A-Z]{3})\s*(?:/\s*)?(?:an|year|annuel|annual)\b", facts["text"], re.I)
                if salary and salary[2].upper() == state["criteria"]["currency"] and 0 < float(salary[1].replace(",", ".")) <= 10000000:
                    match["expected_salary"] = float(salary[1].replace(",", "."))
                missing = [label for key, label in (("interest", "interest"), ("availability", "availability")) if match[key] != "confirmed"]
                if not match.get("sharing_consent"):
                    missing.append("permission to share the CV")
                if not match["candidate"].get("cv"):
                    missing.append("CV")
                if state["criteria"]["require_dossier"] and not match["candidate"].get("dossier"):
                    missing.append("skills dossier")
                match["next_action"] = "Missing information: " + ", ".join(missing) if missing else "Ready for client presentation."
            elif kind == "candidate_interview":
                interview = match.setdefault("interview", {})
                # A candidate may accept the client's dated slot or offer another.
                # Only the exact client slot can be confirmed without another turn.
                if facts["slot_accept"] and interview.get("slot") and (not facts["slot"] or facts["slot"] == interview["slot"]):
                    interview.update({"candidate_confirmed": True, "candidate_reply": facts["text"], "status": "ready_to_confirm"})
                    match["next_action"] = "Confirm the interview with the client."
                else:
                    interview.update({"candidate_reply": facts["text"], "candidate_slot": facts["slot"], "status": "candidate_proposed"})
                    match["next_action"] = "Share available time slots with the client."
                match["preparation"] = preparation(store.get_opportunity(record["opportunity_id"]), match)
        elif kind in {"client", "client_slots"}:
            if facts["client_decline"]:
                match.update({"stage": "rejected", "next_action": "Candidate not selected. Other candidates remain active."})
            elif facts["client_accept"] or (kind == "client_slots" and facts["slot"]):
                interview = match.setdefault("interview", {})
                interview.update({"status": "client_accepted", "client_reply": facts["text"], "slot": facts["slot"],
                                  "revision": int(interview.get("revision", 0)) + 1, "candidate_confirmed": False})
                match.update({"stage": "interview", "next_action": "Informer le candidat et confirmer ses disponibilités."})
            else:
                match["next_action"] = "Client response received; clarification needed."
        else:
            match["next_action"] = "New interview message received."
        match.setdefault("history", []).append({"at": time.time(), "stage": match["stage"], "event": "reply_received"})
        _save(store, state)
        queue_mail_notification(mail_state, "sourcing-reply:" + identity, title="Carrière : réponse reçue",
                                detail=f"{match['candidate']['name']} - {match['next_action']}", event_type="sourcing_reply")
        return True


def _send(store: CareerStore, state: dict[str, Any], match: dict[str, Any], offer: dict[str, Any], kind: str,
          *, smtp_factory: Any = None, automatic: bool = False, deadline: float | None = None) -> dict[str, Any]:
    criteria = state["criteria"]
    to_candidate = kind.startswith("candidate")
    permission = "auto_contact" if to_candidate else "auto_present"
    def check_running():
        if deadline is not None and time.monotonic() >= deadline:
            raise CareerError("Cycle deadline reached. Resuming on the next cycle.")
        if automatic and (not store.load_loop().get("enabled") or store.load_loop_intent().get("enabled") is False):
            raise CareerError("Automatic cycle is paused.")
    check_running()
    config = normalize_mailbox(store.load_profile().get("mailbox"))
    if not config["enabled"] or not criteria[permission]:
        raise CareerError("Automatic sending is disabled.")
    validate_mailbox(config)
    account = _account_identity(store, config)
    password = store.get_secret("CAREER_SMTP_PASSWORD")
    if not password and not config.get("account_id"):
        raise CareerError("SMTP password is required for this legacy account.")
    candidate = match["candidate"]
    ref = _ref(offer["id"], candidate["id"])
    key = f"sourcing:{offer['id']}:{candidate['id']}:{kind}"
    if kind not in {"candidate", "client"}:
        key += ":" + (hashlib.sha256(str(match.get("reply_ids", [""])[-1]).encode()).hexdigest()[:16]
                      if kind == "candidate_followup" else str(match.get("interview", {}).get("revision", 1)))
    signature = criteria.get("signature") or criteria["company"].get("name", "")
    attachments = []
    if kind == "candidate":
        recipient = email_address(candidate.get("email"))
        subject = f"[{ref}] Proposition : {offer['title']}"
        body = (f"Bonjour,\n\nVotre profil a retenu notre attention pour : {offer['title']}.\n"
                f"Client : {offer.get('company') or 'à préciser'}\n"
                f"Lieu : {offer.get('location') or offer.get('country') or 'à confirmer'}\n"
                f"Compétences : {', '.join(offer.get('stack') or criteria['skills']) or 'Voir le besoin ci-dessous'}\n"
                f"Besoin : {str(offer.get('description') or '')[:1600]}\n"
                f"Offre : {offer.get('url') or ''}\n\n"
                "Si cette mission vous intéresse, merci de joindre votre CV et votre dossier de compétences, "
                "et de confirmer votre date de disponibilité, votre TJM ou salaire attendu ainsi que votre accord "
                "pour présenter votre candidature à ce client. Vous pouvez répondre librement ou avec cette première ligne :\n"
                f"ACCORD {ref}\n\n"
                "Cet accord confirme votre intérêt, votre disponibilité pour cette mission et votre autorisation de transmettre "
                "ce CV au client pour cette mission uniquement. Précisez votre date de disponibilité et vos conditions.\n"
                f"Pour un freelance, indiquez sur une ligne : TJM: votre_montant {criteria['currency']} / jour\n"
                f"Pour décliner : REFUS {ref}\n\n{signature}")
    elif kind == "candidate_followup":
        recipient = email_address(candidate.get("email"))
        subject = f"[{ref}] Compléments pour votre candidature : {offer['title']}"
        missing = []
        if match.get("interest") != "confirmed":
            missing.append("votre intérêt pour cette mission")
        if match.get("availability") != "confirmed" or not match.get("availability_detail"):
            missing.append("votre date de disponibilité")
        if not match.get("sharing_consent"):
            missing.append("votre autorisation de transmettre votre CV à ce client pour cette mission")
        if not candidate.get("cv"):
            missing.append("votre CV en pièce jointe PDF ou DOCX")
        elif not candidate["cv"].get("text"):
            missing.append("une version de votre CV avec du texte sélectionnable, pour étudier votre expérience")
        if criteria["require_dossier"] and not candidate.get("dossier"):
            missing.append("votre dossier de compétences en PDF ou DOCX")
        if offer.get("track") != "jobs" and not match.get("purchase_rate"):
            missing.append(f"votre TJM, avec la devise (exemple : TJM: 400 {criteria['currency']})")
        if offer.get("track") == "jobs" and not match.get("expected_salary") and (criteria["salary_max"] or offer.get("salary_max")):
            missing.append(f"votre salaire annuel attendu (exemple : Salaire: 45000 {criteria['currency']} / an)")
        if not missing:
            raise CareerError("No additional information is needed.")
        body = ("Bonjour,\n\nMerci pour votre retour. Pour compléter votre candidature, il nous manque :\n" +
                "\n".join("- " + item for item in missing) + "\n\nMerci de répondre à ce message.\n\n" + signature)
    elif kind == "client":
        from navin.career.needs import is_project_need

        if match.get("interest") != "confirmed" or match.get("availability") != "confirmed" or not match.get("sharing_consent"):
            raise CareerError("Candidate consent is required before sending the profile to the client.")
        if not match.get("availability_detail"):
            raise CareerError("The candidate availability date must be confirmed.")
        recipient = client_recipient(offer)
        if not recipient:
            raise CareerError("A client contact email is required.")
        recipient = email_address(recipient)
        attachments = [read_candidate_cv(store, candidate)]
        if not candidate.get("cv", {}).get("text"):
            raise CareerError("CV text extraction must be completed before automatic presentation.")
        if candidate.get("dossier"):
            attachments.append(read_candidate_cv(store, candidate, "dossier"))
        elif criteria["require_dossier"]:
            raise CareerError("A skills dossier is required before client presentation.")
        rate = proposal_rate(criteria, match, offer)
        if match["score"] < criteria["min_score"]:
            raise CareerError("Candidate match is below the configured minimum score.")
        if offer.get("track") != "jobs" and (not rate or not match.get("purchase_rate")):
            raise CareerError("Confirmed buying and selling day rates are required to propose a freelancer.")
        if criteria["buy_rate_max"] and float(match.get("purchase_rate") or 0) > criteria["buy_rate_max"]:
            raise CareerError("Buying day rate exceeds the configured limit.")
        if (offer.get("daily_rate_max") or offer.get("salary_max")) and offer.get("currency") != criteria["currency"]:
            raise CareerError("Client budget currency differs or is unknown. Confirm terms before presentation.")
        if offer.get("daily_rate_max") and rate > float(offer["daily_rate_max"]):
            raise CareerError("Selling day rate exceeds the published client budget.")
        subject = f"[{ref}] Profil proposé : {offer['title']}"
        commercial = f"TJM proposé : {rate:.2f} {criteria['currency']} / jour.\n" if rate and offer.get("track") != "jobs" else ""
        if is_project_need(offer):
            commercial = "Le chiffrage du projet reste à établir selon le périmètre et les livrables.\n"
        if offer.get("track") == "jobs":
            salary = float(match.get("expected_salary") or 0)
            caps = [float(v) for v in (criteria["salary_max"], offer.get("salary_max")) if v]
            if caps and (not salary or salary > min(caps)):
                raise CareerError("A confirmed annual salary within budget is required.")
            if salary:
                commercial = f"Salaire annuel attendu : {salary:.2f} {criteria['currency']}.\n"
        body = (f"Bonjour,\n\nPour votre besoin {offer['title']}, nous vous proposons le profil {candidate['name']}.\n"
                "Le candidat a confirmé son intérêt et autorisé le partage de son CV pour cette mission. "
                "Son CV est joint.\n"
                f"Disponibilité déclarée : {match.get('availability_detail') or 'Date de démarrage à convenir'}.\n"
                f"Compétences documentées : {', '.join(candidate.get('skills') or []) or 'Voir CV joint'}.\n"
                f"{commercial}\nSouhaitez-vous organiser un entretien à distance ou sur place ? "
                "Merci de préciser vos créneaux, le fuseau horaire et le lieu ou lien de connexion. "
                "Pour faciliter la confirmation automatique, vous pouvez écrire le créneau au format "
                "AAAA-MM-JJTHH:MM+HH:MM.\n\n" + signature)
    else:
        from navin.career.sourcing_replies import preparation, valid_slot

        interview = match.get("interview", {})
        if match.get("stage") != "interview" or not interview:
            raise CareerError("Client agreement to an interview is required.")
        recipient = email_address(candidate.get("email")) if to_candidate else email_address(client_recipient(offer))
        if kind == "candidate_interview":
            subject = f"[{ref}] Bonne nouvelle : entretien pour {offer['title']}"
            body = (f"Bonjour {candidate['name']},\n\nLe client souhaite vous rencontrer pour {offer['title']}.\n"
                    f"Son message :\n{interview.get('client_reply', '')}\n\n"
                    "Merci de confirmer le créneau proposé ou d'indiquer vos disponibilités et votre fuseau horaire. "
                    "Nous confirmerons ensuite l'entretien au client et préparerons cet échange avec vous.\n\n" + signature)
        elif kind == "client_slots":
            subject = f"[{ref}] Disponibilités de {candidate['name']}"
            body = (f"Bonjour,\n\nVoici la réponse de {candidate['name']} pour l'entretien concernant {offer['title']} :\n"
                    f"Créneau proposé : {interview.get('candidate_slot') or 'Le candidat demande de nouveaux créneaux.'}\n\n"
                    "Merci de confirmer le créneau retenu, son fuseau horaire et les modalités (sur place ou à distance). "
                    "Pour un traitement automatique, indiquez le créneau au format AAAA-MM-JJTHH:MM+HH:MM.\n\n" + signature)
        elif kind in {"client_confirmation", "candidate_preparation"}:
            if not interview.get("candidate_confirmed") or not valid_slot(str(interview.get("slot") or "")):
                raise CareerError("A future time slot with timezone and candidate agreement is required.")
            subject = f"[{ref}] Entretien confirmé : {offer['title']}"
            body = (f"Bonjour,\n\nL'entretien avec {candidate['name']} est confirmé pour le {interview['slot']}.\n"
                    f"Modalités transmises par le client :\n{interview.get('client_reply', '')}\n\n")
            if kind == "candidate_preparation":
                match["preparation"] = preparation(offer, match)
                body += match["preparation"] + "\n\n"
            body += signature
        else:
            raise CareerError("Unknown communication stage.")
    cc = [email_address(a) for a in criteria["candidate_cc" if to_candidate else "client_cc"]]
    cc = list(dict.fromkeys(a for a in cc if a != recipient))
    with mail_lock(store):
        mail_state = load_mail_state(store)
        prior = mail_state["outbox"].get(key)
        if prior and (prior.get("status") != "failed" or float(prior.get("retry_at") or 0) > time.time()):
            # Even uncertain SMTP acceptance must never cause duplicate outreach.
            if prior.get("status") == "accepted":
                _advance(match, kind)
            return {**prior, "reused": True}
        day = _day_key(store, time.time())
        attempts = [*mail_state["outbox"].values(), *mail_state.get("sourcing_attempts", [])]
        sent = [r for r in attempts if r.get("sourcing_kind") and
                _day_key(store, float(r.get("attempted_at") or 0)) == day]
        if len(sent) >= int(criteria["max_per_day"]):
            raise CareerError("Daily limit reached.")
        if kind == "candidate" and any(r.get("sourcing_kind") == "candidate" and r.get("recipient") == recipient and r.get("status") in {"accepted", "unknown", "sending"} for r in sent):
            raise CareerError("This candidate has already been contacted today.")
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = formataddr((config["sender_name"], config["sender_email"]))
        message["To"] = recipient
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message["Message-ID"] = make_msgid()
        last_thread = match.get("candidate_mail" if to_candidate else "client_mail", {}).get("message_id")
        if last_thread:
            message["In-Reply-To"] = last_thread
            message["References"] = last_thread
        message.set_content(body)
        for attachment in attachments:
            main, sub = attachment["mime"].split("/", 1)
            message.add_attachment(base64.b64decode(attachment["data_b64"]), maintype=main, subtype=sub, filename=attachment["name"])
        record = {"application_id": ref, "opportunity_id": offer["id"], "candidate_id": candidate["id"],
                  "sourcing_kind": kind, "recipient": recipient, "cc": cc, "subject": subject, "body": body,
                  "message_id": str(message["Message-ID"]), "status": "sending", "attempted_at": time.time(),
                  "attempt_count": int((prior or {}).get("attempt_count", 0)) + 1}
        if prior:
            mail_state.setdefault("sourcing_attempts", []).append(prior)
        mail_state["outbox"][key] = record
        save_mail_state(store, mail_state)
        client, data_started = None, False
        try:
            client = (smtp_factory or _smtp_connection)(config)
            client.login(config["smtp_username"], password)
            code, _ = client.mail(config["sender_email"])
            if code != 250:
                raise CareerError("Sender rejected.")
            for address in [recipient, *cc]:
                code, _ = client.rcpt(address)
                if code not in {250, 251}:
                    raise CareerError("Recipient rejected.")
            latest = normalize_mailbox(store.load_profile().get("mailbox"))
            check_running()
            live = _state(store)["criteria"]
            if _account_identity(store, latest) != account or not latest["enabled"] or not live[permission]:
                raise CareerError("Settings changed. Sending was stopped.")
            data_started = True
            code, _ = client.data(message.as_bytes())
            if code != 250:
                data_started = False
                raise CareerError("The server rejected the message.")
            record.update({"status": "accepted", "accepted_at": time.time()})
            _advance(match, kind)
            match.setdefault("history", []).append({"at": time.time(), "event": "email_sent", "kind": kind})
            match.setdefault("emails", []).append({"kind": kind, "recipient": recipient, "cc": cc, "body": body,
                                                    "status": "accepted", "at": time.time()})
            queue_mail_notification(mail_state, key, title="Carrière : email envoyé", detail=f"{subject} - {recipient}", event_type="sourcing_sent")
        except Exception as exc:
            record.update({"status": "unknown" if data_started else "failed", "error": _error_code(exc)})
            if not data_started:
                record["retry_at"] = time.time() + min(3600, 60 * 2 ** min(record["attempt_count"], 6))
        finally:
            if client is not None:
                _close_smtp(client)
            save_mail_state(store, mail_state)
        return record


def run_sourcing_cycle(store: CareerStore, *, smtp_factory: Any = None, imap_factory: Any = None,
                       deadline: float | None = None, automatic: bool = False,
                       sync_result: dict[str, Any] | None = None) -> dict[str, Any]:
    from navin.career.mail import flush_mail_notifications
    from navin.career.mailbox import sync_mailbox

    deadline = deadline or time.monotonic() + 90
    sync = sync_result if sync_result is not None else sync_mailbox(store, imap_factory=imap_factory, deadline=deadline)
    outcomes = []
    with FileLock(str(store.root / "prospecting.lock"), timeout=0):
        state = _state(store)
        for oid, group in state["matches"].items():
            try:
                offer = store.get_opportunity(oid)
            except CareerError:
                continue
            if offer.get("archived"):
                continue
            for match in group["results"]:
                if time.monotonic() > deadline or sum(not r["reused"] for r in outcomes) >= 25:
                    break
                if match["stage"] in {"rejected", "placed", "contracted", "submitted"} or (match["stage"] == "discovered" and match["score"] < state["criteria"]["min_score"]):
                    continue
                if match["stage"] == "interview":
                    kind = {"client_accepted": "candidate_interview", "candidate_proposed": "client_slots",
                            "ready_to_confirm": "client_confirmation", "client_confirmed": "candidate_preparation"}.get(match.get("interview", {}).get("status"))
                    if not kind:
                        continue
                else:
                    ready = (match.get("sharing_consent") and match["candidate"].get("cv", {}).get("text") and
                             match["interest"] == match["availability"] == "confirmed" and
                             match.get("availability_detail") and
                             (not state["criteria"]["require_dossier"] or match["candidate"].get("dossier")) and
                             (offer.get("track") == "jobs" or match.get("purchase_rate")) and
                             (offer.get("track") != "jobs" or match.get("expected_salary") or
                              not (state["criteria"]["salary_max"] or offer.get("salary_max"))))
                    kind = "client" if ready else "candidate_followup" if match.get("reply_ids") else "candidate"
                if not state["criteria"]["auto_contact" if kind.startswith("candidate") else "auto_present"]:
                    continue
                try:
                    receipt = _send(store, state, match, offer, kind, smtp_factory=smtp_factory, automatic=automatic, deadline=deadline)
                    match[kind + "_mail"] = {k: receipt[k] for k in ("status", "recipient", "message_id", "subject", "body", "error") if k in receipt}
                    outcomes.append({"id": oid, "candidate_id": match["candidate"]["id"], "status": receipt["status"], "reused": bool(receipt.get("reused"))})
                    if receipt["status"] in {"unknown", "failed"}:
                        match["next_action"] = "Check send status: " + receipt["status"]
                except CareerError as exc:
                    match["next_action"] = str(exc)
                except (ValueError, TypeError, OSError):
                    match["next_action"] = "Review dossier data. Other dossiers will continue."
                _save(store, state)
        state["automation"] = {"at": time.time(), "sync": sync, "outcomes": outcomes}
        _save(store, state)
    flush_mail_notifications(store)
    return {"sent": sum(r["status"] == "accepted" and not r["reused"] for r in outcomes), "sync": sync}
