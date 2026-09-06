"""Cognition sidecar: opt-in episodic journal and recall for one project.

Everything here lives beside the harness, never inside it. The agent loop,
the runner, the context builder, Dream and the desks do not import this
package. Two contracts hold:

* Off by default. Nothing is written, no tool is registered and the
  per-turn hook factory returns ``None`` until ``.navin/cognition.json``
  in the project says ``{"enabled": true}``.
* Off the hot path. The journal is appended by a background thread after
  the model answered; recall runs only when the model calls the tool.
"""

from navin.cognition.registration import recall_registered, sync_recall_tool
from navin.cognition.settings import (
    SETTINGS_NAME,
    CognitionSettings,
    cognition_enabled,
    cognition_state,
    read_settings,
    settings_path,
    update_settings,
    write_settings,
)

__all__ = [
    "SETTINGS_NAME",
    "CognitionSettings",
    "cognition_enabled",
    "cognition_state",
    "read_settings",
    "recall_registered",
    "settings_path",
    "sync_recall_tool",
    "update_settings",
    "write_settings",
]
