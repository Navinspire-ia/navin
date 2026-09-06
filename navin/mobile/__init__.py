"""Mobile project detection, environment doctor, and run helpers.

Supports Expo, React Native, and Flutter detection. The first shipping
target is Expo / React Native; Flutter is detected so doctor/run can
report a clear next step without pretending full support yet.
"""

from navin.mobile.adb import (
    AdbLocation,
    host_platform,
    preview_readiness,
    resolve_adb_binary,
    resolve_adb_location,
    try_install_adb,
)
from navin.mobile.bootstrap import run_bootstrap
from navin.mobile.detect import MobileProject, detect_mobile_project
from navin.mobile.doctor import DoctorReport, run_doctor
from navin.mobile.preview import PreviewError, open_preview
from navin.mobile.run import RunPlan, build_run_plan

__all__ = [
    "AdbLocation",
    "DoctorReport",
    "MobileProject",
    "PreviewError",
    "RunPlan",
    "build_run_plan",
    "detect_mobile_project",
    "host_platform",
    "open_preview",
    "preview_readiness",
    "resolve_adb_binary",
    "resolve_adb_location",
    "run_bootstrap",
    "run_doctor",
    "try_install_adb",
]
