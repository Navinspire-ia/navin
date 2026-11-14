# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Project discovery for the security audit.

Reads manifests, configuration and a bounded sample of the tree to say what
the project is made of: languages, frameworks, databases, auth mechanisms,
API styles, infrastructure, native targets, AI components, and the attack
surfaces that follow. Every later decision (threat model, skills, scanners,
levels reachable) derives from this record, so it only states what it saw.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_MAX_FILES = 4000
_MAX_READ = 200_000
_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "target", "__pycache__",
    ".next", ".nuxt", "coverage", ".tox", ".mypy_cache", ".pytest_cache", "vendor",
}

_LANG_BY_EXT = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".jsx": "javascript", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".swift": "swift", ".php": "php", ".rb": "ruby", ".cs": "csharp", ".c": "c",
    ".cpp": "cpp", ".h": "c", ".sql": "sql", ".dart": "dart",
}

# Package name (npm / pip / cargo) -> (kind, label)
_PACKAGE_SIGNALS: dict[str, tuple[str, str]] = {
    "react": ("frameworks", "react"), "next": ("frameworks", "nextjs"), "vue": ("frameworks", "vue"),
    "nuxt": ("frameworks", "nuxt"), "@angular/core": ("frameworks", "angular"), "svelte": ("frameworks", "svelte"),
    "express": ("frameworks", "express"), "fastify": ("frameworks", "fastify"), "koa": ("frameworks", "koa"),
    "@nestjs/core": ("frameworks", "nestjs"), "hono": ("frameworks", "hono"),
    "fastapi": ("frameworks", "fastapi"), "django": ("frameworks", "django"), "flask": ("frameworks", "flask"),
    "starlette": ("frameworks", "starlette"), "aiohttp": ("frameworks", "aiohttp"), "tornado": ("frameworks", "tornado"),
    "actix-web": ("frameworks", "actix"), "axum": ("frameworks", "axum"), "rocket": ("frameworks", "rocket"),
    "gin": ("frameworks", "gin"), "spring-boot": ("frameworks", "spring"),
    "pg": ("databases", "postgresql"), "postgres": ("databases", "postgresql"), "psycopg2": ("databases", "postgresql"),
    "psycopg": ("databases", "postgresql"), "asyncpg": ("databases", "postgresql"), "sqlx": ("databases", "sql"),
    "mysql2": ("databases", "mysql"), "pymysql": ("databases", "mysql"), "mongoose": ("databases", "mongodb"),
    "pymongo": ("databases", "mongodb"), "mongodb": ("databases", "mongodb"), "redis": ("databases", "redis"),
    "ioredis": ("databases", "redis"), "sqlalchemy": ("databases", "sql"), "prisma": ("databases", "sql"),
    "@prisma/client": ("databases", "sql"), "typeorm": ("databases", "sql"), "sequelize": ("databases", "sql"),
    "knex": ("databases", "sql"), "drizzle-orm": ("databases", "sql"), "sqlite3": ("databases", "sqlite"),
    "better-sqlite3": ("databases", "sqlite"), "@supabase/supabase-js": ("databases", "supabase"),
    "supabase": ("databases", "supabase"), "firebase": ("databases", "firebase"), "firebase-admin": ("databases", "firebase"),
    "mysql": ("databases", "mysql"), "mysql-connector-python": ("databases", "mysql"), "mysqlclient": ("databases", "mysql"),
    "aiomysql": ("databases", "mysql"), "mariadb": ("databases", "mysql"), "mysql-connector-j": ("databases", "mysql"),
    "mssql": ("databases", "mssql"), "tedious": ("databases", "mssql"), "pyodbc": ("databases", "mssql"), "pymssql": ("databases", "mssql"),
    "mssql-jdbc": ("databases", "mssql"), "microsoft.data.sqlclient": ("databases", "mssql"), "system.data.sqlclient": ("databases", "mssql"),
    "oracledb": ("databases", "oracle"), "cx_oracle": ("databases", "oracle"), "cx-oracle": ("databases", "oracle"), "ojdbc8": ("databases", "oracle"),
    "ojdbc11": ("databases", "oracle"), "oracle.manageddataaccess.core": ("databases", "oracle"),
    "postgresql": ("databases", "postgresql"), "npgsql": ("databases", "postgresql"), "pgx": ("databases", "postgresql"),
    "motor": ("databases", "mongodb"), "mongoengine": ("databases", "mongodb"), "mongodb-driver-sync": ("databases", "mongodb"),
    "mongodb.driver": ("databases", "mongodb"), "redis-py": ("databases", "redis"), "jedis": ("databases", "redis"),
    "lettuce-core": ("databases", "redis"), "stackexchange.redis": ("databases", "redis"),
    "cassandra-driver": ("databases", "cassandra"), "scylla-driver": ("databases", "cassandra"),
    "elasticsearch": ("databases", "elasticsearch"), "@elastic/elasticsearch": ("databases", "elasticsearch"),
    "opensearch-py": ("databases", "elasticsearch"), "@opensearch-project/opensearch": ("databases", "elasticsearch"),
    "neo4j": ("databases", "neo4j"), "neo4j-driver": ("databases", "neo4j"),
    "@aws-sdk/client-dynamodb": ("databases", "dynamodb"), "@aws-sdk/lib-dynamodb": ("databases", "dynamodb"), "pynamodb": ("databases", "dynamodb"),
    "@azure/cosmos": ("databases", "cosmosdb"), "azure-cosmos": ("databases", "cosmosdb"),
    "@google-cloud/firestore": ("databases", "firebase"), "google-cloud-firestore": ("databases", "firebase"),
    "nano": ("databases", "couchdb"), "couchdb": ("databases", "couchdb"), "clickhouse-connect": ("databases", "clickhouse"),
    "@clickhouse/client": ("databases", "clickhouse"), "clickhouse-driver": ("databases", "clickhouse"),
    "influxdb-client": ("databases", "influxdb"), "@influxdata/influxdb-client": ("databases", "influxdb"),
    "duckdb": ("databases", "duckdb"), "snowflake-connector-python": ("databases", "snowflake"), "snowflake-sdk": ("databases", "snowflake"),
    "trino": ("databases", "trino"), "cockroachdb": ("databases", "cockroachdb"), "sqlalchemy-cockroachdb": ("databases", "cockroachdb"),
    "hibernate-core": ("databases", "sql"), "spring-boot-starter-data-jpa": ("databases", "sql"), "gorm": ("databases", "sql"),
    "entityframeworkcore": ("databases", "sql"), "microsoft.entityframeworkcore": ("databases", "sql"), "doctrine/orm": ("databases", "sql"),
    "peewee": ("databases", "sql"), "tortoise-orm": ("databases", "sql"), "mikro-orm": ("databases", "sql"), "@mikro-orm/core": ("databases", "sql"),
    "jsonwebtoken": ("auth", "jwt"), "jose": ("auth", "jwt"), "pyjwt": ("auth", "jwt"), "python-jose": ("auth", "jwt"),
    "passport": ("auth", "passport"), "next-auth": ("auth", "oauth"), "@auth/core": ("auth", "oauth"),
    "authlib": ("auth", "oauth"), "oauthlib": ("auth", "oauth"), "django-allauth": ("auth", "oauth"),
    "bcrypt": ("auth", "password"), "argon2-cffi": ("auth", "password"), "passlib": ("auth", "password"),
    "express-session": ("auth", "session"), "@clerk/nextjs": ("auth", "clerk"), "keycloak-js": ("auth", "oidc"),
    "graphql": ("api_styles", "graphql"), "apollo-server": ("api_styles", "graphql"), "@apollo/server": ("api_styles", "graphql"),
    "strawberry-graphql": ("api_styles", "graphql"), "graphene": ("api_styles", "graphql"), "ariadne": ("api_styles", "graphql"),
    "ws": ("api_styles", "websocket"), "socket.io": ("api_styles", "websocket"), "websockets": ("api_styles", "websocket"),
    "@grpc/grpc-js": ("api_styles", "grpc"), "grpcio": ("api_styles", "grpc"),
    "stripe": ("payments", "stripe"), "paypal-rest-sdk": ("payments", "paypal"), "braintree": ("payments", "braintree"),
    "openai": ("ai", "llm"), "anthropic": ("ai", "llm"), "langchain": ("ai", "llm"), "@langchain/core": ("ai", "llm"),
    "llamaindex": ("ai", "rag"), "llama-index": ("ai", "rag"), "chromadb": ("ai", "rag"), "pinecone-client": ("ai", "rag"),
    "@pinecone-database/pinecone": ("ai", "rag"), "qdrant-client": ("ai", "rag"), "weaviate-client": ("ai", "rag"),
    "mcp": ("ai", "mcp"), "@modelcontextprotocol/sdk": ("ai", "mcp"), "litellm": ("ai", "llm"), "transformers": ("ai", "llm"),
    "@capacitor/core": ("native", "capacitor"), "react-native": ("native", "react_native"), "expo": ("native", "expo"),
    "electron": ("native", "electron"), "@tauri-apps/api": ("native", "tauri"), "tauri": ("native", "tauri"),
    "multer": ("features", "file_upload"), "formidable": ("features", "file_upload"), "python-multipart": ("features", "file_upload"),
    "pickle": ("features", "deserialization"), "pyyaml": ("features", "yaml"), "celery": ("features", "task_queue"),
    "bullmq": ("features", "task_queue"), "nodemailer": ("features", "email"), "helmet": ("hardening", "helmet"),
    "express-rate-limit": ("hardening", "rate_limit"), "slowapi": ("hardening", "rate_limit"), "cors": ("features", "cors"),
    "fastapi-cors": ("features", "cors"), "django-cors-headers": ("features", "cors"),
}

