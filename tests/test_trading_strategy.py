"""Natural-language mandate compiler."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.trading.store import TradingStore
from navin.trading.strategy import infer_skill, parse_strategy


class TradingStrategyParseTest(unittest.TestCase):
    def test_french_nasdaq_mandate(self) -> None:
        row = parse_strategy(
            "Analyse chaque matin les actions US du Nasdaq 100. "
            "Ne considere que les societes avec croissance CA > 15 %, dette raisonnable "
            "et tendance haussiere. Stop loss a 5 %. N'investis jamais plus de 3 % "
            "du portefeuille. Si confiance < 80 %, ne fais rien. "
            "Demande-moi validation avant tout achat superieur a 2000 EUR."
        )
        self.assertEqual(row["universe"], "NASDAQ100")
        self.assertEqual(row["min_revenue_growth"], 15.0)
        self.assertEqual(row["stop_loss"], 5.0)
        self.assertEqual(row["max_position"], 3.0)
        self.assertEqual(row["min_confidence"], 80.0)
        self.assertEqual(row["approval_notional"], 2000.0)
        self.assertEqual(row["execution_mode"], "autonomous")
        self.assertTrue(row["require_uptrend"])

    def test_ask_every_buy_stays_approval(self) -> None:
        row = parse_strategy("Scan Nasdaq. Demande-moi validation avant tout achat.")
        self.assertEqual(row["execution_mode"], "approval")

    def test_plain_brief_fills_paper(self) -> None:
        row = parse_strategy("Surveille BTC, ETH et SOL 24/7.")
        self.assertEqual(row["execution_mode"], "autonomous")

    def test_crypto_24_7(self) -> None:
        row = parse_strategy(
            "Surveille BTC, ETH et SOL 24/7. Entre uniquement si le sentiment "
            "news n'est pas fortement negatif."
        )
        self.assertEqual(row["universe"], "CRYPTO")
        self.assertIn("BTC-USD", row["symbols"])
        self.assertEqual(row["scan_interval_s"], 300)
        self.assertEqual(infer_skill(row["brief"]), "crypto-swing")

    def test_autonomous_keyword(self) -> None:
        row = parse_strategy("Paper auto, autonomous execution on Nasdaq.")
        self.assertEqual(row["execution_mode"], "autonomous")

    def test_old_approval_book_migrates_to_paper_fills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "strategies.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "old",
                            "execution_mode": "approval",
                            "min_confidence": 80,
                            "require_consensus": "4/5",
                            "active": True,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            store = TradingStore(root)
            row = store.active_strategy()
            self.assertEqual(row["execution_mode"], "autonomous")
            self.assertEqual(row["min_confidence"], 55.0)
            self.assertEqual(row["require_consensus"], "2/5")
            self.assertEqual(store.load_settings().get("execution_mode"), "autonomous")


if __name__ == "__main__":
    unittest.main()
