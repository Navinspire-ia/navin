# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What the exec tool refuses out of the box, and what an operator can add.

navin ships without an opinion on which commands an agent may run: the deny set
is opt-in, any shell the host has is fair game, and a timeout the model asks for
is the timeout it gets. Each test here has a pair - the open default, and the
same case once an operator has configured the restriction - because the point is
not that the limits are gone but that they moved into the operator's hands.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from navin.agent.tools.shell import BUILTIN_DENY_PATTERNS, ExecTool

WINDOWS = sys.platform == "win32"


class _ExecTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def run_exec(self, tool: ExecTool, command: str) -> str:
        return str(asyncio.run(tool.execute(command=command)))

    def tool(self, **kwargs: object) -> ExecTool:
        return ExecTool(working_dir=str(self.root), **kwargs)  # type: ignore[arg-type]


class BuiltinDenyRulesTest(_ExecTest):
    def test_a_recursive_delete_runs_by_default(self) -> None:
        (self.root / "build").mkdir()
        out = self.run_exec(self.tool(), "rm -rf build")
        self.assertNotIn("blocked", out)
        self.assertFalse((self.root / "build").exists())

    def test_turning_them_on_blocks_it_again(self) -> None:
        (self.root / "build").mkdir()
        out = self.run_exec(self.tool(builtin_deny_rules=True), "rm -rf build")
        self.assertIn("blocked by deny pattern filter", out)
        self.assertTrue((self.root / "build").exists())

    def test_the_patterns_are_absent_from_the_default_deny_list(self) -> None:
        self.assertEqual(self.tool().deny_patterns, [])
        self.assertEqual(
            self.tool(builtin_deny_rules=True).deny_patterns, BUILTIN_DENY_PATTERNS
        )

    def test_an_operators_own_pattern_still_applies_on_its_own(self) -> None:
        out = self.run_exec(self.tool(deny_patterns=[r"\bcurl\b"]), "curl http://x.test")
        self.assertIn("blocked by deny pattern filter", out)

    def test_hot_reload_keeps_the_operators_choice(self) -> None:
        tool = self.tool(builtin_deny_rules=True)
        tool.apply_policy(allow_patterns=[], deny_patterns=[r"\bcurl\b"])
        self.assertIn(r"\bcurl\b", tool.deny_patterns)
        for pattern in BUILTIN_DENY_PATTERNS:
            self.assertIn(pattern, tool.deny_patterns)

    def test_hot_reload_does_not_smuggle_them_back_in(self) -> None:
        tool = self.tool()
        tool.apply_policy(allow_patterns=[], deny_patterns=[r"\bcurl\b"])
        self.assertEqual(tool.deny_patterns, [r"\bcurl\b"])


class _DenyGuardTest(_ExecTest):
    """Helpers shared by the builtin deny-rule suites."""

    def guard(self, command: str) -> object:
        tool = self.tool(builtin_deny_rules=True)
        return tool._guard_command(command, cwd=str(self.root))

    def assert_blocked(self, command: str, label: str) -> None:
        verdict = self.guard(command)
        self.assertIsNotNone(verdict, f"expected a block for: {command}")
        self.assertEqual(verdict.approvable_rule, label)

    def assert_allowed(self, command: str) -> None:
        self.assertIsNone(self.guard(command), f"expected no block for: {command}")


class GitDestructiveDenyRulesTest(_DenyGuardTest):
    """History-destroying git commands are caught by the builtin deny set.

    The git *tool* asks before force pushes and hard resets; these rules close
    the raw-shell path. They are approvable, so a hit pauses for the user
    instead of silently destroying work.
    """

    def test_hard_reset_is_blocked_but_approvable(self) -> None:
        self.assert_blocked("git reset --hard HEAD~3", "gitHardReset")

    def test_soft_and_mixed_reset_pass(self) -> None:
        self.assert_allowed("git reset --soft HEAD~1")
        self.assert_allowed("git reset HEAD -- file.py")

    def test_clean_deleting_files_is_blocked(self) -> None:
        self.assert_blocked("git clean -fd", "gitClean")
        self.assert_blocked("git clean -xdf", "gitClean")

    def test_clean_dry_run_passes(self) -> None:
        self.assert_allowed("git clean -n")

    def test_force_push_is_blocked(self) -> None:
        self.assert_blocked("git push --force origin main", "gitForcePush")
        self.assert_blocked("git push -f", "gitForcePush")
        self.assert_blocked("git push --force-with-lease", "gitForcePush")

    def test_normal_push_passes(self) -> None:
        self.assert_allowed("git push -u origin navin/task-t-1-fix")
        self.assert_allowed("git push origin main")

    def test_branch_delete_is_blocked(self) -> None:
        self.assert_blocked("git branch -D navin/task-t-1-fix", "gitBranchDelete")
        self.assert_blocked("git branch -d merged-branch", "gitBranchDelete")

    def test_branch_create_and_list_pass(self) -> None:
        self.assert_allowed("git branch new-branch")
        self.assert_allowed("git branch --list")

    def test_forced_checkout_is_blocked(self) -> None:
        self.assert_blocked("git checkout --force main", "gitForceCheckout")
        self.assert_blocked("git checkout -f main", "gitForceCheckout")

    def test_safe_checkout_and_switch_pass(self) -> None:
        self.assert_allowed("git checkout -b navin/task-t-1-fix")
        self.assert_allowed("git switch -c navin/task-t-1-fix")

    def test_off_by_default_like_the_rest_of_the_set(self) -> None:
        tool = self.tool()
        self.assertIsNone(
            tool._guard_command("git reset --hard HEAD~3", cwd=str(self.root))
        )


