"""Whether this checkout exposes navin.live account + the managed Navin provider.

On ``main`` / navin-agi the product is BYOK only: no Account UI, no Navin
provider, no license sync. The commercial modules may still exist on disk
(they are stripped from the public GitHub tree). They stay dark unless
``NAVIN_LIVE_ACCOUNT`` is explicitly enabled.
"""

from __future__ import annotations

import importlib.util
import os


def live_modules_available() -> bool:
    flag = os.environ.get("NAVIN_LIVE_ACCOUNT", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return False
    return importlib.util.find_spec("navin.license_client") is not None
