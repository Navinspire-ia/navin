# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Windows UI Automation snapshot through PowerShell (no Python dependency).

Every Windows install has Windows PowerShell 5.1 and the .NET
``UIAutomationClient`` assembly. A short script walks the descendants of one
window and prints them as JSON: control type, name, value, screen rectangle.
That is what turns "click somewhere near the Save button" into "click the
centre of element ref 17".
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from navin.computer.base import ComputerError, UIElement

_SCRIPT = r"""
param([long]$Hwnd, [int]$Limit)
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$ae = [System.Windows.Automation.AutomationElement]
$root = $ae::FromHandle([IntPtr]$Hwnd)
if ($null -eq $root) { Write-Output '[]'; exit 0 }
$cond = [System.Windows.Automation.Condition]::TrueCondition
$all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
$interactive = @('Button','Edit','ComboBox','CheckBox','RadioButton','MenuItem','TabItem',
  'ListItem','Hyperlink','TreeItem','SplitButton','Slider','Spinner','Document','DataItem','Thumb')
$out = New-Object System.Collections.Generic.List[object]
$i = 0
foreach ($el in $all) {
  if ($i -ge $Limit) { break }
  try {
    $c = $el.Current
    if ($c.IsOffscreen) { continue }
    $r = $c.BoundingRectangle
    if ([double]::IsInfinity($r.Width) -or $r.Width -le 0 -or $r.Height -le 0) { continue }
    $type = $c.ControlType.ProgrammaticName -replace '^ControlType\.',''
    $name = [string]$c.Name
    $val = ''
    $vp = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
      $val = [string]$vp.Current.Value
    }
    if (-not $name -and -not $val -and ($interactive -notcontains $type)) { continue }
    if ($name.Length -gt 120) { $name = $name.Substring(0, 120) }
    if ($val.Length -gt 80) { $val = $val.Substring(0, 80) }
    $out.Add([pscustomobject]@{
      role = $type; name = $name; value = $val
      x = [int][math]::Round($r.X); y = [int][math]::Round($r.Y)
      w = [int][math]::Round($r.Width); h = [int][math]::Round($r.Height)
      enabled = [bool]$c.IsEnabled; focused = [bool]$c.HasKeyboardFocus
    })
    $i++
  } catch {}
}
if ($out.Count -eq 0) { Write-Output '[]'; exit 0 }
# A generic List trips ConvertTo-Json on Windows PowerShell 5.1 ("Argument
# types do not match"); a plain object[] serialises fine.
[object[]]$rows = $out.ToArray()
ConvertTo-Json -InputObject $rows -Compress -Depth 3
"""


def _powershell() -> str | None:
    found = shutil.which("powershell") or shutil.which("pwsh")
    if found:
        return found
    system_root = os.environ.get("SystemRoot", "")
    if system_root:
        native = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if native.is_file():
            return str(native)
    return None


def uia_available() -> tuple[bool, str]:
    exe = _powershell()
    if not exe:
        return False, "PowerShell not found"
    base = exe.replace("\\", "/").rsplit("/", 1)[-1]
    return True, f"UI Automation via {base}"


def uia_snapshot(hwnd: int, *, limit: int = 300, timeout: float = 25.0) -> list[UIElement]:
    exe = _powershell()
    if not exe:
        raise ComputerError("PowerShell is required for the accessibility snapshot")
    script = f"{_SCRIPT}\n"
    # The script declares parameters; bind them by wrapping in a script block.
    wrapped = f"& {{ {script} }} -Hwnd {int(hwnd)} -Limit {int(limit)}"
    encoded = base64.b64encode(wrapped.encode("utf-16-le")).decode("ascii")
    try:
        out = subprocess.run(
            [
                exe,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ComputerError(f"UI Automation snapshot failed: {exc}") from exc
    text = (out.stdout or "").strip()
    if not text:
        raise ComputerError(
            f"UI Automation snapshot returned nothing: {(out.stderr or '').strip()[:200]}"
        )
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ComputerError(f"UI Automation snapshot is not JSON: {text[:120]}") from exc
    if isinstance(data, dict):
        data = [data]
    elements: list[UIElement] = []
    for i, row in enumerate(data if isinstance(data, list) else []):
        if not isinstance(row, dict):
            continue
        try:
            elements.append(
                UIElement(
                    ref=i,
                    role=str(row.get("role") or ""),
                    name=str(row.get("name") or ""),
                    left=int(row.get("x") or 0),
                    top=int(row.get("y") or 0),
                    width=int(row.get("w") or 0),
                    height=int(row.get("h") or 0),
                    value=str(row.get("value") or ""),
                    enabled=bool(row.get("enabled", True)),
                    focused=bool(row.get("focused", False)),
                )
            )
        except (TypeError, ValueError):
            continue
    return elements
