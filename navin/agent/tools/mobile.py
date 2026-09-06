"""Mobile tool: detect Expo/RN/Flutter, run packagers, drive Android preview."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.context import current_request_session_key
from navin.agent.tools.exec_session import (
    DEFAULT_EXEC_SESSION_MANAGER,
    DEFAULT_MAX_OUTPUT_CHARS,
    DEFAULT_YIELD_MS,
    MAX_OUTPUT_CHARS,
    MAX_YIELD_MS,
    clamp_session_int,
    format_session_poll,
)
from navin.agent.tools.quality import _QualityTool
from navin.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)

# workspace root -> last packager session id started by this tool
_LAST_SESSIONS: dict[str, str] = {}
_LAST_EMULATORS: dict[str, str] = {}
# workspace root -> live preview session for agent tap/ui_dump
_PREVIEW_SESSIONS: dict[str, Any] = {}


@tool_parameters(
    tool_parameters_schema(
        required=["action"],
        action=StringSchema(
            "detect|doctor|setup|bootstrap|devices|run|logs|stop|plan for packager "
            "lifecycle; preview_start|preview_stop|screenshot|tap|swipe|key|text|"
            "ui_dump|metrics for the interactive Android preview. "
            "setup discovers/installs adb; bootstrap runs the full cycle "
            "(adb → JDK → cmdline-tools → SDK → AVD → emulator when accelerated).",
            enum=[
                "detect",
                "doctor",
                "setup",
                "bootstrap",
                "devices",
                "run",
                "logs",
                "stop",
                "plan",
                "preview_start",
                "preview_stop",
                "screenshot",
                "tap",
                "swipe",
                "key",
                "text",
                "ui_dump",
                "metrics",
            ],
        ),
        target=StringSchema(
            "Platform for action=run|plan: android, ios, web, or metro.",
            enum=["android", "ios", "web", "metro"],
            nullable=True,
        ),
        emulator=StringSchema(
            "Optional AVD name for action=run when no device is connected.",
            nullable=True,
        ),
        device=StringSchema(
            "Optional device id for action=run|plan (adb serial or iPhone UDID). "
            "Never pass a platform name: 'ios' / 'android' match no device.",
            nullable=True,
        ),
        start_emulator=BooleanSchema(
            description=(
                "When action=run and target=android, start an AVD if none is connected "
                "(default true)."
            ),
            default=True,
            nullable=True,
        ),
        session_id=StringSchema(
            "Optional exec session id for action=logs|stop.",
            nullable=True,
        ),
        serial=StringSchema(
            "Optional adb device serial for preview_* / tap / swipe actions.",
            nullable=True,
        ),
        x=NumberSchema(description="Device X for tap / swipe start.", nullable=True),
        y=NumberSchema(description="Device Y for tap / swipe start.", nullable=True),
        x2=NumberSchema(description="Device X for swipe end.", nullable=True),
        y2=NumberSchema(description="Device Y for swipe end.", nullable=True),
        duration_ms=IntegerSchema(
            300,
            description="Swipe duration in ms.",
            minimum=1,
            maximum=5000,
            nullable=True,
        ),
        keycode=StringSchema(
            "Android keyevent name/code for action=key (BACK, HOME, ENTER, ...).",
            nullable=True,
        ),
        text=StringSchema("Text to type for action=text.", nullable=True),
        yield_time_ms=IntegerSchema(
            DEFAULT_YIELD_MS,
            description="Milliseconds to wait for packager output on run/logs.",
            minimum=0,
            maximum=MAX_YIELD_MS,
            nullable=True,
        ),
        max_output_chars=IntegerSchema(
            DEFAULT_MAX_OUTPUT_CHARS,
            description="Maximum characters of packager output to return.",
            minimum=1000,
            maximum=MAX_OUTPUT_CHARS,
            nullable=True,
        ),
    )
)
class MobileTool(_QualityTool):
    """Detect mobile projects, run packagers, and drive the Android preview."""

    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        tool = super().create(ctx)
        assert isinstance(tool, MobileTool)
        tool._bus = ctx.bus
        return tool

    @property
    def name(self) -> str:
        return "mobile"

    @property
    def description(self) -> str:
        return (
            "Mobile development helper for Expo, React Native, and Flutter. "
            "Use detect/doctor/setup/bootstrap/run/logs for the toolchain, and "
            "preview_start / tap / swipe / key / text / ui_dump / screenshot / "
            "metrics to interact with a live Android device or emulator. Prefer "
            "this over raw adb. setup installs adb; bootstrap runs the full "
            "multi-OS cycle under ~/.navin (JDK, cmdline-tools, emulator, AVD) "
            "with progress bars, and stops cleanly when WSL lacks /dev/kvm. "
            "The WebUI Mobile Preview panel shares the same stack."
        )

    @property
    def read_only(self) -> bool:
        return False

    def call_concurrency_safe(self, arguments: Any) -> bool:
        action = ""
        if isinstance(arguments, dict):
            action = str(arguments.get("action") or "")
        return action in {
            "detect",
            "doctor",
            "devices",
            "plan",
            "logs",
            "screenshot",
            "metrics",
            "ui_dump",
        }

    async def execute(
        self,
        action: str,
        target: str | None = None,
        emulator: str | None = None,
        device: str | None = None,
        start_emulator: bool | None = True,
        session_id: str | None = None,
        serial: str | None = None,
        x: float | None = None,
        y: float | None = None,
        x2: float | None = None,
        y2: float | None = None,
        duration_ms: int | None = None,
        keycode: str | None = None,
        text: str | None = None,
        yield_time_ms: int | None = None,
        max_output_chars: int | None = None,
        **kwargs: Any,
    ) -> str:
        root, error = self._root_or_error()
        if root is None:
            return error

        from navin.mobile.detect import detect_mobile_project
        from navin.mobile.doctor import run_doctor
        from navin.mobile.run import build_run_plan

        project = await asyncio.to_thread(detect_mobile_project, root)

        if action == "detect":
            return project.render()

        if action == "doctor":
            report = await asyncio.to_thread(run_doctor, project)
            return report.render()

        if action == "setup":
            return await asyncio.to_thread(self._setup_toolchain)

        if action == "bootstrap":
            from navin.mobile.bootstrap import render_bootstrap_result, run_bootstrap

            payload = await run_bootstrap(start_emulator=bool(start_emulator))
            return render_bootstrap_result(payload)

        if action == "devices":
            report = await asyncio.to_thread(run_doctor, project)
            lines = ["ADB devices:"]
            if report.devices:
                lines.extend(f"  - {d}" for d in report.devices)
            else:
                lines.append("  (none)")
            lines.append("")
            lines.append("Android virtual devices:")
            if report.avds:
                lines.extend(f"  - {a}" for a in report.avds)
            else:
                lines.append("  (none)")
            return "\n".join(lines)

        if action == "plan":
            report = await asyncio.to_thread(run_doctor, project)
            plan = build_run_plan(
                project,
                target=_normalize_target(target),
                emulator=emulator,
                doctor=report,
                start_emulator=bool(start_emulator if start_emulator is not None else True),
                device_id=device,
            )
            return "\n\n".join([plan.render(), report.render()])

        if action == "run":
            return await self._run(
                root=root,
                project=project,
                target=_normalize_target(target),
                emulator=emulator,
                device_id=device,
                start_emulator=bool(start_emulator if start_emulator is not None else True),
                yield_time_ms=yield_time_ms,
                max_output_chars=max_output_chars,
            )

        if action == "logs":
            return await self._logs(
                root=root,
                session_id=session_id,
                yield_time_ms=yield_time_ms,
                max_output_chars=max_output_chars,
            )

        if action == "stop":
            return await self._stop(root=root, session_id=session_id)

        if action in {
            "preview_start",
            "preview_stop",
            "screenshot",
            "tap",
            "swipe",
            "key",
            "text",
            "ui_dump",
            "metrics",
        }:
            return await self._preview_action(
                root=root,
                action=action,
                serial=serial,
                x=x,
                y=y,
                x2=x2,
                y2=y2,
                duration_ms=duration_ms,
                keycode=keycode,
                text=text,
            )

        return self.unknown_action(action)

    async def _preview_action(
        self,
        *,
        root: Path,
        action: str,
        serial: str | None,
        x: float | None,
        y: float | None,
        x2: float | None,
        y2: float | None,
        duration_ms: int | None,
        keycode: str | None,
        text: str | None,
    ) -> str:
        from navin.mobile.preview import PreviewError, open_preview
        from navin.utils.native import has_native, native

        key = str(root)

        if action == "preview_stop":
            session = _PREVIEW_SESSIONS.pop(key, None)
            if session is None:
                return "No agent preview session was open."
            await asyncio.to_thread(session.kill)
            return "Preview session stopped."

        if action == "preview_start":
            old = _PREVIEW_SESSIONS.pop(key, None)
            if old is not None:
                await asyncio.to_thread(old.kill)
            try:
                session = await asyncio.to_thread(
                    open_preview, serial=serial, fps=3.0
                )
            except PreviewError as exc:
                return ToolResult.error(f"Error: {exc}")
            _PREVIEW_SESSIONS[key] = session
            frame = await asyncio.to_thread(session.poll_frame, 3000, 0)
            backend = (
                "rust"
                if has_native() and hasattr(native() or object(), "MobilePreviewSession")
                else "python"
            )
            # Open the WebUI Mobile tab so the user sees the device without a click.
            from navin.agent.tools.open_preview import emit_preview_open_request

            ui_err = emit_preview_open_request(
                bus=getattr(self, "_bus", None),
                kind="mobile",
            )
            lines = [
                f"Preview started ({backend} backend).",
                f"Device serial: {session.device_serial() or 'default'}",
                f"Frame: {frame.get('width')}x{frame.get('height')} seq={frame.get('seq')}",
                f"FPS≈{float(frame.get('fps') or 0):.1f}  mem≈{float(frame.get('mem_mb') or 0):.0f}MB",
                (
                    "Mobile Preview tab opened for the user."
                    if ui_err is None
                    else "Open the Mobile Preview tab in the Dev workbench to see/click the screen."
                ),
                "Use mobile(action=tap|swipe|key|text|ui_dump|screenshot) to drive it.",
            ]
            if frame.get("error"):
                lines.append(f"Warning: {frame['error']}")
            return "\n".join(lines)

        session = _PREVIEW_SESSIONS.get(key)
        if session is None or not session.is_alive():
            if action == "preview_start":
                pass
            try:
                session = await asyncio.to_thread(
                    open_preview, serial=serial, fps=2.0
                )
            except PreviewError as exc:
                return ToolResult.error(
                    f"Error: {exc}. Start a device, then mobile(action=preview_start)."
                )
            _PREVIEW_SESSIONS[key] = session

        try:
            if action == "tap":
                if x is None or y is None:
                    return ToolResult.error("Error: tap requires x and y (device pixels).")
                await asyncio.to_thread(session.tap, int(x), int(y))
                return f"Tapped ({int(x)}, {int(y)})."
            if action == "swipe":
                if None in (x, y, x2, y2):
                    return ToolResult.error(
                        "Error: swipe requires x, y, x2, y2 (device pixels)."
                    )
                await asyncio.to_thread(
                    session.swipe,
                    int(x),
                    int(y),
                    int(x2),
                    int(y2),
                    int(duration_ms or 300),
                )
                return (
                    f"Swiped ({int(x)},{int(y)}) -> ({int(x2)},{int(y2)}) "
                    f"in {int(duration_ms or 300)}ms."
                )
            if action == "key":
                if not (keycode or "").strip():
                    return ToolResult.error("Error: key requires keycode.")
                await asyncio.to_thread(session.key, str(keycode).strip())
                return f"Sent keyevent {keycode}."
            if action == "text":
                if text is None:
                    return ToolResult.error("Error: text action requires text=...")
                await asyncio.to_thread(session.text, str(text))
                return f"Typed {len(str(text))} characters."
            if action == "ui_dump":
                xml = await asyncio.to_thread(session.ui_dump)
                # Keep tool results usable: trim huge dumps.
                if len(xml) > 24_000:
                    xml = xml[:24_000] + "\n... (truncated)"
                return "UI dump (UIAutomator):\n" + xml
            if action == "metrics":
                metrics = await asyncio.to_thread(session.metrics)
                return (
                    f"fps≈{float(metrics.get('fps') or 0):.1f}  "
                    f"mem≈{float(metrics.get('mem_mb') or 0):.0f}MB  "
                    f"cpu≈{float(metrics.get('cpu_pct') or 0):.0f}%  "
                    f"size={metrics.get('width')}x{metrics.get('height')}  "
                    f"seq={metrics.get('frame_seq')}"
                )
            if action == "screenshot":
                frame = await asyncio.to_thread(session.poll_frame, 2000, 0)
                png = frame.get("png") or b""
                if not png:
                    return ToolResult.error(
                        f"Error: no frame yet ({frame.get('error') or 'empty'})."
                    )
                from navin.utils.artifacts import ArtifactError, artifact_directory
                from navin.utils.helpers import build_image_content_blocks

                try:
                    directory = artifact_directory("mobile")
                except ArtifactError as exc:
                    return ToolResult.error(
                        f"Error: cannot save mobile screenshot ({exc})."
                    )
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / f"mobile_{int(time.time() * 1000)}.png"
                path.write_bytes(bytes(png))
                w = frame.get("width")
                h = frame.get("height")
                seq = frame.get("seq")
                return build_image_content_blocks(
                    bytes(png),
                    "image/png",
                    str(path),
                    (
                        f"(Mobile screenshot {w}x{h} seq={seq}, saved to {path}. "
                        "Use ui_dump for control bounds; tap/swipe/key/text to drive.)"
                    ),
                )
        except PreviewError as exc:
            return ToolResult.error(f"Error: {exc}")
        except Exception as exc:
            return ToolResult.error(f"Error: {exc}")

        return self.unknown_action(action)

    async def _run(
        self,
        *,
        root: Path,
        project: Any,
        target: str,
        emulator: str | None,
        device_id: str | None,
        start_emulator: bool,
        yield_time_ms: int | None,
        max_output_chars: int | None,
    ) -> str:
        from navin.mobile.doctor import run_doctor
        from navin.mobile.run import build_run_plan

        report = await asyncio.to_thread(run_doctor, project)
        plan = build_run_plan(
            project,
            target=target,  # type: ignore[arg-type]
            emulator=emulator,
            doctor=report,
            start_emulator=start_emulator,
            device_id=device_id,
        )
        if plan.blocked:
            return ToolResult.error(plan.render() + "\n\n" + report.render())

        key = str(root)
        parts: list[str] = [plan.render(), ""]

        if plan.emulator_command and start_emulator:
            emu_id, emu_poll = await self._start_session(
                command=plan.emulator_command,
                cwd=str(root),
                yield_time_ms=min(
                    clamp_session_int(yield_time_ms, 2000, 0, MAX_YIELD_MS),
                    5000,
                ),
                max_output_chars=clamp_session_int(
                    max_output_chars, 4000, 1000, MAX_OUTPUT_CHARS
                ),
            )
            _LAST_EMULATORS[key] = emu_id
            parts.append(f"Emulator session_id: {emu_id}")
            if emu_poll.output:
                parts.append(emu_poll.output.strip())
            await asyncio.sleep(1.0)
            parts.append(
                "Emulator starting. Wait until mobile(action=devices) shows a "
                "device before expecting the app to open."
            )
            parts.append("")

        session_id, poll = await self._start_session(
            command=plan.packager_command,
            cwd=str(root),
            yield_time_ms=clamp_session_int(
                yield_time_ms, 4000, 0, MAX_YIELD_MS
            ),
            max_output_chars=clamp_session_int(
                max_output_chars, DEFAULT_MAX_OUTPUT_CHARS, 1000, MAX_OUTPUT_CHARS
            ),
            wait_hint=plan.wait_for,
        )
        _LAST_SESSIONS[key] = session_id
        parts.append(format_session_poll(session_id, poll))
        parts.append("")
        parts.append(
            "Next: mobile(action=logs) for packager output; "
            "mobile(action=preview_start) + Dev Mobile Preview tab to see/click; "
            "mobile(action=stop) to terminate."
        )
        if plan.open_command:
            parts.append(
                f"If the app did not open: run `{plan.open_command}` via exec "
                "once a device is online."
            )
        return "\n".join(parts)

    async def _logs(
        self,
        *,
        root: Path,
        session_id: str | None,
        yield_time_ms: int | None,
        max_output_chars: int | None,
    ) -> str:
        sid = (session_id or _LAST_SESSIONS.get(str(root)) or "").strip()
        if not sid:
            return ToolResult.error(
                "Error: no packager session_id. Pass session_id or run "
                "mobile(action=run) first."
            )
        try:
            poll = await DEFAULT_EXEC_SESSION_MANAGER.write(
                session_id=sid,
                chars=None,
                close_stdin=False,
                terminate=False,
                yield_time_ms=clamp_session_int(
                    yield_time_ms, DEFAULT_YIELD_MS, 0, MAX_YIELD_MS
                ),
                max_output_chars=clamp_session_int(
                    max_output_chars, DEFAULT_MAX_OUTPUT_CHARS, 1000, MAX_OUTPUT_CHARS
                ),
                owner_session_key=current_request_session_key(),
            )
        except KeyError:
            _LAST_SESSIONS.pop(str(root), None)
            return ToolResult.error(
                f"Error: session {sid} not found (already exited?). "
                "Start again with mobile(action=run)."
            )
        except RuntimeError as exc:
            return ToolResult.error(f"Error: {exc}")
        return format_session_poll(sid, poll)

    def _setup_toolchain(self) -> str:
        """Discover adb/SDK; auto-install when rights allow; else clear steps."""
        from navin.mobile.adb import (
            adb_setup_help,
            can_auto_install_adb,
            host_platform,
            preview_readiness,
            try_install_adb,
        )

        plat = host_platform()
        lines = [
            f"Mobile toolchain setup ({plat})",
            "",
        ]
        status = preview_readiness(auto_install=False)
        if status.get("adb"):
            lines.append(f"adb: {status['adb']} (via {status.get('source') or 'unknown'})")
            if status.get("sdk"):
                lines.append(f"sdk: {status['sdk']}")
            devices = status.get("devices") or []
            if devices:
                lines.append("devices:")
                lines.extend(f"  - {d}" for d in devices)
            else:
                lines.append("devices: none online")
                if status.get("help"):
                    lines.append("")
                    lines.append(str(status["help"]))
            return "\n".join(lines)

        lines.append("adb: missing")
        if can_auto_install_adb():
            lines.append("Attempting automatic install (non-interactive)...")
            attempt = try_install_adb()
            lines.append(str(attempt.get("detail") or attempt))
            if attempt.get("ok"):
                status = preview_readiness(auto_install=False)
                lines.append(f"adb: {status.get('adb')}")
                return "\n".join(lines)
            if attempt.get("log"):
                lines.append("")
                lines.append("Install log (tail):")
                lines.append(str(attempt["log"])[-1500:])
        else:
            lines.append(
                "No automatic install rights (need root/passwordless sudo on Linux/WSL, "
                "or Homebrew on macOS). Follow the steps below."
            )
        lines.append("")
        lines.append(adb_setup_help())
        return "\n".join(lines)

    async def _stop(self, *, root: Path, session_id: str | None) -> str:
        key = str(root)
        targets: list[tuple[str, str]] = []
        if session_id:
            targets.append(("session", session_id.strip()))
        else:
            packager = _LAST_SESSIONS.get(key)
            emu = _LAST_EMULATORS.get(key)
            if packager:
                targets.append(("packager", packager))
            if emu:
                targets.append(("emulator", emu))
        preview = _PREVIEW_SESSIONS.pop(key, None)
        if preview is not None:
            await asyncio.to_thread(preview.kill)
        if not targets and preview is None:
            return ToolResult.error(
                "Error: nothing to stop. Pass session_id or run mobile(action=run) first."
            )

        lines: list[str] = []
        if preview is not None:
            lines.append("Stopped preview session.")
        for label, sid in targets:
            try:
                poll = await DEFAULT_EXEC_SESSION_MANAGER.write(
                    session_id=sid,
                    chars=None,
                    close_stdin=False,
                    terminate=True,
                    yield_time_ms=500,
                    max_output_chars=2000,
                    owner_session_key=current_request_session_key(),
                )
                lines.append(f"Stopped {label} {sid}.")
                if poll.output:
                    lines.append(poll.output.strip())
            except KeyError:
                lines.append(f"{label} {sid}: already gone.")
            except RuntimeError as exc:
                lines.append(f"{label} {sid}: {exc}")
            if label == "packager":
                _LAST_SESSIONS.pop(key, None)
            elif label == "emulator":
                _LAST_EMULATORS.pop(key, None)
        return "\n".join(lines)

    async def _start_session(
        self,
        *,
        command: str,
        cwd: str,
        yield_time_ms: int,
        max_output_chars: int,
        wait_hint: str | None = None,
    ) -> tuple[str, Any]:
        env = os.environ.copy()
        env.setdefault("CI", "1")
        env.setdefault("EXPO_NO_TELEMETRY", "1")
        env.setdefault("BROWSER", "none")
        started = time.monotonic()
        session_id, poll = await DEFAULT_EXEC_SESSION_MANAGER.start(
            command=command,
            cwd=cwd,
            env=env,
            timeout=None,
            shell_program=None,
            login=False,
            yield_time_ms=yield_time_ms,
            max_output_chars=max_output_chars,
            owner_session_key=current_request_session_key(),
        )
        if wait_hint and not poll.done and wait_hint not in (poll.output or ""):
            remaining_ms = max(0, yield_time_ms - int((time.monotonic() - started) * 1000))
            if remaining_ms > 200:
                try:
                    poll = await DEFAULT_EXEC_SESSION_MANAGER.write(
                        session_id=session_id,
                        chars=None,
                        close_stdin=False,
                        terminate=False,
                        yield_time_ms=min(remaining_ms, 3000),
                        max_output_chars=max_output_chars,
                        owner_session_key=current_request_session_key(),
                    )
                except KeyError:
                    pass
        return session_id, poll


def _normalize_target(target: str | None) -> str:
    value = (target or "android").strip().lower()
    if value in {"android", "ios", "web", "metro"}:
        return value
    return "android"