_FILE_SIGNALS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(^|/)dockerfile(\.|$)", re.I), "infrastructure", "docker"),
    (re.compile(r"(^|/)(docker-)?compose[^/]*\.ya?ml$", re.I), "infrastructure", "compose"),
    (re.compile(r"\.tf$"), "infrastructure", "terraform"),
    (re.compile(r"(^|/)(k8s|kubernetes|manifests|helm|charts)/.*\.ya?ml$", re.I), "infrastructure", "kubernetes"),
    (re.compile(r"(^|/)Chart\.yaml$"), "infrastructure", "helm"),
    (re.compile(r"(^|/)\.github/workflows/.*\.ya?ml$"), "infrastructure", "github_actions"),
    (re.compile(r"(^|/)\.gitlab-ci\.ya?ml$"), "infrastructure", "gitlab_ci"),
    (re.compile(r"(^|/)Jenkinsfile$"), "infrastructure", "jenkins"),
    (re.compile(r"(^|/)serverless\.ya?ml$"), "infrastructure", "serverless"),
    (re.compile(r"(^|/)cdk\.json$"), "infrastructure", "aws_cdk"),
    (re.compile(r"(^|/)src-tauri/tauri\.conf\.json$"), "native", "tauri"),
    (re.compile(r"(^|/)capacitor\.config\.(ts|js|json)$"), "native", "capacitor"),
    (re.compile(r"(^|/)AndroidManifest\.xml$"), "native", "android"),
    (re.compile(r"(^|/)Info\.plist$"), "native", "ios"),
    (re.compile(r"(^|/)pubspec\.yaml$"), "native", "flutter"),
    (re.compile(r"(^|/)openapi\.(ya?ml|json)$|(^|/)swagger\.(ya?ml|json)$", re.I), "api_styles", "openapi"),
    (re.compile(r"\.graphql$|\.gql$"), "api_styles", "graphql"),
    (re.compile(r"\.proto$"), "api_styles", "grpc"),
    (re.compile(r"(^|/)supabase/.*\.sql$"), "databases", "supabase"),
    (re.compile(r"(^|/)(migrations?|alembic)/.*\.(sql|py)$"), "databases", "sql"),
    (re.compile(r"(^|/)redis[^/]*\.conf$", re.I), "databases", "redis"),
    (re.compile(r"(^|/)(mongod|mongodb)\.(conf|ya?ml)$|(^|/)mongod[-_.][^/]*\.conf$", re.I), "databases", "mongodb"),
    (re.compile(r"(^|/)(my|mysqld|mariadb)[^/]*\.cnf$", re.I), "databases", "mysql"),
    (re.compile(r"(^|/)(pg_hba|postgresql)[^/]*\.conf$", re.I), "databases", "postgresql"),
    (re.compile(r"(^|/)(elasticsearch|opensearch)\.ya?ml$", re.I), "databases", "elasticsearch"),
    (re.compile(r"(^|/)cassandra\.ya?ml$|\.cql$", re.I), "databases", "cassandra"),
    (re.compile(r"(^|/)(firestore|storage)[^/]*\.rules$|(^|/)database\.rules\.json$", re.I), "databases", "firebase"),
    (re.compile(r"\.(mdf|ldf)$|(^|/)[^/]*\.sqlproj$", re.I), "databases", "mssql"),
    (re.compile(r"(^|/)\.mcp\.json$"), "ai", "mcp"),
    (re.compile(r"(^|/)\.env(\..*)?$"), "features", "env_files"),
    (re.compile(r"(^|/)SKILL\.md$"), "ai", "agent_skills"),
]

