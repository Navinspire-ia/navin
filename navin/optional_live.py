"""Whether this checkout includes navin.live / managed-provider modules.

The public GitHub tree omits ``license_client`` and the account API. Callers
must keep those imports optional so ``navin-cli`` still starts on BYOK only.
"""

from __future__ import annotations

import importlib.util


def live_modules_available() -> bool:
    return importlib.util.find_spec("navin.license_client") is not None
