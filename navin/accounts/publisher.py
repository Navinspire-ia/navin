"""OAuth registrations owned by Navinspire, populated by the desktop release build.

Google: Desktop app, Gmail and Calendar APIs, verified consent screen.
Microsoft: public desktop client, personal and organizational accounts,
redirect URI http://localhost/callback, delegated Graph permissions.
The Google desktop client secret is a public-client registration parameter,
not a confidential credential. User tokens belong only in the OS vault.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""
MICROSOFT_CLIENT_ID = ""

REGISTRATION_FILE = Path(__file__).with_name("publisher.json")


def _validated(raw: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        raise ValueError("OAuth publisher configuration must be a JSON object.")
    result = {}
    for provider in ("google", "microsoft"):
        entry = raw.get(provider) or {}
        if not isinstance(entry, dict):
            raise ValueError("Invalid OAuth publisher configuration for " + provider + ".")
        client_id = str(entry.get("client_id") or "").strip()
        client_secret = str(entry.get("client_secret") or "").strip() if provider == "google" else ""
        pattern = r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com" if provider == "google" else r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}"
        if client_id and not re.fullmatch(pattern, client_id):
            raise ValueError("Invalid " + provider + " desktop application client ID.")
        if client_secret and (not client_id or len(client_secret) > 512 or any(c.isspace() for c in client_secret)):
            raise ValueError("Invalid Google desktop application registration parameter.")
        result[provider] = {"client_id": client_id, "client_secret": client_secret}
    return result


def configuration() -> dict[str, dict[str, str]]:
    """Load the publisher identity bundled with Navin, without any user tokens."""
    raw = json.loads(REGISTRATION_FILE.read_text(encoding="utf-8")) if REGISTRATION_FILE.is_file() else {
        "google": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET},
        "microsoft": {"client_id": MICROSOFT_CLIENT_ID},
    }
    result = _validated(raw)
    for provider in result:
        client_id = os.environ.get(f"NAVIN_{provider.upper()}_CLIENT_ID", "").strip()
        if client_id:
            # A different app ID must never inherit another app's parameter.
            if client_id != result[provider]["client_id"]:
                result[provider]["client_secret"] = ""
            result[provider]["client_id"] = client_id
    if os.environ.get("NAVIN_GOOGLE_CLIENT_SECRET"):
        result["google"]["client_secret"] = os.environ["NAVIN_GOOGLE_CLIENT_SECRET"].strip()
    return _validated(result)


def write_configuration(path: Path, raw: Any) -> dict[str, dict[str, str]]:
    from navin.career.store import _atomic_write

    config = _validated(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, config)
    return config


def bundle_registration(folder: Path) -> list[tuple[str, str]]:
    """Stage the shared public client registrations for every desktop OS."""
    config = configuration()
    if not any(entry["client_id"] for entry in config.values()):
        return []
    path = folder / "publisher.json"
    write_configuration(path, config)
    return [(str(path), "navin/accounts")]


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure Navin's shared desktop OAuth registrations once, before distribution.",
        epilog="Google: create a Desktop app client in Google Auth Platform. Microsoft: register a mobile/desktop public client for personal and organizational accounts, with http://localhost/callback as a redirect URI. Each end user then signs into their own account in Navin.")
    parser.add_argument("--google-client-json", type=Path, help="Downloaded Google Desktop app JSON, never a web application or service account key.")
    parser.add_argument("--microsoft-client-id", help="Microsoft Entra application (client) ID; no client secret is used.")
    parser.add_argument("--from-env", action="store_true", help="Import NAVIN_GOOGLE_CLIENT_ID, NAVIN_GOOGLE_CLIENT_SECRET and NAVIN_MICROSOFT_CLIENT_ID.")
    parser.add_argument("--output", type=Path, default=REGISTRATION_FILE, help="Publisher file to bundle with Navin.")
    parser.add_argument("--check", action="store_true", help="Report readiness only, without printing credentials or writing files.")
    args = parser.parse_args()
    try:
        config = configuration()
        if args.google_client_json:
            raw = json.loads(args.google_client_json.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("installed"), dict):
                raise ValueError("Choose the JSON for a Google Desktop app client, not a web client or service account.")
            config["google"] = raw["installed"]
        if args.microsoft_client_id:
            config["microsoft"] = {"client_id": args.microsoft_client_id}
        config = _validated(config)
        if not args.check:
            if not (args.google_client_json or args.microsoft_client_id or args.from_env):
                parser.error("Choose --google-client-json, --microsoft-client-id, --from-env or --check.")
            config = write_configuration(args.output, config)
        print(json.dumps({provider: {"configured": bool(entry["client_id"])} for provider, entry in config.items()}))
    except (OSError, ValueError) as exc:
        parser.error(str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "The OAuth publisher file could not be read or written.")


if __name__ == "__main__":
    main()
