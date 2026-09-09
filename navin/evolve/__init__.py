# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Desktop side of the Navin Evolve engine.

The Rust daemon (navin-engine) shells out to `python3 -m navin.evolve.bridge`
to turn a diagnosed finding into concrete fix candidates via the configured
LLM provider. Everything else (proof, gate, promotion) stays in the daemon.
"""
