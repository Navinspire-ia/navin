# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Security catalog: the reference every engine, skill and screen reads.

- 18 vulnerability families (what to look for, with priority);
- 70 controls in 7 groups (the audit checklist, each control knowing its
  families, the level that can assess it, the engines that help and the skill
  that carries the methodology);
- 14 external frameworks the findings and controls are mapped to;
- 5 audit levels (L1 static to L5 continuous);
- 20 security skills (methodologies loaded on demand by the skill router).

The catalog is plain data written for Navin, not copied from any external
standard text: framework entries only carry the name, the publisher, the URL
and the role of each reference. Labels ship in French, English and Arabic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PRIORITY_CRITICAL = "critical"
PRIORITY_HIGH = "high"


@dataclass(frozen=True)
class Family:
    id: str
    number: int
    name: dict[str, str]
    attacks: list[str]
    priority: str
    skill: str
    owasp_2025: str
    needs_running_app: bool


@dataclass(frozen=True)
class Control:
    id: str
    group: str
    label: dict[str, str]
    families: list[str]
    level: str
    detection: str
    scanners: list[str]
    skill: str
    references: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ControlGroup:
    id: str
    number: int
    title: dict[str, str]


@dataclass(frozen=True)
class Framework:
    id: str
    name: str
    publisher: str
    purpose: dict[str, str]
    url: str


@dataclass(frozen=True)
class Level:
    id: str
    name: dict[str, str]
    capabilities: dict[str, str]
    needs: list[str]
    engines: list[str]


@dataclass(frozen=True)
class SkillSpec:
    id: str
    teaches: dict[str, str]
    families: list[str]
    triggers: list[str]


def _n(fr: str, en: str, ar: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "ar": ar}


# ---------------------------------------------------------------------------
# 18 families
# ---------------------------------------------------------------------------

FAMILIES: tuple[Family, ...] = (
    Family(
        "injection", 1, _n("Injection", "Injection", "الحقن"),
        ["SQLi", "NoSQLi", "XSS", "SSTI", "XXE", "LDAP injection", "XPath injection",
         "command injection", "code injection", "CRLF injection"],
        PRIORITY_CRITICAL, "security-injection", "A05:2025 Injection", False,
    ),
    Family(
        "authentication", 2, _n("Authentification", "Authentication", "المصادقة"),
        ["brute force", "credential stuffing", "MFA bypass", "JWT forgery", "session fixation",
         "password reset bypass", "OAuth/OIDC flaws"],
        PRIORITY_CRITICAL, "security-authentication", "A07:2025 Authentication Failures", True,
    ),
    Family(
        "authorization", 3, _n("Autorisations", "Authorization", "التفويض"),
        ["IDOR", "BOLA", "BFLA", "RBAC bypass", "ABAC bypass", "horizontal privilege escalation",
         "vertical privilege escalation"],
        PRIORITY_CRITICAL, "security-authorization", "A01:2025 Broken Access Control", True,
    ),
    Family(
        "multitenancy", 4, _n("Multi-tenant", "Multi-tenant", "تعدد المستأجرين"),
        ["cross-tenant data leak", "tenant ID manipulation", "shared cache", "RLS bypass",
         "insufficient isolation"],
        PRIORITY_CRITICAL, "security-multitenancy", "A01:2025 Broken Access Control", True,
    ),
    Family(
        "code_execution", 5, _n("Execution de code", "Code execution", "تنفيذ الشيفرة"),
        ["RCE", "unsafe deserialization", "insecure eval", "arbitrary file execution",
         "plugin execution abuse"],
        PRIORITY_CRITICAL, "security-sast-review", "A05:2025 Injection", False,
    ),
    Family(
        "files", 6, _n("Fichiers", "Files", "الملفات"),
        ["path traversal", "LFI", "RFI", "arbitrary file upload", "ZIP Slip", "symlink attacks",
         "unrestricted file download"],
        PRIORITY_CRITICAL, "security-sast-review", "A01:2025 Broken Access Control", False,
    ),
    Family(
        "api", 7, _n("API", "API", "واجهات API"),
        ["mass assignment", "excessive data exposure", "API key leakage", "rate-limit bypass",
         "GraphQL abuse", "REST authorization flaws"],
        PRIORITY_CRITICAL, "security-api", "A01:2025 Broken Access Control", True,
    ),
    Family(
        "secrets", 8, _n("Secrets", "Secrets", "الأسرار"),
        ["hardcoded passwords", "exposed tokens", "private keys", "git secrets",
         "leaked environment variables"],
        PRIORITY_CRITICAL, "security-secrets", "A02:2025 Security Misconfiguration", False,
    ),
    Family(
        "cryptography", 9, _n("Cryptographie", "Cryptography", "التشفير"),
        ["weak encryption", "predictable keys", "missing rotation", "invalid certificates",
         "weak TLS configuration"],
        PRIORITY_HIGH, "security-cryptography", "A04:2025 Cryptographic Failures", False,
    ),
    Family(
        "supply_chain", 10, _n("Supply chain", "Supply chain", "سلسلة التوريد"),
        ["vulnerable dependencies", "dependency confusion", "typosquatting", "malicious packages",
         "CI/CD compromise"],
        PRIORITY_CRITICAL, "security-supply-chain", "A03:2025 Software Supply Chain Failures", False,
    ),
    Family(
        "infrastructure", 11, _n("Infrastructure", "Infrastructure", "البنية التحتية"),
        ["exposed ports", "insecure services", "Docker escape", "Kubernetes RBAC", "excessive IAM",
         "public storage"],
        PRIORITY_CRITICAL, "security-containers", "A02:2025 Security Misconfiguration", False,
    ),
    Family(
        "frontend", 12, _n("Frontend", "Frontend", "الواجهة الأمامية"),
        ["DOM XSS", "clickjacking", "CORS", "weak CSP", "prototype pollution",
         "sensitive data in the browser"],
        PRIORITY_HIGH, "security-frontend", "A02:2025 Security Misconfiguration", True,
    ),
    Family(
        "business_logic", 13, _n("Logique metier", "Business logic", "منطق الأعمال"),
        ["price manipulation", "payment bypass", "double spending", "workflow bypass",
         "race conditions", "credit fraud"],
        PRIORITY_CRITICAL, "security-business-logic", "A06:2025 Insecure Design", True,
    ),
    Family(
        "availability", 14, _n("Disponibilite", "Availability", "التوافر"),
        ["DoS", "ReDoS", "resource exhaustion", "zip bombs", "expensive queries", "amplification"],
        PRIORITY_HIGH, "security-api", "A06:2025 Insecure Design", True,
    ),
    Family(
        "network", 15, _n("Reseau", "Network", "الشبكة"),
        ["DNS rebinding", "SSRF", "request smuggling", "host header injection", "cache poisoning",
         "MITM"],
        PRIORITY_CRITICAL, "security-api", "A10:2025 Mishandling of Exceptional Conditions", True,
    ),
    Family(
        "mobile_desktop", 16, _n("Mobile et desktop", "Mobile and desktop", "الجوال وسطح المكتب"),
        ["insecure local storage", "IPC abuse", "deep-link hijacking", "WebView injection",
         "excessive permissions"],
        PRIORITY_HIGH, "security-mobile", "A02:2025 Security Misconfiguration", False,
    ),
    Family(
        "ai_agents", 17, _n("IA et agents", "AI and agents", "الذكاء الاصطناعي والوكلاء"),
        ["prompt injection", "tool abuse", "RAG poisoning", "exfiltration", "model endpoint abuse",
         "excessive agency"],
        PRIORITY_CRITICAL, "security-ai-agents", "LLM01:2025 Prompt Injection", True,
    ),
    Family(
        "audit_logging", 18, _n("Audit et tracabilite", "Audit and traceability", "التدقيق والتتبع"),
        ["missing logs", "log tampering", "no correlation", "no alerting", "sensitive data in logs"],
        PRIORITY_HIGH, "security-compliance", "A09:2025 Security Logging and Alerting Failures", True,
    ),
)

