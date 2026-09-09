# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Recurrences, failure handling and spend guardrails for scheduled loops."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from croniter import croniter

from navin.cron.recurrence import (
    HOUR_INTERVALS,
    LAST_DAY,
    MINUTE_INTERVALS,
    Recurrence,
    RecurrenceError,
    parse_expr,
    parse_schedule,
    recurrence_from_payload,
    recurrence_payload,
)
from navin.cron.service import CronService
from navin.cron.types import CronJob, CronLimits, CronPayload, CronRunRecord, CronSchedule


class RecurrenceExpressionTest(unittest.TestCase):
    """Every preset has to compile to an expression the scheduler accepts."""

    def _expr(self, recurrence: Recurrence) -> str:
        expr = recurrence.to_expr()
        self.assertTrue(croniter.is_valid(expr), f"{expr} is not a valid expression")
        return expr

    def test_every_n_minutes_fires_on_the_clock(self):
        self.assertEqual(self._expr(Recurrence(kind="minutes", interval=30)), "*/30 * * * *")

    def test_every_minute_needs_no_step(self):
        self.assertEqual(self._expr(Recurrence(kind="minutes", interval=1)), "* * * * *")

    def test_hourly_fires_at_a_chosen_minute(self):
        self.assertEqual(self._expr(Recurrence(kind="hourly", minute=15)), "15 * * * *")

    def test_every_eight_hours_fires_at_midnight_eight_and_sixteen(self):
        expr = self._expr(Recurrence(kind="every_hours", interval=8))
        self.assertEqual(expr, "0 */8 * * *")
        cursor = croniter(expr, datetime(2026, 1, 1, tzinfo=UTC))
        fired = [cursor.get_next(datetime) for _ in range(3)]
        self.assertEqual([moment.hour for moment in fired], [8, 16, 0])
        self.assertTrue(all(moment.minute == 0 for moment in fired))

    def test_daily_fires_once_at_the_chosen_time(self):
        self.assertEqual(self._expr(Recurrence(kind="daily", hour=3, minute=30)), "30 3 * * *")

    def test_weekly_maps_iso_monday_to_the_cron_week(self):
        self.assertEqual(self._expr(Recurrence(kind="weekly", weekday=1, hour=9)), "0 9 * * 1")

    def test_weekly_wraps_iso_sunday_to_zero(self):
        # ISO calls Sunday 7, this cron dialect calls it 0.
        self.assertEqual(self._expr(Recurrence(kind="weekly", weekday=7, hour=9)), "0 9 * * 0")

    def test_monthly_fires_on_the_chosen_day(self):
        self.assertEqual(self._expr(Recurrence(kind="monthly", day=15, hour=8)), "0 8 15 * *")

    def test_monthly_can_ask_for_the_last_day(self):
        self.assertEqual(
            self._expr(Recurrence(kind="monthly", day=LAST_DAY, hour=23)),
            "0 23 L * *",
        )

    def test_the_last_day_of_the_month_actually_moves(self):
        # A job asking for the end of the month must not settle on one date:
        # January ends on the 31st and February on the 28th.
        expr = Recurrence(kind="monthly", day=LAST_DAY).to_expr()
        cursor = croniter(expr, datetime(2026, 1, 1, tzinfo=UTC))
        days = {cursor.get_next(datetime).day for _ in range(3)}
        self.assertGreater(len(days), 1, "the last day never changed")

    def test_a_timezone_travels_with_the_schedule(self):
        schedule = Recurrence(kind="daily", hour=3, tz="Europe/Paris").to_schedule()
        self.assertEqual(schedule.kind, "cron")
        self.assertEqual(schedule.tz, "Europe/Paris")