_TEXT_SIGNALS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\btenant_id\b|\borganization_id\b|\bworkspace_id\b|\baccount_id\b", re.I), "features", "multitenant"),
    (re.compile(r"ROW LEVEL SECURITY|CREATE POLICY", re.I), "features", "rls"),
    (re.compile(r"CREATE\s+SECURITY\s+POLICY|DBMS_RLS\.ADD_POLICY", re.I), "features", "rls"),
    (re.compile(r"(?i)\b(jdbc:sqlserver:|sqlserver://|Server=tcp:|\bNVARCHAR\(MAX\))"), "databases", "mssql"),
    (re.compile(r"(?i)\b(jdbc:oracle:|VARCHAR2\s*\()"), "databases", "oracle"),
    (re.compile(r"(?i)\b(jdbc:mysql:|mysql://|mariadb://)"), "databases", "mysql"),
    (re.compile(r"(?i)\b(jdbc:postgresql:|postgres(ql)?://)"), "databases", "postgresql"),
    (re.compile(r"(?i)\bmongodb(\+srv)?://"), "databases", "mongodb"),
    (re.compile(r"(?i)\brediss?://"), "databases", "redis"),
    (re.compile(r"\bprice\b.*\b(total|amount)\b|\bcheckout\b|\binvoice\b", re.I), "features", "payments_flow"),
    (re.compile(r"webhook", re.I), "features", "webhooks"),
    (re.compile(r"\brole\b.*\b(admin|owner|member)\b|\bpermission", re.I), "features", "rbac"),
    (re.compile(r"dangerouslySetInnerHTML|innerHTML\s*=", re.I), "features", "html_sink"),
    (re.compile(r"system_prompt|systemPrompt|tool_call|function_call", re.I), "ai", "agent"),
]

_SAMPLE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".sql", ".java", ".php", ".rb", ".cs"}


@dataclass
class Discovery:
    root: str
    files_seen: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    frameworks: list[str] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    auth: list[str] = field(default_factory=list)
    api_styles: list[str] = field(default_factory=list)
    payments: list[str] = field(default_factory=list)
    infrastructure: list[str] = field(default_factory=list)
    native: list[str] = field(default_factory=list)
    ai: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    hardening: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    dependencies: dict[str, int] = field(default_factory=dict)
    entrypoints: list[str] = field(default_factory=list)
    surfaces: list[str] = field(default_factory=list)
    base_url: str = ""
    notes: list[str] = field(default_factory=list)
    modules: list[dict[str, Any]] = field(default_factory=list)
    monorepo: bool = False
    workspaces: list[str] = field(default_factory=list)
    submodules: list[dict[str, str]] = field(default_factory=list)
    git: dict[str, Any] = field(default_factory=dict)
    entrypoint_kinds: dict[str, list[str]] = field(default_factory=dict)
    file_classes: dict[str, int] = field(default_factory=dict)
    file_class_samples: dict[str, list[str]] = field(default_factory=dict)
    deploy_targets: list[dict[str, str]] = field(default_factory=list)
    env_files: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    migrations: list[str] = field(default_factory=list)
    secret_like_files: list[str] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)
    data_signals: list[str] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def has_web(self) -> bool:
        return bool(self.frameworks) or "web" in self.surfaces

    @property
    def has_api(self) -> bool:
        return "api" in self.surfaces

    def tag(self, kind: str, label: str) -> None:
        bucket: list[str] = getattr(self, kind)
        if label not in bucket:
            bucket.append(label)


_ROUTE_HINTS = re.compile(
    r"@(app|router|api|blueprint)\.(get|post|put|patch|delete|route)\(|"
    r"\b(app|router)\.(get|post|put|patch|delete|use)\(\s*['\"]/|"
    r"export\s+(async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\b|"
    r"path\(\s*['\"]|urlpatterns|#\[(get|post|put|delete)\("
)


