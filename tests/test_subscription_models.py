from navin.providers.subscription_models import parse_codex_models, parse_xai_models


def test_grok_subscription_list_keeps_chat_models():
    rows = parse_xai_models({"data": [
        {"id": "grok-4.7"}, {"id": "grok-code-fast-2"}, {"id": "grok-imagine-image-2.0"}, {"id": ""},
    ]})
    assert [r["id"] for r in rows] == ["xai-oauth/grok-4.7", "xai-oauth/grok-code-fast-2"]


def test_codex_list_reads_slugs_and_skips_hidden():
    rows = parse_codex_models({"models": [
        {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "visibility": "list"},
        {"slug": "gpt-5.6-codex-mini", "display_name": "Codex Mini"},
        {"slug": "internal-eval", "visibility": "hide"},
    ]})
    assert [(r["id"], r["label"]) for r in rows] == [
        ("openai-codex/gpt-6.1-sol", "GPT-6.1 Sol"), ("openai-codex/gpt-5.6-codex-mini", "Codex Mini")]
    assert parse_codex_models({"data": [{"id": "gpt-6-astra"}]})[0]["id"] == "openai-codex/gpt-6-astra"
    assert parse_codex_models("nope") == []


def test_signed_out_subscriptions_fall_back_to_the_builtin_list(monkeypatch, tmp_path):
    from navin.providers import subscription_models

    monkeypatch.setattr(subscription_models, "_cache", {})
    monkeypatch.setattr("navin.providers.xai_oauth_provider.get_xai_oauth_login_status", lambda: None)
    assert subscription_models.xai_oauth_models() is None
