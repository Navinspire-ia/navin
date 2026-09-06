"""Montage tool: project marketing kit + lazy HyperFrames toolchain."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, tool_parameters
from navin.agent.tools.quality import _QualityTool
from navin.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)


@tool_parameters(
    tool_parameters_schema(
        required=["action"],
        action=StringSchema(
            "detect/doctor inspect the toolchain; setup installs a package; "
            "stock_search queries Pexels/Unsplash/Pixabay; analyze/calendar "
            "write marketing kit files; screenshot registers stills; "
            "demo_register imports a browser demo; package builds platform "
            "exports; render runs HyperFrames HTML to MP4; assemble muxes "
            "clips, images, music and voice into one MP4 with per-clip trims, "
            "xfade transitions and music level; transcribe extracts timed "
            "speech from a video into .srt + .txt via the configured STT; "
            "voicetrack synthesizes a translated .srt cue by cue and pins each "
            "line back to its timecode, which is what keeps a dub in sync; "
            "voice_sample cuts a short speech clip from a video for cloning; "
            "dub replaces the audio track with a new voice (optionally keeps "
            "the original as a bed and burns an .srt); lipsync sends exactly "
            "one video and one audio track to the configured lip-sync provider; "
            "probe measures a media file (duration, dimensions, fps, audio); "
            "timeline_save builds or replaces a named timeline the user sees in "
            "Montage Studio > Timeline (same arguments as assemble plus name=), "
            "timeline_get/timeline_list read them, timeline_render renders one "
            "with live progress, timeline_delete removes it; "
            "profiles lists sizes; "
            "jobs/job/resume_job/cancel_job inspect, resume and stop durable "
            "Montage jobs.",
            enum=[
                "detect",
                "doctor",
                "setup",
                "stock_search",
                "analyze",
                "calendar",
                "screenshot",
                "demo_register",
                "package",
                "render",
                "assemble",
                "probe",
                "timeline_list",
                "timeline_get",
                "timeline_save",
                "timeline_render",
                "timeline_delete",
                "transcribe",
                "voicetrack",
                "voice_sample",
                "dub",
                "lipsync",
                "profiles",
                "jobs",
                "job",
                "resume_job",
                "cancel_job",
            ],
        ),
        days=IntegerSchema(
            14,
            description="Calendar length for action=calendar: 7, 14, or 30.",
            minimum=7,
            maximum=30,
            nullable=True,
        ),
        force=BooleanSchema(
            description="When action=setup, reinstall even if the package exists.",
            default=False,
            nullable=True,
        ),
        package=StringSchema(
            "For action=setup: core|ffmpeg|hyperframes|remotion|"
            "stock-pexels|stock-unsplash|stock-pixabay. Default hyperframes.",
            nullable=True,
        ),
        provider=StringSchema(
            "For action=stock_search: pexels|unsplash|pixabay.",
            nullable=True,
        ),
        query=StringSchema(
            "Search query for action=stock_search.",
            nullable=True,
        ),
        kind=StringSchema(
            "For action=stock_search: image|video (default image).",
            nullable=True,
        ),
        path=StringSchema(
            "Path for screenshot (image), demo_register (video), package (demo), "
            "or probe (any media file).",
            nullable=True,
        ),
        paths=StringSchema(
            "Comma-separated workspace image paths for action=screenshot.",
            nullable=True,
        ),
        title=StringSchema(
            "Optional title for action=package social demo brief.",
            nullable=True,
        ),
        srt=StringSchema(
            "Optional .srt path to burn into package exports.",
            nullable=True,
        ),
        name=StringSchema(
            "Timeline name for timeline_get/timeline_save/timeline_render/"
            "timeline_delete (1-64 lowercase letters, digits, _ or -); optional "
            "stem name for action=demo_register.",
            nullable=True,
        ),
        composition=StringSchema(
            "Workspace-relative HyperFrames HTML for action=render.",
            nullable=True,
        ),
        output=StringSchema(
            "Optional workspace-relative MP4 path for action=render.",
            nullable=True,
        ),
        width=IntegerSchema(
            1080,
            description="Render width (action=render).",
            minimum=320,
            maximum=3840,
            nullable=True,
        ),
        height=IntegerSchema(
            1920,
            description="Render height (action=render).",
            minimum=320,
            maximum=3840,
            nullable=True,
        ),
        fps=IntegerSchema(
            30,
            description="Render fps (action=render).",
            minimum=1,
            maximum=60,
            nullable=True,
        ),
        profiles=StringSchema(
            "For action=package: default|all or comma ids "
            "(youtube_landscape,youtube_shorts,instagram_reels,instagram_feed,"
            "tiktok,linkedin,youtube_4k,cinematic).",
            nullable=True,
        ),
        profile=StringSchema(
            "For action=render: built-in profile id (overrides width/height), "
            "e.g. youtube_shorts, instagram_feed, cinematic.",
            nullable=True,
        ),
        visuals=StringSchema(
            "For action=assemble: comma-separated visual paths in playback order. "
            "Mix generated video clips and images freely (generated artifact paths "
            "or workspace paths).",
            nullable=True,
        ),
        durations=StringSchema(
            "For action=assemble: comma-separated seconds per visual, aligned with "
            "visuals. Only images need one; pass 0 for video clips. Defaults to 3s "
            "per image.",
            nullable=True,
        ),
        trims=StringSchema(
            "For action=assemble: comma-separated start-end source trims in seconds "
            "per visual, aligned with visuals. '2-8' plays 2s to 8s, '3-' drops the "
            "first 3s, '-5' keeps the first 5s, '-' or empty plays the whole clip. "
            "Video clips only; images use durations instead.",
            nullable=True,
        ),
        transition=StringSchema(
            "For action=assemble: transition between clips. none (hard cut, default) "
            "or an xfade style: fade (crossfade), fadeblack, fadewhite, dissolve, "
            "wipeleft/right/up/down, slideleft/right/up/down, circleopen, "
            "circleclose, radial, smoothleft, smoothright, pixelize, hblur, "
            "distance, zoomin.",
            nullable=True,
        ),
        transition_duration=NumberSchema(
            0.5,
            description=(
                "For action=assemble: transition length in seconds (0-5, default "
                "0.5). Must be shorter than the shortest clip."
            ),
            minimum=0.05,
            maximum=5,
            nullable=True,
        ),
        music_gain_db=NumberSchema(
            -16.0,
            description=(
                "For action=assemble: music bed level in dB (default -16). Use -10 "
                "for a louder bed, -25 for barely-there. Ducking under any voice "
                "over still applies on top."
            ),
            minimum=-60,
            maximum=12,
            nullable=True,
        ),
        music=StringSchema(
            "For action=assemble: background music path. Looped to cover the edit "
            "and ducked automatically under any voice over.",
            nullable=True,
        ),
        voice=StringSchema(
            "For action=assemble: voice over / narration audio path, typically from "
            "generate_speech. For action=dub: the new voice track that replaces "
            "the original audio. For action=voicetrack: optional catalogue or "
            "provider voice id (ElevenLabs / Fish Audio id).",
            nullable=True,
        ),
        reference=StringSchema(
            "For action=voicetrack: optional path to a short speech clip used as "
            "a clone reference. Ignored unless the configured TTS model accepts "
            "one (Fish Audio, ElevenLabs). Extract it first with voice_sample.",
            nullable=True,
        ),
        language=StringSchema(
            "For action=transcribe: source language hint (ISO code like 'en', "
            "'fr'). Default: provider auto-detect.",
            nullable=True,
        ),
        original_gain_db=NumberSchema(
            description=(
                "For action=dub: keep the original audio as an ambience bed at "
                "this level in dB (e.g. -20). Omit to fully replace the track."
            ),
            minimum=-60,
            maximum=0,
            nullable=True,
        ),
        max_tempo=NumberSchema(
            1.35,
            description="For action=voicetrack: maximum natural speech speed-up.",
            minimum=1.0,
            maximum=4.0,
            nullable=True,
        ),
        sync_threshold_ms=IntegerSchema(
            120,
            description=(
                "For action=voicetrack: maximum overlap or overrun in milliseconds "
                "before the delivery gate blocks the track."
            ),
            minimum=0,
            maximum=5000,
            nullable=True,
        ),
        job_id=StringSchema(
            "Job identifier for action=job, resume_job or cancel_job.",
            nullable=True,
        ),
    )
)
class MontageTool(_QualityTool):
    """Project marketing analysis, live demo packaging, and HyperFrames tooling."""

    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        tool = super().create(ctx)
        assert isinstance(tool, MontageTool)
        tool._bus = ctx.bus
        return tool

    @property
    def name(self) -> str:
        return "montage"

    @property
    def description(self) -> str:
        return (
            "Montage studio helper: analyze the project, propose a content calendar, "
            "register UI captures, import live browser demos, package platform exports, "
            "edit clips and stills into one MP4 (assemble, or a named timeline the "
            "user sees and can adjust in Montage Studio > Timeline), measure media "
            "(probe), search builtin stock (Pexels/Unsplash/Pixabay), localize videos "
            "(transcribe to timed .srt via the configured STT, dub with a new "
            "voice track, burn subtitles), and lazily install "
            "ffmpeg / HyperFrames / optional Remotion (cross-platform). "
            "Never auto-publish. Stock clients are builtin; heavy deps install on demand. "
            "Requires Navin Plus+ for managed media, or BYOK OpenRouter keys."
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
            "profiles",
            "stock_search",
            "probe",
            "timeline_list",
            "timeline_get",
            "jobs",
            "job",
        }

    async def execute(
        self,
        action: str,
        days: int | None = None,
        force: bool | None = False,
        package: str | None = None,
        provider: str | None = None,
        query: str | None = None,
        kind: str | None = None,
        path: str | None = None,
        paths: str | None = None,
        title: str | None = None,
        srt: str | None = None,
        name: str | None = None,
        composition: str | None = None,
        output: str | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: int | None = None,
        profiles: str | None = None,
        profile: str | None = None,
        visuals: str | None = None,
        durations: str | None = None,
        trims: str | None = None,
        transition: str | None = None,
        transition_duration: float | None = None,
        music_gain_db: float | None = None,
        music: str | None = None,
        voice: str | None = None,
        language: str | None = None,
        original_gain_db: float | None = None,
        reference: str | None = None,
        max_tempo: float | None = None,
        sync_threshold_ms: int | None = None,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        action = (action or "").strip().lower()
        if action == "profiles":
            from navin.montage.profiles import render_profiles_help

            return render_profiles_help()

        if action == "stock_search":
            import json

            from navin.config.loader import load_config
            from navin.montage.stock import search_stock

            result = search_stock(
                provider or "pexels",
                query or "",
                kind=kind or "image",
                config=load_config(),
            )
            return json.dumps(result, indent=2)

        root, error = self._root_or_error()
        if root is None:
            return error

        if action == "detect":
            from navin.montage.detect import detect_toolchain

            return detect_toolchain().render()

        if action == "doctor":
            from navin.config.loader import load_config
            from navin.montage.doctor import run_doctor

            return run_doctor(config=load_config()).render()

        if action in {"jobs", "job", "resume_job", "cancel_job"}:
            import json

            from navin.montage.jobs import (
                MontageJobError,
                cancel_job,
                get_job,
                job_summary,
                list_jobs,
                resume_job,
            )

            try:
                if action == "jobs":
                    result: Any = {"jobs": [job_summary(row) for row in list_jobs(root)]}
                elif not (job_id or "").strip():
                    return f"{action} requires job_id="
                elif action == "job":
                    result = get_job(root, job_id or "")
                elif action == "cancel_job":
                    result = job_summary(cancel_job(root, job_id or ""))
                    self._notify_job(root, result)
                else:
                    result = await resume_job(root, job_id or "")
            except MontageJobError as exc:
                return f"{action} error: {exc}"
            return json.dumps(result, indent=2, ensure_ascii=False)

        if action == "probe":
            import json

            from navin.montage.assemble import AssembleError
            from navin.montage.probe import ProbeError, probe_media

            if not (path or "").strip():
                return "probe requires path= to a video, image or audio file."
            try:
                resolved = self._resolve_media_input(root, path or "")
                info = await probe_media(resolved)
            except (AssembleError, ProbeError) as exc:
                return f"probe error: {exc}"
            info["path"] = self._display_path(root, info.get("path") or resolved)
            return json.dumps(info, indent=2, ensure_ascii=False)

        if action in {
            "timeline_list",
            "timeline_get",
            "timeline_save",
            "timeline_render",
            "timeline_delete",
        }:
            return await self._timeline_action(
                action,
                root,
                name=name,
                visuals=visuals,
                durations=durations,
                trims=trims,
                transition=transition,
                transition_duration=transition_duration,
                music_gain_db=music_gain_db,
                music=music,
                voice=voice,
                subtitles=srt,
                output=output,
                width=width,
                height=height,
                fps=fps,
                profile=profile,
            )

        if action == "setup":
            from navin.config.loader import load_config
            from navin.montage.bootstrap import render_setup_result, run_setup

            result = await run_setup(
                force=bool(force),
                package=package or "hyperframes",
                config=load_config(),
            )
            return render_setup_result(result)

        if action == "analyze":
            from navin.montage.analyze import (
                analyze_project,
                render_analyze_result,
                write_project_kit,
            )

            kit = analyze_project(root)
            written = write_project_kit(root, kit)
            return render_analyze_result(root, written, kit)

        if action == "calendar":
            from navin.montage.analyze import analyze_project
            from navin.montage.calendar import (
                build_calendar,
                render_calendar_result,
                write_calendar,
            )

            day_count = int(days or 14)
            if day_count not in {7, 14, 30}:
                day_count = 14
            kit = analyze_project(root)
            # Ensure kit exists alongside calendar
            from navin.montage.analyze import write_project_kit

            write_project_kit(root, kit)
            rows = build_calendar(kit, days=day_count)
            written = write_calendar(root, rows, kit=kit, days=day_count)
            return render_calendar_result(root, written, day_count)

        if action == "screenshot":
            from navin.montage.screenshot import (
                register_captures,
                render_screenshot_result,
            )

            collected: list[str] = []
            if path:
                collected.append(path)
            if paths:
                collected.extend(p.strip() for p in paths.split(",") if p.strip())
            result = register_captures(root, collected)
            return render_screenshot_result(result)

        if action == "demo_register":
            from navin.montage.demo import register_demo, render_register_result

            if not (path or "").strip():
                return (
                    "demo_register requires path= to a browser demo recording "
                    "(from browser action=record_stop)."
                )
            result = register_demo(root, path or "", name=name)
            return render_register_result(result)

        if action == "package":
            from navin.montage.demo import package_social, render_package_result

            if not (path or "").strip():
                return (
                    "package requires path= to a demo under marketing/montage/demos/ "
                    "(or any workspace video). Fix: browser record → demo_register first."
                )
            result = package_social(
                root,
                path or "",
                title=(title or "Product demo").strip() or "Product demo",
                burn_srt=srt,
                profiles=profiles,
            )
            return render_package_result(result)

        if action == "render":
            from navin.montage.render import render_composition, render_result_text

            if not (composition or "").strip():
                return (
                    "render requires composition= (workspace-relative HyperFrames HTML). "
                    "Fix: write a composition under marketing/montage/compositions/ "
                    "then montage(action=render, composition=..., profile=youtube_shorts)."
                )
            result = await render_composition(
                root,
                composition=composition or "",
                output=output,
                width=int(width or 1080),
                height=int(height or 1920),
                fps=int(fps or 30),
                profile=profile,
            )
            return render_result_text(result)

        if action == "assemble":
            return await self._assemble(
                root,
                visuals=visuals,
                durations=durations,
                trims=trims,
                transition=transition,
                transition_duration=transition_duration,
                music_gain_db=music_gain_db,
                music=music,
                voice=voice,
                subtitles=srt,
                output=output,
                width=width,
                height=height,
                fps=fps,
                profile=profile,
            )

        if action == "transcribe":
            import json

            from navin.montage.assemble import AssembleError
            from navin.montage.localize import LocalizeError, transcribe_video

            if not (path or "").strip():
                return (
                    "transcribe requires path= to a video or audio file in the "
                    "workspace (upload or yt-dlp download first)."
                )
            try:
                resolved = self._resolve_media_input(root, path or "")
                result = await transcribe_video(root, resolved, language=language)
            except (AssembleError, LocalizeError) as exc:
                return f"transcribe error: {exc}"
            return json.dumps(result, indent=2, ensure_ascii=False)

        if action == "voicetrack":
            import json

            from navin.montage.assemble import AssembleError
            from navin.montage.localize import LocalizeError, build_dub_track

            if not (srt or "").strip():
                return (
                    "voicetrack requires srt= pointing at the translated .srt. "
                    "Each cue is synthesized on its own and placed at its own "
                    "timecode, so the dub cannot drift away from the picture."
                )
            try:
                resolved_srt = self._resolve_media_input(root, srt or "")
                resolved_ref = (
                    self._resolve_media_input(root, reference)
                    if (reference or "").strip()
                    else None
                )
                result = await build_dub_track(
                    root,
                    resolved_srt,
                    output=output,
                    voice=voice,
                    reference=resolved_ref,
                    max_tempo=float(max_tempo or 1.35),
                    sync_threshold_ms=int(
                        120 if sync_threshold_ms is None else sync_threshold_ms
                    ),
                )
            except (AssembleError, LocalizeError) as exc:
                return f"voicetrack error: {exc}"
            return json.dumps(result, indent=2, ensure_ascii=False)

        if action == "voice_sample":
            import json

            from navin.montage.assemble import AssembleError
            from navin.montage.localize import LocalizeError, extract_voice_sample

            if not (path or "").strip():
                return (
                    "voice_sample requires path= to the source video. It cuts "
                    "about 12 seconds of speech to use as a clone reference."
                )
            try:
                resolved = self._resolve_media_input(root, path or "")
                result = await extract_voice_sample(root, resolved, output=output)
            except (AssembleError, LocalizeError) as exc:
                return f"voice_sample error: {exc}"
            return json.dumps(result, indent=2, ensure_ascii=False)

        if action == "dub":
            import json

            from navin.montage.assemble import AssembleError
            from navin.montage.localize import LocalizeError, dub_video

            if not (path or "").strip() or not (voice or "").strip():
                return (
                    "dub requires path= (source video) and voice= (new audio "
                    "track produced by action=voicetrack). Optional: "
                    "srt= to burn subtitles, original_gain_db= to keep the "
                    "original audio as a bed."
                )
            try:
                resolved = self._resolve_media_input(root, path or "")
                resolved_voice = self._resolve_media_input(root, voice or "")
                resolved_srt = self._resolve_media_input(root, srt) if srt else None
                result = await dub_video(
                    root,
                    resolved,
                    resolved_voice,
                    srt=resolved_srt,
                    original_gain_db=original_gain_db,
                    output=output,
                )
            except (AssembleError, LocalizeError) as exc:
                return f"dub error: {exc}"
            return json.dumps(result, indent=2, ensure_ascii=False)

        if action == "lipsync":
            import json
            import os

            from navin.config.loader import load_config
            from navin.montage.assemble import AssembleError
            from navin.montage.jobs import (
                create_lipsync_job,
                run_job,
                run_lipsync_step,
            )

            if not (path or "").strip() or not (voice or "").strip():
                return (
                    "lipsync requires path= (one source video) and voice= "
                    "(one dubbed audio track from action=voicetrack)."
                )
            config = load_config().tools.montage.lip_sync
            if not (config.api_key or os.environ.get("SYNC_API_KEY")):
                return json.dumps(
                    {
                        "ok": False,
                        "error": "credentials_required",
                        "requires_credentials": True,
                        "provider": config.provider,
                        "fix": (
                            "Set tools.montage.lipSync.apiKey or SYNC_API_KEY "
                            "before running lipsync."
                        ),
                    },
                    indent=2,
                )
            try:
                resolved_video = self._resolve_media_input(root, path or "")
                resolved_audio = self._resolve_media_input(root, voice or "")
            except AssembleError as exc:
                return f"lipsync error: {exc}"
            target = (output or "").strip()
            if target:
                destination = Path(target)
                if not destination.is_absolute():
                    destination = root / destination
            else:
                destination = (
                    root
                    / "marketing"
                    / "montage"
                    / "localization"
                    / Path(resolved_video).stem
                    / f"{Path(resolved_video).stem}-lipsynced.mp4"
                )
            try:
                destination.resolve().relative_to(root.resolve())
            except ValueError:
                return "lipsync error: output must stay inside the workspace"
            job = create_lipsync_job(
                root,
                video=resolved_video,
                audio=resolved_audio,
                output=str(destination),
                model=config.model,
            )
            manifest = await run_job(
                root, job["id"], {"generate": run_lipsync_step}
            )
            result = dict(manifest["steps"][-1].get("result") or {})
            result.setdefault("ok", manifest.get("status") == "completed")
            result["job_id"] = job["id"]
            result["job_status"] = manifest.get("status")
            result["requires_credentials"] = True
            if manifest.get("error"):
                result["error"] = manifest["error"]
            return json.dumps(result, indent=2, ensure_ascii=False)

        return (
            f"Unknown action={action!r}. Use detect|doctor|setup|stock_search|"
            "analyze|calendar|screenshot|demo_register|package|render|assemble|probe|"
            "timeline_list|timeline_get|timeline_save|timeline_render|timeline_delete|"
            "transcribe|voicetrack|voice_sample|dub|lipsync|profiles|jobs|job|"
            "resume_job|cancel_job."
        )

    def _bus_or_none(self) -> Any:
        return getattr(self, "_bus", None)

    def _job_notifier(self, root: Path) -> Any:
        """Stream job progress to Montage Studio while the agent renders."""
        from navin.montage.notify import job_notifier

        return job_notifier(self._bus_or_none(), root)

    def _notify_job(self, root: Path, summary: dict[str, Any]) -> None:
        from navin.montage.notify import publish_montage_update

        publish_montage_update(
            self._bus_or_none(),
            root,
            kind="job",
            name=str(summary.get("id") or ""),
            job=summary,
        )

    def _notify_timeline(self, root: Path, name: str) -> None:
        from navin.montage.notify import publish_montage_update

        publish_montage_update(self._bus_or_none(), root, kind="timeline", name=name)

    @staticmethod
    def _display_path(root: Path, value: Any) -> str:
        try:
            return Path(str(value)).resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            return str(value)

    def _build_spec(
        self,
        root: Path,
        *,
        visuals: str | None,
        durations: str | None,
        trims: str | None,
        transition: str | None,
        transition_duration: float | None,
        music_gain_db: float | None,
        music: str | None,
        voice: str | None,
        subtitles: str | None,
        output: str,
        width: int | None,
        height: int | None,
        fps: int | None,
        profile: str | None,
        resolve: Any,
    ) -> Any:
        """Shared spec construction for assemble and timeline_save.

        ``resolve`` maps a user-supplied input to the path the spec stores:
        the absolute file for a one-off assemble, the imported
        workspace-relative copy for a saved timeline.
        """
        from navin.montage.assemble import (
            parse_durations,
            parse_path_list,
            parse_trims,
            spec_from_paths,
        )

        visual_paths = parse_path_list(visuals)
        canvas_width, canvas_height = int(width or 1080), int(height or 1920)
        if profile:
            from navin.montage.profiles import get_profile

            found = get_profile(profile)
            if found is not None:
                canvas_width, canvas_height = found.width, found.height
        return spec_from_paths(
            visuals=[resolve(item) for item in visual_paths],
            durations=parse_durations(durations),
            trims=parse_trims(trims),
            transition=transition,
            transition_duration=transition_duration,
            music_gain_db=music_gain_db,
            music=resolve(music) if music else None,
            voice=resolve(voice) if voice else None,
            subtitles=resolve(subtitles) if subtitles else None,
            output=output,
            width=canvas_width,
            height=canvas_height,
            fps=int(fps or 30),
        )

    async def _timeline_action(
        self,
        action: str,
        root: Path,
        *,
        name: str | None,
        visuals: str | None,
        durations: str | None,
        trims: str | None,
        transition: str | None,
        transition_duration: float | None,
        music_gain_db: float | None,
        music: str | None,
        voice: str | None,
        subtitles: str | None,
        output: str | None,
        width: int | None,
        height: int | None,
        fps: int | None,
        profile: str | None,
    ) -> str:
        """Timelines are the documents the user sees and edits in Montage Studio.

        The agent and the human share them: the agent lays out clips with
        ``timeline_save``, the user adjusts trims in the Timeline tab, either
        side renders. Every change is broadcast so the open editor refreshes.
        """
        import json

        from navin.montage.assemble import AssembleError
        from navin.montage.timeline import (
            TimelineError,
            delete_timeline,
            get_timeline,
            import_media,
            list_timelines,
            put_timeline,
            render_timeline,
            timeline_from_spec,
            timeline_to_spec,
            validate_name,
        )

        if action == "timeline_list":
            rows = list_timelines(root)
            return json.dumps(
                {
                    "count": len(rows),
                    "timelines": rows,
                    "hint": (
                        "The user sees these under Montage Studio > Timeline. "
                        "Use timeline_get name=... for the full document."
                    ),
                },
                indent=2,
                ensure_ascii=False,
            )

        try:
            timeline_name = validate_name(name or "")
        except TimelineError as exc:
            return f"{action} error: {exc} (pass name=)"

        try:
            if action == "timeline_get":
                document = get_timeline(root, timeline_name)
                spec = timeline_to_spec(document, root, validate=False)
                from navin.montage.assemble import (
                    probe_video_lengths,
                    resolve_clip_durations,
                    resolve_total_duration,
                )
                from navin.montage.detect import find_ffmpeg

                probed = await probe_video_lengths(spec, find_ffmpeg())
                document["clip_durations_s"] = resolve_clip_durations(spec, probed.get)
                document["duration_s"] = resolve_total_duration(spec, probed.get)
                return json.dumps(document, indent=2, ensure_ascii=False)

            if action == "timeline_delete":
                result = delete_timeline(root, timeline_name)
                self._notify_timeline(root, timeline_name)
                return json.dumps(result, indent=2, ensure_ascii=False)

            if action == "timeline_save":
                if not (visuals or "").strip():
                    return (
                        "timeline_save requires name= and visuals= (comma-separated clip "
                        "and/or image paths in playback order); optional durations, "
                        "trims, transition, transition_duration, music, voice, srt, "
                        "music_gain_db, profile or width/height, fps, output."
                    )
                target = (output or "").strip() or (
                    f"marketing/montage/exports/{timeline_name}.mp4"
                )

                def resolve(value: str) -> str:
                    resolved = self._resolve_media_input(root, value)
                    return import_media(root, resolved)

                spec = self._build_spec(
                    root,
                    visuals=visuals,
                    durations=durations,
                    trims=trims,
                    transition=transition,
                    transition_duration=transition_duration,
                    music_gain_db=music_gain_db,
                    music=music,
                    voice=voice,
                    subtitles=subtitles,
                    output=target,
                    width=width,
                    height=height,
                    fps=fps,
                    profile=profile,
                    resolve=resolve,
                )
                document = timeline_from_spec(spec, root, name=timeline_name)
                saved = put_timeline(root, timeline_name, document)
                self._notify_timeline(root, timeline_name)
                saved["next_step"] = (
                    "The timeline is now open to the user in Montage Studio > Timeline. "
                    f"Render it with montage(action=timeline_render, name={timeline_name})."
                )
                return json.dumps(saved, indent=2, ensure_ascii=False)

            # timeline_render
            manifest = await render_timeline(
                root,
                timeline_name,
                output=(output or "").strip() or None,
                notify=self._job_notifier(root),
            )
        except (AssembleError, TimelineError) as exc:
            return f"{action} error: {exc}"

        result = dict((manifest["steps"][-1].get("result") or {}))
        result.setdefault("ok", manifest.get("status") == "completed")
        result["job_id"] = manifest["id"]
        result["job_status"] = manifest.get("status")
        result["latency_s"] = manifest.get("latency_s")
        if manifest.get("error"):
            result["error"] = manifest["error"]
        if result.get("output"):
            result["output"] = self._display_path(root, result["output"])
        if result.get("ok"):
            result["next_step"] = (
                "Call the message tool with this output path in the media parameter "
                "to deliver the video to the user."
            )
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _resolve_media_input(self, root: Path, value: str) -> str:
        """Resolve a visual or audio input inside the workspace or the media dir."""
        from navin.agent.tools.path_utils import project_rooted_path
        from navin.config.paths import get_media_dir
        from navin.montage.assemble import AssembleError
        from navin.security.workspace_access import current_tool_workspace
        from navin.security.workspace_policy import (
            WorkspaceBoundaryError,
            resolve_allowed_path,
        )

        access = current_tool_workspace(root, restrict_to_workspace=True)
        workspace = access.project_path or root
        try:
            resolved = resolve_allowed_path(
                project_rooted_path(value, workspace, [access.allowed_root, get_media_dir()]),
                workspace=workspace,
                allowed_root=access.allowed_root,
                extra_allowed_roots=(
                    [get_media_dir()] if access.allowed_root is not None else None
                ),
                strict=True,
            )
        except WorkspaceBoundaryError as exc:
            raise AssembleError(
                f"input must be inside the workspace or navin media directory: {value}"
            ) from exc
        except OSError as exc:
            raise AssembleError(f"input not found: {value}") from exc
        if not resolved.is_file():
            raise AssembleError(f"input is not a file: {value}")
        return str(resolved)

    async def _assemble(
        self,
        root: Path,
        *,
        visuals: str | None,
        durations: str | None,
        trims: str | None,
        transition: str | None,
        transition_duration: float | None,
        music_gain_db: float | None,
        music: str | None,
        voice: str | None,
        subtitles: str | None,
        output: str | None,
        width: int | None,
        height: int | None,
        fps: int | None,
        profile: str | None,
    ) -> str:
        import json

        from navin.montage.assemble import AssembleError, parse_path_list
        from navin.montage.jobs import (
            create_assemble_job,
            run_assemble_step,
            run_job,
        )

        if not parse_path_list(visuals):
            return (
                "assemble requires visuals= (comma-separated clip and/or image paths, "
                "in playback order). Fix: generate_video / generate_image first, then "
                "pass the returned artifact paths."
            )

        target = (output or "").strip() or "marketing/montage/exports/final.mp4"
        destination = Path(target)
        if not destination.is_absolute():
            destination = root / destination

        try:
            spec = self._build_spec(
                root,
                visuals=visuals,
                durations=durations,
                trims=trims,
                transition=transition,
                transition_duration=transition_duration,
                music_gain_db=music_gain_db,
                music=music,
                voice=voice,
                subtitles=subtitles,
                output=str(destination),
                width=width,
                height=height,
                fps=fps,
                profile=profile,
                resolve=lambda value: self._resolve_media_input(root, value),
            )
        except AssembleError as exc:
            return f"assemble error: {exc}"

        job = create_assemble_job(root, spec)
        manifest = await run_job(
            root,
            job["id"],
            {"render": run_assemble_step},
            notify=self._job_notifier(root),
        )
        result = dict((manifest["steps"][-1].get("result") or {}))
        result.setdefault("ok", manifest.get("status") == "completed")
        result["job_id"] = job["id"]
        result["job_status"] = manifest.get("status")
        result["latency_s"] = manifest.get("latency_s")
        result["cost"] = manifest.get("cost")
        if manifest.get("error"):
            result["error"] = manifest["error"]
        if result.get("ok"):
            result["next_step"] = (
                "Call the message tool with this output path in the media parameter "
                "to deliver the video to the user."
            )
        return json.dumps(result, indent=2, ensure_ascii=False)
