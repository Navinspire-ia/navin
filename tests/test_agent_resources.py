"""The concurrency limit has to follow the machine, not a constant.

The same number used to apply to a laptop, to a 64-core server and to a
container capped at one core and one gigabyte. That is either a waste of a big
host or a way to swap a small one to death, so these cover what the governor
measures, what it does when it cannot measure anything, and the fact that it can
only ever lower a limit.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.agent.resources import (
    MIN_AGENTS,
    _cgroup_cpu_quota,
    _cgroup_memory_limit,
    describe_capacity,
    governed_agent_count,
    usable_cores,
    usable_memory_bytes,
)

GB = 1024 ** 3


class CgroupParsingTest(unittest.TestCase):
    """os.cpu_count() reports the host from inside a container, so the cgroup
    files are the only place the real allowance is written down."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_cgroup_v2_quota(self) -> None:
        self._write("cpu.max", "150000 100000\n")
        self.assertAlmostEqual(_cgroup_cpu_quota(self.root), 1.5)

    def test_cgroup_v2_unlimited_reads_as_no_limit(self) -> None:
        self._write("cpu.max", "max 100000\n")
        self.assertIsNone(_cgroup_cpu_quota(self.root))

    def test_cgroup_v1_quota_across_two_files(self) -> None:
        self._write("cpu/cpu.cfs_quota_us", "200000\n")
        self._write("cpu/cpu.cfs_period_us", "100000\n")
        self.assertAlmostEqual(_cgroup_cpu_quota(self.root), 2.0)

    def test_cgroup_v1_writes_minus_one_when_unrestricted(self) -> None:
        self._write("cpu/cpu.cfs_quota_us", "-1\n")
        self._write("cpu/cpu.cfs_period_us", "100000\n")
        self.assertIsNone(_cgroup_cpu_quota(self.root))

    def test_missing_files_are_not_an_error(self) -> None:
        self.assertIsNone(_cgroup_cpu_quota(self.root))
        self.assertIsNone(_cgroup_memory_limit(self.root))

    def test_garbage_is_not_an_error(self) -> None:
        self._write("cpu.max", "not a number\n")
        self._write("memory.max", "???\n")
        self.assertIsNone(_cgroup_cpu_quota(self.root))
        self.assertIsNone(_cgroup_memory_limit(self.root))

    def test_memory_v2_limit(self) -> None:
        self._write("memory.max", f"{2 * GB}\n")
        self.assertEqual(_cgroup_memory_limit(self.root), 2 * GB)

    def test_memory_v1_limit(self) -> None:
        self._write("memory/memory.limit_in_bytes", f"{4 * GB}\n")
        self.assertEqual(_cgroup_memory_limit(self.root), 4 * GB)

    def test_the_v1_sentinel_is_not_a_limit(self) -> None:
        """v1 writes a huge number rather than omitting an absent limit; taken
        literally it reads as several exabytes of RAM."""
        self._write("memory/memory.limit_in_bytes", "9223372036854771712\n")
        self.assertIsNone(_cgroup_memory_limit(self.root))


