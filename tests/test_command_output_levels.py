"""Compaction at every exec level + Kubernetes/helm coverage.

Levels exercised:
1. filter_command_output (pure)
2. ExecTool sync execute
3. ExecTool background / yield_time_ms session done
4. WriteStdinTool poll after session completes

Kubernetes uses a stub ``kubectl`` / ``helm`` on PATH so CI never needs a cluster.

Policy: never fail on missing tools / races - skip or accept equivalent
success shapes. Failures here mean a real compaction regression.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from navin.agent.command_output import classify_command, filter_command_output
from navin.agent.tools.exec_session import WriteStdinTool
from navin.agent.tools.shell import ExecTool

WINDOWS = sys.platform == "win32"

_KUBECTL_STUB = r"""#!/bin/sh
# Fake kubectl for Navin compaction tests (no cluster).
cmd="$1"
shift || true
case "$cmd" in
  get)
    cat <<'EOF'
NAME                                READY   STATUS             RESTARTS   AGE     IP            NODE
web-7d9f8b6c4d-abc12                1/1     Running            0          2d      10.0.0.11     node-a
api-5c6d7e8f9a-def34                0/1     CrashLoopBackOff   12         5h      10.0.0.12     node-b
db-0                                1/1     Running            0          10d     10.0.0.13     node-a
worker-6b7c8d9e0f-ghi56             1/1     Running            0          3d      10.0.0.14     node-c
EOF
    ;;
  describe)
    cat <<'EOF'
Name:             api-5c6d7e8f9a-def34
Namespace:        production
Node:             node-b/10.0.1.2
Status:           Running
Restart Count:    12
Image:            ghcr.io/acme/api:1.2.3
Controlled By:    ReplicaSet/api-5c6d7e8f9a
QoS Class:        Burstable
Events:
  Type     Reason     Age                   From               Message
  ----     ------     ----                  ----               -------
  Normal   Scheduled  5h                    default-scheduler  Successfully assigned
  Normal   Pulled     5h                    kubelet            Container image pulled
  Warning  BackOff    2m (x40 over 5h)      kubelet            Back-off restarting failed container
  Warning  Unhealthy  1m (x12 over 4h)      kubelet            Liveness probe failed: HTTP 500
EOF
    ;;
  logs)
    i=0
    while [ "$i" -lt 8 ]; do
      echo "same log line spam"
      i=$((i + 1))
    done
    echo "ERROR panic: nil pointer"
    ;;
  version)
    echo "Client Version: v1.29.0"
    echo "Kustomize Version: v5.0.0"
    ;;
  *)
    echo "kubectl stub: unsupported $cmd" >&2
    exit 2
    ;;
esac
"""

_HELM_STUB = r"""#!/bin/sh
cat <<'EOF'
NAME            NAMESPACE       REVISION        UPDATED                                 STATUS          CHART               APP VERSION
web             production      12              2026-08-01 10:00:00.000000000 +0000 UTC deployed        web-1.4.2           1.4.2
api             production      3               2026-08-07 12:00:00.000000000 +0000 UTC failed          api-0.9.1           0.9.1
EOF
"""


def _assert_compacted_dedupe(out: str, *, label: str) -> None:
    """Accept either compaction note or already-reduced SAME_LINE count."""
    if "identical lines omitted" in out:
        return
    if out.count("SAME_LINE") <= 2 and "Exit code: 0" in out:
        return
    raise AssertionError(f"{label}: expected dedupe compaction, got:\n{out[:800]}")


class KubernetesClassifyTests(unittest.TestCase):
    def test_kubectl_and_oc_and_helm_verbs(self) -> None:
        cases = [
            ("kubectl get pods -A", "kubectl"),
            ("kubectl describe pod api-1", "kubectl"),
            ("kubectl logs api-1 -c app", "kubectl"),
            ("kubectl apply -f deploy.yaml", "kubectl"),
            ("kubectl rollout status deploy/api", "kubectl"),
            ("kubectl top nodes", "kubectl"),
            ("kubectl exec -it api-1 -- sh", "kubectl"),
            ("oc get pods", "kubectl"),
            ("helm list -A", "kubectl"),
            ("helm status web", "kubectl"),
            ("helm upgrade api ./chart", "kubectl"),
        ]
        for cmd, kind in cases:
            with self.subTest(cmd=cmd):
                self.assertEqual(classify_command(cmd), kind)


class KubernetesFilterUnitTests(unittest.TestCase):
    def test_get_pods_table_compacts(self) -> None:
        raw = (
            "NAME         READY   STATUS             RESTARTS   AGE     IP            NODE\n"
            "web-abc      1/1     Running            0          2d      10.0.0.11     node-a\n"
            "api-def      0/1     CrashLoopBackOff   12         5h      10.0.0.12     node-b\n"
            "Exit code: 0\n"
        )
        result = filter_command_output("kubectl get pods -o wide", raw)
        self.assertEqual(result.kind, "kubectl")
        self.assertIn("CrashLoopBackOff", result.text)
        self.assertIn("web-abc", result.text)
        self.assertLess(result.filtered_chars, result.original_chars)

    def test_describe_keeps_warnings_drops_normal_noise(self) -> None:
        raw = """Name:             api-x
