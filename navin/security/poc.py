"""PoC / payload sketches for structured AppSec findings.

Produces non-destructive illustration payloads for reports. These are
educational templates tied to rule ids - not live exploit runners.
"""

from __future__ import annotations

from typing import Any

# rule_id → (malicious_input_example, poc_sketch)
_POC_BY_ID: dict[str, tuple[str, str]] = {
    "private-key": (
        "-----BEGIN PRIVATE KEY----- (redacted)",
        "Confirm the key material is live (not a fixture), rotate immediately, "
        "purge from git history (`git filter-repo` / BFG), and move to a secrets manager.",
    ),
    "aws-key": (
        "AKIA················",
        "aws sts get-caller-identity with the leaked key (isolated lab only) to confirm "
        "liveness, then revoke via IAM and rotate dependents.",
    ),
    "hardcoded-secret": (
        "password=••••••••",
        "Treat as live until proven otherwise: rotate the credential, remove from tree "
        "and history, load from env / vault.",
    ),
    "sql-injection": (
        "' OR 1=1--",
        "Reproduce with a parameterized request that injects `' OR 1=1--` (or a "
        "boolean/time blind probe) on the tainted field, then rewrite the query with "
        "bound parameters.",
    ),
    "eval-exec": (
        "__import__('os').system('id')",
        "If user input reaches eval/exec, pass a benign marker string and observe "
        "execution; replace with safe parsing (ast.literal_eval / JSON / allow-list).",
    ),
    "pickle": (
        "pickle.loads(attacker_blob)",
        "Craft a pickle that runs a no-op marker on load in a sandbox; never unpickle "
        "untrusted data - switch to JSON or a signed format.",
    ),
    "shell-true": (
        "; id #",
        "If argv is user-controlled with shell=True, inject `; id` / `& whoami` in a "
        "lab; fix by passing a list argv without shell=True.",
    ),
    "tls-verify-off": (
        "verify=False",
        "Point the client at a host with an invalid cert and confirm the call still "
        "succeeds; re-enable verification (custom CA bundle if needed).",
    ),
    "inner-html": (
        "<img src=x onerror=alert(1)>",
        "Inject an HTML/JS marker into the rendered field; sanitize with DOMPurify or "
        "use textContent / safe framework APIs.",
    ),
    "jwt-none": (
        '{"alg":"none"}',
        "Forge a token with alg=none (or skip signature) and call a protected route; "
        "enforce verify + allow-listed algorithms server-side.",
    ),
    "cors-wildcard": (
        "Origin: https://evil.example",
        "From a foreign origin with credentials, confirm Access-Control-Allow-Origin "
        "reflects * or the attacker origin; restrict to an explicit allow-list.",
    ),
    "yaml-load": (
        "!!python/object/apply:os.system ['id']",
        "Feed a YAML gadget in a sandbox if yaml.load is unrestricted; switch to "
        "yaml.safe_load.",
    ),
    "os-system": (
        "; id",
        "If the command string is tainted, inject a shell metacharacter in a lab; "
        "replace os.system with subprocess.run([...]).",
    ),
    "weak-hash": (
        "md5(password)",
        "Demonstrate collision / fast offline crack risk for password use; migrate to "
        "bcrypt/argon2 (SHA-256+ only for non-security checksums).",
    ),
    "debug-true": (
        "DEBUG=True",
        "Hit an error path and confirm stack traces / interactive debugger leak; gate "
        "debug via environment, never hardcode for production.",
    ),
    "http-url": (
        "http://external.example/api",
        "Prefer HTTPS; if plaintext is required on a private network, document the "
        "exception and lock the destination.",
    ),
    "chmod-777": (
        "chmod 777",
        "Show the file is world-writable; tighten to the minimum mode required.",
    ),
}

_POC_BY_CATEGORY: dict[str, tuple[str, str]] = {
    "Secrets": (
        "<redacted-secret>",
        "Rotate, purge from history, store in a secrets manager.",
    ),
    "Injection": (
        "' OR 1=1--",
        "Prove the sink with a safe lab payload, then parameterize / escape.",
    ),
    "XSS": (
        "<img src=x onerror=alert(1)>",
        "Inject a marker into the reflected/stored field; sanitize or escape output.",
    ),
    "Supply chain": (
        "CVE in lockfile package",
        "Confirm the vulnerable version is resolved, then upgrade / pin a fixed release.",
    ),
    "SAST": (
        "(see finding explanation)",
        "Trace source→sink, craft a minimal PoC for the sink class, then apply the fix.",
    ),
    "Authentication": (
        "forged session / JWT",
        "Attempt access with a forged or unsigned token in a lab; enforce server-side checks.",
    ),
    "CORS": (
        "Origin: https://evil.example",
        "Cross-origin credentialed request should fail; tighten ACAO allow-list.",
    ),
    "TLS": (
        "MITM with invalid cert",
        "Disable verify only in documented lab overrides; production must verify certs.",
    ),
}


def poc_for_finding(finding: dict[str, Any]) -> dict[str, str]:
    """Return malicious_input_example + poc_sketch for a finding."""
    rule_id = str(finding.get("id") or "").strip()
    category = str(finding.get("category") or "").strip()
    existing_ex = str(finding.get("malicious_input_example") or "").strip()
    existing_poc = str(finding.get("poc_sketch") or "").strip()

    pair = _POC_BY_ID.get(rule_id) or _POC_BY_CATEGORY.get(category)
    if pair is None:
        example = existing_ex or "(no generic payload - trace the sink manually)"
        sketch = existing_poc or (
            "Trace user-controlled input to this sink, craft a minimal lab PoC, "
            "document impact, then apply the recommended fix."
        )
    else:
        example = existing_ex or pair[0]
        sketch = existing_poc or pair[1]

    loc = str(finding.get("file_path") or "")
    line = finding.get("line")
    if loc and isinstance(line, int):
        sketch = f"Location `{loc}:{line}`. {sketch}"
    elif loc:
        sketch = f"Location `{loc}`. {sketch}"

    return {
        "malicious_input_example": example[:500],
        "poc_sketch": sketch[:1200],
    }


def enrich_findings_with_poc(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach PoC fields to each finding (idempotent)."""
    out: list[dict[str, Any]] = []
    for item in findings:
        enriched = dict(item)
        poc = poc_for_finding(enriched)
        enriched["malicious_input_example"] = poc["malicious_input_example"]
        enriched["poc_sketch"] = poc["poc_sketch"]
        out.append(enriched)
    return out
