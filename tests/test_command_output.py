"""Native command-output compaction (RTK-style, Python)."""

from __future__ import annotations

import unittest

from navin.agent.command_output import (
    classify_command,
    filter_command_output,
    strip_ansi,
)


class ClassifyCommandTests(unittest.TestCase):
    def test_git_and_tests(self) -> None:
        self.assertEqual(classify_command("git status"), "git_status")
        self.assertEqual(classify_command("git diff --stat"), "git_diff")
        self.assertEqual(classify_command("git log -n 5"), "git_log")
        self.assertEqual(classify_command("git push origin main"), "git_mutate")
        self.assertEqual(classify_command("pytest -q"), "pytest")
        self.assertEqual(classify_command("python -m pytest tests/"), "pytest")
        self.assertEqual(classify_command("npm test"), "jest")
        self.assertEqual(classify_command("cargo test"), "cargo_test")
        self.assertEqual(classify_command("go test ./..."), "go_test")
        self.assertEqual(classify_command("echo hi"), "generic")

    def test_docker_npm_python(self) -> None:
        self.assertEqual(classify_command("docker ps"), "docker_ps")
        self.assertEqual(classify_command("docker logs app"), "docker_logs")
        self.assertEqual(classify_command("docker compose ps"), "docker_ps")
        self.assertEqual(classify_command("docker compose build"), "docker")
        self.assertEqual(classify_command("npm install"), "npm_install")
        self.assertEqual(classify_command("pnpm i"), "npm_install")
        self.assertEqual(classify_command("ruff check ."), "python_lint")
        self.assertEqual(classify_command("python -m ruff check src"), "python_lint")
        self.assertEqual(classify_command("mypy navin"), "python_lint")
        self.assertEqual(classify_command("pip install -r requirements.txt"), "pip")
        self.assertEqual(classify_command("python -m pip list"), "pip")
        self.assertEqual(classify_command("uv sync"), "pip")
        self.assertEqual(classify_command("python -m unittest discover"), "generic_test")

    def test_build_lint_k8s_and_wrappers(self) -> None:
        self.assertEqual(classify_command("cargo build"), "cargo_build")
        self.assertEqual(classify_command("cargo clippy"), "cargo_build")
        self.assertEqual(classify_command("npx eslint ."), "js_lint")
        self.assertEqual(classify_command("npm run lint"), "js_lint")
        self.assertEqual(classify_command("tsc --noEmit"), "tsc")
        self.assertEqual(classify_command("next build"), "next_build")
        self.assertEqual(classify_command("npm run build"), "next_build")
        self.assertEqual(classify_command("npx playwright test"), "playwright")
        self.assertEqual(classify_command("kubectl get pods"), "kubectl")
        self.assertEqual(classify_command("terraform plan"), "terraform")
        self.assertEqual(classify_command("make build"), "make_build")
        self.assertEqual(classify_command("curl -s https://example.com"), "curl")
        self.assertEqual(classify_command("gh pr list"), "gh")
        self.assertEqual(classify_command("sh -c 'git status'"), "git_status")
        self.assertEqual(classify_command("bash -lc 'pytest -q'"), "pytest")

    def test_env_prefix(self) -> None:
        self.assertEqual(classify_command("FOO=1 BAR=2 pytest -q"), "pytest")


