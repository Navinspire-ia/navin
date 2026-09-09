# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""User-or-agent mandate: domains, countries, risk, targets, alerts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.trading.mandate import (
    agent_risk_for,
    book_currency,
    normalize_mandate,
    parse_mandate_from_brief,
    resolve_mandate,
)
from navin.trading.loop import desk_snapshot
from navin.trading.notify import alert_cycle
from navin.trading.store import TradingStore
from navin.trading.strategy import infer_skill, parse_strategy
from navin.trading.universes import symbols_for_mandate
from navin.webui.trading_api import handle_trading_action


class TradingMandateTest(unittest.TestCase):
    def test_book_currency_euro_area_vs_dollar(self) -> None:
        self.assertEqual(book_currency(["FR"]), "EUR")
        self.assertEqual(book_currency(["FR", "DE"]), "EUR")
        self.assertEqual(book_currency(["AE"]), "USD")
        self.assertEqual(book_currency(["FR", "AE"]), "USD")
        self.assertEqual(book_currency([]), "USD")
        resolved_fr = resolve_mandate({"domains": ["equities"], "countries": ["FR"], "domains_mode": "user", "countries_mode": "user"})
        self.assertEqual(resolved_fr["currency"], "EUR")
        resolved_ae = resolve_mandate({"domains": ["crypto"], "countries": ["AE"], "domains_mode": "user", "countries_mode": "user"})
        self.assertEqual(resolved_ae["currency"], "USD")

    def test_store_book_currency_follows_mandate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            settings = store.load_settings()
            settings["mandate"] = normalize_mandate(
                {"domains": ["crypto"], "countries": ["AE"], "domains_mode": "user", "countries_mode": "user"}
            )
            store.save_settings(settings)
            self.assertEqual(store.load_settings()["currency"], "USD")
            self.assertEqual(store.load_portfolio()["currency"], "USD")
            settings["mandate"] = normalize_mandate(
                {"domains": ["equities"], "countries": ["FR"], "domains_mode": "user", "countries_mode": "user"}
            )
            store.save_settings(settings)
            self.assertEqual(store.load_portfolio()["currency"], "EUR")

    def test_aliases_and_agent_modes(self) -> None:
        row = parse_mandate_from_brief(
            "bourse, crypto, mft et immobilier en France et aux Emirats. "
            "Laisse l'agent gerer le risque. Gain cible 15%."
        )
        self.assertEqual(set(row["domains"]), {"equities", "crypto", "nft", "realestate"})
        self.assertEqual(row["domains_mode"], "user")
        self.assertIn("FR", row["countries"])
        self.assertIn("AE", row["countries"])
        self.assertEqual(row["risk_mode"], "agent")
        self.assertEqual(row["target_mode"], "user")
        self.assertEqual(row["target_return_pct"], 15.0)

    def test_risk_only_brief_keeps_locked_countries(self) -> None:
        row = parse_mandate_from_brief(
            "Laisse l'agent gerer le risque. Gain cible 11%.",
            {"countries": ["US", "FR", "AE"], "countries_mode": "user", "domains": ["equities"]},
        )
        self.assertEqual(row["countries"], ["US", "FR", "AE"])
        self.assertEqual(row["countries_mode"], "user")
        self.assertEqual(row["domains_mode"], "user")
        self.assertEqual(row["risk_mode"], "agent")
        self.assertEqual(row["target_return_pct"], 11.0)

    def test_worldwide_leaves_countries_to_agent(self) -> None:
        row = parse_mandate_from_brief("Tous les pays du monde, laisse l'agent choisir les domaines.")
        resolved = resolve_mandate(row)
        self.assertEqual(resolved["countries_mode"], "agent")
        self.assertEqual(resolved["domains_mode"], "agent")
        self.assertGreaterEqual(len(resolved["active_countries"]), 40)
        self.assertEqual(set(resolved["active_domains"]), {"equities", "crypto", "nft", "realestate"})

    def test_agent_risk_tightens_on_nft(self) -> None:
        nft = agent_risk_for(["nft"])
        eq = agent_risk_for(["equities"])
        self.assertLess(nft["max_position_pct"], eq["max_position_pct"])
        self.assertLessEqual(nft["approval_notional"], 800.0)
        re_only = agent_risk_for(["realestate"])
        self.assertEqual(re_only["max_position_pct"], 5.0)

    def test_symbols_cover_domains(self) -> None:
        tape = symbols_for_mandate(["crypto", "nft", "realestate"], ["FR", "AE"])
        self.assertIn("BTC-USD", tape)
        self.assertIn("NFT-BAYC", tape)
        self.assertIn("LI.PA", tape)
        world = symbols_for_mandate(["equities"], ["US", "FR", "JP", "AE", "BR", "IN", "ZA", "NG"])
        self.assertIn("AAPL", world)
        self.assertIn("MC.PA", world)

    def test_tapes_are_explicit_pairs(self) -> None:
        row = normalize_mandate(
            {
                "tapes": [
                    {"domain": "equities", "country": "FR"},
                    {"domain": "crypto", "country": "US"},
                    {"domain": "equities", "country": "FR"},
                ]
            }
        )
        self.assertEqual(row["domains"], ["equities", "crypto"])
        self.assertEqual(row["countries"], ["FR", "US"])
        self.assertEqual(
            row["tapes"],
            [
                {"domain": "equities", "country": "FR"},
                {"domain": "crypto", "country": "US"},
            ],
        )
        resolved = resolve_mandate(row)
        self.assertEqual(len(resolved["active_tapes"]), 2)
        tape = symbols_for_mandate([], [], tapes=row["tapes"])
        self.assertIn("MC.PA", tape)
        self.assertIn("BTC-USD", tape)

    def test_normalize_keeps_channels(self) -> None:
        row = normalize_mandate(
            {
                "domains": ["immobilier", "mft"],
                "channels": {"telegram": True, "telegram_to": "123", "email": 1, "email_to": "a@b.c"},
            }
        )
        self.assertEqual(set(row["domains"]), {"nft", "realestate"})
        self.assertTrue(row["channels"]["telegram"])
        self.assertEqual(row["channels"]["telegram_to"], "123")
        self.assertTrue(row["channels"]["email"])