Namespace:        production
Status:           Running
Restart Count:    12
Events:
  Type     Reason     Age      From     Message
  ----     ------     ----     ----     -------
  Normal   Scheduled  5h       sched    assigned
  Normal   Pulled     5h       kubelet  pulled
  Warning  BackOff    2m       kubelet  Back-off restarting failed container
  Warning  Unhealthy  1m       kubelet  Liveness probe failed

Exit code: 0
"""
        result = filter_command_output("kubectl describe pod api-x", raw)
        self.assertEqual(result.kind, "kubectl")
        self.assertIn("BackOff", result.text)
        self.assertIn("Unhealthy", result.text)
        self.assertIn("Restart Count", result.text)
        self.assertNotIn("Successfully assigned", result.text)
        self.assertNotIn("Container image pulled", result.text)

    def test_logs_dedupe(self) -> None:
        lines = ["same log line spam"] * 8 + ["ERROR boom"]
        raw = "\n".join(lines) + "\nExit code: 0\n"
        result = filter_command_output("kubectl logs api-1", raw)
        self.assertEqual(result.kind, "kubectl")
        self.assertIn("identical lines omitted", result.text)
        self.assertIn("ERROR boom", result.text)


class _KubeStubMixin:
    def _install_stubs(self) -> Path:
        bindir = Path(self._tmp.name) / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        for name, body in (("kubectl", _KUBECTL_STUB), ("helm", _HELM_STUB), ("oc", _KUBECTL_STUB)):
            path = bindir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.environ["PATH"] = f"{bindir}{os.pathsep}{self._old_path}"
        return bindir


@unittest.skipIf(WINDOWS, "kubectl stubs are POSIX shell scripts")
class KubernetesLiveLevelsTests(unittest.TestCase, _KubeStubMixin):
    def setUp(self) -> None:
        if shutil.which("sh") is None:
            self.skipTest("sh not available")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self._old_path = os.environ.get("PATH", "")
        self.addCleanup(lambda: os.environ.__setitem__("PATH", self._old_path))
        self._install_stubs()
        # Prove stubs resolve before running exec.
        if shutil.which("kubectl") is None or shutil.which("helm") is None:
            self.skipTest("stub kubectl/helm not on PATH after install")
        self.tool = ExecTool(working_dir=str(self.root))
        self.stdin = WriteStdinTool(manager=self.tool._session_manager)

    def _run(self, command: str, **kwargs: object) -> str:
        return str(asyncio.run(self.tool.execute(command=command, **kwargs)))  # type: ignore[arg-type]

    def test_level_sync_kubectl_get(self) -> None:
        out = self._run("kubectl get pods -o wide")
        self.assertIn("Exit code: 0", out)
        self.assertIn("CrashLoopBackOff", out)
        self.assertIn("web-", out)
        self.assertNotIn("kubectl stub: unsupported", out)

    def test_level_sync_kubectl_describe(self) -> None:
        out = self._run("kubectl describe pod api-5c6d7e8f9a-def34")
        self.assertIn("Exit code: 0", out)
        self.assertIn("BackOff", out)
        self.assertIn("Unhealthy", out)
        self.assertNotIn("Successfully assigned", out)

    def test_level_sync_kubectl_logs_dedupe(self) -> None:
        out = self._run("kubectl logs api-1")
        self.assertIn("Exit code: 0", out)
        self.assertIn("identical lines omitted", out)
        self.assertIn("ERROR panic", out)

    def test_level_sync_helm_list(self) -> None:
        out = self._run("helm list -A")
        self.assertIn("Exit code: 0", out)
        self.assertIn("failed", out.lower())
        self.assertIn("web", out)

    def test_level_sync_oc_alias(self) -> None:
        out = self._run("oc get pods")
        self.assertIn("Exit code: 0", out)
        self.assertIn("Running", out)

    def test_level_background_session_done(self) -> None:
        out = self._run("kubectl get pods", yield_time_ms=8000)
        self.assertIn("Exit code: 0", out)
        self.assertIn("CrashLoopBackOff", out)

    def test_level_write_stdin_poll_after_done(self) -> None:
        async def scenario() -> str:
            started = str(
                await self.tool.execute(
                    command="kubectl logs api-1",
                    yield_time_ms=2000,
                    background=True,
                )
            )
            if "Exit code:" in started:
                return started
            session_id = None
            for line in started.splitlines():
                if "session_id:" in line:
                    session_id = line.split("session_id:", 1)[1].strip()
                    break
            if session_id is None:
                return started
            last = started
            for _ in range(40):
                last = str(
                    await self.stdin.execute(
                        session_id=session_id,
                        chars="",
                        yield_time_ms=200,
                    )
                )
                if "Exit code:" in last:
                    return last
                if "not found" in last.lower():
                    # Session reaped after done - accept the first snapshot.
                    return started
                await asyncio.sleep(0.05)
            return last

        out = asyncio.run(scenario())
        self.assertIn("ERROR panic", out)
        self.assertTrue(
            "identical lines omitted" in out or "Exit code:" in out,
            msg=out[:800],
        )

    def test_sh_c_unwrap_still_kubectl(self) -> None:
        out = self._run("sh -c 'kubectl get pods'")
        self.assertIn("CrashLoopBackOff", out)
        self.assertIn("Exit code: 0", out)


@unittest.skipIf(WINDOWS, "POSIX shell loops")
class AllLevelsSmokeTests(unittest.TestCase):
    """Non-k8s smoke: sync / background / write_stdin still compact."""

    def setUp(self) -> None:
        if shutil.which("sh") is None:
            self.skipTest("sh not available")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.tool = ExecTool(working_dir=str(self.root))
        self.stdin = WriteStdinTool(manager=self.tool._session_manager)

    def test_sync_and_background_dedupe(self) -> None:
        cmd = (
            "sh -c 'i=0; while [ $i -lt 8 ]; do echo SAME_LINE; "
            "i=$((i+1)); done; echo done'"
        )
        sync = str(asyncio.run(self.tool.execute(command=cmd)))
        bg = str(asyncio.run(self.tool.execute(command=cmd, yield_time_ms=5000)))
        for label, out in (("sync", sync), ("background", bg)):
            with self.subTest(level=label):
                self.assertIn("Exit code: 0", out)
                self.assertIn("done", out)
                _assert_compacted_dedupe(out, label=label)

    def test_write_stdin_compacts_when_process_exits(self) -> None:
        async def scenario() -> str:
            started = str(
                await self.tool.execute(
                    command=(
                        "sh -c 'i=0; while [ $i -lt 8 ]; do echo SAME_LINE; "
                        "i=$((i+1)); done; echo done'"
                    ),
                    background=True,
                    yield_time_ms=3000,
                )
            )
            if "Exit code:" in started:
                return started
            session_id = None
            for line in started.splitlines():
                if "session_id:" in line:
                    session_id = line.split("session_id:", 1)[1].strip()
                    break
            if session_id is None:
                return started
            last = started
            for _ in range(50):
                last = str(
                    await self.stdin.execute(
                        session_id=session_id,
                        chars="",
                        yield_time_ms=150,
                    )
                )
                if "Exit code:" in last:
                    return last
                if "not found" in last.lower():
                    return started
                await asyncio.sleep(0.05)
            return last

        out = asyncio.run(scenario())
        self.assertIn("done", out)
        _assert_compacted_dedupe(out, label="write_stdin")


if __name__ == "__main__":
    unittest.main()
