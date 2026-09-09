# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Whether this checkout exposes navin.live account + the managed Navin provider.

prod-v2 / Forgejo (this tree): ``navin.license_client`` is present, so Account,
subscriptions and the Navin provider are on by default.

main / Navinspire-ia/navin (public GitHub): those modules are stripped, so the product
stays BYOK only.

Override: ``NAVIN_LIVE_ACCOUNT=0`` forces Account off even on a prod tree
(useful to preview the public UI). ``NAVIN_LIVE_ACCOUNT=1`` does not invent
the modules if they are missing.
"""

from __future__ import annotations

import importlib.util
import os


def live_modules_available() -> bool:
    flag = os.environ.get("NAVIN_LIVE_ACCOUNT", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return importlib.util.find_spec("navin.license_client") is not None
