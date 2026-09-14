# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Revocable browser analysis and import. Tokens never grant general Navin access."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from navin.browser_import import KINDS, import_record
from navin.career.errors import CareerError
from navin.career.store import _atomic_write, _read_json
from navin.config.paths import get_runtime_subdir


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class BrowserBridge:
    def __init__(self, root: Path | None = None, *, roots: dict[str, Path] | None = None):
        self.root = root or get_runtime_subdir("browser-bridge")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.path = self.root / "bridge.json"
        self.roots = roots

    def handle(self, action: str, body: dict[str, Any], *, token: str = "", admin: bool = False,
               local: bool = False) -> dict[str, Any]:
        try:
            with FileLock(str(self.root / "bridge.lock"), timeout=0):
                return self._handle(action, body, token=token, admin=admin, local=local)
        except Timeout:
            raise CareerError("Un import est en cours. Réessayez dans un instant.", status=409) from None

    def _handle(self, action: str, body: dict[str, Any], *, token: str, admin: bool, local: bool) -> dict[str, Any]:
        state = _read_json(self.path, {"devices": [], "pairings": [], "receipts": [], "attempts": []})
        now = time.time()
        if admin:
            if action == "package":
                import base64

                browser = body.get("browser")
                if browser not in {"chrome", "edge", "firefox"}:
                    raise CareerError("Choisissez Chrome, Microsoft Edge ou Firefox.")
                package = Path(__file__).parents[1] / "browser_extension" / f"{browser}.zip"
                if not package.is_file():
                    raise CareerError("Le paquet de l'extension n'est pas présent dans cette installation.", status=404)
                return {"package": {"name": f"navin-import-{browser}.zip", "mime": "application/zip",
                                    "data": base64.b64encode(package.read_bytes()).decode()}}
            if action == "create":
                code = secrets.token_hex(12).upper()
                state["pairings"] = [p for p in state["pairings"] if p["expires_at"] > now][-4:]
                state["pairings"].append({"hash": _digest(code), "expires_at": now + 300,
                                          "label": str(body.get("label") or "Mon navigateur")[:100]})
                _atomic_write(self.path, state)
                return {"code": code, "expires_at": now + 300}
            if action == "revoke":
                device = next((d for d in state["devices"] if d["id"] == body.get("id")), None)
                if not device:
                    raise CareerError("Navigateur introuvable.", status=404)
                state["devices"].remove(device)
                _atomic_write(self.path, state)
            elif action != "devices":
                raise CareerError("Action de connexion inconnue.")
            return {"devices": [{k: v for k, v in d.items() if k != "hash"} for d in state["devices"]],
                    "receipts": state["receipts"][-50:], "downloads": {
                        browser: (Path(__file__).parents[1] / "browser_extension" / f"{browser}.zip").is_file()
                        for browser in ("chrome", "edge", "firefox")}}
        if action == "pair":
            state["attempts"] = [t for t in state["attempts"] if t > now - 60]
            if len(state["attempts"]) >= 12:
                raise CareerError("Trop de tentatives d'appairage. Réessayez dans une minute.", status=429)
            state["attempts"].append(now)
            code = str(body.get("code") or "").replace(" ", "").replace("-", "").upper()
            pairing = next((p for p in state["pairings"] if p["expires_at"] > now and hmac.compare_digest(p["hash"], _digest(code))), None) if code else None
            if not pairing and not local:
                _atomic_write(self.path, state)
                raise CareerError("Code invalide, expiré ou déjà utilisé. Générez un nouveau code dans Navin.", status=401)
            if len(state["devices"]) >= 30:
                raise CareerError("Révoquez un navigateur avant d'en ajouter un autre.", status=409)
            access = secrets.token_urlsafe(32)
            label = pairing["label"] if pairing else str(body.get("label") or "Mon navigateur")[:100]
            device = {"id": uuid.uuid4().hex, "hash": _digest(access), "label": label,
                      "created_at": now, "last_seen": now, "scopes": list(KINDS)}
            state["devices"].append(device)
            if pairing:
                state["pairings"].remove(pairing)
            _atomic_write(self.path, state)
            return {"token": access, "device": {k: v for k, v in device.items() if k != "hash"}, "auto": not bool(pairing)}
        device = next((d for d in state["devices"] if token and hmac.compare_digest(d["hash"], _digest(token))), None)
        if not device:
            raise CareerError("Extension non appairée ou accès révoqué. Reconnectez-la depuis Navin.", status=401)
        device["last_seen"] = now
        if action == "disconnect":
            state["devices"].remove(device)
            _atomic_write(self.path, state)
            return {"disconnected": True}
        if action == "status":
            _atomic_write(self.path, state)
            return {"device": {k: v for k, v in device.items() if k != "hash"},
                    "receipts": [r for r in state["receipts"] if r["device"] == device["id"]][-20:],
                    "analysis_version": 1}
        if action in {"criteria", "analyze"}:
            from navin.browser_analysis import analyze_records

            if action == "criteria":
                return analyze_records(roots=self.roots)
            records = body.get("records")
            if not isinstance(records, list) or not 1 <= len(records) <= 100 or len(json.dumps(records).encode()) > 1000000:
                raise CareerError("Analysez de 1 à 100 fiches, dans la limite de 1 Mo.")
            if any(not isinstance(row, dict) or row.get("kind") not in device["scopes"] for row in records):
                raise CareerError("Type de fiche non autorisé.", status=403)
            return analyze_records(records, roots=self.roots)
        if action != "import":
            raise CareerError("Cette connexion permet uniquement l'analyse et l'import de fiches.", status=403)
        if body.get("criteria_revision"):
            from navin.browser_analysis import analyze_records

            if body["criteria_revision"] != analyze_records(roots=self.roots)["revision"]:
                raise CareerError("Les critères Navin ont changé. Relancez l'analyse avant de valider l'import.", status=409)
        records = body.get("records")
        if not isinstance(records, list) or not 1 <= len(records) <= 100 or len(json.dumps(records)) > 1000000:
            raise CareerError("Envoyez de 1 à 100 fiches, dans la limite de 1 Mo.")
        results = []
        for raw in records:
            if not isinstance(raw, dict) or raw.get("kind") not in device["scopes"]:
                raise CareerError("Type de fiche non autorisé.", status=403)
            identity = str(raw.get("request_id") or "")
            if not 16 <= len(identity) <= 80:
                raise CareerError("L'identifiant de l'import est invalide.")
            fingerprint = _digest(json.dumps(raw, sort_keys=True, ensure_ascii=False))
            previous = next((r for r in state["receipts"] if r["device"] == device["id"] and r["request_id"] == identity), None)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise CareerError("La fiche a changé. Relisez-la avant un nouvel envoi.", status=409)
                results.append(previous)
                continue
            try:
                result = import_record(raw, device["id"], roots=self.roots)
            except CareerError as exc:
                # Validation errors remain actionable; transient busy errors may be retried.
                results.append({"request_id": identity, "status": "error", "error": exc.message})
                continue
            receipt = {**result, "request_id": identity, "fingerprint": fingerprint, "device": device["id"],
                       "kind": raw["kind"], "at": now}
            state["receipts"] = (state["receipts"] + [receipt])[-500:]
            _atomic_write(self.path, state)
            results.append(receipt)
        return {"results": results}
