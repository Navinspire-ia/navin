# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""First-run setup stays usable while the remote catalog is slow."""

import threading
from unittest.mock import patch

from navin.config.loader import load_config, save_config


def test_first_run_catalog_refresh_does_not_block_startup():
    from navin.cli.commands import _start_gateway_catalog_sync

    config = load_config()
    config.model_catalog.enabled = True
    config.agents.defaults.model = ""
    save_config(config)
    entered = threading.Event()
    release = threading.Event()

    def slow_catalog(loaded, **kwargs):
        assert loaded.agents.defaults.model == ""
        entered.set()
        assert release.wait(timeout=5)

    with patch("navin.providers.managed_catalog.sync_managed_catalog", side_effect=slow_catalog):
        worker = _start_gateway_catalog_sync(config)
        try:
            assert worker is not None
            assert entered.wait(timeout=2)
            assert worker.is_alive()
        finally:
            release.set()
            if worker is not None:
                worker.join(timeout=5)
                assert not worker.is_alive()


def test_disabled_catalog_does_not_start_a_worker():
    from navin.cli.commands import _start_gateway_catalog_sync

    config = load_config()
    config.model_catalog.enabled = False
    with patch("navin.providers.managed_catalog.sync_managed_catalog") as sync:
        assert _start_gateway_catalog_sync(config) is None
    sync.assert_not_called()