class FilterTests(unittest.TestCase):
    def test_strip_ansi(self) -> None:
        raw = "\x1b[31mFAIL\x1b[0m ok"
        self.assertEqual(strip_ansi(raw), "FAIL ok")

    def test_git_status_compacts(self) -> None:
        raw = """On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
	modified:   a.py
	modified:   b.py

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	c.py

no changes added to commit

Exit code: 0
"""
        result = filter_command_output("git status", raw)
        self.assertEqual(result.kind, "git_status")
        self.assertIn("On branch main", result.text)
        self.assertIn("unstaged", result.text)
        self.assertIn("a.py", result.text)
        self.assertIn("untracked", result.text)
        self.assertIn("Exit code: 0", result.text)
        self.assertNotIn("use \"git add", result.text)
        self.assertLess(result.filtered_chars, result.original_chars)

    def test_git_push_progress_stripped(self) -> None:
        raw = """Enumerating objects: 5, done.
Counting objects: 100% (5/5), done.
Delta compression using up to 8 threads
Compressing objects: 100% (3/3), done.
Writing objects: 100% (3/3), 400 bytes | 400.00 KiB/s, done.
To github.com:acme/app.git
   abc1234..def5678  main -> main

Exit code: 0
"""
        result = filter_command_output("git push", raw)
        self.assertEqual(result.kind, "git_mutate")
        self.assertNotIn("Enumerating", result.text)
        self.assertNotIn("Counting objects", result.text)
        self.assertIn("Exit code: 0", result.text)
        self.assertLess(result.filtered_chars, result.original_chars)

    def test_pytest_keeps_failures(self) -> None:
        raw = """============================= test session starts ==============================
collected 3 items

test_a.py .                                                              [ 33%]
test_b.py F                                                              [ 66%]
test_c.py .                                                              [100%]

=================================== FAILURES ===================================
________________________________ test_boom ____________________________________

    def test_boom():
>       assert 1 == 2
E       assert 1 == 2

test_b.py:2: AssertionError
=========================== short test summary info ============================
FAILED test_b.py::test_boom - assert 1 == 2
========================= 1 failed, 2 passed in 0.05s ==========================

Exit code: 1
"""
        result = filter_command_output("pytest -q", raw)
        self.assertEqual(result.kind, "pytest")
        self.assertIn("FAILURES", result.text)
        self.assertIn("test_boom", result.text)
        self.assertIn("Exit code: 1", result.text)
        self.assertLess(result.filtered_chars, result.original_chars)

    def test_cargo_test_all_ok(self) -> None:
        raw = """running 2 tests
test a ... ok
test b ... ok

test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured

Exit code: 0
"""
        result = filter_command_output("cargo test", raw)
        self.assertEqual(result.kind, "cargo_test")
        self.assertIn("test result: ok", result.text)
        self.assertNotIn("test a ... ok", result.text)

    def test_never_expands(self) -> None:
        raw = "hi\nExit code: 0\n"
        result = filter_command_output("echo hi", raw)
        self.assertLessEqual(result.filtered_chars, result.original_chars)

    def test_dedupe_generic_runs(self) -> None:
        lines = ["same line"] * 6 + ["done"]
        raw = "\n".join(lines) + "\nExit code: 0\n"
        result = filter_command_output("make noise", raw)
        self.assertIn("identical lines omitted", result.text)
        self.assertLess(result.text.count("same line"), 6)

    def test_npm_install_keeps_summary(self) -> None:
        raw = """npm warn deprecated foo@1.0.0: use bar
npm warn deprecated baz@2.0.0: gone
reify:foo: timing
http fetch GET 200 https://registry.npmjs.org/lodash
added 120 packages in 3s
found 0 vulnerabilities

Exit code: 0
"""
        result = filter_command_output("npm install", raw)
        self.assertEqual(result.kind, "npm_install")
        self.assertIn("added 120 packages", result.text)
        self.assertNotIn("http fetch", result.text)
        self.assertLess(result.filtered_chars, result.original_chars)

    def test_ruff_keeps_findings(self) -> None:
        raw = """navin/agent/x.py:12:5: F401 [*] `os` imported but unused
navin/agent/y.py:3:1: E501 Line too long (121 > 100)
Found 2 errors.
[*] 1 fixable with the `--fix` option.

Exit code: 1
"""
        result = filter_command_output("ruff check .", raw)
        self.assertEqual(result.kind, "python_lint")
        self.assertIn("F401", result.text)
        self.assertIn("E501", result.text)
        self.assertIn("Exit code: 1", result.text)

    def test_docker_ps_compacts_columns(self) -> None:
        raw = (
            "CONTAINER ID   IMAGE          COMMAND       CREATED        STATUS         PORTS     NAMES\n"
            "abc123def456   nginx:latest   \"/docker-entrypoint.sh nginx -g 'daemon off;'\"   2 hours ago    Up 2 hours     80/tcp    web\n"
            "Exit code: 0\n"
        )
        result = filter_command_output("docker ps", raw)
        self.assertEqual(result.kind, "docker_ps")
        self.assertIn("nginx:latest", result.text)
        self.assertIn("web", result.text)
        self.assertNotIn("docker-entrypoint", result.text)

    def test_pip_install_strips_download_noise(self) -> None:
        raw = """Collecting requests
  Downloading requests-2.0.0-py3-none-any.whl
Using cached urllib3-1.0.0-py3-none-any.whl
Requirement already satisfied: certifi
Successfully installed requests-2.0.0 urllib3-1.0.0

Exit code: 0
"""
        result = filter_command_output("pip install requests", raw)
        self.assertEqual(result.kind, "pip")
        self.assertIn("Successfully installed", result.text)
        self.assertNotIn("Downloading", result.text)

    def test_tsc_keeps_errors(self) -> None:
        raw = """src/a.ts:10:5 - error TS2322: Type 'string' is not assignable to type 'number'.
src/b.ts:2:1 - error TS2304: Cannot find name 'foo'.

Found 2 errors.

Exit code: 2
"""
        result = filter_command_output("tsc --noEmit", raw)
        self.assertEqual(result.kind, "tsc")
        self.assertIn("TS2322", result.text)
        self.assertIn("TS2304", result.text)

    def test_kubectl_compacts_table(self) -> None:
        raw = (
            "NAME        READY   STATUS    RESTARTS   AGE\n"
            "web-1       1/1     Running   0          2d\n"
            "api-1       1/1     Running   0          5h\n"
            "Exit code: 0\n"
        )
        result = filter_command_output("kubectl get pods", raw)
        self.assertEqual(result.kind, "kubectl")
        self.assertIn("web-1", result.text)
        self.assertIn("Running", result.text)

    def test_curl_truncates_huge_body(self) -> None:
        body = "x" * 9000
        raw = body + "\nExit code: 0\n"
        result = filter_command_output("curl -s http://x", raw)
        self.assertEqual(result.kind, "curl")
        self.assertIn("chars omitted", result.text)
        self.assertLess(result.filtered_chars, result.original_chars)


if __name__ == "__main__":
    unittest.main()
