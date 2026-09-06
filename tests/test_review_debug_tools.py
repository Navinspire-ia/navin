"""Real tests for review / debug / security / PR tools - no mocks."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from navin.debug.mcp_client import probe_debugmcp
from navin.debug.repair import write_debug_report
from navin.github.pr_comments import (
    build_comment_plan,
    gh_available,
    preview_pr_comments,
)
from navin.review.gates import (
    _load_default_path_patterns,
    _load_supported_exts,
    apply_file_gates,
    filter_findings_precision,
    path_matches,
)
from navin.review.report_html import write_review_report
from navin.review.rules import load_review_rules
from navin.review.schema import filter_by_confidence, normalize_review_finding
from navin.review.scope import collect_review_scope
from navin.security.fp_filter import apply_fp_filter, hard_exclusion_reason
from navin.security.scan import run_security_scan


def _git_init_with_file(root: Path, rel: str, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(  # noqa: S603
        ["git", "init"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(  # noqa: S603
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(  # noqa: S603
        ["git", "config", "user.name", "Test"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(  # noqa: S603
        ["git", "add", "-A"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(  # noqa: S603
        ["git", "commit", "-m", "init"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


class ReviewSchemaTest(unittest.TestCase):
    def test_normalize_and_confidence_filter(self):
        f = normalize_review_finding(
            {
                "severity": "high",
                "category": "bug",
                "confidence": 0.9,
                "summary": "Null deref",
                "relevant_file": "a.py",
                "start_line": 10,
                "existing_code": "x.y",
                "improved_code": "if x: x.y",
            }
        )
        self.assertEqual(f["file_path"], "a.py")
        self.assertEqual(f["suggested_code"], "if x: x.y")
        kept = filter_by_confidence(
            [f, normalize_review_finding({"confidence": 0.2, "summary": "nit"})]
        )
        self.assertEqual(len(kept), 1)


class ReviewGatesTest(unittest.TestCase):
    def test_allowlists_loaded_from_embedded_ocr_json(self):
        exts = _load_supported_exts()
        patterns = _load_default_path_patterns()
        self.assertIn(".py", exts)
        self.assertIn(".ts", exts)
        self.assertNotIn(".png", exts)
        self.assertTrue(any("*.test." in p or "*.test.{" in p for p in patterns))

    def test_five_gates_drop_binary_tests_and_keep_source(self):
        result = apply_file_gates(
            [
                "src/app.py",
                "logo.png",
                "src/app.test.ts",
                "vendor/lib.js",
                "readme.xyz",
            ]
        )
        self.assertIn("src/app.py", result["files"])
        reasons = {d["path"]: d["reason"] for d in result["dropped"]}
        self.assertEqual(reasons["logo.png"], "binary")
        self.assertEqual(reasons["src/app.test.ts"], "default_path")
        self.assertEqual(reasons["vendor/lib.js"], "vendor_path")
        self.assertEqual(reasons["readme.xyz"], "unsupported_ext")
        self.assertTrue(result["allowlist_source"].endswith("supported_file_types.json"))

    def test_user_include_bypasses_default_path(self):
        result = apply_file_gates(
            ["src/app.test.ts"],
            include=["**/*.test.ts"],
        )
        self.assertEqual(result["files"], ["src/app.test.ts"])

    def test_brace_glob(self):
        self.assertTrue(path_matches("a/b.ts", "**/*.{ts,tsx}"))
        self.assertFalse(path_matches("a/b.py", "**/*.{ts,tsx}"))

    def test_precision_filter_drops_theoretical(self):
        kept, dropped = filter_findings_precision(
            [
                {
                    "severity": "high",
                    "confidence": 0.9,
                    "file_path": "a.py",
                    "start_line": 3,
                    "summary": "Null deref on user",
                    "existing_code": "user.name.upper()",
                },
                {
                    "severity": "low",
                    "confidence": 0.7,
                    "file_path": "a.py",
                    "start_line": 4,
                    "summary": "Could be improved theoretically",
                },
                {
                    "severity": "medium",
                    "confidence": 0.9,
                    "summary": "Missing path",
                },
                {
                    "severity": "high",
                    "confidence": 0.9,
                    "file_path": "a.py",
                    "start_line": 5,
                    "summary": "Tools are always sequential / no concurrency",
                },
                {
                    "severity": "medium",
                    "confidence": 0.9,
                    "file_path": "a.py",
                    "start_line": 6,
                    "summary": "Missing helper",
                },
            ]
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["summary"], "Null deref on user")
        reasons = {d.get("drop_reason") for d in dropped}
        self.assertIn("missing_evidence", reasons)
        self.assertTrue({"speculative", "missing_evidence"} & reasons)
        self.assertGreaterEqual(len(dropped), 3)


class ReviewRulesAndScopeTest(unittest.TestCase):
    def test_load_navin_rules_and_scope_real_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_init_with_file(root, "src/api/handler.py", "def handle():\n    return 1\n")
            (root / ".navin").mkdir()
            (root / ".navin" / "review-rules.json").write_text(
                json.dumps(
                    {
                        "exclude": ["**/generated/**"],
                        "rules": [
                            {
                                "path": "src/api/**/*.py",
                                "rule": "Validate request bodies",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            # Dirty change
            (root / "src" / "api" / "handler.py").write_text(
                "def handle(x):\n    return x\n",
                encoding="utf-8",
            )
            (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            payload = load_review_rules(root)
            self.assertEqual(payload["source"], ".navin/review-rules.json")
            scope = collect_review_scope(root)
            self.assertTrue(scope["ok"])
            self.assertIn("src/api/handler.py", scope["files"])
            self.assertNotIn("logo.png", scope["files"])
            self.assertIn("Validate request bodies", scope["path_rules"].get("src/api/handler.py", []))


class ReviewReportTest(unittest.TestCase):
    def test_write_report_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_review_report(
                root,
                findings=[
                    {
                        "severity": "high",
                        "category": "security",
                        "confidence": 0.9,
                        "summary": "SQLi",
                        "file_path": "db.py",
                        "start_line": 3,
                        "existing_code": 'f"SELECT {q}"',
                        "recommendation": "Parameterize",
                    }
                ],
                files=["db.py"],
                verdict="request_changes",
                effort=3,
            )
            text = path.read_text(encoding="utf-8")
            for marker in (
                "SQLi",
                "Request changes",
                "Real example",
                "Deliverables",
                "@media print",
                "Start with #N",
                "db.py:3",
            ):
                self.assertIn(marker, text)


class DebugReportTest(unittest.TestCase):
    def test_write_debug_report_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_debug_report(
                root,
                {
                    "signal": "AssertionError: expected 1",
                    "repro_steps": "pytest tests/test_x.py",
                    "root_cause": "off-by-one in parse()",
                    "hypotheses": ["fencepost"],
                    "before": "FAILED",
                    "after": "PASSED",
                    "stack": ["parse:12"],
                    "variables": {"n": 0},
                    "findings": [
                        {
                            "severity": "high",
                            "summary": "Off-by-one",
                            "file_path": "parse.py",
                            "start_line": 12,
                            "explanation": "Fencepost",
                            "evidence": "n=0",
                            "recommendation": "Use <",
                        }
                    ],
                    "latent_bugs": [
                        {
                            "severity": "medium",
                            "summary": "Same loop",
                            "file_path": "serialize.py",
                            "start_line": 40,
                            "explanation": "Copy-paste",
                            "evidence": "range(len(xs))",
                            "recommendation": "Share helper",
                        }
                    ],
                },
            )
            html = path.read_text(encoding="utf-8")
            for marker in (
                "off-by-one",
                "Real example",
                "Deliverables",
                "@media print",
                "Start with #N",
                "Related latent bugs",
                "parse.py:12",
            ):
                self.assertIn(marker, html)
            self.assertTrue((root / path.name.replace(".html", ".json")).is_file())

    def test_mcp_status_real_connection_refused(self):
        # Port 9 is discard - nothing listens. Real TCP failure, no mock.
        result = probe_debugmcp("http://127.0.0.1:9/mcp", timeout=0.5)
        self.assertFalse(result["reachable"])
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_report_coerces_malformed_payload_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_debug_report(
                root,
                {
                    "signal": "fail",
                    "repro_steps": "pytest",
                    "root_cause": "bug",
                    "ask_log": "why?",
                    "variables": "n=0",
                    "findings": "string finding",
                    "latent_bugs": {"summary": "nearby"},
                    "stack": "frame",
                    "hypotheses": "one idea",
                },
            )
            self.assertTrue(path.is_file())
            html = path.read_text(encoding="utf-8")
            self.assertIn("Real example", html)
            self.assertIn("Deliverables", html)


class FpFilterTest(unittest.TestCase):
    def test_hard_rules_and_evidence_gate(self):
        kept, dropped = apply_fp_filter(
            [
                {
                    "id": "sql-injection",
                    "summary": "SQL string build",
                    "file_path": "app.py",
                    "confidence": 0.9,
                    "severity": "medium",
                    "poc_sketch": "OR 1=1",
                },
                {
                    "id": "noise",
                    "summary": "Consider adding rate limit against DoS",
                    "file_path": "app.py",
                    "confidence": 0.9,
                },
                {
                    "id": "uaf",
                    "summary": "Use after free risk",
                    "file_path": "app.py",
                    "confidence": 0.9,
                    "severity": "high",
                    "poc_sketch": "free then use",
                },
            ]
        )
        ids = {f["id"] for f in kept}
        self.assertEqual(ids, {"sql-injection"})
        self.assertGreaterEqual(len(dropped), 2)
        self.assertEqual(
            hard_exclusion_reason({"summary": "Missing rate limit", "file_path": "a.py"}),
            "rate_limit_noise",
        )


class SecurityScanRealTest(unittest.TestCase):
    def test_scan_real_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text(
                'import subprocess\nsubprocess.run("ls", shell=True)\n',
                encoding="utf-8",
            )
            result = run_security_scan(root, kind="quick", max_findings=50)
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["finding_count"], 1)
            first = result["findings"][0]
            self.assertTrue(first.get("poc_sketch"))


class PrCommentsRealTest(unittest.TestCase):
    def test_build_plan_skips_missing_line(self):
        plan = build_comment_plan(
            [
                {"summary": "A", "file_path": "a.py", "start_line": 10, "severity": "high"},
                {"summary": "B", "file_path": "b.py", "severity": "low"},
            ]
        )
        self.assertEqual(len(plan["comments"]), 1)
        self.assertEqual(plan["comments"][0]["path"], "a.py")
        self.assertEqual(plan["skipped"][0]["reason"], "missing_path_or_line")

    def test_preview_without_github_remote_is_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_init_with_file(root, "a.py", "x=1\n")
            result = preview_pr_comments(
                root,
                [{"summary": "x", "file_path": "a.py", "start_line": 1, "severity": "high"}],
                kind="review",
            )
            # Real gh failure (no remote / no PR) - never mocked.
            self.assertIn("comment_count", result)
            self.assertEqual(result["comment_count"], 1)
            if gh_available():
                self.assertFalse(result.get("ok", True) and result.get("action") == "preview")
            else:
                self.assertFalse(result.get("ok", False))


class PrCommentsForgeTest(unittest.TestCase):
    """Inline review comments on GitLab and Forgejo, entirely mocked."""

    def _fake_http(self, responses):
        from unittest.mock import patch

        from navin.webui import forge_api

        class _Response:
            def __init__(self, status_code, payload=None, text=None):
                self.status_code = status_code
                self._payload = payload
                self.text = text if text is not None else "json"

            def json(self):
                if self._payload is None:
                    raise ValueError("no json")
                return self._payload

        calls: list[dict] = []

        def _request(method, url, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            if not responses:
                raise AssertionError(f"unexpected extra request: {method} {url}")
            status, payload = responses.pop(0)
            return _Response(status, payload)

        return patch.object(forge_api.httpx, "request", _request), calls

    def _forgejo_remote(self):
        from navin.webui.forge_api import FORGEJO, ForgeRemote

        return ForgeRemote(kind=FORGEJO, host="forgejo.navinspire.ai", owner="o", repo="r")

    def test_post_creates_a_forgejo_review_with_inline_comments(self):
        from unittest.mock import patch

        from navin.github import pr_comments as mod
        from navin.webui import forge_api

        responses = [
            # PR read by number, changed files, then the review itself.
            (200, {"number": 5, "title": "Ship", "html_url": "https://f/o/r/pulls/5",
                   "head": {"ref": "feature"}, "base": {"ref": "main"}, "state": "open"}),
            (200, [{"filename": "a.py"}]),
            (200, {"id": 42, "html_url": "https://f/o/r/pulls/5#issuecomment-42"}),
        ]
        http, calls = self._fake_http(responses)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(mod, "_config", return_value=None),
                patch.object(forge_api, "detect_forge", return_value=self._forgejo_remote()),
                patch.object(forge_api, "resolve_token", return_value=("t0k3n", "settings")),
                http,
            ):
                result = mod.post_pr_review(
                    root,
                    [{"summary": "x", "file_path": "a.py", "start_line": 3, "severity": "high"}],
                    kind="review",
                    pr=5,
                )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["forge"], "forgejo")
        self.assertEqual(result["comment_count"], 1)
        review = calls[-1]
        self.assertTrue(review["url"].endswith("/repos/o/r/pulls/5/reviews"))
        self.assertEqual(review["json"]["comments"][0]["new_position"], 3)

    def test_missing_token_says_where_to_add_one(self):
        from unittest.mock import patch

        from navin.github import pr_comments as mod
        from navin.webui import forge_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(mod, "_config", return_value=None),
                patch.object(mod, "gh_available", return_value=True),
                patch.object(forge_api, "detect_forge", return_value=self._forgejo_remote()),
                patch.object(forge_api, "resolve_token", return_value=("", "")),
            ):
                result = mod.resolve_pr_context(root, 5)
        self.assertFalse(result["ok"])
        self.assertIn("forgejo.navinspire.ai", result["error"])
        self.assertIn("Settings > Git", result["error"])

    def test_gitlab_preview_labels_a_merge_request(self):
        from unittest.mock import patch

        from navin.github import pr_comments as mod
        from navin.webui import forge_api
        from navin.webui.forge_api import GITLAB, ForgeRemote

        responses = [
            (200, {"iid": 7, "title": "Ship", "web_url": "https://gitlab.com/g/r/-/merge_requests/7",
                   "source_branch": "feature", "target_branch": "main", "state": "opened"}),
            (200, [{"new_path": "a.py"}]),
        ]
        http, _calls = self._fake_http(responses)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(mod, "_config", return_value=None),
                patch.object(
                    forge_api,
                    "detect_forge",
                    return_value=ForgeRemote(kind=GITLAB, host="gitlab.com", owner="g", repo="r"),
                ),
                patch.object(forge_api, "resolve_token", return_value=("t0k3n", "settings")),
                http,
            ):
                result = mod.preview_pr_comments(
                    root,
                    [{"summary": "x", "file_path": "a.py", "start_line": 3, "severity": "high"}],
                    kind="review",
                    pr=7,
                )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["request_label"], "merge request")
        self.assertEqual(result["comment_count"], 1)


class ToolExecutionRealTest(unittest.TestCase):
    def test_code_review_security_debug_tools_execute(self):
        from navin.agent.tools.code_review import CodeReviewTool
        from navin.agent.tools.debug_repair import DebugRepairTool
        from navin.agent.tools.security_scan import SecurityScanTool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_init_with_file(root, "svc.py", "x = 1\n")
            (root / "svc.py").write_text(
                'PASSWORD = "secret-value-12345"\nx = 1\n',
                encoding="utf-8",
            )

            code_review = CodeReviewTool(workspace=root)
            security_scan = SecurityScanTool(workspace=root)
            debug_repair = DebugRepairTool(workspace=root)

            async def _run() -> None:
                scope = json.loads(await code_review.execute(action="scope"))
                self.assertTrue(scope["ok"])

                findings = [
                    {
                        "severity": "high",
                        "category": "security",
                        "confidence": 0.9,
                        "summary": "Hardcoded secret",
                        "file_path": "svc.py",
                        "start_line": 1,
                        "existing_code": 'PASSWORD = "..."',
                        "recommendation": "Use env var",
                    }
                ]
                filt = json.loads(
                    await code_review.execute(
                        action="filter",
                        findings_json=json.dumps(findings),
                    )
                )
                self.assertEqual(filt["kept_count"], 1)

                report = json.loads(
                    await code_review.execute(
                        action="report",
                        findings_json=json.dumps(findings),
                        verdict="request_changes",
                        effort=2,
                    )
                )
                self.assertTrue(Path(report["report_path"]).is_file())

                scan = await security_scan.execute(kind="quick", write_report=True)
                self.assertIn("findings=", scan)
                self.assertIn("report=", scan)

                mcp = json.loads(
                    await debug_repair.execute(
                        action="mcp_status",
                        mcp_url="http://127.0.0.1:9/mcp",
                    )
                )
                self.assertFalse(mcp["reachable"])

                status = json.loads(await debug_repair.execute(action="status"))
                self.assertIn("branch", status)

                dbg = json.loads(
                    await debug_repair.execute(
                        action="report",
                        payload_json=json.dumps(
                            {
                                "signal": "fail",
                                "repro_steps": "pytest",
                                "root_cause": "secret hardcoded",
                                "before": "red",
                                "after": "green",
                                "findings": findings,
                            }
                        ),
                    )
                )
                self.assertTrue(Path(dbg["report_path"]).is_file())

            import asyncio

            asyncio.run(_run())


class ToolDiscoveryTest(unittest.TestCase):
    def test_all_expert_tools_discovered(self):
        from navin.agent.tools.loader import ToolLoader

        names = {cls.__name__ for cls in ToolLoader().discover()}
        for required in (
            "CodeReviewTool",
            "DebugRepairTool",
            "PrCommentsTool",
            "SecurityScanTool",
            "SetComposerModeTool",
        ):
            self.assertIn(required, names)


class McpPresetTest(unittest.TestCase):
    def test_debugmcp_preset_registered(self):
        from navin.webui.mcp_presets_api import MCP_PRESETS

        preset = next(p for p in MCP_PRESETS if p.name == "debugmcp")
        self.assertEqual(preset.transport, "streamableHttp")
        self.assertTrue(preset.auto_enable)
        self.assertIn("3001", preset.server.url)

    def test_google_ads_and_search_console_presets_in_catalogue(self):
        from navin.webui.mcp_presets_api import MCP_PRESETS, mcp_presets_payload

        by_name = {p.name: p for p in MCP_PRESETS}
        self.assertIn("google-ads", by_name)
        self.assertIn("search-console", by_name)

        ads = by_name["google-ads"]
        self.assertEqual(ads.category, "ads")
        self.assertEqual(ads.transport, "stdio")
        self.assertEqual(ads.server.command, "pipx")
        self.assertIn("google-ads-mcp", ads.server.args)
        ads_env = {f.target[1] for f in ads.fields}
        self.assertIn("GOOGLE_ADS_DEVELOPER_TOKEN", ads_env)
        self.assertIn("GOOGLE_PROJECT_ID", ads_env)
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", ads_env)

        gsc = by_name["search-console"]
        self.assertEqual(gsc.category, "seo")
        self.assertEqual(gsc.transport, "stdio")
        self.assertEqual(gsc.server.command, "uvx")
        self.assertEqual(gsc.server.args, ["mcp-search-console"])
        gsc_env = {f.target[1] for f in gsc.fields}
        self.assertIn("GSC_OAUTH_CLIENT_SECRETS_FILE", gsc_env)
        self.assertIn("GSC_CREDENTIALS_PATH", gsc_env)

        payload = mcp_presets_payload()
        names = {item["name"] for item in payload.get("presets", [])}
        self.assertIn("google-ads", names)
        self.assertIn("search-console", names)

    def test_ads_platform_presets_in_catalogue(self):
        from navin.webui.mcp_presets_api import MCP_PRESETS, mcp_presets_payload

        by_name = {p.name: p for p in MCP_PRESETS}
        for name in ("google-ads", "meta-ads", "tiktok-ads", "reddit-ads"):
            self.assertIn(name, by_name)
            self.assertEqual(by_name[name].category, "ads", name)

        meta = by_name["meta-ads"]
        self.assertEqual(meta.transport, "streamableHttp")
        self.assertEqual(meta.server.url, "https://mcp.facebook.com/ads")
        self.assertEqual(
            {f.target for f in meta.fields},
            {("header", "Authorization")},
        )

        tiktok = by_name["tiktok-ads"]
        self.assertEqual(tiktok.transport, "stdio")
        self.assertEqual(tiktok.server.command, "uvx")
        self.assertEqual(tiktok.server.args, ["tiktok-ads-mcp"])
        tiktok_env = {f.target[1] for f in tiktok.fields}
        self.assertTrue(
            {"TIKTOK_APP_ID", "TIKTOK_SECRET", "TIKTOK_ACCESS_TOKEN"} <= tiktok_env
        )

        reddit = by_name["reddit-ads"]
        self.assertEqual(reddit.transport, "stdio")
        self.assertEqual(reddit.server.command, "npx")
        self.assertIn("mcp-server-reddit-ads", reddit.server.args)
        self.assertEqual(reddit.server.env.get("REDDIT_ADS_WRITE_TIER"), "read")
        reddit_env = {f.target[1] for f in reddit.fields}
        self.assertTrue(
            {
                "REDDIT_CLIENT_ID",
                "REDDIT_CLIENT_SECRET",
                "REDDIT_REFRESH_TOKEN",
            }
            <= reddit_env
        )

        payload = mcp_presets_payload()
        names = {item["name"] for item in payload.get("presets", [])}
        for name in ("google-ads", "meta-ads", "tiktok-ads", "reddit-ads"):
            self.assertIn(name, names)

    def test_ensure_auto_enabled_installs_debugmcp_once(self):
        from navin.config.schema import Config
        from navin.webui.mcp_presets_api import ensure_auto_enabled_mcp_presets

        config = Config()
        self.assertNotIn("debugmcp", config.tools.mcp_servers)
        added = ensure_auto_enabled_mcp_presets(config)
        self.assertIn("debugmcp", added)
        self.assertIn("debugmcp", config.tools.mcp_servers)
        self.assertEqual(
            config.tools.mcp_servers["debugmcp"].url,
            "http://127.0.0.1:3001/mcp",
        )
        # Second pass does not overwrite or re-add.
        config.tools.mcp_servers["debugmcp"].tool_timeout = 99
        added_again = ensure_auto_enabled_mcp_presets(config)
        self.assertEqual(added_again, [])
        self.assertEqual(config.tools.mcp_servers["debugmcp"].tool_timeout, 99)

    def test_ensure_respects_opt_out(self):
        from navin.config.schema import Config
        from navin.webui.mcp_presets_api import ensure_auto_enabled_mcp_presets

        config = Config()
        config.tools.auto_enable_mcp_presets = False
        self.assertEqual(ensure_auto_enabled_mcp_presets(config), [])
        self.assertNotIn("debugmcp", config.tools.mcp_servers)


@unittest.skipUnless(shutil.which("gh") is not None, "gh CLI not installed")
class GhCliPresenceTest(unittest.TestCase):
    def test_gh_binary_runs(self):
        completed = subprocess.run(  # noqa: S603
            ["gh", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("gh version", completed.stdout.lower() + completed.stderr.lower())


if __name__ == "__main__":
    unittest.main()
