"""Vision 360 audit heuristics must avoid misleading false positives."""

from __future__ import annotations

from pathlib import Path

from navin.webui.audit_ast import scan_python_file_ast
from navin.webui.project_audit import (
    _scan_performance,
    _scan_security,
    _ScannedFile,
)


def _file(tmp_path: Path, rel: str, content: str) -> _ScannedFile:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    scanned = _ScannedFile(rel.replace("\\", "/"), path, path.suffix.lower(), path.stat().st_size)
    scanned.text = content
    scanned.lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    return scanned


def _ids(result: dict) -> set[str]:
    return {f["id"] for f in result["findings"]}


def test_security_ignores_poc_and_redacted_key(tmp_path: Path) -> None:
    files = [
        _file(
            tmp_path,
            "navin/security/poc.py",
            'X = "-----BEGIN PRIVATE KEY----- (redacted)"\n',
        ),
        _file(
            tmp_path,
            "secrets/prod.pem",
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvRealPrivateKeyMaterialNotAPlaceholderXYZ\n"
            "-----END PRIVATE KEY-----\n",
        ),
    ]
    result = _scan_security(files, [])
    key_findings = [f for f in result["findings"] if f["id"] == "private-key"]
    assert key_findings
    assert all(f["file"] != "navin/security/poc.py" for f in key_findings)
    assert key_findings[0]["confidence"] == "high"


def test_security_ignores_comment_exec_and_python_c_runner(tmp_path: Path) -> None:
    files = [
        _file(
            tmp_path,
            "webui/src/lib/navin-client.ts",
            "/** Live feed of a command the agent runs via exec (read-only). */\n"
            "export const x = 1;\n",
        ),
        _file(
            tmp_path,
            "cli.py",
            "def run(args):\n"
            "    exec(compile(args[1], '<command>', 'exec'), {'__name__': '__main__'})\n",
        ),
        _file(
            tmp_path,
            "bad.py",
            "def run(code):\n"
            "    exec(code)\n",
        ),
    ]
    result = _scan_security(files, [])
    exec_findings = [f for f in result["findings"] if f["id"] == "eval-exec"]
    assert len(exec_findings) == 1
    assert exec_findings[0]["file"] == "bad.py"
    assert exec_findings[0]["confidence"] == "high"
    assert "AST" in exec_findings[0]["evidence"]


def test_security_tls_fallback_and_sha1_checksum_skipped(tmp_path: Path) -> None:
    files = [
        _file(
            tmp_path,
            "provider.py",
            "async def call():\n"
            "    try:\n"
            "        await request(url, verify=True)\n"
            "    except Exception as e:\n"
            "        if 'CERTIFICATE_VERIFY_FAILED' not in str(e):\n"
            "            raise\n"
            "        await request(url, verify=False)\n",
        ),
        _file(
            tmp_path,
            "ids.py",
            "import hashlib\n"
            "digest = hashlib.sha1(name.encode()).hexdigest()[:8]\n",
        ),
        _file(
            tmp_path,
            "auth.py",
            "import hashlib\n"
            "def store(password: str) -> str:\n"
            "    return hashlib.sha1(password.encode()).hexdigest()\n",
        ),
    ]
    result = _scan_security(files, [])
    assert "tls-verify-off" not in _ids(result)
    weak = [f for f in result["findings"] if f["id"] == "weak-hash"]
    assert len(weak) == 1
    assert weak[0]["file"] == "auth.py"
    assert weak[0]["confidence"] in {"high", "medium"}


def test_security_cors_star_without_credentials_skipped(tmp_path: Path) -> None:
    files = [
        _file(
            tmp_path,
            "route.ts",
            'const CORS = { "Access-Control-Allow-Origin": "*" };\n',
        ),
        _file(
            tmp_path,
            "auth_route.ts",
            'headers = {\n'
            '  "Access-Control-Allow-Origin": "*",\n'
            '  "Access-Control-Allow-Credentials": "true",\n'
            '};\n',
        ),
    ]
    result = _scan_security(files, [])
    cors = [f for f in result["findings"] if f["id"] == "cors-wildcard"]
    assert len(cors) == 1
    assert cors[0]["file"] == "auth_route.ts"


def test_security_jsonld_and_chmod_string_skipped(tmp_path: Path) -> None:
    files = [
        _file(
            tmp_path,
            "page.tsx",
            '<script type="application/ld+json"\n'
            "  dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}\n"
            "/>\n",
        ),
        _file(
            tmp_path,
            "tool.py",
            'NOTE = "chmod 777"\n',
        ),
    ]
    result = _scan_security(files, [])
    assert "inner-html" not in _ids(result)
    assert "chmod-777" not in _ids(result)


def test_perf_sleep_in_sync_helper_not_flagged(tmp_path: Path) -> None:
    files = [
        _file(
            tmp_path,
            "scrape.py",
            "import time\n"
            "import asyncio\n"
            "\n"
            "def _fetch():\n"
            "    time.sleep(0.1)\n"
            "    return {}\n"
            "\n"
            "async def execute():\n"
            "    return await asyncio.to_thread(_fetch)\n",
        ),
    ]
    result = _scan_performance(files)
    assert "sleep-in-async" not in _ids(result)
    assert "sync-call-from-async" not in _ids(result)


def test_perf_flags_direct_sleep_and_blocking_call(tmp_path: Path) -> None:
    files = [
        _file(
            tmp_path,
            "bad_async.py",
            "import time\n"
            "import subprocess\n"
            "\n"
            "def _rg_scan():\n"
            "    subprocess.run(['rg'])\n"
            "    return {}\n"
            "\n"
            "def _collect():\n"
            "    return _rg_scan()\n"
            "\n"
            "async def execute():\n"
            "    time.sleep(1)\n"
            "    return _collect()\n",
        ),
    ]
    result = _scan_performance(files)
    ids = _ids(result)
    assert "sleep-in-async" in ids
    assert "sync-call-from-async" in ids
    sleep = next(f for f in result["findings"] if f["id"] == "sleep-in-async")
    assert sleep["confidence"] == "high"
    assert "AST" in sleep["evidence"]


def test_perf_ignores_http_retry_as_n_plus_one(tmp_path: Path) -> None:
    files = [
        _file(
            tmp_path,
            "web.py",
            "async def search():\n"
            "    for attempt in range(2):\n"
            "        r = await client.get('https://example.com')\n"
            "        if r.status_code != 429:\n"
            "            break\n",
        ),
        _file(
            tmp_path,
            "db.py",
            "def load(rows, session):\n"
            "    for row in rows:\n"
            "        session.execute('SELECT 1')\n",
        ),
    ]
    result = _scan_performance(files)
    n1 = [f for f in result["findings"] if f["id"] == "n-plus-one"]
    assert len(n1) == 1
    assert n1[0]["file"] == "db.py"


def test_ast_pickle_and_shell_true() -> None:
    findings = scan_python_file_ast(
        "x.py",
        "import pickle, subprocess\n"
        "def bad(data):\n"
        "    pickle.loads(data)\n"
        "    subprocess.run('ls', shell=True)\n",
    )
    ids = {f["id"] for f in findings}
    assert "pickle" in ids
    assert "shell-true" in ids
    assert all(f["confidence"] == "high" for f in findings if f["id"] in ids)