class RecurrenceValidationTest(unittest.TestCase):
    """A recurrence the scheduler cannot honour is refused where it is written."""

    def test_an_uneven_minute_interval_is_refused(self):
        # */7 would fire at :56 then :00, a four minute gap.
        with self.assertRaises(RecurrenceError):
            Recurrence(kind="minutes", interval=7)

    def test_an_uneven_hour_interval_is_refused(self):
        with self.assertRaises(RecurrenceError):
            Recurrence(kind="every_hours", interval=5)

    def test_every_offered_interval_divides_its_unit_evenly(self):
        self.assertTrue(all(60 % value == 0 for value in MINUTE_INTERVALS))
        self.assertTrue(all(24 % value == 0 for value in HOUR_INTERVALS))

    def test_an_impossible_hour_is_refused(self):
        with self.assertRaises(RecurrenceError):
            Recurrence(kind="daily", hour=24)

    def test_an_impossible_minute_is_refused(self):
        with self.assertRaises(RecurrenceError):
            Recurrence(kind="daily", minute=60)

    def test_a_day_that_some_months_lack_is_refused(self):
        # The 31st would silently skip the months without one.
        with self.assertRaises(RecurrenceError):
            Recurrence(kind="monthly", day=31)

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(RecurrenceError):
            Recurrence(kind="fortnightly")  # type: ignore[arg-type]

    def test_a_boolean_is_not_an_hour(self):
        with self.assertRaises(RecurrenceError):
            Recurrence(kind="daily", hour=True)


class RecurrenceRoundTripTest(unittest.TestCase):
    """A saved loop has to reopen as the preset that created it."""

    def test_every_preset_survives_a_round_trip(self):
        presets = [
            Recurrence(kind="minutes", interval=15),
            Recurrence(kind="hourly", minute=5),
            Recurrence(kind="every_hours", interval=8, minute=30),
            Recurrence(kind="daily", hour=6, minute=45),
            Recurrence(kind="weekly", weekday=3, hour=18),
            Recurrence(kind="monthly", day=2, hour=7),
            Recurrence(kind="monthly", day=LAST_DAY, hour=7),
        ]
        for preset in presets:
            with self.subTest(kind=preset.kind, interval=preset.interval, day=preset.day):
                self.assertEqual(parse_expr(preset.to_expr()), preset)

    def test_a_hand_written_expression_is_left_alone(self):
        # No preset describes weekdays only, and rounding it to "daily" would
        # quietly change when the loop runs.
        self.assertIsNone(parse_expr("0 9 * * 1-5"))

    def test_sunday_written_as_zero_reads_back_as_iso_sunday(self):
        parsed = parse_expr("0 9 * * 0")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.kind, "weekly")
        self.assertEqual(parsed.weekday, 7)

    def test_a_drifting_interval_schedule_has_no_preset(self):
        # "every" fires relative to the last run, which no clock preset matches.
        self.assertIsNone(parse_schedule(CronSchedule(kind="every", every_ms=60_000)))

    def test_a_malformed_expression_is_not_a_preset(self):
        for expr in ("", "0 9 * *", "not a cron", "0 9 * 1 *", "*/0 * * * *"):
            with self.subTest(expr=expr):
                self.assertIsNone(parse_expr(expr))


class RecurrencePayloadTest(unittest.TestCase):
    """The client sends untrusted JSON, so the parser answers in plain words."""

    def test_a_schedule_serializes_for_the_editor(self):
        schedule = Recurrence(kind="weekly", weekday=6, hour=10, minute=20).to_schedule()
        self.assertEqual(
            recurrence_payload(schedule),
            {
                "kind": "weekly",
                "interval": 1,
                "minute": 20,
                "hour": 10,
                "weekday": 6,
                "day": 1,
                "tz": None,
            },
        )

    def test_a_payload_round_trips_through_json(self):
        original = Recurrence(kind="every_hours", interval=12, minute=5, tz="UTC")
        rebuilt = recurrence_from_payload(json.loads(json.dumps(recurrence_payload(
            original.to_schedule()
        ))))
        self.assertEqual(rebuilt, original)

    def test_the_last_day_survives_the_payload(self):
        rebuilt = recurrence_from_payload({"kind": "monthly", "day": LAST_DAY, "hour": 4})
        self.assertEqual(rebuilt.day, LAST_DAY)

    def test_a_missing_kind_is_reported(self):
        with self.assertRaises(RecurrenceError):
            recurrence_from_payload({"hour": 3})

    def test_a_string_hour_is_reported(self):
        with self.assertRaises(RecurrenceError):
            recurrence_from_payload({"kind": "daily", "hour": "3"})

    def test_a_non_object_is_reported(self):
        with self.assertRaises(RecurrenceError):
            recurrence_from_payload(["daily"])

    def test_an_unusable_day_string_is_reported(self):
        with self.assertRaises(RecurrenceError):
            recurrence_from_payload({"kind": "monthly", "day": "tuesday"})


def _service(tmp: Path, **kwargs) -> CronService:
    service = CronService(tmp / "cron.json", **kwargs)
    service._load_store()
    return service


