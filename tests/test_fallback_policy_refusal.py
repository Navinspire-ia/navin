from types import SimpleNamespace

from navin.providers.fallback_policy import REFUSAL_COOLDOWN_S, circuit_cooldown_s


def _error(status):
    return SimpleNamespace(finish_reason="error", error_status_code=status, content="Error: AccessDeniedException")


def test_access_refusal_parks_the_model_long_enough():
    # Bedrock "not authorized" / "not available for this account": no probe every minute.
    assert circuit_cooldown_s(_error(403)) == REFUSAL_COOLDOWN_S
    assert circuit_cooldown_s(_error(404)) == REFUSAL_COOLDOWN_S


def test_busy_model_keeps_the_default_circuit():
    assert circuit_cooldown_s(_error(503)) is None
