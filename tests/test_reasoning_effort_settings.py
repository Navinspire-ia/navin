"""The Thinking ladder follows the provider+model pair, whatever the spelling.

One bug report (MiniMax-M3 refusing "low" with a bare "invalid
reasoning_effort") had four holes behind it, and each of them could recur
with any other provider, so each is locked here for the whole registry:

- ``find_by_name`` only matched snake_case config keys, so the display names
  the Settings form sends ("MiniMax", "Kimi Coding") fell through to the
  default ladder.
- A custom OpenAI-compatible gateway serving a known vendor's model got the
  default ladder instead of that vendor's rules.
- The refusal named neither the provider, the model nor the accepted levels.
- The form showed the ladder of the preset saved earlier, not of the pair the
  user had just picked, so a level the new model rejects could be submitted.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from navin.providers.registry import PROVIDERS, find_by_model, find_by_name
from navin.webui import settings_api
from navin.webui.settings_api import (
    WebUISettingsError,
    _check_reasoning_effort,
    _reasoning_effort_values_for,
    reasoning_effort_values_payload,
)

ROOT = Path(__file__).resolve().parent.parent

AUTO_ONLY = [""]


def _spellings(spec) -> list[str]:
    """Every way a provider name reaches the API: config key, display name, casings."""
    return [
        spec.name,
        spec.name.upper(),
        spec.name.replace("_", "-"),
        spec.display_name,
        spec.display_name.lower(),
        spec.display_name.upper(),
        spec.display_name.replace(" ", "_"),
        spec.display_name.replace(" ", "-"),
        spec.display_name.replace(" ", ""),
    ]


class FindByNameTest(unittest.TestCase):
    def test_every_provider_resolves_under_every_spelling(self):
        for spec in PROVIDERS:
            canonical = spec.settings_alias_for or spec.name
            for spelling in _spellings(spec):
                got = find_by_name(spelling)
                self.assertIsNotNone(got, f"{spelling!r} (from {spec.name}) resolved to nothing")
                # An alias spec (opencode_zen) shares its display name with its
                # canonical provider; either is the same provider in Settings.
                self.assertIn(
                    got.name,
                    {spec.name, canonical},
                    f"{spelling!r} resolved to {got.name}, expected {spec.name}",
                )

    def test_exact_config_key_always_wins(self):
        for spec in PROVIDERS:
            self.assertEqual(find_by_name(spec.name).name, spec.name)

    def test_shared_display_name_prefers_the_canonical_provider(self):
        self.assertEqual(find_by_name("OpenCode Zen").name, "opencode")
        self.assertEqual(find_by_name("opencode_zen").name, "opencode_zen")

    def test_display_names_are_unique_per_canonical_provider(self):
        """Two unrelated providers with one display name would make the form ambiguous."""
        seen: dict[str, str] = {}
        for spec in PROVIDERS:
            canonical = spec.settings_alias_for or spec.name
            key = spec.display_name.lower()
            self.assertEqual(
                seen.setdefault(key, canonical),
                canonical,
                f"display name {spec.display_name!r} used by {seen[key]} and {canonical}",
            )

    def test_unknown_and_empty_names_return_none(self):
        self.assertIsNone(find_by_name(""))
        self.assertIsNone(find_by_name("mon-gateway-perso"))
        self.assertIsNone(find_by_name("Not A Provider"))


class FindByModelTest(unittest.TestCase):
    def test_known_vendor_models_resolve_to_their_vendor(self):
        cases = {
            "MiniMax-M3": "minimax",
            "minimax/minimax-m3": "minimax",
            "magistral-medium-latest": "mistral",
            "claude-sonnet-4-6": "anthropic",
            "gpt-5.1": "openai",
            "deepseek-reasoner": "deepseek",
            "gemini-3-flash-preview": "gemini",
        }
        for model, vendor in cases.items():
            got = find_by_model(model)
            self.assertIsNotNone(got, model)
            self.assertEqual(got.name, vendor, model)

    def test_unknown_model_resolves_to_nothing(self):
        self.assertIsNone(find_by_model(""))
        self.assertIsNone(find_by_model("un-modele-inconnu"))

    def test_transcription_only_providers_never_own_a_chat_model(self):
        for spec in PROVIDERS:
            if not spec.is_transcription_only:
                continue
            for keyword in spec.keywords:
                got = find_by_model(keyword)
                self.assertTrue(
                    got is None or not got.is_transcription_only,
                    f"{keyword!r} landed on transcription-only {spec.name}",
                )


class ReasoningLadderTest(unittest.TestCase):
    PROBE_MODELS = (
        "MiniMax-M3",
        "magistral-medium",
        "mistral-large-latest",
        "claude-sonnet-4-6",
        "gpt-5.1",
        "gemini-3-flash-preview",
        "grok-4-6",
        "kimi-k3",
        "deepseek-reasoner",
        "qwen3-8-max",
        "some-unlisted-model",
    )

    def test_display_name_and_config_key_give_the_same_ladder(self):
        """Whatever the form sends, the ladder is the one the config key gets."""
        for spec in PROVIDERS:
            for model in self.PROBE_MODELS:
                expected = _reasoning_effort_values_for(spec.name, model)
                for spelling in _spellings(spec):
                    self.assertEqual(
                        _reasoning_effort_values_for(spelling, model),
                        expected,
                        f"{spelling!r} + {model!r} diverged from {spec.name!r}",
                    )

    def test_minimax_m_family_is_auto_only_everywhere(self):
        for provider in ("minimax", "MiniMax", "MINIMAX", "mon-gateway", ""):
            for model in ("MiniMax-M3", "minimax/minimax-m3", "MiniMax-M4-preview"):
                self.assertEqual(
                    _reasoning_effort_values_for(provider, model), AUTO_ONLY, (provider, model)
                )

    def test_custom_gateway_inherits_the_vendor_rules_of_the_model(self):
        # Magistral always reasons: Auto only, exactly as through Mistral itself.
        self.assertEqual(_reasoning_effort_values_for("mon-gateway", "magistral-medium"), AUTO_ONLY)
        self.assertEqual(
            _reasoning_effort_values_for("mon-gateway", "magistral-medium"),
            _reasoning_effort_values_for("mistral", "magistral-medium"),
        )
        # Mistral Large only knows high / none on the wire: Auto + High.
        self.assertEqual(
            _reasoning_effort_values_for("mon-gateway", "mistral-large-latest"),
            _reasoning_effort_values_for("mistral", "mistral-large-latest"),
        )
        self.assertEqual(
            _reasoning_effort_values_for("mon-gateway", "claude-sonnet-4-6"),
            _reasoning_effort_values_for("anthropic", "claude-sonnet-4-6"),
        )

    def test_unknown_model_on_a_custom_gateway_keeps_the_default_ladder(self):
        """Nothing is known about it; offering levels is right, the runtime negotiates."""
        values = _reasoning_effort_values_for("mon-gateway", "un-modele-inconnu")
        self.assertIn("", values)
        self.assertGreater(len(values), 1)

    def test_catalog_provider_rules_see_the_canonical_key(self):
        """provider_any rules in the catalog name config keys, not display names."""
        self.assertEqual(
            _reasoning_effort_values_for("Anthropic", "claude-mystery-model"),
            _reasoning_effort_values_for("anthropic", "claude-mystery-model"),
        )


class SettingsAndRuntimeAgreeTest(unittest.TestCase):
    """The ladder Settings offers and what the wire does must tell one story.

    Two knowledge sources describe a model's reasoning knob: the Settings
    catalog (which levels the form offers) and reasoning_control (what the
    provider sends). Grok 4.6 had the form offering "xhigh" while the runtime
    dropped the parameter, so the choice did nothing and the model ran at its
    costliest default. Every family in either table is cross-checked here.
    """

    def _probe_models(self) -> list[str]:
        """A model name for every catalog rule and fallback, plus the runtime tables."""
        from navin.providers import reasoning_control
        from navin.webui.settings_api import _load_reasoning_catalog

        probes: list[str] = []
        for family in _load_reasoning_catalog().get("families", []):
            heads = [str(t) for t in family.get("match", {}).get("contains_any", [])] or [""]
            for rule in family.get("rules", []):
                for token in (str(t) for t in rule.get("contains_any", [])):
                    probes.append(
                        token if any(h and h in token for h in heads) else f"{heads[0]}-{token}"
                    )
            if heads[0]:
                probes.append(f"{heads[0]}-probe")
        probes.extend(reasoning_control._ALWAYS_MARKERS)
        probes.extend(reasoning_control._ALWAYS_TUNABLE_MARKERS)
        return probes

    def test_every_level_the_form_offers_reaches_the_wire(self):
        from navin.providers.reasoning_control import always_reasons

        for model in self._probe_models():
            ladder = _reasoning_effort_values_for("", model)
            if ladder == AUTO_ONLY:
                continue
            with self.subTest(model=model, ladder=ladder):
                self.assertFalse(
                    always_reasons(model),
                    f"Settings offers {ladder} for {model} but the runtime drops the knob",
                )

    def test_every_family_the_runtime_silences_is_auto_only_in_settings(self):
        from navin.providers import reasoning_control

        for marker in reasoning_control._ALWAYS_MARKERS:
            with self.subTest(marker=marker):
                self.assertTrue(reasoning_control.always_reasons(marker))
                self.assertEqual(
                    _reasoning_effort_values_for("", marker),
                    AUTO_ONLY,
                    f"the runtime never sends a level for {marker!r}; Settings must not offer one",
                )

    def test_grok_versions_split_where_xai_put_the_knob_back(self):
        from navin.providers.reasoning_control import always_reasons

        for model in ("grok-4", "grok-4-0709", "grok-4-1-fast-reasoning"):
            self.assertTrue(always_reasons(model), model)
            self.assertEqual(_reasoning_effort_values_for("xai", model), AUTO_ONLY, model)
        for model in ("grok-4-6", "grok-4.5", "grok-4-3"):
            self.assertFalse(always_reasons(model), model)
            self.assertGreater(len(_reasoning_effort_values_for("xai", model)), 1, model)


class CheckReasoningEffortTest(unittest.TestCase):
    def test_auto_and_accepted_levels_pass(self):
        _check_reasoning_effort("", "MiniMax", "MiniMax-M3")
        _check_reasoning_effort("high", "openai", "gpt-5.1")
        _check_reasoning_effort("high", "mon-gateway", "un-modele-inconnu")

    def test_refusal_names_the_level_the_pair_and_the_accepted_ladder(self):
        with self.assertRaises(WebUISettingsError) as ctx:
            _check_reasoning_effort("low", "MiniMax", "MiniMax-M3")
        err = ctx.exception
        self.assertEqual(err.field, "reasoning_effort")
        self.assertEqual(err.status, 400)
        for fragment in ("invalid reasoning_effort", "'low'", "MiniMax/MiniMax-M3", "Auto"):
            self.assertIn(fragment, err.message)

    def test_refusal_lists_every_accepted_level(self):
        with self.assertRaises(WebUISettingsError) as ctx:
            _check_reasoning_effort("xhigh", "openai", "gpt-5.1")
        message = ctx.exception.message
        for level in _reasoning_effort_values_for("openai", "gpt-5.1"):
            self.assertIn("Auto" if level == "" else level, message)

    def test_refusal_covers_custom_gateways(self):
        with self.assertRaises(WebUISettingsError) as ctx:
            _check_reasoning_effort("medium", "mon-gateway", "magistral-medium")
        self.assertIn("mon-gateway/magistral-medium", ctx.exception.message)

    def test_error_field_defaults_to_empty_for_other_errors(self):
        self.assertEqual(WebUISettingsError("x").field, "")


class LiveLadderEndpointTest(unittest.TestCase):
    def test_payload_echoes_the_pair_and_its_ladder(self):
        payload = reasoning_effort_values_payload("MiniMax", "MiniMax-M3")
        self.assertEqual(
            payload, {"provider": "MiniMax", "model": "MiniMax-M3", "values": AUTO_ONLY}
        )
        self.assertEqual(
            reasoning_effort_values_payload("openai", "gpt-5.1")["values"],
            _reasoning_effort_values_for("openai", "gpt-5.1"),
        )

    def test_empty_pair_is_harmless(self):
        payload = reasoning_effort_values_payload("", "")
        self.assertEqual(payload["provider"], "")
        self.assertIn("", payload["values"])

    def test_route_is_dispatched_and_the_form_calls_it(self):
        routes = (ROOT / "navin/webui/settings_routes.py").read_text(encoding="utf-8")
        self.assertIn('path == "/api/settings/reasoning-effort-values"', routes)
        self.assertIn("reasoning_effort_values_payload(provider, model)", routes)

        api = (ROOT / "webui/src/lib/api.ts").read_text(encoding="utf-8")
        self.assertIn("/api/settings/reasoning-effort-values?", api)
        self.assertIn("export async function fetchReasoningEffortValues", api)
        self.assertIn("export function isReasoningEffortError", api)

        view = (ROOT / "webui/src/components/settings/SettingsView.tsx").read_text(
            encoding="utf-8"
        )
        # The ladder is fetched for the pair the form shows, not the saved preset.
        self.assertIn("fetchReasoningEffortValues(token, formProvider, formModel)", view)
        # A level the new model rejects drops back to Auto before Save.
        self.assertIn("reasoningEffort: \"\"", view)
        # The refusal is shown on the Thinking control, not the generic banner.
        self.assertIn('data-testid="settings-models-reasoning-effort-error"', view)
        self.assertIn("isReasoningEffortError(message)", view)


class SaveRefusalEndToEndTest(unittest.TestCase):
    """update_agent_settings refuses with the detailed message, for any spelling."""

    def _update(self, query: dict[str, list[str]], *, stored_effort: str | None = None):
        from navin.config.schema import Config

        config = Config()
        config.agents.defaults.provider = "minimax"
        config.agents.defaults.model = "MiniMax-M3"
        config.agents.defaults.reasoning_effort = stored_effort
        config.providers.minimax.api_key = "sk-test"
        saved: list = []
        with (
            patch.object(settings_api, "load_config", return_value=config),
            patch.object(settings_api, "save_config", side_effect=saved.append),
            patch.object(settings_api, "settings_payload", return_value={}),
        ):
            settings_api.update_agent_settings(query)
        return config, saved

    def test_level_the_model_rejects_is_refused_with_context(self):
        with self.assertRaises(WebUISettingsError) as ctx:
            self._update({"reasoning_effort": ["low"]})
        self.assertEqual(ctx.exception.field, "reasoning_effort")
        self.assertIn("minimax/MiniMax-M3", ctx.exception.message)
        self.assertIn("Auto", ctx.exception.message)

    def test_auto_is_accepted_and_persisted(self):
        """A level left over from an earlier model is cleared, not refused."""
        config, saved = self._update({"reasoning_effort": [""]}, stored_effort="high")
        self.assertIsNone(config.agents.defaults.reasoning_effort)
        self.assertEqual(len(saved), 1)


if __name__ == "__main__":
    unittest.main()