_MANIFEST_NAMES = {
    "package.json", "pyproject.toml", "setup.py", "requirements.txt", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "build.gradle.kts", "composer.json", "Gemfile", "pubspec.yaml",
}
# Names the legacy manifests list always held (kept identical for callers).
_LEGACY_MANIFESTS = {"package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "composer.json", "Gemfile"}

_ENTRY_CAP = 50
_ENTRY_TEXT: list[tuple[str, re.Pattern[str]]] = [
    ("websocket", re.compile(r"@\w+\.websocket\(|WebSocketServer|new\s+WebSocket\.Server|\bwebsockets\.serve\(|socket\.io|io\.on\(\s*['\"]connection|WebSocket\b.*accept\(")),
    ("graphql", re.compile(r"\btype\s+Query\s*\{|ApolloServer|graphqlHTTP|strawberry\.type|graphene\.ObjectType|GraphQLSchema")),
    ("grpc", re.compile(r"grpc\.server\(|add_\w+Servicer_to_server|\.addService\(|grpc\.NewServer\(|tonic::")),
    ("worker", re.compile(r"@(\w+\.)?(task|shared_task)\b|Celery\(|dramatiq\.actor|\brq\b.*Worker|new\s+Worker\(|Sidekiq::Worker|include\s+Sidekiq")),
    ("cron", re.compile(r"APScheduler|BackgroundScheduler|BlockingScheduler|add_job\(|node-cron|cron\.schedule\(|schedule\.every\(|crontab\(|beat_schedule")),
    ("cli", re.compile(r"argparse\.ArgumentParser|\bimport\s+argparse|@click\.(command|group)|typer\.Typer\(|\bfrom\s+commander\b|require\(['\"]commander|\byargs\b|clap::Parser|#\[derive\([^)]*Parser")),
    ("serverless", re.compile(r"def\s+lambda_handler\(|exports\.handler\s*=|export\s+default\s*\{\s*(async\s+)?fetch\b|functions_framework|@app\.function")),
]
_ENTRY_CONFIG: list[tuple[str, re.Pattern[str]]] = [
    ("cron", re.compile(r"kind:\s*CronJob\b|^\s*schedule:\s*$|^\s*-\s*cron:", re.M)),
]
_WEBHOOK = re.compile(r"webhook", re.I)

_CI_LABELS = {"github_actions", "gitlab_ci", "jenkins", "azure_pipelines", "circleci", "bitbucket_pipelines"}
_IAC_LABELS = {"docker", "compose", "kubernetes", "helm", "terraform", "cloudformation", "pulumi", "ansible", "aws_cdk"}
_EXTRA_FILE_SIGNALS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(^|/)azure-pipelines\.ya?ml$"), "infrastructure", "azure_pipelines"),
    (re.compile(r"(^|/)\.circleci/config\.ya?ml$"), "infrastructure", "circleci"),
    (re.compile(r"(^|/)bitbucket-pipelines\.ya?ml$"), "infrastructure", "bitbucket_pipelines"),
    (re.compile(r"(^|/)Pulumi\.ya?ml$"), "infrastructure", "pulumi"),
    (re.compile(r"(^|/)(playbook[^/]*|site)\.ya?ml$|(^|/)ansible\.cfg$|(^|/)roles/[^/]+/tasks/", re.I), "infrastructure", "ansible"),
    (re.compile(r"(^|/)(cloudformation|cfn)[^/]*\.(ya?ml|json)$|\.template\.(ya?ml|json)$", re.I), "infrastructure", "cloudformation"),
]

_GENERATED_NAME = re.compile(r"_pb2(_grpc)?\.py$|\.pb\.go$|\.generated\.|\.g\.dart$")
_GENERATED_HEAD = re.compile(r"@generated|DO NOT EDIT", re.I)
_VENDORED = re.compile(r"(^|/)(vendor|third_party|thirdparty)/|\.min\.(js|css)$")
_TEST = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$")
_MIGRATION = re.compile(r"(^|/)(migrations?|alembic/versions|db/migrate|supabase/migrations)/[^/]+$")
_EXAMPLE = re.compile(r"(^|/)(examples?|samples?|demo)/|\.example$|\.sample$", re.I)
_DOCS = re.compile(r"(^|/)docs?/|\.(md|rst|adoc)$", re.I)
_CONFIG_EXTS = {".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".json", ".properties", ".xml"}
_CONFIG_NAMES = {"Dockerfile", "Procfile", "Makefile", "nginx.conf", ".gitmodules", ".editorconfig"}
_ENV_FILE = re.compile(r"(^|/)\.env(\.[^/]*)?$|(^|/)[^/]+\.env$")
_SECRET_FILE = re.compile(r"\.(pem|key|p12|pfx|jks|keystore)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)[^/]*$|(^|/)credentials\.json$|(^|/)service[-_]account[^/]*\.json$", re.I)
_TOKEN_IN_RC = re.compile(r"_authToken\s*=|^\s*password\s*[:=]|^\s*username\s*[:=].*\n\s*password|_auth\s*=", re.I | re.M)

_DEPLOY_FILES = {
    "vercel.json": "vercel", "netlify.toml": "netlify", "fly.toml": "fly", "render.yaml": "render",
    "app.yaml": "gcp_app_engine", "Procfile": "heroku", "railway.json": "railway", "wrangler.toml": "cloudflare",
}
_INGRESS_HOST = re.compile(r"^\s*-?\s*host:\s*['\"]?([A-Za-z0-9*.-]+\.[A-Za-z]{2,})", re.M)
_SERVER_NAME = re.compile(r"\bserver_name\s+([^;]+);")
_HTTPS_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._~/-]*)?")
_SECRETISH = re.compile(r"(token|secret|key|password|passwd|sig|signature|auth)=|[A-Za-z0-9_-]{32,}", re.I)
_DEPLOY_CONFIG = re.compile(r"(^|/)(k8s|kubernetes|manifests|helm|charts|deploy|deployment|infra|\.github/workflows)/|\.(tf|tfvars)$|(^|/)(docker-)?compose[^/]*\.ya?ml$|(^|/)nginx[^/]*\.conf$|(^|/)(vercel\.json|netlify\.toml|fly\.toml|render\.yaml|app\.yaml|serverless\.ya?ml|wrangler\.toml)$", re.I)