FAMILY_IDS: tuple[str, ...] = tuple(f.id for f in FAMILIES)


# ---------------------------------------------------------------------------
# 7 groups x 10 controls
# ---------------------------------------------------------------------------

CONTROL_GROUPS: tuple[ControlGroup, ...] = (
    ControlGroup("G1", 1, _n("Code source et execution", "Source code and execution", "الشيفرة المصدرية والتنفيذ")),
    ControlGroup("G2", 2, _n("Identites, sessions et permissions", "Identities, sessions and permissions", "الهويات والجلسات والصلاحيات")),
    ControlGroup("G3", 3, _n("Site web, API et logique metier", "Web site, API and business logic", "الموقع وواجهات API ومنطق الأعمال")),
    ControlGroup("G4", 4, _n("Infrastructure, cloud et supply chain", "Infrastructure, cloud and supply chain", "البنية التحتية والسحابة وسلسلة التوريد")),
    ControlGroup("G5", 5, _n("Applications mobiles et desktop", "Mobile and desktop applications", "تطبيقات الجوال وسطح المكتب")),
    ControlGroup("G6", 6, _n("Agents IA, RAG et MCP", "AI agents, RAG and MCP", "وكلاء الذكاء الاصطناعي وRAG وMCP")),
    ControlGroup("G7", 7, _n("Resilience et surveillance", "Resilience and monitoring", "المرونة والمراقبة")),
)


def _c(
    cid: str,
    group: str,
    fr: str,
    en: str,
    ar: str,
    families: list[str],
    level: str,
    detection: str,
    scanners: list[str],
    skill: str,
    refs: list[str],
) -> Control:
    return Control(cid, group, _n(fr, en, ar), families, level, detection, scanners, skill, refs)


