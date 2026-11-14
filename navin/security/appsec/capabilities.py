# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What Navin really does for each of the 302 referential controls.

Written from the code, not from the catalog's wishes: a control is
``automated`` only when an engine that exists in this repository targets it,
``partial`` when an engine covers part of the scenarios, ``manual`` when a
human has to review it (Navin gives the checklist, not the verdict) and
``missing`` when nothing implements it yet. The matrix never reports a control
``passed`` that is not ``automated``, and only when an engine of its list really
ran during the audit.

``legacy`` lists the controls of the 70-control catalog whose findings bear on
the control, ``finding_ids`` the exact finding ids (a trailing ``*`` is a
prefix). Both link findings to the control; ``engines`` are the engines whose
run counts as an execution of the control.
"""

from __future__ import annotations

from dataclasses import dataclass

from navin.security.appsec import referential as ref

# Built-in engines (ids as recorded in an audit's engine list).
DISC = "navin_discovery"
TM = "navin_threat_model"
MX = "navin_matrix"
RULES = "navin_rules"
DF = "navin_dataflow"
AUTH = "navin_auth"
TEN = "navin_tenant"
BL = "navin_business_logic"
DB = "navin_database"
DS = "navin_datastores"
DBE = "navin_db_ephemeral"
WEB = "navin_web_baseline"
SEMGREP, GITLEAKS, TRIVY, OSV, BANDIT = "semgrep", "gitleaks", "trivy", "osv_scanner", "bandit"
GITLEAKS_HISTORY = "gitleaks_history"
PIPA, NPMA, CARGOA = "pip_audit", "npm_audit", "cargo_audit"

MODES = ("automated", "partial", "manual", "missing")
P0, P1, P2 = "P0", "P1", "P2"
_CATEGORY_PRIORITY = {
    "AUTHZ": P0, "AUTH": P0, "SECRET": P0, "SAST": P0, "DB": P0, "SCA": P0, "API": P0, "QA": P0,
    "DISC": P1, "ARCH": P1, "BUS": P1, "CRYP": P1, "WEB": P1, "DATA": P1, "CICD": P1, "IAC": P1,
    "RUN": P1, "AI": P1, "OBS": P1, "INT": P1,
    "MOBILE": P2, "DESK": P2, "SRV": P2, "BIN": P2,
}


@dataclass(frozen=True)
class Capability:
    mode: str
    engines: tuple[str, ...] = ()
    legacy: tuple[str, ...] = ()
    finding_ids: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()  # base_url | git | accounts | database
    note: str = ""


def _c(mode: str, engines: tuple[str, ...] = (), legacy: tuple[str, ...] = (), ids: tuple[str, ...] = (),
       requires: tuple[str, ...] = (), note: str = "") -> Capability:
    assert mode in MODES, mode
    return Capability(mode, engines, legacy, ids, requires, note)


def _a(*engines: str, legacy: tuple[str, ...] = (), ids: tuple[str, ...] = (), requires: tuple[str, ...] = (), note: str = "") -> Capability:
    return _c("automated", engines, legacy, ids, requires, note)


def _p(*engines: str, legacy: tuple[str, ...] = (), ids: tuple[str, ...] = (), requires: tuple[str, ...] = (), note: str = "") -> Capability:
    return _c("partial", engines, legacy, ids, requires, note)


def _m(note: str = "") -> Capability:
    return _c("manual", note=note or "a human reviews it: Navin has no automated check for it yet")


def _x(note: str = "", requires: tuple[str, ...] = ()) -> Capability:
    return _c("missing", requires=requires, note=note or "no engine in Navin implements this control yet")


_BY_CATEGORY: dict[str, tuple[Capability, ...]] = {
    "DISC": (
        _a(DISC, note="modules, submodules, git branches and monorepo workspaces read from manifests and .git"),
        _p(DISC, note="languages, frameworks and manifests are detected; framework versions are not resolved"),
        _p(DISC, note="module kinds come from each manifest's own signals"),
        _a(DISC, note="HTTP, WebSocket, GraphQL, gRPC, worker, cron, CLI, serverless and webhook entry points"),
        _p(DISC, note="deploy targets are read from deployment files; no DNS or subdomain enumeration"),
        _p(DISC, note="databases, integrations and infrastructure files; queues and object stores only by signal"),
        _p(TM, note="trust boundaries and scenarios are derived from the surfaces, not from a traced data flow"),
        _p(DISC, note="role handling is only flagged (rbac signal); roles and journeys are not inventoried"),
        _a(DISC, note="config, env, migration and secret-like files are listed by name"),
        _x("deployed artifacts are not tied to commits"),
        _a(DISC, note="app, test, generated, example, vendored, migration, config and docs classes"),
        _a(MX, note="every control carries its scope, exclusions and evidence in the matrix"),
        _x("undocumented surfaces and orphan components are not compared with a reference"),
    ),
    "ARCH": (
        _p(DISC, TM, note="logical map from surfaces and modules; no architecture graph"),
        _p(TM, note="trust boundaries from the threat model"),
        _p(TM, note="scenarios per surface, not per feature and role"),
        _p(BL, DB, legacy=("C3.08",), ids=("navin-logic-*", "db-service-role-client"), note="client-set prices and service keys in the client"),
        _p(DB, AUTH, legacy=("C2.08",), note="deny by default is seen on database policies and route authentication only"),
        _m(), _p(TEN, DB, DBE, legacy=("C2.09",), note="tenant scoping of queries and policies; environments are not compared"),
        _m(),
        _p(AUTH, legacy=("C3.10",), ids=("navin-auth-webhook-unsigned",), note="unsigned webhooks only"),
        _m(), _m(), _m(),
    ),
    "SAST": (
        _a(DF, RULES, SEMGREP, BANDIT, legacy=("C1.01",), ids=("navin-dataflow-sql", "sql-*", "nosql-*", "go-sql-sprintf", "db-dynamic-sql"), note="Python dataflow plus line rules for the other languages"),
        _a(DF, RULES, SEMGREP, BANDIT, legacy=("C1.04",), ids=("navin-dataflow-command", "shell-*", "os-system", "child-process-exec", "node-exec", "java-exec", "php-exec", "ruby-exec", "go-exec-shell", "cs-process-start", "c-system")),
        _p(RULES, legacy=("C1.01",), ids=("ldap-injection", "xpath-injection"), note="LDAP and XPath patterns; no XQuery or search-engine rule"),
        _a(DF, RULES, SEMGREP, legacy=("C1.03",), ids=("navin-dataflow-template", "ssti", "spel-injection", "ognl-injection")),
        _p(RULES, SEMGREP, legacy=("C1.02",), ids=("inner-html", "reflected-xss", "xss-safe-filter", "rails-raw"), note="sink patterns without a JavaScript dataflow"),
        _a(DF, RULES, BANDIT, legacy=("C1.05",), ids=("navin-dataflow-deserialization", "pickle", "yaml-load", "java-deser", "php-unserialize", "ruby-marshal", "cs-binaryformatter", "node-unsafe-deser", "dotnet-typenamehandling", "jackson-default-typing")),
        _a(DF, RULES, SEMGREP, legacy=("C1.06",), ids=("navin-dataflow-path", "path-traversal*", "php-lfi")),
        _p(DF, RULES, legacy=("C3.06",), ids=("navin-dataflow-ssrf", "ssrf", "ruby-open-url", "outbound-request-tainted", "cloud-metadata-url"), note="Python dataflow plus patterns; cloud metadata only by literal address"),
        _a(RULES, SEMGREP, legacy=("C1.02",), ids=("xxe", "xml-stdlib", "java-xxe-factory")),
        _a(DF, RULES, BANDIT, legacy=("C1.04",), ids=("navin-dataflow-code", "eval-exec", "js-new-function")),
        _p(RULES, ids=("prototype-pollution",), note="one pattern rule"),
        _p(RULES, DF, ids=("redirect-input", "redirect-tainted"), note="pattern rules"),
        _p(RULES, ids=("regex-from-input",), legacy=("C7.02",), note="regexes built from input; no complexity analysis"),
        _p(RULES, legacy=("C1.07",), ids=("c-unsafe-fn", "c-format-string", "c-system", "rust-unsafe-block"), note="unsafe C functions and Rust unsafe blocks"),
        _p(RULES, legacy=("C1.08",), ids=("toctou-access",), note="one Python TOCTOU pattern"),
        _p(RULES, ids=("zip-slip", "decompression-bomb", "php-extract"), note="archive extraction patterns"),
        _p(RULES, ids=("csrf-disabled", "auth-bypass-flag", "tls-verify-off", "jwt-none"), note="security switches turned off in code"),
        _p(DF, SEMGREP, legacy=("C1.09",), note="validation is only seen as input reaching a sink"),
    ),
    "AUTH": (
        _m(), _p(RULES, legacy=("C2.10",), ids=("weak-hash-password", "weak-hash", "weak-hash-sha1", "bcrypt-low-cost", "password-plain-compare"), note="hash algorithm and cost; no policy check"),
        _m(), _m(),
        _p(AUTH, legacy=("C2.01",), ids=("navin-auth-login-throttle",), note="login routes without rate limiting"),
        _m(),
        _p(RULES, WEB, legacy=("C2.04",), ids=("insecure-cookie", "web-cookie-flags"), note="cookie attributes in code and on a live response"),
        _a(AUTH, RULES, legacy=("C2.03",), ids=("navin-auth-jwt", "jwt-*", "weak-jwt-secret")),
        _m(), _m(), _m(),
        _p(AUTH, legacy=("C2.08",), ids=("navin-auth-missing",), note="state-changing routes without an authentication check"),
        _p(RULES, DS, ids=("default-credentials", "django-insecure-key", "flask-secret", "auth-bypass-flag", "ds-db-default-password",
                           "ds-db-no-password", "ds-mysql-empty-password"),
           note="default credentials, empty database passwords and bypass flags"),
        _a(AUTH, legacy=("C3.10",), ids=("navin-auth-webhook-unsigned",)),
    ),
    "AUTHZ": (
        _x("object-level access needs two synthetic accounts on an isolated target", ("accounts",)),
        _p(AUTH, legacy=("C2.08",), ids=("navin-auth-missing",), note="routes without authentication; role checks per function are not evaluated"),
        _x("field-level access needs a running target", ("accounts",)),
        _x("horizontal escalation needs two accounts of the same level", ("accounts",)),
        _p(AUTH, legacy=("C2.08",), ids=("navin-auth-missing",), note="missing authentication only; no role matrix"),
        _p(TEN, DB, DBE, legacy=("C2.09",), ids=("navin-tenant-*", "db-*"),
           note="static tenant scoping and policy analysis, plus cross-user SELECT / INSERT / UPDATE / DELETE in a throwaway PostgreSQL"),
        _p(AUTH, legacy=("C2.08",), ids=("navin-auth-missing",), note="authentication on routes"),
        _x("exports, search and batch endpoints are not tested", ("accounts",)),
        _p(RULES, legacy=("C3.01",), ids=("mass-assignment",), note="mass assignment pattern"),
        _m(), _m(), _x("related resources are not tested", ("accounts",)), _m(),
        _x("caches and search indexes are not tested against access rules", ("accounts",)),
    ),
    "BUS": (
        _m(), _x("workflow step skipping needs scenario tests"), _x("post-closure access needs scenario tests"),
        _x("replay and idempotence need scenario tests"), _x("concurrency needs an isolated target and concurrent requests"),
        _m(), _m(), _m(),
        _a(BL, legacy=("C3.08",), ids=("navin-logic-client-price", "navin-logic-client-amount"), note="amounts and prices taken from the client"),
        _m(), _m(), _m(),
    ),
    "SECRET": (
        _a(RULES, GITLEAKS, TRIVY, legacy=("C1.10",), ids=("hardcoded-secret*", "aws-key", "github-token", "slack-token", "stripe-key", "google-api-key", "openai-key", "sendgrid-key", "db-url-credentials", "env-credential")),
        _a(GITLEAKS_HISTORY, requires=("git",), legacy=("C1.10",), note="gitleaks reads every commit of every ref"),
        _p(RULES, GITLEAKS, ids=("env-file-committed", "env-tracked", "env-credential", "hardcoded-secret-env"), note="env files and scripts; notebooks are not parsed"),
        _p(TRIVY, GITLEAKS, note="files in the tree only; no image layer or binary scan"),
        _p(RULES, legacy=("C7.07",), ids=("log-secret", "sensitive-log", "gha-echo-secret"), note="log statements and CI echo of secrets"),
        _m(), _m(),
        _p(RULES, DB, legacy=("C1.10",), ids=("hardcoded-secret*", "db-service-role-client"), note="keys in code and service keys in client code"),
        _m(),
        _a(RULES, ids=("private-key", "key-file-committed")),
        _p(RULES, ids=("db-url-credentials", "conn-string-password"), note="connection strings in the tree"),
    ),
    "CRYP": (
        _p(RULES, WEB, DS, legacy=("C4.09",), ids=("http-url", "cleartext-http", "old-tls", "tls-verify-off", "web-hsts-missing",
                                                  "web-https-redirect-missing", "ds-conn-tls-disabled", "ds-conn-cert-unverified"),
           note="TLS settings in code and database connection strings and, with a base URL, on the live site"),
        _a(RULES, SEMGREP, BANDIT, legacy=("C4.10",), ids=("weak-hash", "weak-hash-sha1", "weak-cipher", "weak-rsa-key")),
        _p(RULES, legacy=("C2.10",), ids=("weak-hash-password", "bcrypt-low-cost"), note="algorithm and cost"),
        _a(RULES, BANDIT, legacy=("C4.10",), ids=("insecure-random*", "java-weak-random")),
        _p(RULES, ids=("static-iv-key", "weak-cipher"), note="static IV and ECB; no nonce reuse analysis"),
        _m(), _m(),
        _p(RULES, ids=("tls-verify-off", "ssl-default-sockets"), note="TLS validation turned off"),
        _p(RULES, legacy=("C4.10",), ids=("tf-unencrypted", "tf-key-rotation-off"), note="Terraform encryption flags only"),
        _p(RULES, ids=("hardcoded-secret*", "private-key"), note="keys in the tree; binaries are not inspected"),
        _m(),
    ),
    "API": (
        _p(DISC, note="entry points from the code; no runtime inventory"),
        _x("routes are not compared with an OpenAPI, GraphQL or protobuf specification"),
        _p(AUTH, legacy=("C2.08",), ids=("navin-auth-missing",), note="authentication per route; authorization per method is not evaluated"),
        _p(RULES, legacy=("C3.01",), ids=("mass-assignment",), note="mass assignment only; no BOLA or BFLA test"),
        _p(DF, legacy=("C1.09",), note="input reaching a sink; no schema validation check"),
        _m(),
        _p(AUTH, legacy=("C3.02",), ids=("navin-auth-login-throttle",), note="login rate limiting only"),
        _p(RULES, WEB, legacy=("C3.04",), ids=("cors-wildcard", "cors-reflect", "web-cors-reflect", "web-cors-wildcard-credentials"), note="CORS in code and on a live response"),
        _p(WEB, RULES, legacy=("C7.10",), ids=("web-error-leak", "debug-true"), note="debug mode and error pages"),
        _m(),
        _p(AUTH, legacy=("C3.10",), ids=("navin-auth-webhook-unsigned",), note="signature presence; replay protection is not checked"),
        _m(),
        _p(WEB, ids=("web-admin-exposed", "web-debug-exposed"), requires=("base_url",), note="exposed admin and debug paths on a live target"),
        _a(DF, RULES, legacy=("C3.06",), ids=("navin-dataflow-ssrf", "ssrf", "outbound-request-tainted")),
    ),
    "WEB": (
        _p(WEB, requires=("base_url",), note="a bounded set of paths, not a crawl"),
        _a(WEB, requires=("base_url",), ids=("web-hsts-missing", "web-https-redirect-missing")),
        _a(WEB, requires=("base_url",), ids=("web-csp-missing", "web-csp-unsafe", "web-xcto-missing", "web-framing-missing")),
        _p(WEB, requires=("base_url",), ids=("web-cookie-flags",), note="cookie attributes on the probed responses"),
        _x("active XSS payloads are not sent", ("base_url",)),
        _x("CSRF on state-changing actions is not tested", ("base_url",)),
        _x("active injection and traversal payloads are not sent", ("base_url",)),
        _x("uploads are not tested", ("base_url",)),
        _x("downloads and private files are not tested", ("base_url",)),
        _p(WEB, requires=("base_url",), ids=("web-cors-reflect", "web-cors-wildcard-credentials"), note="CORS only; no postMessage or iframe test"),
        _p(WEB, requires=("base_url",), ids=("web-framing-missing",), note="framing headers; no open-redirect test"),
        _p(WEB, requires=("base_url",), ids=("web-cache-private",), note="cache headers on the probed responses"),
        _a(WEB, requires=("base_url",), ids=("web-sourcemap-exposed", "web-debug-exposed", "web-server-banner")),
        _p(WEB, requires=("base_url",), ids=("web-admin-exposed",), note="a few well-known admin paths"),
    ),
    "DB": (
        _p(DB, DBE, DS, DISC, note=("PostgreSQL objects declared in the migrations; MySQL/MariaDB, SQL Server, Oracle, MongoDB, Redis, "
                                   "Elasticsearch, Cassandra, Firebase, DynamoDB, Cosmos DB and others detected from drivers, configs "
                                   "and scripts; the live catalog of a running database needs a connection")),
        _p(DB, DBE, DS, ids=("db-grant-broad", "db-bypassrls-role", "ds-sql-grant-all-global", "ds-sql-high-privilege",
                             "ds-sql-grant-public", "ds-mysql-any-host"),
           note="grants and roles in PostgreSQL, MySQL/MariaDB, SQL Server and Oracle scripts"),
        _p(DS, DB, ids=("ds-sql-grant-all-global", "ds-sql-high-privilege", "ds-mssql-sa-login", "ds-mssql-trustworthy", "db-bypassrls-role"),
           note="administrator grants and logins; separate read, write and admin accounts are not verified"),
        _p(DF, DB, RULES, legacy=("C1.01",), ids=("db-dynamic-sql", "navin-dataflow-sql", "sql-*", "nosql-*", "go-sql-sprintf"),
           note="SQL and NoSQL (MongoDB $where, operators) queries built from input; ORM-generated queries are not analysed"),
        _p(DS, RULES, ids=("ds-db-port-exposed", "ds-db-bind-all", "ds-redis-protected-mode-off", "ds-redis-no-auth", "ds-mongo-no-auth",
                           "ds-search-security-off", "ds-cassandra-no-auth", "ds-pg-hba-open", "ds-mysql-skip-grant-tables",
                           "ds-db-default-password", "ds-db-no-password",
                           "tf-publicly-accessible", "tf-public-db", "bind-all", "bind-all-interfaces"),
           note="published ports, bind addresses, default passwords and unauthenticated servers in Compose, server configs and Terraform; no network scan"),
        _p(DS, ids=("ds-conn-tls-disabled", "ds-conn-cert-unverified"),
           note="connection strings (PostgreSQL, MySQL, SQL Server, MongoDB, Redis, JDBC) and server TLS settings; a live server is not probed"),
        _a(DB, DBE, DS, legacy=("C2.09",), ids=("db-rls-disabled", "ds-rls-policy-off", "ds-firebase-open-rules"),
           note="PostgreSQL / Supabase RLS, SQL Server and Oracle security policies, Firebase security rules"),
        _p(DBE, DB, DS, legacy=("C2.09",), ids=("db-policy-permissive-true", "db-rls-no-policy", "ds-firebase-open-rules", "db-rls-anon-read",
                                              "db-rls-cross-tenant-read"),
           note=("SELECT is run in a throwaway PostgreSQL as anon and as two synthetic users on every table whose policies use "
                 "auth.uid(); membership-based tenants are not simulated and other engines are analysed statically")),
        _p(DB, DBE, DS, legacy=("C2.09",), ids=("db-policy-permissive-true", "db-policy-tenant-change", "ds-firebase-open-rules",
                                              "db-rls-cross-tenant-insert"),
           note="INSERT of a row owned by another user is run in the throwaway PostgreSQL; other engines are analysed statically"),
        _p(DB, DBE, legacy=("C2.09",), ids=("db-policy-tenant-change", "db-policy-no-tenant-scope", "db-rls-cross-tenant-update"),
           note="UPDATE of another user's row and owner change of one's own row are run in the throwaway PostgreSQL"),
        _p(DB, DBE, DS, ids=("db-policy-permissive-true", "db-grant-broad", "ds-firebase-open-rules", "db-rls-cross-tenant-delete"),
           note="DELETE of another user's rows is run in the throwaway PostgreSQL"),
        _p(DB, DBE, ids=("db-permissive-or", "db-policy-no-tenant-scope"), note="OR and AND semantics of PostgreSQL policies"),
        _p(DB, DBE, DS, ids=("db-bypassrls-role", "ds-rls-policy-off"), note="BYPASSRLS and superuser roles, disabled security policies; FORCE ROW LEVEL SECURITY is not checked"),
        _a(DB, DBE, DS, ids=("db-security-definer-search-path", "db-security-definer-exposed", "ds-definer-routine", "ds-mssql-xp-cmdshell",
                             "ds-mssql-clr-unsafe"),
           note="PostgreSQL SECURITY DEFINER, MySQL SQL SECURITY DEFINER, SQL Server EXECUTE AS OWNER, Oracle AUTHID DEFINER"),
        _a(DB, DBE, ids=("db-view-bypasses-rls",)),
        _p(DB, DS, ids=("db-service-role-client", "ds-mssql-sa-login"), note="service keys in client code and administrator logins in connection strings"),
        _p(DB, DBE, DS, ids=("db-grant-broad", "db-bypassrls-role", "ds-sql-grant-public", "ds-mysql-empty-password", "ds-definer-routine"),
           note="grants, roles and accounts in migrations; default privileges are not parsed"),
        _m(),
    ),
    "DATA": (
        _p(DISC, note="PII-like field names in sampled code and SQL"),
        _m(), _m(), _m(), _m(), _m(),
        _p(TEN, legacy=("C2.09",), ids=("navin-tenant-*",), note="tenant scoping of queries"),
        _p(RULES, legacy=("C7.07",), ids=("log-secret", "sensitive-log"), note="secrets and tokens in log statements"),
        _m(), _m(),
    ),
    "SCA": (
        _p(DISC, note="dependency names from the manifests; transitive dependencies need a scanner's resolution"),
        _x("no SBOM generator (Syft or Trivy SBOM) is integrated"),
        _a(OSV, TRIVY, PIPA, NPMA, CARGOA, legacy=("C4.01",), ids=("dep:*",), note="known vulnerabilities; the scanner that ran is named in the evidence"),
        _x("CISA KEV and EPSS are not queried"),
        _x("abandoned or unmaintained packages are not detected"),
        _p(RULES, OSV, legacy=("C4.02",), ids=("no-lockfile",), note="missing lockfile only; no typosquatting or confusion check"),
        _p(RULES, legacy=("C4.02",), ids=("no-lockfile",), note="presence of a lockfile; consistency is not checked"),
        _x("floating versions are not detected"),
        _x("install scripts and hooks are not inspected"),
        _x("package provenance, signatures and attestations are not verified"),
        _x("licences are not scanned"),
        _p(DISC, note="vendored directories and submodules are listed; their content is not scanned"),
        _x("deployed versions are not monitored for new vulnerabilities"),
    ),
    "CICD": (
        _x("branch protection needs the forge API"), _x("CODEOWNERS and approvals are not read"),
        _a(RULES, legacy=("C4.04",), ids=("gha-expression-injection", "gha-injection", "gha-pr-target", "gha-pr-target-checkout")),
        _p(RULES, legacy=("C4.04",), ids=("gha-write-all",), note="GitHub Actions token permissions only"),
        _m(),
        _p(RULES, legacy=("C4.04",), ids=("gha-pr-target", "gha-pr-target-checkout"), note="pull_request_target workflows only"),
        _x("commit and release signatures are not verified"), _x("build provenance and artifact signatures are not verified"),
        _m(), _m(), _m(), _x("the deployed version is not matched with the audited commit"),
    ),
    "IAC": (
        _p(RULES, TRIVY, legacy=("C4.05",), ids=("docker-latest*", "docker-add-*", "docker-curl-pipe-sh"), note="Dockerfile rules; image vulnerabilities need a scanner run"),
        _a(RULES, legacy=("C4.05",), ids=("docker-root-user", "k8s-run-as-root", "k8s-root", "k8s-privileged", "compose-privileged", "compose-cap-admin")),
        _p(RULES, legacy=("C4.05",), ids=("k8s-privilege-escalation", "k8s-no-securitycontext", "k8s-hostpath"), note="securityContext flags"),
        _p(RULES, TRIVY, legacy=("C4.10",), ids=("tf-*",), note="Terraform rules; CloudFormation and OpenTofu are not parsed"),
        _p(RULES, legacy=("C4.06",), ids=("k8s-*",), note="manifest rules; Helm templates and Kustomize overlays are not rendered"),
        _p(RULES, legacy=("C4.06",), ids=("k8s-cluster-admin",), note="cluster-admin bindings"),
        _p(RULES, ids=("tf-open-ingress", "tf-public-ingress"), note="open ingress rules; NetworkPolicy presence is not checked"),
        _p(RULES, ids=("k8s-secret-manifest", "k8s-env-secret"), note="secrets in manifests"),
        _x("admission policies and Pod Security Standards are not checked"),
        _p(RULES, ids=("k8s-no-limits",), note="missing resource limits"),
        _a(RULES, DS, legacy=("C4.08",), ids=("tf-public-acl", "tf-public-s3", "tf-public-access-block-off", "tf-publicly-accessible", "tf-public-db",
                                              "ds-db-port-exposed")),
        _p(RULES, ids=("tf-iam-wildcard",), note="IAM wildcards in Terraform"),
        _p(RULES, ids=("tf-imdsv1", "cloud-metadata-url"), note="IMDSv1 and metadata literals"),
        _m(), _x("drift between declared and deployed configuration is not computed"),
    ),
    "RUN": (
        _p(RULES, legacy=("C7.10",), ids=("debug-true", "django-insecure-key", "pdb-trace"), note="debug flags in code"),
        _x("effective configuration is not read from a running target"),
        _m(),
        _p(WEB, RULES, ids=("spring-actuator", "web-debug-exposed", "web-admin-exposed"), note="actuator and debug endpoints"),
        _p(RULES, ids=("chmod-777", "mktemp"), note="permissive modes and temporary files"),
        _m(), _m(),
        _p(RULES, ids=("k8s-no-limits",), note="missing resource limits"),
        _m(), _m(), _m(),
    ),
    "MOBILE": (
        _x("no mobile analyzer (MobSF or MASTG) is integrated"), _x("no iOS analyzer is integrated"),
        _x("local storage of mobile apps is not analysed"), _x("Keychain and Keystore usage is not analysed"),
        _x("local data protection is not analysed"),
        _p(RULES, legacy=("C4.09",), ids=("cleartext-http", "tls-verify-off", "http-url"), note="TLS settings in source code; no ATS or network security config"),
        _x("deep links, intents and WebViews are not analysed"), _x("biometric flows are not analysed"),
        _x("APK and IPA files are not scanned"), _x("package tampering resilience is not tested"),
        _x("mobile update and signing flows are not analysed"), _x("mobile SDK privacy is not analysed"),
    ),
    "DESK": (
        _x("installers and binaries are not scanned"), _x("code signing of an audited desktop app is not verified"),
        _x("update substitution and downgrade are not tested"),
        _p(RULES, ids=("chmod-777",), note="permissive file modes"),
        _m(),
        _p(RULES, ids=("bind-all", "bind-all-interfaces"), note="services bound to all interfaces"),
        _x("Electron and Tauri configurations are not analysed"),
        _p(DF, RULES, legacy=("C1.04",), ids=("navin-dataflow-command", "shell-true"), note="command injection patterns"),
        _m(), _m(),
        _p(RULES, legacy=("C7.07",), ids=("sensitive-log",), note="sensitive data in log statements"),
    ),
    "AI": (
        _p(DISC, note="AI components are detected from dependencies and agent markers"),
        _x("prompt injection tests need a sandboxed agent run"), _x("instruction and data boundary tests need a sandboxed agent run"),
        _x("model output validation before commands, SQL and APIs is not traced"),
        _m(), _m(), _m(),
        _p(RULES, ids=("openai-key",), note="LLM provider keys committed; prompts and traces are not scanned"),
        _x("memory and embedding isolation is not tested"), _x("document and index poisoning is not tested"),
        _m(), _m(), _x("MCP tool poisoning and shadowing are not tested"), _x("exfiltration through tools and URLs is not tested"),
        _m(), _m(), _m(),
    ),
    "OBS": (
        _m(), _m(), _m(), _m(),
        _p(RULES, legacy=("C7.07",), ids=("log-secret", "sensitive-log", "log-injection"), note="log statements"),
        _m(),
        _p(WEB, RULES, legacy=("C7.10",), ids=("web-error-leak", "debug-true"), note="debug mode and error pages"),
        _p(RULES, ids=("tf-skip-snapshot",), note="database snapshot flags; restore is not tested"),
        _m(), _m(),
    ),
    "SRV": (
        _m(), _p(RULES, ids=("tf-iam-wildcard",), note="IAM wildcards in Terraform"), _m(), _m(),
        _p(RULES, ids=("hardcoded-secret-env",), note="secrets in environment files"),
        _m(), _m(), _x("layer and package permissions are not checked"), _x("timeout, retry and dead-letter settings are not checked"),
    ),
    "BIN": (
        _p(RULES, CARGOA, legacy=("C1.07",), ids=("c-unsafe-fn", "c-format-string", "c-system", "rust-unsafe-block"), note="unsafe C functions and Rust unsafe blocks"),
        _x("compiler hardening flags are not checked"), _x("firmware provenance is not verified"), _x("firmware update integrity is not verified"),
        _m(), _x("WebAssembly modules are not analysed"), _m(),
        _p(RULES, GITLEAKS, ids=("private-key", "key-file-committed"), note="keys in the tree; firmware images are not unpacked"),
        _x("no fuzzing harness is integrated"),
    ),
    "INT": (
        _p(DISC, note="third-party SDKs from manifests and imports"),
        _m(),
        _p(AUTH, legacy=("C3.10",), ids=("navin-auth-webhook-unsigned",), note="signature presence on inbound webhooks"),
        _p(DF, RULES, legacy=("C3.06",), ids=("navin-dataflow-ssrf", "ssrf"), note="outbound requests built from input"),
        _m(), _m(),
        _p(TEN, legacy=("C2.09",), ids=("navin-tenant-*",), note="tenant scoping of queries"),
        _m(), _m(), _m(),
    ),
    "QA": (
        _a(MX), _a(MX), _a(MX), _a(MX), _a(MX),
        _p(MX, note="critical findings are listed with their evidence level; safe reproduction runs on demand"),
        _m(), _a(MX),
        _p(MX, note="the verification engine adds regression tests and rescans on demand, not on every audit"),
        _x("no pull request or release hook re-runs the audit"),
        _a(MX), _p(MX, note="machine-readable matrix and gaps; owners are not recorded"),
    ),
}


def _build() -> dict[str, Capability]:
    out: dict[str, Capability] = {}
    for cat in ref.CATEGORIES:
        items = _BY_CATEGORY[cat.id]
        assert len(items) == len(cat.tasks), (cat.id, len(items), len(cat.tasks))
        for control, cap in zip(ref.controls_of(cat.id), items):
            out[control.id] = cap
    return out


CAPABILITIES: dict[str, Capability] = _build()


def capability(control_id: str) -> Capability:
    return CAPABILITIES[control_id]


def priority(control_id: str) -> str:
    return _CATEGORY_PRIORITY[ref.control(control_id).category]


_BUILT_IN = frozenset({DISC, TM, MX, RULES, DF, AUTH, TEN, BL, DB, DS, DBE, WEB, "navin", "navin-deep", "navin_rules"})


def finding_matches(cap: Capability, finding_id: str, finding_controls: list[str], source: str = "") -> bool:
    """Whether a finding bears on the control.

    Built-in engines are matched by finding id (exact, or prefix with a trailing ``*``): their ids are stable
    and precise. External scanners name findings after their own rules, so theirs are matched through the
    legacy catalog controls the correlator gave them; a control with no id list also matches that way.
    """
    for pattern in cap.finding_ids:
        if pattern.endswith("*"):
            if finding_id.startswith(pattern[:-1]):
                return True
        elif finding_id == pattern:
            return True
    if cap.finding_ids and source in _BUILT_IN:
        return False
    return bool(set(cap.legacy) & set(finding_controls))


def counts() -> dict[str, int]:
    out = dict.fromkeys(MODES, 0)
    for cap in CAPABILITIES.values():
        out[cap.mode] += 1
    return out
