# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared helpers for provider wiring tests."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from navin.providers.registry import PROVIDERS

_PROVIDER_ENV_VARS = tuple(
    dict.fromkeys(
        var
        for spec in PROVIDERS
        for var in (spec.env_key, *spec.env_key_aliases)
        if var
    )
)


@contextmanager
def isolated_provider_env() -> Iterator[None]:
    """Keep make_provider / env aliases from leaking into other tests."""
    saved = {name: os.environ.get(name) for name in _PROVIDER_ENV_VARS}
    for name in _PROVIDER_ENV_VARS:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name in _PROVIDER_ENV_VARS:
            os.environ.pop(name, None)
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value