CONTROLS: tuple[Control, ...] = (
    # G1 - source code and execution
    _c("C1.01", "G1", "Injection SQL/NoSQL/LDAP/XPath", "SQL/NoSQL/LDAP/XPath injection", "حقن SQL/NoSQL/LDAP/XPath",
       ["injection"], "L1", "static", ["navin_rules", "navin_dataflow", "semgrep", "bandit", "zap", "sqlmap"], "security-injection",
       ["CWE-89", "CWE-943", "CWE-90", "CWE-643", "ASVS V5", "WSTG-INPV-05"]),
    _c("C1.02", "G1", "XSS reflechi, stocke et DOM", "XSS reflected, stored and DOM", "XSS المنعكس والمخزن وDOM",
       ["injection", "frontend"], "L1", "static", ["navin_rules", "navin_dataflow", "semgrep", "zap"], "security-injection",
       ["CWE-79", "ASVS V5", "WSTG-INPV-01", "WSTG-INPV-02", "WSTG-CLNT-01"]),
    _c("C1.03", "G1", "SSTI et injection de templates", "SSTI and template injection", "حقن القوالب SSTI",
       ["injection"], "L1", "static", ["navin_rules", "navin_dataflow", "semgrep"], "security-injection",
       ["CWE-1336", "CWE-94", "WSTG-INPV-18"]),
    _c("C1.04", "G1", "Injection de commandes et RCE", "Command injection and RCE", "حقن الأوامر وتنفيذ الشيفرة عن بعد",
       ["injection", "code_execution"], "L1", "static", ["navin_rules", "navin_dataflow", "semgrep", "bandit"], "security-injection",
       ["CWE-78", "CWE-94", "ASVS V5", "WSTG-INPV-12"]),
    _c("C1.05", "G1", "Deserialisation non securisee", "Insecure deserialization", "فك التسلسل غير الآمن",
       ["code_execution"], "L1", "static", ["navin_rules", "semgrep", "bandit"], "security-sast-review",
       ["CWE-502", "ASVS V5", "WSTG-INPV-11"]),
    _c("C1.06", "G1", "Path traversal, LFI/RFI et ZIP Slip", "Path traversal, LFI/RFI and ZIP Slip", "اجتياز المسارات وLFI/RFI وZIP Slip",
       ["files"], "L1", "static", ["navin_rules", "navin_dataflow", "semgrep", "zap"], "security-sast-review",
       ["CWE-22", "CWE-98", "CWE-59", "ASVS V12", "WSTG-ATHZ-01"]),
    _c("C1.07", "G1", "Buffer overflow, use-after-free et corruption memoire", "Buffer overflow, use-after-free and memory corruption", "تجاوز المخزن المؤقت وإفساد الذاكرة",
       ["code_execution"], "L1", "static", ["navin_rules", "semgrep", "cargo_audit"], "security-sast-review",
       ["CWE-120", "CWE-416", "CWE-787", "CWE-125"]),
    _c("C1.08", "G1", "Race conditions et TOCTOU", "Race conditions and TOCTOU", "حالات التسابق وTOCTOU",
       ["business_logic", "code_execution"], "L2", "dynamic", ["navin_rules", "navin_business_logic"], "security-business-logic",
       ["CWE-362", "CWE-367", "WSTG-BUSL-07"]),
    _c("C1.09", "G1", "Validation des entrees et des sorties", "Input and output validation", "التحقق من المدخلات والمخرجات",
       ["injection", "api"], "L1", "static", ["navin_rules", "navin_dataflow", "schemathesis"], "security-sast-review",
       ["CWE-20", "CWE-116", "ASVS V5"]),
    _c("C1.10", "G1", "Secrets codes en dur et logs sensibles", "Hardcoded secrets and sensitive logs", "الأسرار المضمنة والسجلات الحساسة",
       ["secrets", "audit_logging"], "L1", "static", ["navin_rules", "gitleaks", "trivy", "trufflehog"], "security-secrets",
       ["CWE-798", "CWE-532", "ASVS V14"]),
    # G2 - identities, sessions and permissions
    _c("C2.01", "G2", "Brute force et credential stuffing", "Brute force and credential stuffing", "القوة الغاشمة وحشو بيانات الاعتماد",
       ["authentication"], "L2", "dynamic", ["navin_auth", "zap"], "security-authentication",
       ["CWE-307", "ASVS V2", "WSTG-ATHN-03"]),
    _c("C2.02", "G2", "MFA: contournement et enrolement", "MFA: bypass and enrolment", "المصادقة متعددة العوامل: التجاوز والتسجيل",
       ["authentication"], "L2", "dynamic", ["navin_auth"], "security-authentication",
       ["CWE-287", "CWE-308", "ASVS V2", "WSTG-ATHN-04"]),
    _c("C2.03", "G2", "JWT: signature, alg none, expiration, kid/jku", "JWT: signature, alg none, expiry, kid/jku", "JWT: التوقيع وalg none والانتهاء وkid/jku",
       ["authentication"], "L1", "static", ["navin_rules", "navin_auth", "semgrep"], "security-authentication",
       ["CWE-347", "CWE-345", "ASVS V3", "WSTG-SESS-10"]),
    _c("C2.04", "G2", "Sessions: fixation, rotation, invalidation, cookies", "Sessions: fixation, rotation, invalidation, cookies", "الجلسات: التثبيت والتدوير والإبطال والكوكيز",
       ["authentication"], "L2", "dynamic", ["navin_rules", "navin_auth", "zap"], "security-authentication",
       ["CWE-384", "CWE-613", "CWE-1004", "ASVS V3", "WSTG-SESS-02"]),
    _c("C2.05", "G2", "Reinitialisation de mot de passe et jetons", "Password reset and tokens", "إعادة تعيين كلمة المرور والرموز",
       ["authentication"], "L2", "dynamic", ["navin_rules", "navin_auth"], "security-authentication",
       ["CWE-640", "CWE-330", "ASVS V2", "WSTG-ATHN-09"]),
    _c("C2.06", "G2", "OAuth/OIDC: redirect_uri, state, PKCE, nonce", "OAuth/OIDC: redirect_uri, state, PKCE, nonce", "OAuth/OIDC: redirect_uri وstate وPKCE وnonce",
       ["authentication"], "L2", "dynamic", ["navin_auth", "zap"], "security-authentication",
       ["CWE-601", "CWE-352", "ASVS V3", "WSTG-ATHZ-05"]),
    _c("C2.07", "G2", "IDOR / BOLA sur chaque ressource", "IDOR / BOLA on every resource", "IDOR / BOLA على كل مورد",
       ["authorization", "api"], "L2", "dynamic", ["navin_auth", "schemathesis", "zap"], "security-authorization",
       ["CWE-639", "CWE-284", "API1:2023", "ASVS V4", "WSTG-ATHZ-04"]),
    _c("C2.08", "G2", "BFLA, RBAC et ABAC: elevation verticale", "BFLA, RBAC and ABAC: vertical escalation", "BFLA وRBAC وABAC: التصعيد العمودي",
       ["authorization"], "L3", "architecture", ["navin_auth"], "security-authorization",
       ["CWE-285", "CWE-269", "API5:2023", "ASVS V4", "WSTG-ATHZ-03"]),
    _c("C2.09", "G2", "Isolation multi-tenant: source du tenant_id, RLS, cache", "Multi-tenant isolation: tenant_id source, RLS, cache", "عزل المستأجرين: مصدر tenant_id وRLS والذاكرة المؤقتة",
       ["multitenancy"], "L3", "architecture", ["navin_tenant"], "security-multitenancy",
       ["CWE-284", "CWE-566", "ASVS V4"]),
    _c("C2.10", "G2", "Stockage des mots de passe et comptes par defaut", "Password storage and default accounts", "تخزين كلمات المرور والحسابات الافتراضية",
       ["authentication", "cryptography"], "L1", "static", ["navin_rules", "semgrep", "bandit"], "security-authentication",
       ["CWE-916", "CWE-1392", "ASVS V2", "WSTG-ATHN-02"]),
    # G3 - web site, API and business logic
    _c("C3.01", "G3", "Mass assignment et exposition excessive de donnees", "Mass assignment and excessive data exposure", "التعيين الجماعي والإفراط في كشف البيانات",
       ["api"], "L2", "dynamic", ["navin_rules", "schemathesis", "zap"], "security-api",
       ["CWE-915", "CWE-213", "API3:2023", "ASVS V13"]),
    _c("C3.02", "G3", "Limitation de debit et quotas API", "API rate limiting and quotas", "تحديد المعدل وحصص API",
       ["api", "availability"], "L2", "dynamic", ["navin_rules", "nuclei"], "security-api",
       ["CWE-770", "CWE-799", "API4:2023", "ASVS V13"]),
    _c("C3.03", "G3", "GraphQL: introspection, profondeur, batching", "GraphQL: introspection, depth, batching", "GraphQL: الاستبطان والعمق والتجميع",
       ["api", "availability"], "L2", "dynamic", ["navin_rules", "nuclei", "zap"], "security-api",
       ["CWE-200", "CWE-400", "WSTG-APIT-01"]),
    _c("C3.04", "G3", "CORS, CSP, clickjacking et en-tetes de securite", "CORS, CSP, clickjacking and security headers", "CORS وCSP وClickjacking ورؤوس الأمان",
       ["frontend", "network"], "L2", "dynamic", ["navin_rules", "navin_headers", "zap", "nuclei"], "security-frontend",
       ["CWE-942", "CWE-1021", "CWE-693", "ASVS V14", "WSTG-CLNT-09"]),
    _c("C3.05", "G3", "DOM XSS, prototype pollution et donnees sensibles cote navigateur", "DOM XSS, prototype pollution and sensitive browser-side data", "DOM XSS وتلويث النموذج الأولي والبيانات الحساسة في المتصفح",
       ["frontend"], "L1", "static", ["navin_rules", "navin_dataflow", "semgrep", "zap"], "security-frontend",
       ["CWE-79", "CWE-1321", "CWE-922", "WSTG-CLNT-01"]),
    _c("C3.06", "G3", "SSRF, host header, request smuggling, cache poisoning", "SSRF, host header, request smuggling, cache poisoning", "SSRF ورأس المضيف وتهريب الطلبات وتسميم الذاكرة المؤقتة",
       ["network"], "L2", "dynamic", ["navin_rules", "navin_dataflow", "zap", "nuclei"], "security-api",
       ["CWE-918", "CWE-444", "CWE-644", "ASVS V12", "WSTG-INPV-19"]),
    _c("C3.07", "G3", "Uploads et telechargements de fichiers", "File uploads and downloads", "تحميل الملفات وتنزيلها",
       ["files"], "L2", "dynamic", ["navin_rules", "zap"], "security-sast-review",
       ["CWE-434", "CWE-22", "ASVS V12", "WSTG-BUSL-08", "WSTG-BUSL-09"]),
    _c("C3.08", "G3", "Logique metier: prix, paiement, double depense, workflow", "Business logic: price, payment, double spend, workflow", "منطق الأعمال: السعر والدفع والإنفاق المزدوج وسير العمل",
       ["business_logic"], "L3", "architecture", ["navin_business_logic"], "security-business-logic",
       ["CWE-840", "CWE-841", "CWE-20", "WSTG-BUSL-01", "WSTG-BUSL-06"]),
    _c("C3.09", "G3", "Race conditions sur les transactions", "Race conditions on transactions", "حالات التسابق في المعاملات",
       ["business_logic"], "L4", "adversarial", ["navin_business_logic"], "security-business-logic",
       ["CWE-362", "WSTG-BUSL-07"]),
    _c("C3.10", "G3", "Webhooks et WebSocket: signature, origine, authentification", "Webhooks and WebSocket: signature, origin, authentication", "Webhooks وWebSocket: التوقيع والأصل والمصادقة",
       ["api", "authentication"], "L2", "dynamic", ["navin_rules", "zap"], "security-api",
       ["CWE-345", "CWE-346", "CWE-1385", "WSTG-CLNT-10"]),
    # G4 - infrastructure, cloud and supply chain
    _c("C4.01", "G4", "Dependances vulnerables (CVE/OSV, KEV, EPSS)", "Vulnerable dependencies (CVE/OSV, KEV, EPSS)", "التبعيات الضعيفة (CVE/OSV وKEV وEPSS)",
       ["supply_chain"], "L1", "static", ["osv_scanner", "trivy", "grype", "pip_audit", "npm_audit", "cargo_audit"], "security-supply-chain",
       ["CWE-1395", "CWE-1104", "A03:2025", "ASVS V14"]),
    _c("C4.02", "G4", "Dependency confusion, typosquatting, integrite des lockfiles", "Dependency confusion, typosquatting, lockfile integrity", "التباس التبعيات وتزوير الأسماء وسلامة ملفات القفل",
       ["supply_chain"], "L1", "static", ["navin_rules", "osv_scanner"], "security-supply-chain",
       ["CWE-427", "CWE-494", "A03:2025"]),
    _c("C4.03", "G4", "SBOM et provenance des artefacts", "SBOM and artifact provenance", "SBOM ومصدر المخرجات",
       ["supply_chain"], "L5", "continuous", ["syft", "trivy"], "security-supply-chain",
       ["NIST SSDF PS.3", "A03:2025"]),
    _c("C4.04", "G4", "CI/CD: permissions, secrets, pull_request_target, actions epinglees", "CI/CD: permissions, secrets, pull_request_target, pinned actions", "CI/CD: الصلاحيات والأسرار وpull_request_target وتثبيت الإجراءات",
       ["supply_chain", "secrets"], "L1", "static", ["navin_rules", "checkov", "gitleaks"], "security-supply-chain",
       ["CWE-829", "CWE-732", "A03:2025", "NIST SSDF PO.3"]),
    _c("C4.05", "G4", "Conteneurs: utilisateur root, images, docker.sock, capacites", "Containers: root user, images, docker.sock, capabilities", "الحاويات: المستخدم root والصور وdocker.sock والقدرات",
       ["infrastructure"], "L1", "static", ["navin_rules", "trivy", "checkov"], "security-containers",
       ["CWE-250", "CWE-269", "CIS Docker"]),
    _c("C4.06", "G4", "Kubernetes: RBAC, NetworkPolicy, securityContext, secrets", "Kubernetes: RBAC, NetworkPolicy, securityContext, secrets", "Kubernetes: RBAC وNetworkPolicy وsecurityContext والأسرار",
       ["infrastructure"], "L1", "static", ["navin_rules", "checkov", "trivy", "kube_bench"], "security-containers",
       ["CWE-250", "CWE-284", "CIS Kubernetes"]),
    _c("C4.07", "G4", "IAM cloud: privileges excessifs, cles, roles", "Cloud IAM: excessive privileges, keys, roles", "IAM السحابي: الصلاحيات المفرطة والمفاتيح والأدوار",
       ["infrastructure"], "L3", "architecture", ["checkov", "prowler"], "security-cloud",
       ["CWE-269", "CWE-732", "CIS AWS", "CIS Azure", "CIS GCP"]),
    _c("C4.08", "G4", "Stockage public et donnees exposees", "Public storage and exposed data", "التخزين العام والبيانات المكشوفة",
       ["infrastructure"], "L3", "architecture", ["checkov", "prowler", "trivy"], "security-cloud",
       ["CWE-200", "CWE-732", "CIS AWS"]),
    _c("C4.09", "G4", "Reseau: ports exposes, TLS, certificats, services non securises", "Network: exposed ports, TLS, certificates, insecure services", "الشبكة: المنافذ المكشوفة وTLS والشهادات والخدمات غير الآمنة",
       ["infrastructure", "network", "cryptography"], "L2", "dynamic", ["navin_rules", "nuclei", "testssl", "sslyze"], "security-cryptography",
       ["CWE-295", "CWE-326", "CWE-319", "ASVS V9"]),
    _c("C4.10", "G4", "Chiffrement: algorithmes, cles, rotation, au repos et en transit", "Encryption: algorithms, keys, rotation, at rest and in transit", "التشفير: الخوارزميات والمفاتيح والتدوير أثناء التخزين والنقل",
       ["cryptography"], "L1", "static", ["navin_rules", "semgrep", "bandit"], "security-cryptography",
       ["CWE-327", "CWE-330", "CWE-321", "ASVS V6"]),
    # G5 - mobile and desktop applications
    _c("C5.01", "G5", "Stockage local: jetons, bases, preferences, keychain/keystore", "Local storage: tokens, databases, preferences, keychain/keystore", "التخزين المحلي: الرموز وقواعد البيانات والتفضيلات وKeychain/Keystore",
       ["mobile_desktop", "secrets"], "L1", "static", ["navin_rules", "mobsf"], "security-mobile",
       ["CWE-922", "CWE-312", "MASVS-STORAGE"]),
    _c("C5.02", "G5", "Transport: TLS, pinning, trafic en clair", "Transport: TLS, pinning, cleartext traffic", "النقل: TLS والتثبيت وحركة المرور غير المشفرة",
       ["mobile_desktop", "cryptography"], "L1", "static", ["navin_rules", "mobsf"], "security-mobile",
       ["CWE-319", "CWE-295", "MASVS-NETWORK"]),
    _c("C5.03", "G5", "IPC, intents, deep links et schemas d'URL", "IPC, intents, deep links and URL schemes", "IPC وIntents والروابط العميقة ومخططات URL",
       ["mobile_desktop"], "L1", "static", ["navin_rules", "mobsf"], "security-mobile",
       ["CWE-926", "CWE-939", "MASVS-PLATFORM"]),
    _c("C5.04", "G5", "WebView: JavaScript, injection, fichiers locaux", "WebView: JavaScript, injection, local files", "WebView: JavaScript والحقن والملفات المحلية",
       ["mobile_desktop", "injection"], "L1", "static", ["navin_rules", "mobsf"], "security-mobile",
       ["CWE-749", "CWE-79", "MASVS-PLATFORM"]),
    _c("C5.05", "G5", "Permissions declarees et usage reel", "Declared permissions versus real usage", "الصلاحيات المعلنة مقابل الاستخدام الفعلي",
       ["mobile_desktop"], "L1", "static", ["navin_rules", "mobsf"], "security-mobile",
       ["CWE-250", "MASVS-PLATFORM"]),
    _c("C5.06", "G5", "Secrets embarques dans les binaires", "Secrets embedded in binaries", "الأسرار المضمنة في الملفات التنفيذية",
       ["mobile_desktop", "secrets"], "L1", "static", ["navin_rules", "gitleaks", "mobsf", "jadx"], "security-mobile",
       ["CWE-798", "MASVS-STORAGE"]),
    _c("C5.07", "G5", "Tauri/Electron: allowlist, CSP, commandes exposees, isolation", "Tauri/Electron: allowlist, CSP, exposed commands, isolation", "Tauri/Electron: قائمة السماح وCSP والأوامر المكشوفة والعزل",
       ["mobile_desktop", "code_execution"], "L1", "static", ["navin_rules", "navin_desktop", "cargo_audit"], "security-desktop",
       ["CWE-749", "CWE-829", "CWE-1021"]),
    _c("C5.08", "G5", "Mise a jour: signature, canal, rollback", "Updates: signature, channel, rollback", "التحديثات: التوقيع والقناة والتراجع",
       ["mobile_desktop", "supply_chain"], "L1", "static", ["navin_rules", "navin_desktop"], "security-desktop",
       ["CWE-494", "CWE-347", "MASVS-RESILIENCE"]),
    _c("C5.09", "G5", "Protection du code: debug, obfuscation, root/jailbreak", "Code protection: debug, obfuscation, root/jailbreak", "حماية الشيفرة: التصحيح والتعتيم وroot/jailbreak",
       ["mobile_desktop"], "L1", "static", ["navin_rules", "mobsf"], "security-mobile",
       ["CWE-489", "MASVS-RESILIENCE"]),
    _c("C5.10", "G5", "Journaux et captures sensibles cote client", "Sensitive client-side logs and screenshots", "السجلات ولقطات الشاشة الحساسة على جهاز العميل",
       ["mobile_desktop", "audit_logging"], "L1", "static", ["navin_rules", "mobsf"], "security-mobile",
       ["CWE-532", "CWE-200", "MASVS-PRIVACY"]),
    # G6 - AI agents, RAG and MCP
    _c("C6.01", "G6", "Prompt injection directe et indirecte", "Direct and indirect prompt injection", "حقن الأوامر المباشر وغير المباشر",
       ["ai_agents"], "L2", "dynamic", ["navin_ai", "garak", "promptfoo"], "security-ai-agents",
       ["LLM01:2025", "OWASP Agentic ASI01", "CWE-77"]),
    _c("C6.02", "G6", "Separation donnees/instructions pour le contenu externe", "Data/instruction separation for external content", "فصل البيانات عن التعليمات للمحتوى الخارجي",
       ["ai_agents"], "L1", "static", ["navin_ai"], "security-ai-agents",
       ["LLM01:2025", "LLM05:2025"]),
    _c("C6.03", "G6", "Permissions des outils et confirmation des actions sensibles", "Tool permissions and confirmation of sensitive actions", "صلاحيات الأدوات وتأكيد الإجراءات الحساسة",
       ["ai_agents", "authorization"], "L1", "static", ["navin_ai"], "security-ai-agents",
       ["LLM06:2025", "OWASP Agentic ASI02"]),
    _c("C6.04", "G6", "Excessive agency: limites, approbation humaine, budget", "Excessive agency: limits, human approval, budget", "الوكالة المفرطة: الحدود والموافقة البشرية والميزانية",
       ["ai_agents"], "L3", "architecture", ["navin_ai"], "security-ai-agents",
       ["LLM06:2025", "OWASP Agentic ASI03"]),
    _c("C6.05", "G6", "RAG poisoning et integrite du corpus", "RAG poisoning and corpus integrity", "تسميم RAG وسلامة المجموعة",
       ["ai_agents"], "L3", "architecture", ["navin_ai", "promptfoo"], "security-ai-agents",
       ["LLM08:2025", "LLM04:2025"]),
    _c("C6.06", "G6", "Exfiltration via outils, URLs, images, markdown", "Exfiltration through tools, URLs, images, markdown", "تسريب البيانات عبر الأدوات والروابط والصور وMarkdown",
       ["ai_agents"], "L2", "dynamic", ["navin_ai", "garak"], "security-ai-agents",
       ["LLM02:2025", "CWE-200"]),
    _c("C6.07", "G6", "Serveurs MCP: provenance, permissions, isolation, versions", "MCP servers: provenance, permissions, isolation, versions", "خوادم MCP: المصدر والصلاحيات والعزل والإصدارات",
       ["ai_agents", "supply_chain"], "L1", "static", ["navin_ai"], "security-ai-agents",
       ["LLM03:2025", "OWASP Agentic ASI04"]),
    _c("C6.08", "G6", "Secrets et donnees sensibles dans les prompts et les logs", "Secrets and sensitive data in prompts and logs", "الأسرار والبيانات الحساسة في الأوامر والسجلات",
       ["ai_agents", "secrets", "audit_logging"], "L1", "static", ["navin_ai", "gitleaks"], "security-ai-agents",
       ["LLM02:2025", "CWE-532"]),
    _c("C6.09", "G6", "Endpoints de modele: authentification, quotas, abus", "Model endpoints: authentication, quotas, abuse", "نقاط نهاية النموذج: المصادقة والحصص وسوء الاستخدام",
       ["ai_agents", "api"], "L2", "dynamic", ["navin_ai", "navin_rules"], "security-ai-agents",
       ["LLM10:2025", "API4:2023"]),
    _c("C6.10", "G6", "Sandbox d'execution du code genere", "Sandbox for generated code execution", "عزل تنفيذ الشيفرة المولدة",
       ["ai_agents", "code_execution"], "L1", "static", ["navin_ai", "navin_rules"], "security-ai-agents",
       ["LLM05:2025", "CWE-94"]),
    # G7 - resilience and monitoring
    _c("C7.01", "G7", "DoS applicatif, requetes couteuses, pagination", "Application DoS, expensive queries, pagination", "حجب الخدمة التطبيقي والاستعلامات المكلفة والترقيم",
       ["availability"], "L2", "dynamic", ["navin_rules", "schemathesis", "k6"], "security-api",
       ["CWE-400", "CWE-770", "API4:2023"]),
    _c("C7.02", "G7", "ReDoS et expressions regulieres", "ReDoS and regular expressions", "ReDoS والتعابير النمطية",
       ["availability"], "L1", "static", ["navin_rules", "navin_redos", "semgrep"], "security-sast-review",
       ["CWE-1333", "CWE-400"]),
    _c("C7.03", "G7", "Zip bombs et limites de taille des uploads", "Zip bombs and upload size limits", "قنابل Zip وحدود حجم التحميل",
       ["availability", "files"], "L1", "static", ["navin_rules"], "security-sast-review",
       ["CWE-409", "CWE-400"]),
    _c("C7.04", "G7", "Timeouts, quotas, circuit breakers, epuisement de ressources", "Timeouts, quotas, circuit breakers, resource exhaustion", "المهلات والحصص وقواطع الدائرة ونفاد الموارد",
       ["availability"], "L1", "static", ["navin_rules", "k6"], "security-api",
       ["CWE-400", "CWE-772"]),
    _c("C7.05", "G7", "Journalisation des evenements de securite", "Security event logging", "تسجيل الأحداث الأمنية",
       ["audit_logging"], "L3", "architecture", ["navin_rules", "navin_audit"], "security-compliance",
       ["CWE-778", "ASVS V7", "A09:2025"]),
    _c("C7.06", "G7", "Integrite des journaux et protection contre la falsification", "Log integrity and tamper protection", "سلامة السجلات والحماية من التلاعب",
       ["audit_logging"], "L3", "architecture", ["navin_audit"], "security-compliance",
       ["CWE-117", "CWE-345", "ASVS V7"]),
    _c("C7.07", "G7", "Donnees sensibles dans les journaux", "Sensitive data in logs", "البيانات الحساسة في السجلات",
       ["audit_logging", "secrets"], "L1", "static", ["navin_rules", "gitleaks"], "security-compliance",
       ["CWE-532", "ASVS V7"]),
    _c("C7.08", "G7", "Correlation et alertes", "Correlation and alerting", "الربط والتنبيهات",
       ["audit_logging"], "L5", "continuous", ["navin_audit"], "security-compliance",
       ["CWE-778", "A09:2025", "MITRE ATT&CK detection"]),
    _c("C7.09", "G7", "Sauvegardes, restauration et plan de reprise", "Backups, restore and recovery plan", "النسخ الاحتياطي والاستعادة وخطة التعافي",
       ["availability", "infrastructure"], "L3", "architecture", ["navin_audit"], "security-compliance",
       ["CIS Controls 11", "NIST SSDF"]),
    _c("C7.10", "G7", "Gestion des erreurs: pas de stack trace, fail closed", "Error handling: no stack traces, fail closed", "معالجة الأخطاء: بدون تتبع المكدس والفشل الآمن",
       ["audit_logging", "api"], "L2", "dynamic", ["navin_rules", "zap", "nuclei"], "security-api",
       ["CWE-209", "CWE-636", "A10:2025", "WSTG-ERRH-01"]),
)