class AlternateDeleteDenyRulesTest(_DenyGuardTest):
    """Builtin deny must catch deletes that are not spelled `rm -rf`."""

    def test_gnu_long_options_are_blocked(self) -> None:
        self.assert_blocked("rm --recursive --force build", "recursiveDelete")
        self.assert_blocked("rm --force src/tmp", "recursiveDelete")

    def test_find_delete_is_blocked(self) -> None:
        self.assert_blocked("find . -name '*.pyc' -delete", "recursiveDelete")

    def test_python_rmtree_is_blocked(self) -> None:
        self.assert_blocked(
            "python -c 'import shutil; shutil.rmtree(\"dist\")'",
            "recursiveDelete",
        )

    def test_ordinary_rm_and_find_still_pass(self) -> None:
        self.assert_allowed("rm README.md")
        self.assert_allowed("find . -name '*.pyc'")
        self.assert_allowed("python -c 'print(1)'")


class InfraDestructiveDenyRulesTest(_DenyGuardTest):
    """Heavy-infra operations pause for the user instead of running silently.

    A long autonomous mission drifting into `terraform destroy` or a cluster
    delete is exactly what supervision is for: each rule is approvable, and
    the read-only counterparts of every command pass untouched.
    """

    def test_kubectl_delete_is_blocked(self) -> None:
        self.assert_blocked("kubectl delete pod api-7f9", "kubectlDelete")
        self.assert_blocked("kubectl -n prod delete deployment web", "kubectlDelete")

    def test_kubectl_reads_pass(self) -> None:
        self.assert_allowed("kubectl get pods -A")
        self.assert_allowed("kubectl describe deployment web")

    def test_terraform_destroy_is_blocked(self) -> None:
        self.assert_blocked("terraform destroy", "terraformDestroy")
        self.assert_blocked("tofu destroy -auto-approve", "terraformDestroy")

    def test_terraform_plan_and_apply_pass(self) -> None:
        """Only deletions ask. Applying a plan is the deploy job itself."""
        self.assert_allowed("terraform plan")
        self.assert_allowed("terraform validate")
        self.assert_allowed("terraform apply -auto-approve")
        self.assert_allowed("tofu apply")

    def test_helm_uninstall_is_blocked(self) -> None:
        self.assert_blocked("helm uninstall my-release", "helmUninstall")
        self.assert_blocked("helm delete my-release", "helmUninstall")

    def test_helm_install_rollback_and_list_pass(self) -> None:
        self.assert_allowed("helm install my-release ./chart")
        self.assert_allowed("helm rollback my-release 2")
        self.assert_allowed("helm list")

    def test_docker_prune_is_blocked(self) -> None:
        self.assert_blocked("docker system prune -af", "dockerPrune")
        self.assert_blocked("docker volume rm pgdata", "dockerPrune")
        self.assert_blocked("docker image prune", "dockerPrune")

    def test_docker_build_and_run_pass(self) -> None:
        self.assert_allowed("docker build -t app .")
        self.assert_allowed("docker compose up -d")

    def test_cloud_deletes_are_blocked(self) -> None:
        self.assert_blocked("aws s3 rm s3://bucket --recursive", "cloudDelete")
        self.assert_blocked("aws s3 rb s3://bucket", "cloudDelete")
        self.assert_blocked("aws ec2 terminate-instances --instance-ids i-1", "cloudDelete")
        self.assert_blocked("aws cloudformation delete-stack --stack-name s", "cloudDelete")
        self.assert_blocked("gcloud compute instances delete vm-1", "cloudDelete")
        self.assert_blocked("az group delete --name rg", "cloudDelete")

    def test_cloud_reads_and_uploads_pass(self) -> None:
        self.assert_allowed("aws s3 ls s3://bucket")
        self.assert_allowed("aws s3 cp dist/ s3://bucket --recursive")
        self.assert_allowed("gcloud compute instances list")

    def test_sql_drop_through_a_client_is_blocked(self) -> None:
        self.assert_blocked('psql -c "DROP TABLE users"', "sqlDrop")
        self.assert_blocked('mysql -e "truncate orders"', "sqlDrop")
        self.assert_blocked('sqlite3 app.db "drop table logs"', "sqlDrop")

    def test_sql_reads_pass(self) -> None:
        self.assert_allowed('psql -c "SELECT count(*) FROM users"')
        self.assert_allowed("grep -r 'DROP TABLE' migrations/")