def _store_job(service: CronService, job: CronJob) -> CronJob:
    """Persist a job, since every public call reloads the store from disk."""
    service._store.jobs.append(job)
    service._save_store()
    return job


def _bound_payload() -> CronPayload:
    """The payload of a loop created from a chat session."""
    return CronPayload(
        kind="agent_turn",
        message="send the report",
        session_key="websocket:main",
        origin_channel="websocket",
        origin_chat_id="main",
    )


class FailureBackoffTest(unittest.IsolatedAsyncioTestCase):
    """A loop that keeps failing must not keep retrying at full speed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def _run_failing(self, service: CronService, job: CronJob, times: int) -> None:
        async def failing(_job):
            raise RuntimeError("provider unreachable")

        service.on_job = failing
        for _ in range(times):
            await service._execute_job(job)

    def _job(self, **kwargs) -> CronJob:
        return CronJob(
            id="job-1",
            name="Nightly report",
            schedule=Recurrence(kind="minutes", interval=1).to_schedule(),
            # A loop the user created is bound to the chat it came from;
            # an unbound one gets disabled on sight, which is another test.
            payload=_bound_payload(),
            **kwargs,
        )

    async def test_a_failure_is_counted(self):
        service = _service(self.root)
        job = self._job()
        _store_job(service, job)
        await self._run_failing(service, job, 1)
        self.assertEqual(job.state.consecutive_failures, 1)

    async def test_each_failure_pushes_the_next_attempt_further_out(self):
        service = _service(self.root, backoff_base_ms=60_000, max_consecutive_failures=0)
        job = self._job()
        _store_job(service, job)

        delays = []
        for _ in range(3):
            await self._run_failing(service, job, 1)
            assert job.state.next_run_at_ms is not None
            delays.append(job.state.next_run_at_ms - job.state.last_run_at_ms)

        self.assertLess(delays[0], delays[1])
        self.assertLess(delays[1], delays[2])

    async def test_the_backoff_stops_growing_at_the_cap(self):
        service = _service(self.root, backoff_base_ms=1_000, backoff_cap_ms=4_000)
        self.assertEqual(service._backoff_ms(1), 1_000)
        self.assertEqual(service._backoff_ms(3), 4_000)
        self.assertEqual(service._backoff_ms(50), 4_000)

    async def test_a_success_clears_the_streak(self):
        service = _service(self.root)
        job = self._job()
        _store_job(service, job)
        await self._run_failing(service, job, 2)

        async def succeeding(_job):
            return None

        service.on_job = succeeding
        await service._execute_job(job)
        self.assertEqual(job.state.consecutive_failures, 0)
        self.assertIsNone(job.state.paused_reason)

    async def test_a_loop_that_keeps_failing_is_paused_with_a_reason(self):
        service = _service(self.root, max_consecutive_failures=3)
        job = self._job()
        _store_job(service, job)
        await self._run_failing(service, job, 3)

        self.assertFalse(job.enabled)
        self.assertIsNone(job.state.next_run_at_ms)
        self.assertIn("3 consecutive failures", job.state.paused_reason or "")
        self.assertIn("provider unreachable", job.state.paused_reason or "")

    async def test_a_loop_is_not_paused_before_its_ceiling(self):
        service = _service(self.root, max_consecutive_failures=3)
        job = self._job()
        _store_job(service, job)
        await self._run_failing(service, job, 2)
        self.assertTrue(job.enabled)

    async def test_a_job_can_set_a_stricter_ceiling_than_the_service(self):
        service = _service(self.root, max_consecutive_failures=10)
        job = self._job(limits=CronLimits(max_consecutive_failures=2))
        _store_job(service, job)
        await self._run_failing(service, job, 2)
        self.assertFalse(job.enabled)

    async def test_turning_a_paused_loop_back_on_gives_it_a_clean_slate(self):
        service = _service(self.root, max_consecutive_failures=2)
        job = self._job()
        _store_job(service, job)
        await self._run_failing(service, job, 2)
        self.assertFalse(job.enabled)

        revived = service.enable_job(job.id, enabled=True)
        assert revived is not None
        self.assertEqual(revived.state.consecutive_failures, 0)
        self.assertIsNone(revived.state.paused_reason)
        self.assertIsNotNone(revived.state.next_run_at_ms)

    async def test_a_skipped_run_does_not_clear_the_streak(self):
        # A skip says the job never ran, so it cannot prove the fault is gone.
        from navin.cron.service import CronJobSkippedError

        service = _service(self.root)
        job = self._job()
        _store_job(service, job)
        await self._run_failing(service, job, 1)

        async def skipping(_job):
            raise CronJobSkippedError("already running")

        service.on_job = skipping
        await service._execute_job(job)
        self.assertEqual(job.state.consecutive_failures, 1)


class SpendGuardrailTest(unittest.IsolatedAsyncioTestCase):
    """An unattended loop must not spend without a ceiling."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _job(self, budget: int) -> CronJob:
        return CronJob(
            id="job-1",
            name="Hourly digest",
            schedule=Recurrence(kind="hourly").to_schedule(),
            limits=CronLimits(daily_token_budget=budget),
        )

    async def _service_with_job(self, budget: int) -> tuple[CronService, CronJob, list[str]]:
        service = _service(self.root, timezone_name="UTC")
        job = self._job(budget)
        _store_job(service, job)
        ran: list[str] = []

        async def running(target):
            ran.append(target.id)

        service.on_job = running
        return service, job, ran

    async def test_a_run_below_the_budget_goes_ahead(self):
        service, job, ran = await self._service_with_job(1_000)
        service.record_job_tokens(job.id, 400)
        await service._execute_job(job)
        self.assertEqual(ran, [job.id])

    async def test_a_run_at_the_budget_is_skipped_and_says_why(self):
        service, job, ran = await self._service_with_job(1_000)
        service.record_job_tokens(job.id, 1_000)
        await service._execute_job(job)

        self.assertEqual(ran, [])
        self.assertEqual(job.state.last_status, "skipped")
        self.assertIn("daily token budget reached", job.state.last_error or "")

    async def test_a_skipped_run_still_schedules_the_next_one(self):
        # The budget pauses spending for the day, it does not stop the loop.
        service, job, _ = await self._service_with_job(100)
        service.record_job_tokens(job.id, 100)
        await service._execute_job(job)
        self.assertTrue(job.enabled)
        self.assertIsNotNone(job.state.next_run_at_ms)

    async def test_a_budget_of_zero_means_no_ceiling(self):
        service, job, ran = await self._service_with_job(0)
        service.record_job_tokens(job.id, 10_000_000)
        await service._execute_job(job)
        self.assertEqual(ran, [job.id])

    async def test_spend_accumulates_across_runs(self):
        service, job, _ = await self._service_with_job(1_000)
        service.record_job_tokens(job.id, 300)
        service.record_job_tokens(job.id, 200)
        self.assertEqual(job.state.tokens_today, 500)

    async def test_spend_is_attributed_to_the_run_that_caused_it(self):
        service, job, _ = await self._service_with_job(0)
        await service._execute_job(job)
        service.record_job_tokens(job.id, 250)
        self.assertEqual(job.state.run_history[-1].tokens, 250)

    async def test_the_counter_resets_when_the_local_day_turns(self):
        service, job, ran = await self._service_with_job(100)
        service.record_job_tokens(job.id, 100)
        job.state.tokens_day = "1999-12-31"
        await service._execute_job(job)

        self.assertEqual(ran, [job.id])
        self.assertEqual(job.state.tokens_today, 0)

    async def test_spend_reported_for_an_unknown_job_is_ignored(self):
        service, _, _ = await self._service_with_job(0)
        service.record_job_tokens("no-such-job", 500)  # must not raise

    async def test_a_negative_report_is_ignored(self):
        service, job, _ = await self._service_with_job(0)
        service.record_job_tokens(job.id, -5)
        self.assertEqual(job.state.tokens_today, 0)