CONTROL_IDS: tuple[str, ...] = tuple(c.id for c in CONTROLS)


# ---------------------------------------------------------------------------
# 14 frameworks
# ---------------------------------------------------------------------------

FRAMEWORKS: tuple[Framework, ...] = (
    Framework("owasp_top10_2025", "OWASP Top 10 2025", "OWASP",
              _n("Risques web prioritaires", "Priority web risks", "مخاطر الويب ذات الأولوية"),
              "https://owasp.org/Top10/"),
    Framework("owasp_asvs_5", "OWASP ASVS 5", "OWASP",
              _n("Exigences de verification de securite applicative", "Application security verification requirements", "متطلبات التحقق من أمان التطبيقات"),
              "https://owasp.org/www-project-application-security-verification-standard/"),
    Framework("owasp_api_top10", "OWASP API Security Top 10", "OWASP",
              _n("Failles des API", "API weaknesses", "ثغرات واجهات API"),
              "https://owasp.org/API-Security/"),
    Framework("owasp_masvs", "OWASP MASVS / MASTG", "OWASP",
              _n("Securite mobile et methodologie de test", "Mobile security and testing methodology", "أمان الجوال ومنهجية الاختبار"),
              "https://mas.owasp.org/"),
    Framework("owasp_wstg", "OWASP WSTG", "OWASP",
              _n("Tests d'intrusion web", "Web penetration testing", "اختبار اختراق الويب"),
              "https://owasp.org/www-project-web-security-testing-guide/"),
    Framework("mitre_cwe", "MITRE CWE", "MITRE",
              _n("Classification des faiblesses du code", "Classification of code weaknesses", "تصنيف نقاط الضعف في الشيفرة"),
              "https://cwe.mitre.org/"),
    Framework("cve_nvd", "CVE / NVD", "MITRE / NIST",
              _n("Vulnerabilites connues", "Known vulnerabilities", "الثغرات المعروفة"),
              "https://nvd.nist.gov/"),
    Framework("cisa_kev", "CISA KEV", "CISA",
              _n("Vulnerabilites activement exploitees", "Actively exploited vulnerabilities", "الثغرات المستغلة فعليا"),
              "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"),
    Framework("epss", "EPSS", "FIRST",
              _n("Probabilite estimee d'exploitation", "Estimated exploitation probability", "احتمال الاستغلال المقدر"),
              "https://www.first.org/epss/"),
    Framework("mitre_attack", "MITRE ATT&CK", "MITRE",
              _n("Techniques des attaquants", "Attacker techniques", "تقنيات المهاجمين"),
              "https://attack.mitre.org/"),
    Framework("owasp_llm_top10", "OWASP LLM Top 10", "OWASP",
              _n("Risques des applications LLM", "LLM application risks", "مخاطر تطبيقات النماذج اللغوية"),
              "https://genai.owasp.org/llm-top-10/"),
    Framework("owasp_agentic_ai", "OWASP Agentic AI", "OWASP",
              _n("Risques des agents autonomes", "Autonomous agent risks", "مخاطر الوكلاء المستقلين"),
              "https://genai.owasp.org/"),
    Framework("cis_benchmarks", "CIS Benchmarks", "CIS",
              _n("Durcissement OS, cloud, Docker, Kubernetes", "OS, cloud, Docker and Kubernetes hardening", "تقوية أنظمة التشغيل والسحابة وDocker وKubernetes"),
              "https://www.cisecurity.org/cis-benchmarks"),
    Framework("nist_ssdf", "NIST SSDF", "NIST",
              _n("Securisation du cycle de developpement", "Secure software development lifecycle", "تأمين دورة حياة التطوير"),
              "https://csrc.nist.gov/Projects/ssdf"),
)


