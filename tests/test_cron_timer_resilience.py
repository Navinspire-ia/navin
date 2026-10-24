# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The scheduler keeps running 24/7: no self-cancel, no dead timer, no hang."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from navin.cron.service import CronService
from navin.cron.types import CronJob, CronLimits, CronPayload, CronSchedule


def _service(tmp: str, on_job=None, **kwargs) -> CronService:
    return CronService(Path(tmp) / "cron" / "jobs.json", on_job=on_job, **kwargs)


def _system_job(every_ms: int = 60_000) -> CronJob:
    return CronJob(
        id="heartbeat",
        name="heartbeat",
        schedule=CronSchedule(kind="every", every_ms=every_ms),
        payload=CronPayload(kind="system_event"),
        limits=CronLimits(),
    )


def test_changing_jobs_during_a_run_does_not_cancel_the_running_job() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            finished: list[str] = []
            service: CronService

            async def on_job(job: CronJob) -> None:
                # The agent adds a job mid-turn: that re-arms the timer.
                service.add_job("other", CronSchedule(kind="every", every_ms=3_600_000), "hi")
                await asyncio.sleep(0.05)
                finished.append(job.id)

            service = _service(tmp, on_job=on_job)
            await service.start()
            service.register_system_job(_system_job())
            job = service.get_job("heartbeat")
            job.state.next_run_at_ms = 1
            service._save_store()
            service._arm_timer()  # the real timer task runs the job
            for _ in range(100):
                if finished:
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.05)
            assert finished == ["heartbeat"]
            assert service.get_job("heartbeat").state.last_status == "ok"
            assert service.get_job("heartbeat").state.next_run_at_ms > 1
            service.stop()

    asyncio.run(scenario())


def test_a_failing_tick_still_rearms_the_timer() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = _service(tmp)
            await service.start()

            def boom() -> None:
                raise OSError("disk full")

            service._load_store = boom  # type: ignore[method-assign]
            service._timer_task = None
            await service._on_timer()
            assert service._timer_task is not None and not service._timer_task.done()
            service.stop()

    asyncio.run(scenario())


def test_a_hung_job_times_out_instead_of_blocking_the_scheduler() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            async def on_job(job: CronJob) -> None:
                await asyncio.sleep(60)

            service = _service(tmp, on_job=on_job, job_timeout_s=0.05)
            await service.start()
            service.register_system_job(_system_job())
            await service.run_job("heartbeat", force=True)
            state = service.get_job("heartbeat").state
            assert state.last_status == "error"
            assert "timed out" in (state.last_error or "")
            service.stop()

    asyncio.run(scenario())


def test_restart_keeps_spend_and_next_slot_of_a_system_job() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = _service(tmp)
            await service.start()
            service.register_system_job(_system_job(every_ms=1_800_000))
            job = service.get_job("heartbeat")
            job.state.tokens_today = 1234
            slot = job.state.next_run_at_ms
            service._save_store()
            service.stop()

            restarted = _service(tmp)
            await restarted.start()
            restarted.register_system_job(_system_job(every_ms=1_800_000))
            again = restarted.get_job("heartbeat")
            assert again.state.tokens_today == 1234
            assert again.state.next_run_at_ms == slot
            restarted.stop()

    asyncio.run(scenario())
