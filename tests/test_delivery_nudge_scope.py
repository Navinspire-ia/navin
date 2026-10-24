# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A request that already ran tools is never told it produced no deliverable."""

from navin.utils.runtime import (
    DELIVERY_CONTINUE_PROMPT,
    REMAINING_WORK_CONTINUE_PROMPT,
    is_harness_nudge,
    request_used_tools,
)


def _call() -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "apply_patch", "arguments": "{}"}}]}


def test_tools_before_a_continuation_prompt_count_for_the_request() -> None:
    messages = [
        {"role": "user", "content": "/forge oui corrige tout"},
        _call(),
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "assistant", "content": "Corrige."},
        {"role": "user", "content": "Wrap-up check: your latest edit is validated (checks green) and the last 3 steps"},
        {"role": "assistant", "content": "Voici le resume."},
    ]
    assert request_used_tools(messages)


def test_a_new_request_starts_from_zero() -> None:
    messages = [
        _call(),
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "user", "content": "/forge redige le message client"},
        {"role": "assistant", "content": "Je regarde."},
    ]
    assert not request_used_tools(messages)


def test_navin_prompts_are_not_mistaken_for_the_user() -> None:
    assert is_harness_nudge(DELIVERY_CONTINUE_PROMPT)
    assert is_harness_nudge(REMAINING_WORK_CONTINUE_PROMPT)
    assert not is_harness_nudge("lol t es un con")


def test_a_step_left_to_the_user_is_not_unfinished_work() -> None:
    from navin.utils.runtime import lists_remaining_work

    assert not lists_remaining_work("La mise en ligne reste a faire de ton cote : sans elle, le client ne voit rien.")
    assert not lists_remaining_work("All done. Remaining: deploy it on your side.")
    assert lists_remaining_work("Il reste a ecrire les tests du module paiement.")