# ---------------------------------------------------------------------------
# 5 levels
# ---------------------------------------------------------------------------

LEVELS: tuple[Level, ...] = (
    Level("L1", _n("Statique", "Static", "ثابت"),
          _n("Code, secrets, dependances et configuration", "Code, secrets, dependencies and configuration", "الشيفرة والأسرار والتبعيات والإعدادات"),
          ["source tree"],
          ["navin_rules", "navin_dataflow", "semgrep", "bandit", "gitleaks", "trivy", "osv_scanner", "checkov", "pip_audit", "npm_audit", "cargo_audit"]),
    Level("L2", _n("Dynamique", "Dynamic", "ديناميكي"),
          _n("Site en fonctionnement, API et authentification", "Running site, API and authentication", "الموقع العامل وواجهات API والمصادقة"),
          ["base_url", "test accounts"],
          ["navin_headers", "navin_auth", "zap", "nuclei", "schemathesis", "testssl"]),
    Level("L3", _n("Architecture", "Architecture", "المعمارية"),
          _n("Permissions, isolation multi-tenant, cloud et logique metier", "Permissions, multi-tenant isolation, cloud and business logic", "الصلاحيات وعزل المستأجرين والسحابة ومنطق الأعمال"),
          ["base_url", "accounts per role", "two tenants", "database schema", "cloud read access"],
          ["navin_tenant", "navin_business_logic", "navin_auth", "prowler", "checkov"]),
    Level("L4", _n("Adversarial", "Adversarial", "هجومي"),
          _n("Simulations controlees d'attaques, chainage de failles et tests de resistance", "Controlled attack simulations, vulnerability chaining and resistance tests", "محاكاة هجمات مضبوطة وربط الثغرات واختبارات المقاومة"),
          ["authorized targets", "isolated environment", "synthetic data", "load limits", "human approval"],
          ["navin_attack_paths", "navin_reproduction", "garak", "promptfoo", "k6", "sqlmap"]),
    Level("L5", _n("Continu", "Continuous", "مستمر"),
          _n("Controle a chaque commit, PR, deploiement et surveillance continue", "Checks on every commit, PR, deployment and continuous monitoring", "فحص كل commit وPR ونشر ومراقبة مستمرة"),
          ["CI integration", "SARIF upload", "alert channel"],
          ["navin_rules", "navin_sarif", "syft", "osv_scanner", "navin_audit"]),
)

