"""Concrete agent hook implementations."""

from navin.agent.hooks.file_edit_activity import (
    FileEditActivityHook,
    create_file_edit_activity_hook,
)
from navin.cognition.hook import EpisodeJournalHook, create_episode_journal_hook
from navin.policy.hook import PolicyHook, create_policy_hook
from navin.skills_evolve.hook import SkillsEvolveHook, create_skills_evolve_hook
from navin.world_model.hook import WorldModelHook, create_world_model_hook

# The factories every entry point (CLI agent, gateway, SDK) installs. A factory
# that answers None for a turn adds nothing to that turn's hook chain.
DEFAULT_HOOK_FACTORIES = [
    create_file_edit_activity_hook,
    create_episode_journal_hook,
    create_skills_evolve_hook,
    create_world_model_hook,
    create_policy_hook,
]

__all__ = [
    "DEFAULT_HOOK_FACTORIES",
    "EpisodeJournalHook",
    "FileEditActivityHook",
    "PolicyHook",
    "SkillsEvolveHook",
    "WorldModelHook",
    "create_episode_journal_hook",
    "create_file_edit_activity_hook",
    "create_policy_hook",
    "create_skills_evolve_hook",
    "create_world_model_hook",
]
