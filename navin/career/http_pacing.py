# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Share public-site request intervals across concurrent role searches."""

import threading
import time

_lock = threading.Lock()
_last: dict[str, float] = {}


def pace_public_request(host: str, interval: float) -> None:
    with _lock:
        now = time.monotonic()
        scheduled = max(now, _last.get(host, 0) + interval)
        _last[host] = scheduled
    if scheduled > now:
        time.sleep(scheduled - now)
