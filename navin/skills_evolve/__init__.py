"""Skills evolution (S2): Navin drafts, examines and corrects skills alone.

A skill only steers the project once a frozen battery says "better than
before, nothing regressed". The global harness (every project) only moves
when a human publishes. Off by default; exams run outside the gateway turn.

Contracts, in order of importance:

* **Flag off = nothing.** No draft folder, no exam, no new skill loaded, and
  the per-turn hook factory answers ``None`` after one ``os.stat``.
* **Out of the turn.** The hook only queues a job; the corridor runs on a
  daemon thread, a cron or ``navin agi run``.
* **Drafts are invisible.** ``.navin/skills-draft`` is not a loader folder.
* **Same exam every time.** The battery is content-versioned; correctors
  see its feedback, never its file.
* **Human = publish / force / discard.** Never the engine.
"""

from navin.skills_evolve.settings import (
    SETTINGS_NAME,
    SkillsEvolveSettings,
    evolve_enabled,
    read_settings,
    settings_path,
    update_settings,
    write_settings,
)

__all__ = [
    "SETTINGS_NAME",
    "SkillsEvolveSettings",
    "evolve_enabled",
    "read_settings",
    "settings_path",
    "update_settings",
    "write_settings",
]