class GuardrailPersistenceTest(unittest.TestCase):
    """Guardrail state is worthless if a restart forgets it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_failure_and_spend_state_survive_a_restart(self):
        service = _service(self.root)
        job = CronJob(
            id="job-1",
            name="Nightly report",
            schedule=Recurrence(kind="daily", hour=2).to_schedule(),
            limits=CronLimits(daily_token_budget=5_000, max_consecutive_failures=4),
        )
        job.state.consecutive_failures = 3
        job.state.paused_reason = "paused automatically after 3 consecutive failures"
        job.state.tokens_today = 1_234
        job.state.tokens_day = "2026-07-26"
        _store_job(service, job)
        service._save_store()

        reloaded = _service(self.root)
        stored = reloaded.get_job("job-1")
        assert stored is not None
        self.assertEqual(stored.limits.daily_token_budget, 5_000)
        self.assertEqual(stored.limits.max_consecutive_failures, 4)
        self.assertEqual(stored.state.consecutive_failures, 3)
        self.assertEqual(stored.state.tokens_today, 1_234)
        self.assertEqual(stored.state.tokens_day, "2026-07-26")
        self.assertIn("paused automatically", stored.state.paused_reason or "")

    def test_a_store_written_before_guardrails_existed_still_loads(self):
        legacy = {
            "version": 1,
            "jobs": [
                {
                    "id": "job-1",
                    "name": "Old job",
                    "enabled": True,
                    "schedule": {"kind": "every", "everyMs": 60_000},
                    "payload": {"kind": "system_event", "message": "tick"},
                    "state": {"nextRunAtMs": None, "runHistory": [{"runAtMs": 1, "status": "ok"}]},
                }
            ],
        }
        (self.root / "cron.json").write_text(json.dumps(legacy), encoding="utf-8")

        stored = _service(self.root).get_job("job-1")
        assert stored is not None
        self.assertEqual(stored.limits.daily_token_budget, 0)
        self.assertEqual(stored.state.consecutive_failures, 0)
        self.assertIsNone(stored.state.paused_reason)
        self.assertEqual(stored.state.run_history[0].tokens, 0)


class UpdatePayloadTest(unittest.TestCase):
    """What the Loop editor sends has to arrive as the schedule it picked."""

    def _parse(self, values: dict) -> dict | str:
        from navin.webui.ws_http import _parse_automation_update

        return _parse_automation_update(values)

    def test_a_preset_becomes_a_clock_anchored_schedule(self):
        parsed = self._parse(
            {
                "schedule": {
                    "kind": "recurrence",
                    "tz": "Europe/Paris",
                    "recurrence": {"kind": "every_hours", "interval": 8},
                }
            }
        )
        assert isinstance(parsed, dict)
        self.assertEqual(parsed["schedule"], CronSchedule(kind="cron", expr="0 */8 * * *", tz="Europe/Paris"))

    def test_every_preset_the_editor_offers_is_accepted(self):
        presets = [
            {"kind": "minutes", "interval": 15},
            {"kind": "hourly", "minute": 30},
            {"kind": "every_hours", "interval": 8},
            {"kind": "daily", "hour": 7, "minute": 15},
            {"kind": "weekly", "weekday": 5, "hour": 18},
            {"kind": "monthly", "day": 1, "hour": 9},
            {"kind": "monthly", "day": LAST_DAY, "hour": 9},
        ]
        for preset in presets:
            with self.subTest(**preset):
                parsed = self._parse({"schedule": {"kind": "recurrence", "recurrence": preset}})
                assert isinstance(parsed, dict)
                self.assertTrue(croniter.is_valid(parsed["schedule"].expr))

    def test_an_unusable_preset_is_refused_with_a_reason(self):
        parsed = self._parse(
            {"schedule": {"kind": "recurrence", "recurrence": {"kind": "every_hours", "interval": 5}}}
        )
        self.assertIsInstance(parsed, str)
        assert isinstance(parsed, str)
        self.assertIn("evenly spaced", parsed)

    def test_a_budget_arrives_as_a_limit(self):
        parsed = self._parse({"limits": {"daily_token_budget": 50_000}})
        assert isinstance(parsed, dict)
        self.assertEqual(parsed["limits"].daily_token_budget, 50_000)

    def test_a_negative_budget_is_refused(self):
        self.assertIsInstance(self._parse({"limits": {"daily_token_budget": -1}}), str)

    def test_a_budget_that_is_not_a_number_is_refused(self):
        self.assertIsInstance(self._parse({"limits": {"daily_token_budget": "lots"}}), str)

    def test_an_absurd_failure_ceiling_is_refused(self):
        self.assertIsInstance(self._parse({"limits": {"max_consecutive_failures": 5_000}}), str)

    def test_limits_that_are_not_an_object_are_refused(self):
        self.assertIsInstance(self._parse({"limits": 5}), str)


class AutomationsPayloadTest(unittest.TestCase):
    """The Loop view can only show what the payload carries."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _payload(self, job: CronJob) -> dict:
        from navin.webui.session_automations import all_automations_payload

        service = _service(self.root)
        _store_job(service, job)
        return all_automations_payload(service)["jobs"][0]

    def test_a_preset_schedule_is_sent_back_as_a_preset(self):
        job = CronJob(
            id="job-1",
            name="Digest",
            schedule=Recurrence(kind="daily", hour=7, minute=30).to_schedule(),
            payload=_bound_payload(),
        )
        recurrence = self._payload(job)["schedule"]["recurrence"]
        self.assertEqual(recurrence["kind"], "daily")
        self.assertEqual(recurrence["hour"], 7)
        self.assertEqual(recurrence["minute"], 30)

    def test_a_hand_written_schedule_has_no_preset(self):
        job = CronJob(
            id="job-1",
            name="Weekdays",
            schedule=CronSchedule(kind="cron", expr="0 9 * * 1-5"),
            payload=_bound_payload(),
        )
        self.assertIsNone(self._payload(job)["schedule"]["recurrence"])

    def test_a_paused_loop_reports_why(self):
        job = CronJob(id="job-1", name="Digest", payload=_bound_payload(), enabled=False)
        job.state.paused_reason = "paused automatically after 5 consecutive failures"
        job.state.consecutive_failures = 5
        state = self._payload(job)["state"]
        self.assertIn("paused automatically", state["paused_reason"])
        self.assertEqual(state["consecutive_failures"], 5)

    def test_spend_and_budget_are_visible(self):
        job = CronJob(
            id="job-1",
            name="Digest",
            payload=_bound_payload(),
            limits=CronLimits(daily_token_budget=10_000),
        )
        job.state.tokens_today = 2_500
        entry = self._payload(job)
        self.assertEqual(entry["state"]["tokens_today"], 2_500)
        self.assertEqual(entry["limits"]["daily_token_budget"], 10_000)

    def test_a_system_loop_is_named_by_a_key_the_client_can_translate(self):
        from navin.cron.types import CronPayload

        job = CronJob(id="dream", name="dream", payload=CronPayload(kind="system_event"))
        entry = self._payload(job)
        self.assertTrue(entry["protected"])
        self.assertEqual(entry["system_key"], "dream")

    def test_a_user_loop_has_no_system_key(self):
        job = CronJob(id="job-1", name="Digest", payload=_bound_payload())
        self.assertIsNone(self._payload(job)["system_key"])


