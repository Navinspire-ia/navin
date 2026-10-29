# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Security remediation: fix guidance, mechanical patches and patch validation."""

from navin.security.remediation.guidance import apply_guidance, remediation_for
from navin.security.remediation.patch_generator import ProposedPatch, propose_patch, propose_patches
from navin.security.remediation.patch_validator import (
    PatchCheck,
    validate_patch,
    validate_patch_async,
)

__all__ = [
    "PatchCheck",
    "ProposedPatch",
    "apply_guidance",
    "propose_patch",
    "propose_patches",
    "remediation_for",
    "validate_patch",
    "validate_patch_async",
]