_INTEGRATION_PACKAGES = {
    "stripe": "stripe", "@stripe/stripe-js": "stripe", "paypal-rest-sdk": "paypal", "@paypal/checkout-server-sdk": "paypal",
    "twilio": "twilio", "@sendgrid/mail": "sendgrid", "sendgrid": "sendgrid", "mailgun": "mailgun", "mailgun.js": "mailgun",
    "@slack/web-api": "slack", "@slack/bolt": "slack", "slack-sdk": "slack", "slack_sdk": "slack", "@octokit/rest": "github",
    "octokit": "github", "pygithub": "github", "boto3": "aws", "botocore": "aws", "aws-sdk": "aws", "@aws-sdk/client-s3": "aws",
    "google-cloud-storage": "gcp", "@google-cloud/storage": "gcp", "azure-storage-blob": "azure", "@azure/storage-blob": "azure",
    "azure-identity": "azure", "firebase": "firebase", "firebase-admin": "firebase", "supabase": "supabase",
    "@supabase/supabase-js": "supabase", "sentry-sdk": "sentry", "@sentry/node": "sentry", "@sentry/react": "sentry",
    "@sentry/nextjs": "sentry", "analytics-node": "segment", "@segment/analytics-node": "segment", "mixpanel": "mixpanel",
    "auth0": "auth0", "@auth0/nextjs-auth0": "auth0", "okta": "okta", "@okta/okta-auth-js": "okta", "@clerk/nextjs": "clerk",
    "@clerk/clerk-sdk-node": "clerk", "openai": "openai", "anthropic": "anthropic", "@anthropic-ai/sdk": "anthropic",
}
_INTEGRATION_CODE = re.compile(
    r"(?:^|\n)\s*(?:import|from)\s+(stripe|twilio|sendgrid|slack_sdk|github|boto3|openai|anthropic|sentry_sdk|firebase_admin|supabase|mixpanel|auth0|okta)\b"
    r"|require\(\s*['\"](stripe|twilio|@sendgrid/mail|@slack/[\w-]+|@octokit/\w+|aws-sdk|@aws-sdk/[\w-]+|openai|@anthropic-ai/sdk|@sentry/\w+|firebase-admin|@supabase/supabase-js|mixpanel)['\"]"
    r"|from\s+['\"](stripe|twilio|@sendgrid/mail|@slack/[\w-]+|@octokit/\w+|aws-sdk|@aws-sdk/[\w-]+|openai|@anthropic-ai/sdk|@sentry/\w+|firebase-admin|@supabase/supabase-js|mixpanel|@clerk/\w+)['\"]"
    r"|(generate_presigned_url|getSignedUrl)"
)
_INTEGRATION_MODULE_LABEL = {
    "slack_sdk": "slack", "boto3": "aws", "sentry_sdk": "sentry", "firebase_admin": "firebase", "@sendgrid/mail": "sendgrid",
    "aws-sdk": "aws", "@anthropic-ai/sdk": "anthropic", "firebase-admin": "firebase", "@supabase/supabase-js": "supabase",
    "generate_presigned_url": "s3_presigned_urls", "getSignedUrl": "s3_presigned_urls",
}

_DATA_SIGNALS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b(e_?mail|email_address)\b", re.I)),
    ("phone", re.compile(r"\b(phone|phone_number|mobile_number|msisdn)\b", re.I)),
    ("iban", re.compile(r"\b(iban|bic|swift_code)\b", re.I)),
    ("ssn", re.compile(r"\b(ssn|social_security(_number)?)\b", re.I)),
    ("birth_date", re.compile(r"\b(birth_?date|date_of_birth|dob|birthday)\b", re.I)),
    ("address", re.compile(r"\b(street_address|postal_address|address_line\w*|home_address|zip_code|postcode)\b", re.I)),
    ("card_number", re.compile(r"\b(card_number|cardnumber|pan|cvv|cvc)\b", re.I)),
    ("passport", re.compile(r"\bpassport(_number|_no)?\b", re.I)),
    ("national_id", re.compile(r"\b(national_id|nin|tax_id|nir)\b", re.I)),
]