class ShellAllowlistTest(_ExecTest):
    def test_any_shell_name_is_accepted_by_default(self) -> None:
        """The name still has to exist on the host; it is simply not vetted."""
        resolved, error = self.tool()._resolve_shell("definitely-not-a-shell")
        self.assertIsNone(resolved)
        self.assertIn("not found", str(error))

    @unittest.skipIf(WINDOWS, "POSIX shell names")
    def test_a_shell_outside_the_old_hardcoded_three_resolves(self) -> None:
        import shutil

        for name in ("dash", "ksh", "fish"):
            if shutil.which(name):
                resolved, error = self.tool()._resolve_shell(name)
                self.assertIsNone(error)
                self.assertTrue(str(resolved).endswith(name))
                return
        self.skipTest("no alternative shell installed")

    def test_an_operator_list_refuses_what_it_omits(self) -> None:
        resolved, error = self.tool(allowed_shells=["bash"])._resolve_shell("zsh")
        self.assertIsNone(resolved)
        self.assertIn("allowedShells", str(error))

    def test_an_operator_list_admits_what_it_names(self) -> None:
        import shutil

        if not shutil.which("bash"):
            self.skipTest("bash not installed")
        resolved, error = self.tool(allowed_shells=["bash"])._resolve_shell("bash")
        self.assertIsNone(error)
        self.assertTrue(str(resolved).endswith("bash"))

    def test_the_exe_suffix_does_not_slip_past_the_list(self) -> None:
        tool = self.tool(allowed_shells=["pwsh"])
        self.assertTrue(tool._shell_allowed("pwsh.exe"))
        self.assertTrue(tool._shell_allowed("pwsh"))
        self.assertFalse(tool._shell_allowed("cmd.exe"))

    def test_an_injected_newline_is_still_refused(self) -> None:
        resolved, error = self.tool()._resolve_shell("bash\nrm -rf /")
        self.assertIsNone(resolved)
        self.assertIn("invalid characters", str(error))


class TimeoutCeilingTest(_ExecTest):
    def test_a_long_build_keeps_the_timeout_it_asked_for(self) -> None:
        self.assertEqual(self.tool()._resolve_timeout(7200), 7200)

    def test_an_operator_ceiling_clamps_it(self) -> None:
        self.assertEqual(self.tool(max_timeout=600)._resolve_timeout(7200), 600)

    def test_a_request_under_the_ceiling_is_untouched(self) -> None:
        self.assertEqual(self.tool(max_timeout=600)._resolve_timeout(30), 30)

    def test_no_request_falls_back_to_the_configured_default(self) -> None:
        self.assertEqual(self.tool(timeout=45)._resolve_timeout(None), 45)

    def test_a_zero_default_means_no_limit(self) -> None:
        self.assertIsNone(self.tool(timeout=0)._resolve_timeout(None))


class RestrictHostPathTest(_ExecTest):
    """restrict_to_workspace must not refuse host tools or in-project ``../``."""

    def _verdict(self, command: str, *, cwd: Path | None = None) -> object:
        tool = self.tool(restrict_to_workspace=True)
        return tool._guard_command(
            command,
            cwd=str(cwd or self.root),
            restrict_to_workspace=True,
            workspace_root=str(self.root),
        )

    @unittest.skipIf(WINDOWS, "POSIX system interpreter")
    def test_usr_bin_env_is_not_blocked(self) -> None:
        self.assertIsNone(self._verdict("/usr/bin/env true"))

    @unittest.skipIf(not WINDOWS, "Windows system interpreter")
    def test_windows_system32_is_not_blocked(self) -> None:
        self.assertIsNone(self._verdict(r"C:\Windows\System32\cmd.exe /c echo ok"))

    def test_in_project_parent_relative_is_not_blocked(self) -> None:
        sub = self.root / "webui"
        sub.mkdir()
        (self.root / "README.md").write_text("hi\n", encoding="utf-8")
        self.assertIsNone(self._verdict("cat ../README.md", cwd=sub))

    def test_escape_to_a_sibling_of_the_workspace_is_still_blocked(self) -> None:
        verdict = self._verdict("cat ../secrets")
        self.assertIsNotNone(verdict)
        self.assertIn("path traversal", str(verdict))

    @unittest.skipIf(WINDOWS, "POSIX foreign project")
    def test_absolute_path_in_another_project_is_still_blocked(self) -> None:
        verdict = self._verdict("cat /home/navin-foreign-workspace/secret.txt")
        self.assertIsNotNone(verdict)
        self.assertIn("path outside working dir", str(verdict))


if __name__ == "__main__":
    unittest.main()
