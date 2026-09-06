"""Periodic license sync must re-clamp the live AgentLoop on renew / expire."""

from __future__ import annotations

import unittest
from unittest import mock

from navin.config.schema import Config
from navin.license_client import LicenseError
from navin.license_sync import LicenseSyncLoop
from navin.plan_limits import FREE_CONCURRENT_AGENTS, FREE_STEPS_PER_TASK


class LicenseSyncOnceTest(unittest.TestCase):
    def test_sync_applies_fresh_limits_to_loop(self):
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "plan": "plus",
                    "stepsPerTask": 30,
                    "concurrentAgents": 1,
                }
            }
        )
        fresh = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "plan": "ultra",
                    "stepsPerTask": 200,
                    "concurrentAgents": 8,
                }
            }
        )

        loop = mock.Mock()
        loop.max_iterations = 30
        loop.subagents = mock.Mock()
        loop.subagents.max_iterations = 30
        loop.subagents.max_concurrent_subagents = 1
        loop._concurrency_gate = None

        loads = iter([config, fresh])
        applied: list[tuple[object, object]] = []

        def validate_fn(_cfg):
            return {"valid": True, "plan": "ultra"}

        sync = LicenseSyncLoop(
            get_loop=lambda: loop,
            validate_fn=validate_fn,
            load_config_fn=lambda: next(loads),
            apply_fn=lambda agent, cfg: applied.append((agent, cfg)),
        )
        self.assertTrue(sync.sync_once())
        self.assertEqual(len(applied), 1)
        self.assertIs(applied[0][0], loop)
        self.assertEqual(applied[0][1].license.plan, "ultra")
        self.assertIsNone(sync.last_error)

    def test_expired_clamps_to_free_safe(self):
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "plan": "ultra",
                    "stepsPerTask": 200,
                    "concurrentAgents": 8,
                }
            }
        )

        loop = mock.Mock()
        loop.max_iterations = 200
        loop.subagents = mock.Mock()
        loop.subagents.max_iterations = 200
        loop.subagents.max_concurrent_subagents = 8
        loop._concurrency_gate = None

        def validate_fn(_cfg):
            raise LicenseError("subscription_expired")

        with mock.patch("navin.config.loader.save_config") as save:
            sync = LicenseSyncLoop(
                get_loop=lambda: loop,
                validate_fn=validate_fn,
                load_config_fn=lambda: config,
            )
            self.assertFalse(sync.sync_once())

        self.assertEqual(config.license.concurrent_agents, FREE_CONCURRENT_AGENTS)
        self.assertEqual(config.license.steps_per_task, FREE_STEPS_PER_TASK)
        self.assertEqual(loop.max_iterations, FREE_STEPS_PER_TASK)
        # A ceiling, not a target: the resource governor lowers this further on
        # a machine that cannot hold the full Free entitlement, so asserting the
        # exact number would only be testing the host the suite runs on.
        self.assertLessEqual(
            loop.subagents.max_concurrent_subagents, FREE_CONCURRENT_AGENTS
        )
        self.assertGreaterEqual(loop.subagents.max_concurrent_subagents, 1)
        save.assert_called()
        self.assertIsNotNone(sync.last_error)

    def test_inactive_license_skips(self):
        config = Config()
        sync = LicenseSyncLoop(
            get_loop=lambda: None,
            load_config_fn=lambda: config,
            validate_fn=lambda _c: (_ for _ in ()).throw(AssertionError("no validate")),
        )
        self.assertFalse(sync.sync_once())


class SubscriptionExpiredCodeTest(unittest.TestCase):
    def test_validate_maps_expired_status(self):
        import httpx

        from navin.license_client import validate

        config = Config.model_validate(
            {"license": {"activationToken": "tok", "device": "fp", "plan": "pro"}}
        )
        payload = {"valid": False, "status": "expired", "plan": "pro"}
        response = httpx.Response(
            200,
            json=payload,
            request=httpx.Request("POST", "https://navin.live/api/x"),
        )
        with mock.patch.object(
            __import__("navin.license_client", fromlist=["httpx"]).httpx,
            "post",
            return_value=response,
        ):
            with self.assertRaises(LicenseError) as caught:
                validate(config)
        self.assertEqual(caught.exception.code, "subscription_expired")


class StripeStatusMirrorTest(unittest.TestCase):
    """Mirror site/src/lib/stripe-subscription-sync.ts status mapping."""

    def _navin_status(self, stripe_status: str, *, force_expired: bool = False) -> str:
        if force_expired:
            return "expired"
        if stripe_status in ("active", "trialing"):
            return "active"
        return "expired"

    def test_active_and_trialing(self):
        self.assertEqual(self._navin_status("active"), "active")
        self.assertEqual(self._navin_status("trialing"), "active")

    def test_past_due_and_unpaid(self):
        self.assertEqual(self._navin_status("past_due"), "expired")
        self.assertEqual(self._navin_status("unpaid"), "expired")
        self.assertEqual(self._navin_status("canceled"), "expired")

    def test_invoice_payment_failed_force(self):
        self.assertEqual(self._navin_status("active", force_expired=True), "expired")


class ApplyLiveAccountRuntimeTest(unittest.TestCase):
    def test_adopts_the_managed_snapshot_when_the_loop_is_still_on_byok(self):
        from navin import license_sync

        previous = mock.Mock(model="z-ai/glm-5.3-flash", snapshot_signature=("z-ai",))
        adopted = mock.Mock(model="z-ai/glm-5.3-flash", snapshot_signature=("navin",))
        snapshot = mock.Mock(model="z-ai/glm-5.3-flash", signature=("navin",))
        resolver = mock.Mock()
        resolver.runtime = previous
        resolver.current.return_value = previous
        resolver.adopt_snapshot.return_value = adopted
        loop = mock.Mock()
        loop.runtime_resolver = resolver
        published: list[object] = []
        loop._publish_runtime_selection = lambda runtime, reason=None: published.append(
            (runtime, reason)
        )
        sync = mock.Mock()
        sync._get_loop.return_value = loop
        config = Config()
        with (
            mock.patch.object(license_sync, "_active_sync", sync),
            mock.patch("navin.config.loader.load_config", return_value=config),
            mock.patch("navin.license_client.uses_managed_key", return_value=True),
            mock.patch(
                "navin.providers.factory.load_provider_snapshot_allowing_unconfigured",
                return_value=snapshot,
            ),
            mock.patch("navin.plan_limits.apply_to_agent_loop") as apply,
        ):
            license_sync.apply_live_account_runtime()
        resolver.adopt_snapshot.assert_called_once()
        self.assertEqual(published, [(adopted, "account")])
        apply.assert_called_once()

    def test_is_a_no_op_without_a_live_loop(self):
        from navin import license_sync

        with mock.patch.object(license_sync, "_active_sync", None):
            license_sync.apply_live_account_runtime()


if __name__ == "__main__":
    unittest.main()
