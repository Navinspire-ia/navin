"""Host OS package-manager adapters (pacman / Omarchy / apt / dnf)."""

from .packages import (
    InstallPlan,
    PkgSpec,
    linux_package_flavor,
    pacman_plan,
    privileged_install,
    sudo_prefix,
)

__all__ = [
    "InstallPlan",
    "PkgSpec",
    "linux_package_flavor",
    "pacman_plan",
    "privileged_install",
    "sudo_prefix",
]