class MeasurementTest(unittest.TestCase):
    def test_usable_cores_is_always_at_least_one(self) -> None:
        """A 0.25-core container may run slowly, never nothing."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cpu.max").write_text("25000 100000\n", encoding="utf-8")
            self.assertEqual(usable_cores(cgroup_root=root), 1)

    def test_the_cgroup_caps_what_the_os_reports(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cpu.max").write_text("200000 100000\n", encoding="utf-8")
            self.assertLessEqual(usable_cores(cgroup_root=root), 2)

    def test_measuring_this_machine_never_raises(self) -> None:
        self.assertGreaterEqual(usable_cores(), 1)
        memory = usable_memory_bytes()
        self.assertTrue(memory is None or memory > 0)
        self.assertIsInstance(describe_capacity(), str)


class EveryPlatformIsMeasuredTest(unittest.TestCase):
    """Sizing only Linux would leave Windows and macOS governed by core count
    alone, which says nothing about the term that actually runs out."""

    def test_macos_falls_back_to_discounted_total(self) -> None:
        from navin.agent import resources

        with mock.patch.object(resources.sys, "platform", "darwin"), \
             mock.patch.object(resources, "_meminfo_available_bytes", return_value=None), \
             mock.patch.object(resources, "_sysconf_total_bytes", return_value=16 * GB):
            measured = resources._platform_available_bytes()
        # Total, not free: it must be discounted, or a Mac with other apps open
        # would be sized as if it were idle.
        self.assertIsNotNone(measured)
        self.assertLess(measured, 16 * GB)
        self.assertGreater(measured, 0)

    def test_windows_never_reads_proc(self) -> None:
        """There is no /proc there; asking would silently measure nothing."""
        from navin.agent import resources

        with mock.patch.object(resources.sys, "platform", "win32"), \
             mock.patch.object(resources, "_windows_available_bytes", return_value=8 * GB), \
             mock.patch.object(resources, "_meminfo_available_bytes") as meminfo:
            self.assertEqual(resources._platform_available_bytes(), 8 * GB)
        meminfo.assert_not_called()

    def test_linux_is_preferred_where_it_answers(self) -> None:
        from navin.agent import resources

        with mock.patch.object(resources.sys, "platform", "linux"), \
             mock.patch.object(resources, "_meminfo_available_bytes", return_value=3 * GB):
            self.assertEqual(resources._platform_available_bytes(), 3 * GB)

    def test_an_unmeasurable_platform_says_so_instead_of_guessing(self) -> None:
        from navin.agent import resources

        with mock.patch.object(resources.sys, "platform", "freebsd"), \
             mock.patch.object(resources, "_meminfo_available_bytes", return_value=None):
            self.assertIsNone(resources._platform_available_bytes())

    def test_the_windows_probe_is_inert_off_windows(self) -> None:
        """It must be safe to call anywhere: ctypes.windll does not exist here."""
        from navin.agent.resources import _windows_available_bytes

        self.assertIsNone(_windows_available_bytes())

    def test_a_probe_that_explodes_degrades_instead_of_raising(self) -> None:
        """Sizing is an optimisation; it may never break a spawn."""
        from navin.agent import resources

        with mock.patch.object(
            resources, "_platform_available_bytes", side_effect=OSError("boom")
        ):
            self.assertIsNone(resources.usable_memory_bytes())
        with mock.patch.object(resources, "os") as fake_os:
            fake_os.cpu_count.side_effect = OSError("boom")
            self.assertEqual(resources.usable_cores(), resources._FALLBACK_CORES)


class GovernedCountTest(unittest.TestCase):
    def test_a_small_container_gets_a_small_limit(self) -> None:
        allowed = governed_agent_count(cores=1, available_bytes=GB, ceiling=200)
        self.assertLess(allowed, 200)
        self.assertGreaterEqual(allowed, MIN_AGENTS)

    def test_a_large_host_keeps_the_full_ceiling(self) -> None:
        self.assertEqual(
            governed_agent_count(cores=64, available_bytes=256 * GB, ceiling=200),
            200,
        )

    def test_memory_is_the_term_that_bites(self) -> None:
        """Agents are network waits, so cores are cheap; each one's context is
        not, and being wrong low on memory means swapping."""
        allowed = governed_agent_count(
            cores=64,
            available_bytes=2 * GB,
            reserve_ratio=0.70,
            memory_per_agent_mb=96,
            ceiling=200,
        )
        self.assertEqual(allowed, int((2 * GB * 0.70) // (96 * 1024 * 1024)))

    def test_an_unmeasurable_machine_keeps_the_old_behaviour(self) -> None:
        self.assertEqual(
            governed_agent_count(cores=8, available_bytes=None, ceiling=50),
            50,
        )

    def test_it_can_only_lower_a_limit_never_raise_one(self) -> None:
        self.assertEqual(
            governed_agent_count(cores=64, available_bytes=256 * GB, ceiling=4),
            4,
        )

    def test_a_loaded_machine_still_gets_one_agent(self) -> None:
        """Zero would deadlock a turn that is waiting on a subagent."""
        self.assertEqual(
            governed_agent_count(cores=1, available_bytes=1024, ceiling=200),
            MIN_AGENTS,
        )

    def test_a_bigger_reserve_allows_more_agents(self) -> None:
        modest = governed_agent_count(
            cores=32, available_bytes=8 * GB, reserve_ratio=0.20, ceiling=200
        )
        generous = governed_agent_count(
            cores=32, available_bytes=8 * GB, reserve_ratio=0.70, ceiling=200
        )
        self.assertLess(modest, generous)


class ComposedWithPlanLimitsTest(unittest.TestCase):
    """The governor sits on top of the plan, and must not replace it."""

    def setUp(self) -> None:
        from navin.plan_limits import reset_capacity_cache

        reset_capacity_cache()
        self.addCleanup(reset_capacity_cache)

    def test_the_plan_limit_still_wins_when_it_is_lower(self) -> None:
        from navin.config.schema import Config
        from navin.plan_limits import effective_concurrent_agents

        config = Config()
        config.license.concurrent_agents = 3
        self.assertEqual(effective_concurrent_agents(config), 3)

    def test_disabling_the_governor_restores_the_plain_limit(self) -> None:
        from navin.config.schema import Config
        from navin.plan_limits import effective_concurrent_agents

        config = Config()
        config.resources.enabled = False
        self.assertEqual(
            effective_concurrent_agents(config),
            config.agents.defaults.max_concurrent_subagents,
        )

    def test_the_governed_limit_never_exceeds_the_configured_one(self) -> None:
        from navin.config.schema import Config
        from navin.plan_limits import effective_concurrent_agents

        config = Config()
        ceiling = config.agents.defaults.max_concurrent_subagents
        allowed = effective_concurrent_agents(config)
        self.assertGreaterEqual(allowed, MIN_AGENTS)
        self.assertLessEqual(allowed, ceiling)

    def test_a_broken_resources_block_falls_back_instead_of_raising(self) -> None:
        """Sizing is an optimisation; it must never be able to break a spawn."""
        from navin.plan_limits import govern_concurrent_agents

        class _Bad:
            enabled = True

            @property
            def max_utilisation(self):  # noqa: ANN202 - deliberately explosive
                raise RuntimeError("boom")

        config = type("C", (), {"resources": _Bad()})()
        self.assertEqual(govern_concurrent_agents(config, 42), 42)


if __name__ == "__main__":
    unittest.main()