LEVEL_IDS: tuple[str, ...] = tuple(level.id for level in LEVELS)


# ---------------------------------------------------------------------------
# 3 result statuses
# ---------------------------------------------------------------------------

STATUS_LABELS: dict[str, dict[str, str]] = {
    "potential": _n("Faiblesse potentielle", "Potential weakness", "ضعف محتمل"),
    "confirmed": _n("Vulnerabilite confirmee", "Confirmed vulnerability", "ثغرة مؤكدة"),
    "exploitable": _n("Exploitabilite demontree", "Exploitability demonstrated", "قابلية استغلال مثبتة"),
}


# ---------------------------------------------------------------------------
# 20 skills
# ---------------------------------------------------------------------------

SKILLS: tuple[SkillSpec, ...] = (
    SkillSpec("security-threat-modeling",
              _n("Identifier les actifs sensibles, les frontieres de confiance et les scenarios d'attaque",
                 "Identify sensitive assets, trust boundaries and attack scenarios",
                 "تحديد الأصول الحساسة وحدود الثقة وسيناريوهات الهجوم"),
              list(FAMILY_IDS), ["always"]),
    SkillSpec("security-sast-review",
              _n("Analyser le code, les sources d'entree et les operations dangereuses",
                 "Analyze code, input sources and dangerous operations",
                 "تحليل الشيفرة ومصادر المدخلات والعمليات الخطرة"),
              ["injection", "code_execution", "files"], ["always"]),
    SkillSpec("security-injection",
              _n("Rechercher SQLi, XSS, SSTI, XXE et injections de commandes",
                 "Find SQLi, XSS, SSTI, XXE and command injections",
                 "البحث عن SQLi وXSS وSSTI وXXE وحقن الأوامر"),
              ["injection"], ["database", "web", "api"]),
    SkillSpec("security-authentication",
              _n("Auditer JWT, OAuth, OIDC, MFA et sessions",
                 "Audit JWT, OAuth, OIDC, MFA and sessions",
                 "تدقيق JWT وOAuth وOIDC والمصادقة متعددة العوامل والجلسات"),
              ["authentication"], ["auth", "web", "api"]),
    SkillSpec("security-authorization",
              _n("Verifier IDOR, BOLA, RBAC et ABAC",
                 "Verify IDOR, BOLA, RBAC and ABAC",
                 "التحقق من IDOR وBOLA وRBAC وABAC"),
              ["authorization"], ["auth", "api", "web"]),
    SkillSpec("security-multitenancy",
              _n("Verifier l'isolation des tenants, les RLS, le cache et le stockage",
                 "Verify tenant isolation, RLS, cache and storage",
                 "التحقق من عزل المستأجرين وRLS والذاكرة المؤقتة والتخزين"),
              ["multitenancy"], ["multitenant", "database"]),
    SkillSpec("security-api",
              _n("Tester REST, GraphQL, WebSocket et webhooks",
                 "Test REST, GraphQL, WebSocket and webhooks",
                 "اختبار REST وGraphQL وWebSocket وWebhooks"),
              ["api", "network", "availability"], ["api", "graphql", "websocket"]),
    SkillSpec("security-cryptography",
              _n("Controler TLS, chiffrement, signatures et cles",
                 "Check TLS, encryption, signatures and keys",
                 "فحص TLS والتشفير والتوقيعات والمفاتيح"),
              ["cryptography"], ["always"]),
    SkillSpec("security-secrets",
              _n("Identifier les secrets exposes et leur propagation",
                 "Identify exposed secrets and their propagation",
                 "تحديد الأسرار المكشوفة وانتشارها"),
              ["secrets"], ["always"]),
    SkillSpec("security-supply-chain",
              _n("Auditer dependances, SBOM, artefacts et CI/CD",
                 "Audit dependencies, SBOM, artifacts and CI/CD",
                 "تدقيق التبعيات وSBOM والمخرجات وCI/CD"),
              ["supply_chain"], ["always"]),
    SkillSpec("security-cloud",
              _n("Analyser AWS, Azure, GCP et IAM",
                 "Analyze AWS, Azure, GCP and IAM",
                 "تحليل AWS وAzure وGCP وIAM"),
              ["infrastructure"], ["cloud", "terraform"]),
    SkillSpec("security-containers",
              _n("Auditer Docker, Kubernetes et OpenShift",
                 "Audit Docker, Kubernetes and OpenShift",
                 "تدقيق Docker وKubernetes وOpenShift"),
              ["infrastructure"], ["docker", "kubernetes"]),
    SkillSpec("security-frontend",
              _n("Verifier DOM, CSP, CORS et stockage navigateur",
                 "Verify DOM, CSP, CORS and browser storage",
                 "التحقق من DOM وCSP وCORS وتخزين المتصفح"),
              ["frontend"], ["web", "frontend"]),
    SkillSpec("security-business-logic",
              _n("Identifier les contournements de regles metier",
                 "Identify business rule bypasses",
                 "تحديد تجاوزات قواعد الأعمال"),
              ["business_logic"], ["payments", "workflow", "api"]),
    SkillSpec("security-mobile",
              _n("Auditer Android, iOS et Capacitor",
                 "Audit Android, iOS and Capacitor",
                 "تدقيق Android وiOS وCapacitor"),
              ["mobile_desktop"], ["mobile"]),
    SkillSpec("security-desktop",
              _n("Auditer Tauri, Rust, IPC et permissions",
                 "Audit Tauri, Rust, IPC and permissions",
                 "تدقيق Tauri وRust وIPC والصلاحيات"),
              ["mobile_desktop"], ["desktop"]),
    SkillSpec("security-ai-agents",
              _n("Auditer prompts, RAG, outils, MCP et exfiltration",
                 "Audit prompts, RAG, tools, MCP and exfiltration",
                 "تدقيق الأوامر وRAG والأدوات وMCP والتسريب"),
              ["ai_agents"], ["ai", "mcp"]),
    SkillSpec("security-attack-chaining",
              _n("Relier plusieurs faiblesses en scenarios de compromission",
                 "Chain several weaknesses into compromise scenarios",
                 "ربط عدة نقاط ضعف في سيناريوهات اختراق"),
              list(FAMILY_IDS), ["findings"]),
    SkillSpec("security-remediation",
              _n("Generer et valider les correctifs",
                 "Generate and validate fixes",
                 "إنشاء الإصلاحات والتحقق منها"),
              list(FAMILY_IDS), ["findings"]),
    SkillSpec("security-compliance",
              _n("Controler les exigences OWASP ASVS, CIS et NIST",
                 "Check OWASP ASVS, CIS and NIST requirements",
                 "فحص متطلبات OWASP ASVS وCIS وNIST"),
              ["audit_logging", "cryptography", "infrastructure"], ["always"]),
)