_NATIVE_FILE = re.compile(r"\.(c|cc|cpp|cxx|h|hpp|wasm)$|(^|/)firmware/|(^|/)(platformio\.ini|sdkconfig|Kconfig)$|\.ld$", re.I)
_RUST_FFI = re.compile(r'extern\s+"C"|\bunsafe\s*\{|\bunsafe\s+fn\b')
_CODE_EXTS = set(_LANG_BY_EXT) - {".sql"} | {".hpp", ".cc", ".cxx", ".mjs", ".cjs", ".scala", ".m"}
_SERVERLESS_PATH = re.compile(r"(^|/)(netlify/functions|api)/[^/]+\.[jt]sx?$|(^|/)functions/[^/]+\.(js|ts|py)$")
_CREDS_IN_URL = re.compile(r"^([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.I)

_WEB_FRAMEWORKS = {"react", "nextjs", "vue", "nuxt", "angular", "svelte"}
_API_FRAMEWORKS = {"express", "fastify", "koa", "nestjs", "hono", "fastapi", "django", "flask", "starlette", "aiohttp", "tornado", "actix", "axum", "rocket", "gin", "spring"}


def _add_capped(bucket: dict[str, list[str]], key: str, value: str, cap: int = _ENTRY_CAP) -> None:
    items = bucket.setdefault(key, [])
    if value not in items and len(items) < cap:
        items.append(value)


def _classify(rel: str, ext: str, head: str) -> str:
    if _VENDORED.search(rel):
        return "vendored"
    if _GENERATED_NAME.search(rel) or (head and _GENERATED_HEAD.search(head)):
        return "generated"
    if _MIGRATION.search(rel):
        return "migration"
    if _TEST.search(rel):
        return "test"
    if _EXAMPLE.search(rel):
        return "example"
    if _DOCS.search(rel):
        return "docs"
    name = rel.rsplit("/", 1)[-1]
    if ext in _CONFIG_EXTS or name in _CONFIG_NAMES or _ENV_FILE.search(rel):
        return "config"
    if ext in _CODE_EXTS or ext == ".sql":
        return "app"
    return ""


def discover(root: Path, *, base_url: str = "") -> Discovery:
    """Inspect *root* and return what the project is made of."""
    root = Path(root).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")
    found = Discovery(root=str(root), base_url=base_url.strip())
    sampled_text = 0
    config_read = 0
    module_dirs: dict[str, dict[str, Any]] = {}
    dir_langs: dict[str, set[str]] = {}
    code_seen = False
    native_seen = False
    serverless_seen = False
    walk_state = {"truncated": False}
    for rel, full in _walk(root, walk_state):
        found.files_seen += 1
        ext = full.suffix.lower()
        lang = _LANG_BY_EXT.get(ext)
        directory = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if lang:
            found.languages[lang] = found.languages.get(lang, 0) + 1
            dir_langs.setdefault(directory, set()).add(lang)
        if ext in _CODE_EXTS:
            code_seen = True
        if _NATIVE_FILE.search(rel):
            native_seen = True
        name = full.name
        is_manifest = name in _MANIFEST_NAMES or ext == ".csproj"
        module_names: list[str] = []
        if name in _LEGACY_MANIFESTS:
            found.manifests.append(rel)
            module_names = _read_manifest(found, full)
        elif is_manifest:
            module_names = _read_manifest(found, full, record=False)
        if is_manifest:
            mod = module_dirs.setdefault(directory, {"manifests": [], "signals": set()})
            mod["manifests"].append(name)
            mod["signals"].update(module_names)
            _module_manifest_extras(found, full, mod)
            for dep in module_names:
                label = _INTEGRATION_PACKAGES.get(dep)
                if label and label not in found.integrations:
                    found.integrations.append(label)
        for pattern, kind, label in _FILE_SIGNALS:
            if pattern.search(rel):
                found.tag(kind, label)
        for pattern, kind, label in _EXTRA_FILE_SIGNALS:
            if pattern.search(rel):
                found.tag(kind, label)
        if name in {"nx.json", "turbo.json", "lerna.json", "pnpm-workspace.yaml", "go.work"}:
            found.monorepo = True
            if name == "pnpm-workspace.yaml":
                for match in re.finditer(r"^\s*-\s*['\"]?([^'\"\n#]+?)['\"]?\s*$", _read(full), re.M):
                    _add_list(found.workspaces, match.group(1))
            elif name == "go.work":
                text = _read(full)
                for block in re.findall(r"use\s*\(([^)]*)\)", text):
                    for item in block.split():
                        _add_list(found.workspaces, item)
                for item in re.findall(r"^use\s+(\S+)\s*$", text, re.M):
                    if item != "(":
                        _add_list(found.workspaces, item)
        if rel == ".gitmodules":
            found.submodules = _parse_gitmodules(_read(full))
        if _ENV_FILE.search(rel) and len(found.env_files) < 100:
            found.env_files.append(rel)
        if _SECRET_FILE.search(rel) or (name in {".npmrc", ".pypirc"} and _TOKEN_IN_RC.search(_read(full, 20_000))):
            if len(found.secret_like_files) < 100:
                found.secret_like_files.append(rel)
        if _MIGRATION.search(rel) and len(found.migrations) < 200:
            found.migrations.append(rel)
        if name in _DEPLOY_FILES:
            _add_deploy(found, "platform", _DEPLOY_FILES[name], rel)
        if name == "CNAME" and "/" not in rel:
            value = _read(full, 1000).strip().splitlines()
            if value:
                _add_deploy(found, "cname", value[0].strip(), rel)
        if _SERVERLESS_PATH.search(rel) or name == "serverless.yml" or name == "serverless.yaml":
            serverless_seen = True
            if ext in _CODE_EXTS:
                _add_capped(found.entrypoint_kinds, "serverless", rel)
        if name == "crontab" or rel.startswith("cron.d/") or ext == ".cron":
            _add_capped(found.entrypoint_kinds, "cron", rel)
        head = ""
        if ext in _SAMPLE_EXTS and sampled_text < _MAX_READ * 10:
            text = _read(full)
            sampled_text += len(text)
            head = text[:600]
            if _ROUTE_HINTS.search(text):
                found.entrypoints.append(rel)
                _add_capped(found.entrypoint_kinds, "http", rel)
                if _WEBHOOK.search(text):
                    _add_capped(found.entrypoint_kinds, "webhook", rel)
            elif "webhook" in rel.lower():
                _add_capped(found.entrypoint_kinds, "webhook", rel)
            for pattern, kind, label in _TEXT_SIGNALS:
                if pattern.search(text):
                    found.tag(kind, label)
            for kind, pattern in _ENTRY_TEXT:
                if pattern.search(text):
                    _add_capped(found.entrypoint_kinds, kind, rel)
                    if kind == "serverless":
                        serverless_seen = True
            for match in _INTEGRATION_CODE.finditer(text):
                token = next(g for g in match.groups() if g)
                label = _INTEGRATION_MODULE_LABEL.get(token) or _INTEGRATION_PACKAGES.get(token) or token.split("/")[0].lstrip("@")
                if label == "octokit":
                    label = "github"
                _add_list(found.integrations, label)
            for label, pattern in _DATA_SIGNALS:
                if label not in found.data_signals and pattern.search(text):
                    found.data_signals.append(label)
            if ext == ".rs" and _RUST_FFI.search(text):
                native_seen = True
        elif ext == ".proto":
            _add_capped(found.entrypoint_kinds, "grpc", rel)
        elif (ext in {".yaml", ".yml", ".conf", ".toml", ".json"} or name == "Procfile") and config_read < _MAX_READ * 10:
            text = _read(full, 100_000)
            config_read += len(text)
            head = text[:600]
            for kind, pattern in _ENTRY_CONFIG:
                if pattern.search(text):
                    _add_capped(found.entrypoint_kinds, kind, rel)
            if _DEPLOY_CONFIG.search(rel):
                _scan_deploy_config(found, rel, text)
        cls = _classify(rel, ext, head)
        if cls:
            found.file_classes[cls] = found.file_classes.get(cls, 0) + 1
            _add_capped(found.file_class_samples, cls, rel, 10)
            if cls == "config" and len(found.config_files) < 100:
                found.config_files.append(rel)
    found.truncated = walk_state["truncated"]
    if found.truncated:
        found.notes.append(f"discovery stopped at {_MAX_FILES} files: results are partial")
    found.languages = dict(sorted(found.languages.items(), key=lambda kv: -kv[1]))
    found.entrypoints = found.entrypoints[:200]
    found.modules = _build_modules(module_dirs, dir_langs, found)
    if len(found.modules) > 1 or found.workspaces:
        found.monorepo = True
    found.git = _read_git(root)
    _derive_surfaces(found)
    _derive_extra_surfaces(found, code_seen=code_seen, native_seen=native_seen, serverless_seen=serverless_seen)
    return found


def _add_list(bucket: list[str], value: str) -> None:
    value = value.strip()
    if value and value not in bucket:
        bucket.append(value)


def _add_deploy(found: Discovery, kind: str, value: str, rel: str) -> None:
    value = value.strip()
    if not value or len(found.deploy_targets) >= 50 or _SECRETISH.search(value):
        return
    entry = {"kind": kind, "value": value, "file": rel}
    if entry not in found.deploy_targets:
        found.deploy_targets.append(entry)


def _scan_deploy_config(found: Discovery, rel: str, text: str) -> None:
    if re.search(r"kind:\s*Ingress\b", text):
        for match in _INGRESS_HOST.finditer(text):
            _add_deploy(found, "ingress_host", match.group(1), rel)
    if rel.endswith(".conf"):
        for match in _SERVER_NAME.finditer(text):
            for host in match.group(1).split():
                if host != "_":
                    _add_deploy(found, "server_name", host, rel)
    for match in _HTTPS_URL.finditer(text):
        _add_deploy(found, "url", match.group(0), rel)


def _strip_credentials(url: str) -> str:
    return _CREDS_IN_URL.sub(r"\1", url.strip())


def _parse_gitmodules(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[submodule"):
            current = {"path": "", "url": ""}
            out.append(current)
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        if key == "path":
            current["path"] = value.strip()
        elif key == "url":
            current["url"] = _strip_credentials(value)
    return [entry for entry in out if entry["path"] or entry["url"]][:100]


def _read_git(root: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"is_repo": False, "head": "", "branches": [], "tags_count": 0}
    git = root / ".git"
    try:
        if git.is_file():
            pointer = _read(git, 4096).strip()
            if not pointer.startswith("gitdir:"):
                return info
            target = Path(pointer[len("gitdir:"):].strip())
            git = target if target.is_absolute() else (root / target).resolve(strict=False)
        if not git.is_dir():
            return info
    except OSError:
        return info
    info["is_repo"] = True
    common = git
    commondir = git / "commondir"
    if commondir.is_file():
        rel = _read(commondir, 4096).strip()
        if rel:
            common = (git / rel).resolve(strict=False)
    head = _read(git / "HEAD", 4096).strip()
    info["head"] = head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else head[:40]
    branches: list[str] = []
    heads = common / "refs" / "heads"
    if heads.is_dir():
        for dirpath, _dirs, files in os.walk(heads):
            for fname in sorted(files):
                ref = str((Path(dirpath) / fname).relative_to(heads)).replace("\\", "/")
                _add_list(branches, ref)
    tags: set[str] = set()
    tag_dir = common / "refs" / "tags"
    if tag_dir.is_dir():
        for dirpath, _dirs, files in os.walk(tag_dir):
            tags.update(str(Path(dirpath) / f) for f in files)
            tags = {t.replace(str(tag_dir), "").lstrip("/\\") for t in tags}
    for line in _read(common / "packed-refs").splitlines():
        parts = line.split()
        if len(parts) == 2 and not line.startswith(("#", "^")):
            if parts[1].startswith("refs/heads/"):
                _add_list(branches, parts[1][len("refs/heads/"):])
            elif parts[1].startswith("refs/tags/"):
                tags.add(parts[1][len("refs/tags/"):])
    info["branches"] = sorted(branches)[:100]
    info["tags_count"] = len(tags)
    return info


def _module_manifest_extras(found: Discovery, path: Path, mod: dict[str, Any]) -> None:
    """Workspaces, bin and scripts entries a manifest declares."""
    name = path.name
    if name not in {"package.json", "pyproject.toml", "Cargo.toml"}:
        return
    text = _read(path)
    if not text:
        return
    rel_dir = str(path.parent.relative_to(found.root)).replace("\\", "/") if str(path.parent) != found.root else ""
    manifest_rel = f"{rel_dir}/{name}" if rel_dir else name
    if name == "package.json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        spaces = data.get("workspaces")
        if isinstance(spaces, dict):
            spaces = spaces.get("packages")
        if isinstance(spaces, list):
            for item in spaces:
                if isinstance(item, str):
                    _add_list(found.workspaces, item)
        if data.get("bin"):
            mod["signals"].add("__cli__")
            _add_capped(found.entrypoint_kinds, "cli", manifest_rel)
    elif name == "pyproject.toml":
        if re.search(r"^\[(project\.scripts|tool\.poetry\.scripts)\]", text, re.M):
            mod["signals"].add("__cli__")
            _add_capped(found.entrypoint_kinds, "cli", manifest_rel)
    elif name == "Cargo.toml":
        block = re.search(r"^\[workspace\][^\[]*?members\s*=\s*\[([^\]]*)\]", text, re.M | re.S)
        if block:
            for item in re.findall(r"[\"']([^\"']+)[\"']", block.group(1)):
                _add_list(found.workspaces, item)
        if re.search(r"^\[\[bin\]\]", text, re.M):
            mod["signals"].add("__cli__")


def _module_kind(signals: set[str], manifests: list[str], path: str, found: Discovery) -> str:
    labels = {_PACKAGE_SIGNALS[s][1] for s in signals if s in _PACKAGE_SIGNALS}
    if labels & {"capacitor", "react_native", "expo"} or "pubspec.yaml" in manifests:
        return "mobile"
    if labels & {"tauri", "electron"}:
        return "desktop"
    if labels & _WEB_FRAMEWORKS:
        return "web"
    if labels & _API_FRAMEWORKS:
        return "api"
    if "task_queue" in labels or signals & {"dramatiq", "rq", "sidekiq"}:
        return "service"
    if "__cli__" in signals or signals & {"click", "typer", "commander", "yargs", "clap"}:
        return "cli"
    if signals & {"pulumi", "aws-cdk-lib", "@pulumi/pulumi", "cdktf"}:
        return "infra"
    if manifests and set(manifests) <= {"pyproject.toml", "setup.py", "Cargo.toml", "package.json", "go.mod", "composer.json", "Gemfile"} and signals:
        return "library"
    return "unknown"


def _build_modules(module_dirs: dict[str, dict[str, Any]], dir_langs: dict[str, set[str]], found: Discovery) -> list[dict[str, Any]]:
    langs: dict[str, set[str]] = {path: set() for path in module_dirs}
    for directory, values in dir_langs.items():
        probe = directory
        while True:
            if probe in langs:
                langs[probe].update(values)
                break
            if not probe:
                break
            probe = probe.rsplit("/", 1)[0] if "/" in probe else ""
    out: list[dict[str, Any]] = []
    for path in sorted(module_dirs):
        info = module_dirs[path]
        out.append({
            "path": path or ".",
            "manifests": sorted(set(info["manifests"])),
            "kind": _module_kind(info["signals"], info["manifests"], path, found),
            "languages": sorted(langs[path]),
        })
    return out[:200]


def _walk(root: Path, state: dict[str, bool] | None = None):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and (not d.startswith(".") or d in {".github", ".gitlab", ".circleci"}))
        for name in sorted(filenames):
            full = Path(dirpath) / name
            try:
                rel = str(full.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            if count >= _MAX_FILES:
                if state is not None:
                    state["truncated"] = True
                return
            yield rel, full
            count += 1


def _read(path: Path, limit: int = _MAX_READ) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_manifest(found: Discovery, path: Path, *, record: bool = True) -> list[str]:
    """Dependency names of one manifest; *record* feeds the global tallies."""
    text = _read(path)
    if not text:
        return []
    names: list[str] = []
    if path.name == "package.json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            found.notes.append(f"{path.name}: invalid JSON ({exc.msg})")
            return []
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            deps = data.get(key) if isinstance(data, dict) else None
            if isinstance(deps, dict):
                names.extend(str(k).lower() for k in deps)
    elif path.name in {"pyproject.toml", "requirements.txt", "Cargo.toml", "Gemfile", "composer.json", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "setup.py", "pubspec.yaml"} or path.suffix == ".csproj":
        for match in re.finditer(r"^\s*[\"']?([A-Za-z0-9_.@/-]+)[\"']?\s*(?:[=<>!~\[]|$|\s)", text, re.M):
            token = match.group(1).lower()
            if token and not token.startswith(("#", "[", "//")):
                names.append(token)
        for match in re.finditer(r"[\"']([a-z0-9_.-]+)[\"']\s*[:=]", text, re.I):
            names.append(match.group(1).lower())
        # Inline arrays: dependencies = ["fastapi>=0.110", "asyncpg", "redis"].
        for match in re.finditer(r"[\"']([a-z0-9_.-]+)(?:\[[^\]\"']*\])?\s*(?:[<>=!~;@ ][^\"']*)?[\"']", text, re.I):
            names.append(match.group(1).lower())
    bases: list[str] = []
    for raw in names:
        base = re.split(r"[\[<>=!~ ;]", raw, maxsplit=1)[0].strip()
        if not base:
            continue
        bases.append(base)
        if not record:
            continue
        found.dependencies[base] = found.dependencies.get(base, 0) + 1
        signal = _PACKAGE_SIGNALS.get(base)
        if signal:
            found.tag(*signal)
    return bases


def _derive_extra_surfaces(found: Discovery, *, code_seen: bool, native_seen: bool, serverless_seen: bool) -> None:
    infra = set(found.infrastructure)
    if code_seen:
        found.tag("surfaces", "source")
    if found.data_signals or found.databases:
        found.tag("surfaces", "data")
    if infra & _CI_LABELS:
        found.tag("surfaces", "cicd")
    if infra & _IAC_LABELS:
        found.tag("surfaces", "iac")
    if serverless_seen or "serverless" in infra or found.entrypoint_kinds.get("serverless"):
        found.tag("surfaces", "serverless")
    if set(found.surfaces) & {"api", "web", "docker", "kubernetes", "serverless", "desktop", "mobile"}:
        found.tag("surfaces", "runtime")
    if native_seen:
        found.tag("surfaces", "native")
    if found.integrations or "webhooks" in found.features or found.entrypoint_kinds.get("webhook"):
        found.tag("surfaces", "integrations")


def _derive_surfaces(found: Discovery) -> None:
    web_frameworks = _WEB_FRAMEWORKS
    api_frameworks = _API_FRAMEWORKS
    if set(found.frameworks) & web_frameworks or "html_sink" in found.features:
        found.tag("surfaces", "web")
    if set(found.frameworks) & api_frameworks or found.entrypoints or set(found.api_styles) & {"openapi", "graphql", "grpc"}:
        found.tag("surfaces", "api")
    if found.auth or "rbac" in found.features:
        found.tag("surfaces", "auth")
    if found.databases:
        found.tag("surfaces", "database")
    if "multitenant" in found.features or "rls" in found.features or "supabase" in found.databases:
        found.tag("surfaces", "multitenant")
    if found.payments or "payments_flow" in found.features:
        found.tag("surfaces", "payments")
    if "webhooks" in found.features or "websocket" in found.api_styles:
        found.tag("surfaces", "workflow")
    if "graphql" in found.api_styles:
        found.tag("surfaces", "graphql")
    if "websocket" in found.api_styles:
        found.tag("surfaces", "websocket")
    if set(found.infrastructure) & {"docker", "compose"}:
        found.tag("surfaces", "docker")
    if set(found.infrastructure) & {"kubernetes", "helm"}:
        found.tag("surfaces", "kubernetes")
    if set(found.infrastructure) & {"terraform", "aws_cdk", "serverless"}:
        found.tag("surfaces", "cloud")
        found.tag("surfaces", "terraform")
    if set(found.native) & {"capacitor", "react_native", "expo", "android", "ios", "flutter"}:
        found.tag("surfaces", "mobile")
    if set(found.native) & {"tauri", "electron"}:
        found.tag("surfaces", "desktop")
    if found.ai:
        found.tag("surfaces", "ai")
    if "mcp" in found.ai:
        found.tag("surfaces", "mcp")
    if "web" in found.surfaces:
        found.tag("surfaces", "frontend")