class SystemLoopConfigTest(unittest.TestCase):
    """A built-in loop is re-registered from the config on every start.

    So a change written only to the store would be undone by the next restart,
    which is the bug these tests exist to prevent.
    """

    def setUp(self) -> None:
        from navin.config.loader import _current_config_path, set_config_path

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._previous_path = _current_config_path
        set_config_path(self.root / "config.json")

    def tearDown(self) -> None:
        from navin.config.loader import set_config_path

        if self._previous_path is not None:
            set_config_path(self._previous_path)
        else:
            import navin.config.loader as loader

            loader._current_config_path = None
        self._tmp.cleanup()

    def _saved(self) -> dict:
        return json.loads((self.root / "config.json").read_text(encoding="utf-8"))

    def test_a_preset_is_written_to_the_config(self):
        from navin.webui.system_loops import update_system_loop

        error = update_system_loop(
            "dream",
            schedule=Recurrence(kind="daily", hour=3, minute=30).to_schedule(),
        )
        self.assertIsNone(error)

        from navin.config.loader import load_config

        dream = load_config().agents.defaults.dream
        assert dream.schedule is not None
        self.assertEqual(dream.schedule.kind, "daily")
        self.assertEqual(dream.schedule.hour, 3)
        self.assertEqual(dream.schedule.minute, 30)

    def test_the_written_config_survives_a_reload_as_the_same_schedule(self):
        from navin.config.loader import load_config
        from navin.webui.system_loops import update_system_loop

        update_system_loop(
            "heartbeat",
            schedule=Recurrence(kind="every_hours", interval=8).to_schedule(),
        )
        rebuilt = load_config().gateway.heartbeat.build_schedule("Europe/Paris")
        self.assertEqual(rebuilt.expr, "0 */8 * * *")
        self.assertEqual(rebuilt.tz, "Europe/Paris")

    def test_a_budget_is_written_to_the_config(self):
        from navin.config.loader import load_config
        from navin.webui.system_loops import update_system_loop

        update_system_loop("dream", limits=CronLimits(daily_token_budget=40_000))
        self.assertEqual(load_config().agents.defaults.dream.daily_token_budget, 40_000)

    def test_a_failure_ceiling_is_written_to_the_config(self):
        from navin.config.loader import load_config
        from navin.webui.system_loops import update_system_loop

        for job_id, read in (
            ("dream", lambda config: config.agents.defaults.dream),
            ("heartbeat", lambda config: config.gateway.heartbeat),
        ):
            with self.subTest(job_id):
                update_system_loop(job_id, limits=CronLimits(max_consecutive_failures=4))
                self.assertEqual(read(load_config()).max_consecutive_failures, 4)

    def test_an_unknown_timezone_is_refused_before_the_config_is_touched(self):
        from navin.webui.system_loops import update_system_loop

        schedule = Recurrence(kind="daily", hour=3, tz="Europe/Pariss").to_schedule()
        error = update_system_loop("heartbeat", schedule=schedule)
        # Left unchecked this writes a zone nothing can resolve, and the loop
        # then computes no next run at all instead of reporting the typo.
        assert error is not None
        self.assertIn("Europe/Pariss", error)
        self.assertFalse((self.root / "config.json").exists())

    def test_turning_a_built_in_loop_off_is_written_to_the_config(self):
        from navin.config.loader import load_config
        from navin.webui.system_loops import update_system_loop

        update_system_loop("heartbeat", enabled=False)
        self.assertFalse(load_config().gateway.heartbeat.enabled)

    def test_a_preset_clears_the_legacy_expression_that_would_outrank_it(self):
        from navin.config.loader import load_config, save_config
        from navin.webui.system_loops import update_system_loop

        config = load_config()
        config.agents.defaults.dream.cron = "0 4 * * *"
        save_config(config)

        update_system_loop("dream", schedule=Recurrence(kind="daily", hour=6).to_schedule())
        dream = load_config().agents.defaults.dream
        self.assertIsNone(dream.cron)
        self.assertEqual(dream.build_schedule("UTC").expr, "0 6 * * *")

    def test_a_one_off_time_is_refused_for_a_repeating_loop(self):
        from navin.webui.system_loops import update_system_loop

        error = update_system_loop("dream", schedule=CronSchedule(kind="at", at_ms=1))
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("repeats", error)

    def test_an_unknown_system_loop_is_refused(self):
        from navin.webui.system_loops import update_system_loop

        self.assertIsNotNone(update_system_loop("some-other-loop", enabled=False))

    def test_rescheduling_keeps_the_run_history_and_the_spend(self):
        from navin.cron.types import CronPayload

        service = _service(self.root)
        job = CronJob(
            id="dream",
            name="dream",
            schedule=Recurrence(kind="every_hours", interval=2).to_schedule(),
            payload=CronPayload(kind="system_event"),
        )
        job.state.tokens_today = 900
        job.state.run_history.append(CronRunRecord(run_at_ms=1, status="ok"))
        _store_job(service, job)

        updated = service.reschedule_system_job(
            "dream",
            schedule=Recurrence(kind="daily", hour=4).to_schedule(),
            limits=CronLimits(daily_token_budget=1_000),
        )
        assert updated is not None
        self.assertEqual(updated.schedule.expr, "0 4 * * *")
        self.assertEqual(updated.limits.daily_token_budget, 1_000)
        self.assertEqual(updated.state.tokens_today, 900)
        self.assertEqual(len(updated.state.run_history), 1)
        self.assertIsNotNone(updated.state.next_run_at_ms)

    def test_rescheduling_refuses_a_job_that_is_not_a_system_loop(self):
        service = _service(self.root)
        _store_job(service, CronJob(id="job-1", name="Digest", payload=_bound_payload()))
        self.assertIsNone(service.reschedule_system_job("job-1", enabled=False))


class LocalDayTest(unittest.TestCase):
    """The daily budget resets on the user's calendar, not on UTC's."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_day_is_read_in_the_configured_zone(self):
        # 23:30 UTC is already the next day in Tokyo.
        moment_ms = 1_769_470_200_000  # 2026-01-26T23:30:00Z
        tokyo = _service(self.root, timezone_name="Asia/Tokyo")._local_day(moment_ms)
        utc = _service(self.root, timezone_name="UTC")._local_day(moment_ms)
        self.assertNotEqual(tokyo, utc)
        self.assertEqual(utc, "2026-01-26")
        self.assertEqual(tokyo, "2026-01-27")

    def test_an_unknown_zone_falls_back_instead_of_failing(self):
        day = _service(self.root, timezone_name="Mars/Olympus_Mons")._local_day(0)
        self.assertRegex(day, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