class TradingMandateApiTest(unittest.TestCase):
    def test_mandate_and_compile_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            with patch("navin.webui.trading_api._store", return_value=store), patch(
                "navin.trading.loop.fetch_quotes", return_value={}
            ):
                saved = handle_trading_action(
                    "mandate",
                    {
                        "domains": ["crypto", "nft"],
                        "domains_mode": "user",
                        "countries": ["FR", "AE"],
                        "countries_mode": "user",
                        "risk_mode": "agent",
                        "target_mode": "user",
                        "target_return_pct": 18,
                        "channels": {"telegram": True, "telegram_to": "99"},
                    },
                )
                self.assertEqual(saved["mandate"]["active_domains"], ["crypto", "nft"])
                self.assertEqual(saved["mandate"]["risk_mode"], "agent")
                self.assertEqual(saved["mandate"]["channels"]["telegram_to"], "99")
                snap = handle_trading_action("snapshot")
                self.assertIn("catalog", snap)
                self.assertIn("channels", snap)
                self.assertIn("nft", [row["id"] for row in snap["catalog"]["domains"]])
                compiled = handle_trading_action(
                    "strategy",
                    {"brief": "Immo et crypto en France. Laisse l'agent gerer le risque. Gain 10%."},
                )
                mandate = compiled["settings"]["mandate"]
                self.assertIn("realestate", mandate["domains"])
                self.assertIn("crypto", mandate["domains"])
                self.assertEqual(mandate["risk_mode"], "agent")
                self.assertEqual(mandate["target_return_pct"], 10.0)
                handle_trading_action(
                    "mandate",
                    {"countries": ["US", "FR", "AE"], "countries_mode": "user"},
                )
                merged = handle_trading_action(
                    "strategy",
                    {"brief": "Laisse l'agent gerer le risque. Gain cible 11%."},
                )
                self.assertEqual(set(merged["settings"]["mandate"]["countries"]), {"US", "FR", "AE"})
                self.assertEqual(merged["strategy"]["universe"], "NASDAQ100")


class TradingDeskPreviewTest(unittest.TestCase):
    def test_snapshot_quotes_the_mandate_tape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            settings = store.load_settings()
            settings["mandate"] = normalize_mandate(
                {
                    "domains": ["crypto", "nft", "realestate"],
                    "countries": ["FR"],
                    "domains_mode": "user",
                    "countries_mode": "user",
                }
            )
            store.save_settings(settings)

            def http_get(url: str) -> bytes:
                if "nfts/" in url:
                    return b'{"floor_price":{"usd":12.5},"floor_price_in_usd_24h_percentage_change":1.2}'
                if "ticker/price" in url or "api.binance" in url:
                    return b'{"lastPrice":"65000","priceChange":"100","priceChangePercent":"1.5","volume":"10"}'
                if "quote?" in url:
                    return b'{"quoteResponse":{"result":[{"symbol":"LI.PA","regularMarketPrice":90,"regularMarketChangePercent":0.4,"currency":"EUR"}]}}'
                return b"{}"

            snap = desk_snapshot(store, http_get=http_get)
            symbols = set(snap["quotes"])
            self.assertTrue(symbols.intersection({"BTC-USD", "ETH-USD", "NFT-BAYC", "LI.PA"}))


class TradingNotifyTest(unittest.TestCase):
    def test_idle_cycle_stays_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            self.assertIsNone(alert_cycle(store, {"orders": [], "stops": [], "reason": "ok"}))

    def test_pending_cycle_journals_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            with patch("navin.trading.notify.notify", return_value=True), patch(
                "navin.trading.notify._put_outbound", return_value=False
            ), patch("navin.trading.notify._send_email", return_value=False):
                result = alert_cycle(
                    store,
                    {
                        "reason": "cycle",
                        "orders": [{"status": "pending", "side": "BUY", "qty": 1, "symbol": "NVDA"}],
                        "stops": [],
                    },
                )
            self.assertIsNotNone(result)
            kinds = [row.get("kind") for row in store.load_journal(10)]
            self.assertIn("alert", kinds)


class TradingSkillInferTest(unittest.TestCase):
    def test_domain_skills(self) -> None:
        self.assertEqual(infer_skill("acheter un nft floor bayc"), "nft-collector")
        self.assertEqual(infer_skill("foncieres et immobilier cote"), "real-estate-investor")
        self.assertEqual(infer_skill("bourse mondiale cac dax"), "global-equities")
        self.assertEqual(infer_skill("crypto et immobilier et nft"), "trading-agent")
        row = parse_strategy("mft et immobilier en France")
        self.assertEqual(set(row["mandate"]["domains"]), {"nft", "realestate"})


if __name__ == "__main__":
    unittest.main()