SKILL_IDS: tuple[str, ...] = tuple(s.id for s in SKILLS)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

_FAMILY_BY_ID = {f.id: f for f in FAMILIES}
_CONTROL_BY_ID = {c.id: c for c in CONTROLS}
_LEVEL_BY_ID = {level.id: level for level in LEVELS}
_SKILL_BY_ID = {s.id: s for s in SKILLS}


def family(family_id: str) -> Family:
    try:
        return _FAMILY_BY_ID[family_id]
    except KeyError:
        raise KeyError(f"unknown security family {family_id!r}; known: {', '.join(FAMILY_IDS)}") from None


def control(control_id: str) -> Control:
    try:
        return _CONTROL_BY_ID[control_id]
    except KeyError:
        raise KeyError(f"unknown security control {control_id!r}") from None


def level(level_id: str) -> Level:
    try:
        return _LEVEL_BY_ID[level_id]
    except KeyError:
        raise KeyError(f"unknown security level {level_id!r}; known: {', '.join(LEVEL_IDS)}") from None


def skill(skill_id: str) -> SkillSpec:
    try:
        return _SKILL_BY_ID[skill_id]
    except KeyError:
        raise KeyError(f"unknown security skill {skill_id!r}") from None


def controls_for_family(family_id: str) -> list[Control]:
    family(family_id)
    return [c for c in CONTROLS if family_id in c.families]


