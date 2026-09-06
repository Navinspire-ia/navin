"""P0-B: marketing copy must not claim false cloud/RL features."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MESSAGES = ROOT / "site" / "front" / "messages"

# Exact false claims removed in the unique Navin narrative pass.
FORBIDDEN_EN = (
    "Online reinforcement learning",
    "Cloud history and full sync",
)
FORBIDDEN_FR = (
    "Apprentissage par renforcement en ligne",
    "Historique cloud et synchronisation complète",
)


class NarrativeCopyTest(unittest.TestCase):
    def _load(self, name: str) -> str:
        path = MESSAGES / name
        self.assertTrue(path.is_file(), f"missing {path}")
        return path.read_text(encoding="utf-8")

    def test_en_json_has_no_false_claims(self) -> None:
        # The hero was rebranded ("far more than an AI IDE"); RiskLens now
        # lives in the preview rows, checked below. Only the false-claim
        # guarantees remain hero-independent.
        text = self._load("en.json")
        data = json.loads(text)
        self.assertTrue(data["hero"]["title"].strip())
        for phrase in FORBIDDEN_EN:
            self.assertNotIn(phrase, text, f"en.json still contains {phrase!r}")

    def test_fr_json_has_no_false_claims(self) -> None:
        text = self._load("fr.json")
        data = json.loads(text)
        self.assertTrue(data["hero"]["title"].strip())
        for phrase in FORBIDDEN_FR:
            self.assertNotIn(phrase, text, f"fr.json still contains {phrase!r}")
        for phrase in FORBIDDEN_EN:
            self.assertNotIn(phrase, text, f"fr.json still contains EN claim {phrase!r}")

    def test_preview_rl_is_risklens_not_reinforcement(self) -> None:
        for locale in ("en.json", "fr.json", "ar.json"):
            data = json.loads(self._load(locale))
            rl = data["preview"]["rows"]["rl"]
            self.assertNotIn("reinforcement", rl.lower())
            self.assertNotIn("renforcement", rl.lower())
            self.assertIn("RiskLens", rl)

    def test_pricing_uses_account_sync_language(self) -> None:
        en = json.loads(self._load("en.json"))
        features = []
        for plan in en["pricing"]["plans"].values():
            features.extend(plan["features"])
        joined = "\n".join(features)
        self.assertIn("Account sync (plan, license, usage)", joined)
        self.assertNotIn("Cloud history and full sync", joined)
        self.assertNotIn("basic sync", joined)
        self.assertNotIn("Full sync", joined)
        self.assertNotIn("Advanced history and sync", joined)


if __name__ == "__main__":
    unittest.main()