def controls_for_group(group_id: str) -> list[Control]:
    return [c for c in CONTROLS if c.group == group_id]


def controls_up_to_level(level_id: str) -> list[Control]:
    """Controls an audit at *level_id* can assess (its level and below)."""
    rank = LEVEL_IDS.index(level(level_id).id)
    return [c for c in CONTROLS if LEVEL_IDS.index(c.level) <= rank]


def controls_for_scanner(scanner_id: str) -> list[Control]:
    return [c for c in CONTROLS if scanner_id in c.scanners]


def families_for_controls(control_ids: list[str]) -> list[str]:
    seen: list[str] = []
    for cid in control_ids:
        for fid in control(cid).families:
            if fid not in seen:
                seen.append(fid)
    return seen


def catalog_payload() -> dict[str, Any]:
    """JSON-ready catalog for the API, the panel and the reports."""
    return {
        "families": [asdict(f) for f in FAMILIES],
        "groups": [asdict(g) for g in CONTROL_GROUPS],
        "controls": [asdict(c) for c in CONTROLS],
        "frameworks": [asdict(fw) for fw in FRAMEWORKS],
        "levels": [asdict(level_item) for level_item in LEVELS],
        "statuses": [{"id": key, "label": value} for key, value in STATUS_LABELS.items()],
        "skills": [asdict(s) for s in SKILLS],
        "totals": {
            "families": len(FAMILIES),
            "controls": len(CONTROLS),
            "groups": len(CONTROL_GROUPS),
            "frameworks": len(FRAMEWORKS),
            "levels": len(LEVELS),
            "skills": len(SKILLS),
        },
    }


def checklist_markdown(checked: set[str] | None = None, lang: str = "fr") -> str:
    """The checklist as copyable markdown, with the current progress."""
    done = checked or set()
    lang_key = lang if lang in {"fr", "en", "ar"} else "en"
    header = {
        "fr": "Checklist d'audit Navin Security",
        "en": "Navin Security audit checklist",
        "ar": "قائمة تدقيق Navin Security",
    }[lang_key]
    lines = [f"# {header}", f"{len(done & set(CONTROL_IDS))}/{len(CONTROLS)}", ""]
    for group in CONTROL_GROUPS:
        items = controls_for_group(group.id)
        group_done = sum(1 for c in items if c.id in done)
        lines.append(f"## {group.number}. {group.title[lang_key]} ({group_done}/{len(items)})")
        for item in items:
            mark = "x" if item.id in done else " "
            lines.append(f"- [{mark}] {item.id} {item.label[lang_key]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
