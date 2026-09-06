"""Curated install.json overlays. Scanner output is merged; these win on listed keys.

Use when the clone is a framework monorepo, catalog-only (no local cache),
or the auto-scan picks docs/tooling instead of the runnable app.
"""

from __future__ import annotations

from typing import Any


def _env(
    key: str,
    default: str = "",
    *,
    required: bool | None = None,
    secret: bool | None = None,
    purpose: str = "",
    src: str = "curated",
) -> dict[str, Any]:
    upper = key.upper()
    if secret is None:
        secret = any(tok in upper for tok in ("SECRET", "PASSWORD", "TOKEN", "PRIVATE", "SERVICE_ROLE"))
        if upper.endswith("_KEY") and "PUBLIC" not in upper and "PUBLISHABLE" not in upper:
            secret = True
    if required is None:
        required = bool(secret or not default)
    return {
        "key": key,
        "default": default,
        "required": required,
        "secret": bool(secret),
        "purpose": purpose,
        "from": src,
    }


def _pkg(
    role: str,
    path: str,
    name: str,
    scripts: dict[str, str],
    deps: list[str],
    node_engine: str = ">=18",
) -> dict[str, Any]:
    return {
        "role": role,
        "path": path,
        "name": name,
        "scripts": scripts,
        "node_engine": node_engine,
        "deps_hint": deps,
    }


def _db(
    engine: str,
    *,
    required: bool,
    image: str,
    port: int,
    env_url: str,
    bootstrap: list[str],
    compose: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "engine": engine,
        "required": required,
        "docker_image": image,
        "port": port,
        "env_url": env_url,
        "compose": compose or [],
        "bootstrap": bootstrap,
    }


# Overlay keys:
#   replace_env / replace_packages / replace_databases / replace_docker / replace_install
#   plus any install.json field to merge (system, tools, launch, modify, docs, env, ...)
CURATED: dict[str, dict[str, Any]] = {
    "ecommerce": {
        "replace_env": True,
        "replace_packages": True,
        "replace_databases": True,
        "replace_docker": True,
        "replace_install": True,
        "studied_from": "curated+templates_apps/ecommerce",
        "docs": {
            "readme": "README.md",
            "extra": [],
            "summary": (
                "Medusa is a commerce framework. The GitHub clone is the monorepo, not a store. "
                "Create a runnable app with `npx create-medusa-app@latest`. "
                "Needs Postgres + Redis. Admin on :9000, storefront on :8000."
            ),
        },
        "system": {
            "node": True,
            "node_engine": ">=20.0.0",
            "python": False,
            "docker": True,
            "package_manager": "yarn",
            "make": False,
            "prisma": False,
            "supabase_cli": False,
        },
        "packages": {
            "frontend": [
                _pkg(
                    "frontend",
                    "apps/storefront/package.json",
                    "medusa-storefront",
                    {"dev": "next dev -p 8000", "build": "next build", "start": "next start"},
                    ["next", "react"],
                    ">=20",
                )
            ],
            "backend": [
                _pkg(
                    "backend",
                    "apps/backend/package.json",
                    "medusa-backend",
                    {
                        "dev": "npx medusa develop",
                        "start": "npx medusa start",
                        "build": "npx medusa build",
                    },
                    ["express"],
                    ">=20",
                )
            ],
            "all": [
                _pkg(
                    "root",
                    "package.json",
                    "create-medusa-app",
                    {"create": "npx create-medusa-app@latest"},
                    ["express", "ioredis"],
                    ">=20",
                )
            ],
        },
        "databases": [
            _db(
                "postgres",
                required=True,
                image="postgres:16-alpine",
                port=5432,
                env_url="DATABASE_URL",
                bootstrap=[
                    "docker run -d --name navin-medusa-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres -e POSTGRES_DB=medusa-store -p 5432:5432 postgres:16-alpine",
                    "Wait for pg_isready -U postgres, then npx medusa db:migrate.",
                ],
            ),
            _db(
                "redis",
                required=True,
                image="redis:7-alpine",
                port=6379,
                env_url="REDIS_URL",
                bootstrap=["docker run -d --name navin-medusa-redis -p 6379:6379 redis:7-alpine"],
            ),
        ],
        "env_files": [".env"],
        "env": [
            _env("DATABASE_URL", "postgres://postgres:postgres@127.0.0.1:5432/medusa-store", required=True, purpose="Postgres connection string"),
            _env("REDIS_URL", "redis://127.0.0.1:6379", required=True, purpose="Redis event bus / cache"),
            _env("JWT_SECRET", "change-me-jwt", required=True, secret=True, purpose="JWT signing secret"),
            _env("COOKIE_SECRET", "change-me-cookie", required=True, secret=True, purpose="Cookie signing secret"),
            _env("STORE_CORS", "http://localhost:8000", required=True, purpose="Storefront origin"),
            _env("ADMIN_CORS", "http://localhost:9000", required=True, purpose="Admin origin"),
            _env("AUTH_CORS", "http://localhost:9000,http://localhost:8000", required=True, purpose="Auth allowed origins"),
            _env("PORT", "9000", required=False, purpose="Medusa HTTP port"),
            _env("NODE_ENV", "development", required=False, purpose="Node environment"),
            _env("MEDUSA_WORKER_MODE", "server", required=False, purpose="server or worker"),
            _env("DISABLE_MEDUSA_ADMIN", "false", required=False, purpose="Build admin with the server"),
        ],
        "ports": [8000, 9000, 5432, 6379],
        "docker": {
            "compose": [],
            "services": [
                {"name": "postgres", "image": "postgres:16-alpine", "ports": ["5432:5432"]},
                {"name": "redis", "image": "redis:7-alpine", "ports": ["6379:6379"]},
            ],
            "dockerfile": False,
            "note": "No compose in the framework clone. Start Postgres + Redis, then create-medusa-app.",
        },
        "auto_start": False,
        "launch": {
            "create": "npx create-medusa-app@latest",
            "dev": "npx medusa develop",
            "migrate": "npx medusa db:migrate",
            "seed": "npx medusa db:seed",
            "build": "npx medusa build",
        },
        "install": {
            "steps": [
                {"id": "read-playbook", "run": "Read .navin/apps/ecommerce/install.json. This clone is the Medusa monorepo, not a store."},
                {"id": "db-postgres", "run": "Start Postgres 16 on :5432 (user/password postgres, db medusa-store)."},
                {"id": "db-redis", "run": "Start Redis 7 on :6379."},
                {"id": "create-app", "run": "npx create-medusa-app@latest --db-url postgres://postgres:postgres@127.0.0.1:5432/medusa-store"},
                {"id": "env", "run": "In the generated app .env set DATABASE_URL, REDIS_URL, JWT_SECRET, COOKIE_SECRET, STORE_CORS, ADMIN_CORS, AUTH_CORS."},
                {"id": "migrate", "run": "npx medusa db:migrate && npx medusa db:seed"},
                {"id": "overlay", "run": "Customize .navin/apps/ecommerce/overlay.json (agents, prompts, extra env)."},
                {"id": "dev", "run": "npx medusa develop (API+admin :9000). Storefront: cd apps/storefront && npm run dev (:8000)."},
            ]
        },
        "modify": {
            "overlay": ".navin/apps/ecommerce/overlay.json",
            "agents": ".navin/apps/ecommerce/agents/",
            "env": ".env",
            "frontend": "apps/storefront (Next.js storefront from create-medusa-app)",
            "backend": "apps/backend or project root (Medusa API + admin)",
            "theme": "Do not rewrite the medusajs/medusa monorepo. Change the generated store app.",
            "rule": "overlay.json wins over navin.json. Change prompts, tools and env there first.",
        },
    },
    "stock-pilot": {
        "replace_env": True,
        "replace_packages": True,
        "replace_databases": True,
        "replace_docker": True,
        "replace_install": True,
        "studied_from": "curated-github",
        "docs": {
            "readme": "README.md",
            "extra": [],
            "summary": (
                "StockPilot: React 19 + Vite frontend (:5173), Express 5 + Prisma + PostgreSQL backend (:5000), "
                "optional Redis and Next.js landing. JWT auth, POS, purchases, invoices, RBAC."
            ),
        },
        "system": {
            "node": True,
            "node_engine": ">=18",
            "python": False,
            "docker": True,
            "package_manager": "npm",
            "make": False,
            "prisma": True,
            "supabase_cli": False,
        },
        "tools": [
            {"id": "git", "why": "Clone https://github.com/zacktam12/StockPilot"},
            {"id": "npm", "why": "Install frontend/ and backend/ separately"},
            {"id": "node", "why": "Node 18+ for Vite and Express"},
            {"id": "docker", "why": "Postgres 14+ and optional Redis"},
            {"id": "prisma", "why": "npx prisma generate / migrate deploy / db seed in backend/"},
        ],
        "packages": {
            "frontend": [
                _pkg(
                    "frontend",
                    "frontend/package.json",
                    "StockPilot",
                    {"dev": "vite", "build": "vite build", "preview": "vite preview"},
                    ["react", "vite", "antd", "axios"],
                )
            ],
            "backend": [
                _pkg(
                    "backend",
                    "backend/package.json",
                    "backend",
                    {"dev": "nodemon src/server.js", "start": "node src/server.js"},
                    ["express", "@prisma/client", "pg", "redis", "jsonwebtoken"],
                )
            ],
            "all": [
                _pkg("root", "package.json", "stockpilot-root", {}, []),
                _pkg(
                    "frontend",
                    "frontend/package.json",
                    "StockPilot",
                    {"dev": "vite", "build": "vite build"},
                    ["react", "vite"],
                ),
                _pkg(
                    "backend",
                    "backend/package.json",
                    "backend",
                    {"dev": "nodemon src/server.js", "start": "node src/server.js"},
                    ["express", "prisma", "pg"],
                ),
                _pkg(
                    "frontend",
                    "stockLandingPage/package.json",
                    "stock-landing",
                    {"dev": "next dev", "build": "next build"},
                    ["next", "react"],
                ),
            ],
        },
        "databases": [
            _db(
                "postgres",
                required=True,
                image="postgres:16-alpine",
                port=5432,
                env_url="DATABASE_URL",
                bootstrap=[
                    "docker run -d --name navin-stockpilot-pg -e POSTGRES_PASSWORD=navin -e POSTGRES_USER=navin -e POSTGRES_DB=stockpilot -p 5432:5432 postgres:16-alpine",
                    "cd backend && npx prisma generate && npx prisma migrate deploy && npx prisma db seed",
                ],
            ),
            _db(
                "redis",
                required=False,
                image="redis:7-alpine",
                port=6379,
                env_url="REDIS_URL",
                bootstrap=["docker run -d --name navin-stockpilot-redis -p 6379:6379 redis:7-alpine"],
            ),
        ],
        "env_files": ["backend/.env.example", "frontend/.env.example"],
        "env": [
            _env("DATABASE_URL", "postgresql://navin:navin@127.0.0.1:5432/stockpilot", required=True, purpose="Prisma Postgres URL", src="backend/.env"),
            _env("JWT_SECRET", "change-me-jwt", required=True, secret=True, purpose="JWT signing secret", src="backend/.env"),
            _env("JWT_EXPIRE", "7d", required=False, purpose="JWT expiry", src="backend/.env"),
            _env("PORT", "5000", required=False, purpose="Express API port", src="backend/.env"),
            _env("NODE_ENV", "development", required=False, purpose="Node environment", src="backend/.env"),
            _env("EMAIL_HOST", "smtp.gmail.com", required=False, purpose="SMTP host", src="backend/.env"),
            _env("EMAIL_PORT", "587", required=False, purpose="SMTP port", src="backend/.env"),
            _env("EMAIL_USER", "", required=False, purpose="SMTP user", src="backend/.env"),
            _env("EMAIL_PASSWORD", "", required=False, secret=True, purpose="SMTP password", src="backend/.env"),
            _env("REDIS_URL", "redis://127.0.0.1:6379", required=False, purpose="Redis cache", src="backend/.env"),
            _env("MAX_FILE_SIZE", "5242880", required=False, purpose="Upload size bytes", src="backend/.env"),
            _env("UPLOAD_DIR", "uploads", required=False, purpose="Upload directory", src="backend/.env"),
            _env("CORS_ORIGIN", "http://localhost:5173", required=False, purpose="Frontend origin", src="backend/.env"),
            _env("RATE_LIMIT_WINDOW_MS", "900000", required=False, purpose="Rate limit window", src="backend/.env"),
            _env("RATE_LIMIT_MAX_REQUESTS", "100", required=False, purpose="Rate limit max", src="backend/.env"),
            _env("VITE_API_URL", "http://localhost:5000/api", required=True, purpose="API base URL", src="frontend/.env"),
            _env("VITE_APP_NAME", "StockPilot", required=False, purpose="App title", src="frontend/.env"),
            _env("VITE_SOCKET_URL", "http://localhost:5000", required=False, purpose="Socket.io URL", src="frontend/.env"),
        ],
        "ports": [5000, 5173, 3000, 5432, 6379],
        "docker": {
            "compose": [],
            "services": [
                {"name": "postgres", "image": "postgres:16-alpine", "ports": ["5432:5432"]},
                {"name": "redis", "image": "redis:7-alpine", "ports": ["6379:6379"]},
            ],
            "dockerfile": False,
            "note": "No official compose. Start Postgres (required) and Redis (optional).",
        },
        "launch": {
            "dev": "cd backend && npm run dev  (and  cd frontend && npm run dev)",
            "migrate": "cd backend && npx prisma migrate deploy",
            "seed": "cd backend && npx prisma db seed",
            "build": "cd frontend && npm run build",
        },
        "install": {
            "steps": [
                {"id": "read-playbook", "run": "Read .navin/apps/stock-pilot/install.json first. Do not guess env or DB."},
                {"id": "clone", "run": "git clone https://github.com/zacktam12/StockPilot.git . (or extract the S3 pack)."},
                {"id": "db-postgres", "run": "Start Postgres 16, db stockpilot, user/password navin."},
                {"id": "env-backend", "run": "cd backend && cp .env.example .env and set DATABASE_URL, JWT_SECRET, PORT=5000, CORS_ORIGIN=http://localhost:5173."},
                {"id": "env-frontend", "run": "cd frontend && set VITE_API_URL=http://localhost:5000/api and VITE_SOCKET_URL=http://localhost:5000."},
                {"id": "node-backend", "run": "cd backend && npm install && npx prisma generate && npx prisma migrate deploy && npx prisma db seed"},
                {"id": "node-frontend", "run": "cd frontend && npm install"},
                {"id": "overlay", "run": "Customize .navin/apps/stock-pilot/overlay.json."},
                {"id": "dev", "run": "Terminal 1: cd backend && npm run dev (:5000). Terminal 2: cd frontend && npm run dev (:5173)."},
            ]
        },
        "modify": {
            "overlay": ".navin/apps/stock-pilot/overlay.json",
            "agents": ".navin/apps/stock-pilot/agents/",
            "env": "backend/.env and frontend/.env",
            "frontend": "frontend/package.json (Vite + React + Ant Design)",
            "backend": "backend/package.json (Express + Prisma). Schema: backend/prisma/schema.prisma",
            "theme": "Frontend Tailwind + Ant Design. Do not rewrite the upstream repo.",
            "rule": "overlay.json wins over navin.json. Change prompts, tools and env there first.",
        },
    },
    "sales-inventory": {
        "replace_env": True,
        "replace_packages": True,
        "replace_databases": True,
        "replace_docker": True,
        "replace_install": True,
        "studied_from": "curated-github",
        "docs": {
            "readme": "README.md",
            "extra": [],
            "summary": (
                "mhShohan inventory: React+Vite client, Express+TypeScript+MongoDB server, optional next-client. "
                "Products, sellers, purchases, sales history."
            ),
        },
        "system": {
            "node": True,
            "node_engine": ">=18",
            "python": False,
            "docker": True,
            "package_manager": "npm",
            "make": False,
            "prisma": False,
            "supabase_cli": False,
        },
        "tools": [
            {"id": "git", "why": "Clone https://github.com/mhShohan/inventory-management-system"},
            {"id": "npm", "why": "Install client/ and server/ separately"},
            {"id": "node", "why": "Node 18+"},
            {"id": "docker", "why": "MongoDB 7 on :27017"},
        ],
        "packages": {
            "frontend": [
                _pkg(
                    "frontend",
                    "client/package.json",
                    "client",
                    {"dev": "vite", "build": "vite build"},
                    ["react", "vite"],
                )
            ],
            "backend": [
                _pkg(
                    "backend",
                    "server/package.json",
                    "server",
                    {"dev": "dev", "start": "start"},
                    ["express", "mongoose"],
                )
            ],
            "all": [
                _pkg("frontend", "client/package.json", "client", {"dev": "vite"}, ["react", "vite"]),
                _pkg("backend", "server/package.json", "server", {"dev": "dev"}, ["express", "mongoose"]),
                _pkg("frontend", "next-client/package.json", "next-client", {"dev": "next dev"}, ["next", "react"]),
            ],
        },
        "databases": [
            _db(
                "mongodb",
                required=True,
                image="mongo:7",
                port=27017,
                env_url="DATABASE_URL",
                bootstrap=[
                    "docker run -d --name navin-sales-mongo -p 27017:27017 mongo:7",
                    "Set server DATABASE_URL to mongodb://127.0.0.1:27017/inventory",
                ],
            )
        ],
        "env_files": ["client/.env", "server/.env"],
        "env": [
            _env("VITE_BASE_URL", "http://localhost:8000/api/v1", required=True, purpose="API base for Vite client", src="client/.env"),
            _env("NODE_ENV", "dev", required=False, purpose="Node environment", src="server/.env"),
            _env("PORT", "8000", required=False, purpose="Express port", src="server/.env"),
            _env("DATABASE_URL", "mongodb://127.0.0.1:27017/inventory", required=True, purpose="MongoDB URI", src="server/.env"),
            _env("JWT_SECRET", "change-me-jwt", required=True, secret=True, purpose="JWT signing secret", src="server/.env"),
        ],
        "ports": [8000, 5173, 27017],
        "docker": {
            "compose": [],
            "services": [{"name": "mongo", "image": "mongo:7", "ports": ["27017:27017"]}],
            "dockerfile": False,
            "note": "No official compose. Start MongoDB then npm run dev in server/ and client/.",
        },
        "launch": {
            "dev": "cd server && npm run dev  (and  cd client && npm run dev)",
            "build": "cd client && npm run build",
        },
        "install": {
            "steps": [
                {"id": "read-playbook", "run": "Read .navin/apps/sales-inventory/install.json first."},
                {"id": "clone", "run": "git clone https://github.com/mhShohan/inventory-management-system.git ."},
                {"id": "db-mongodb", "run": "Start MongoDB 7 on :27017."},
                {"id": "env-server", "run": "Create server/.env with NODE_ENV=dev PORT=8000 DATABASE_URL=mongodb://127.0.0.1:27017/inventory JWT_SECRET=..."},
                {"id": "env-client", "run": "Create client/.env with VITE_BASE_URL=http://localhost:8000/api/v1"},
                {"id": "node-server", "run": "cd server && npm install && npm run dev"},
                {"id": "node-client", "run": "cd client && npm install && npm run dev"},
                {"id": "overlay", "run": "Customize .navin/apps/sales-inventory/overlay.json."},
            ]
        },
        "modify": {
            "overlay": ".navin/apps/sales-inventory/overlay.json",
            "agents": ".navin/apps/sales-inventory/agents/",
            "env": "server/.env and client/.env",
            "frontend": "client/package.json (Vite + React). Optional next-client/.",
            "backend": "server/package.json (Express + Mongoose)",
            "theme": "Ant Design on the client. Do not rewrite the upstream repo.",
            "rule": "overlay.json wins over navin.json. Change prompts, tools and env there first.",
        },
    },
    "purchase-orders": {
        "replace_env": True,
        "replace_packages": True,
        "replace_databases": True,
        "replace_docker": True,
        "replace_install": True,
        "studied_from": "curated-github",
        "docs": {
            "readme": "README.md",
            "extra": [],
            "summary": (
                "nshakib inventory API: Node + Express + Mongoose + MongoDB. "
                "Products, categories, suppliers, purchases, sales, JWT. Frontend folder optional. API :5000."
            ),
        },
        "system": {
            "node": True,
            "node_engine": ">=18",
            "python": False,
            "docker": True,
            "package_manager": "npm",
            "make": False,
            "prisma": False,
            "supabase_cli": False,
        },
        "tools": [
            {"id": "git", "why": "Clone https://github.com/nshakib/inventory-management-system-backend"},
            {"id": "npm", "why": "Install root (API) and frontend/ if present"},
            {"id": "node", "why": "Node 18+ (nodemon --env-file=.env)"},
            {"id": "docker", "why": "MongoDB 7 on :27017"},
        ],
        "packages": {
            "frontend": [
                _pkg("frontend", "frontend/package.json", "frontend", {"dev": "dev", "build": "build"}, ["react"])
            ],
            "backend": [
                _pkg(
                    "backend",
                    "package.json",
                    "server",
                    {"dev": "nodemon --env-file=.env index.js", "start": "node --env-file=.env index.js"},
                    ["express", "mongoose", "jsonwebtoken"],
                )
            ],
            "all": [
                _pkg(
                    "backend",
                    "package.json",
                    "server",
                    {"dev": "nodemon --env-file=.env index.js", "start": "node --env-file=.env index.js"},
                    ["express", "mongoose"],
                ),
                _pkg("frontend", "frontend/package.json", "frontend", {"dev": "dev"}, ["react"]),
            ],
        },
        "databases": [
            _db(
                "mongodb",
                required=True,
                image="mongo:7",
                port=27017,
                env_url="MONGO_URI",
                bootstrap=[
                    "docker run -d --name navin-po-mongo -p 27017:27017 mongo:7",
                    "Set MONGO_URI=mongodb://127.0.0.1:27017/inventory_db",
                ],
            )
        ],
        "env_files": [".env.example"],
        "env": [
            _env("PORT", "5000", required=False, purpose="Express port"),
            _env("MONGO_URI", "mongodb://127.0.0.1:27017/inventory_db", required=True, purpose="MongoDB connection string"),
            _env("JWT_ACCESS_SECRET", "change-me-access", required=True, secret=True, purpose="Access token secret"),
            _env("JWT_REFRESH_SECRET", "change-me-refresh", required=True, secret=True, purpose="Refresh token secret"),
            _env("ACCESS_TOKEN_EXPIRES", "15m", required=False, purpose="Access token TTL"),
            _env("REFRESH_TOKEN_EXPIRES", "7d", required=False, purpose="Refresh token TTL"),
            _env("NODE_ENV", "development", required=False, purpose="Node environment"),
        ],
        "ports": [5000, 27017],
        "docker": {
            "compose": [],
            "services": [{"name": "mongo", "image": "mongo:7", "ports": ["27017:27017"]}],
            "dockerfile": False,
            "note": "No official compose. Start MongoDB then npm run dev at repo root.",
        },
        "launch": {
            "dev": "npm run dev",
            "start": "npm start",
            "seed": "node seed.js",
        },
        "install": {
            "steps": [
                {"id": "read-playbook", "run": "Read .navin/apps/purchase-orders/install.json first."},
                {"id": "clone", "run": "git clone https://github.com/nshakib/inventory-management-system-backend.git ."},
                {"id": "db-mongodb", "run": "Start MongoDB 7 on :27017."},
                {"id": "env", "run": "Create .env with PORT=5000 MONGO_URI=mongodb://127.0.0.1:27017/inventory_db JWT_ACCESS_SECRET JWT_REFRESH_SECRET NODE_ENV=development."},
                {"id": "node-deps", "run": "npm install && npm run dev"},
                {"id": "overlay", "run": "Customize .navin/apps/purchase-orders/overlay.json."},
            ]
        },
        "modify": {
            "overlay": ".navin/apps/purchase-orders/overlay.json",
            "agents": ".navin/apps/purchase-orders/agents/",
            "env": ".env",
            "frontend": "frontend/ if present",
            "backend": "package.json (Express + Mongoose). Models in models/ or server/src/models.",
            "theme": "API-first. Do not rewrite the upstream repo.",
            "rule": "overlay.json wins over navin.json. Change prompts, tools and env there first.",
        },
    },
    "rag-platform": {
        "replace_databases": True,
        "replace_docker": True,
        "studied_from": "curated+templates_apps/rag-platform",
        "docs": {
            "summary": (
                "RAGFlow: Python backend + Vite web UI. Official path is Docker Compose "
                "(MySQL, Elasticsearch or OpenSearch, Redis, MinIO). See docker/docker-compose.yml."
            ),
        },
        "system": {"docker": True, "python": True, "node": True, "package_manager": "uv"},
        "databases": [
            _db(
                "mysql",
                required=True,
                image="mysql:8",
                port=3306,
                env_url="MYSQL_PASSWORD",
                compose=["docker/docker-compose.yml", "docker/docker-compose-base.yml"],
                bootstrap=["cd docker && docker compose -f docker-compose-base.yml -f docker-compose.yml --profile elasticsearch --profile cpu up -d"],
            ),
            _db(
                "elasticsearch",
                required=True,
                image="elasticsearch:8",
                port=9200,
                env_url="ELASTIC_PASSWORD",
                compose=["docker/docker-compose-base.yml"],
                bootstrap=["Use compose profile elasticsearch (es01). Do not invent a sqlite file DB."],
            ),
            _db(
                "redis",
                required=True,
                image="redis:7-alpine",
                port=6379,
                env_url="REDIS_PASSWORD",
                compose=["docker/docker-compose-base.yml"],
                bootstrap=["Started by docker-compose-base.yml"],
            ),
            _db(
                "minio",
                required=True,
                image="minio/minio",
                port=9000,
                env_url="MINIO_PASSWORD",
                compose=["docker/docker-compose-base.yml"],
                bootstrap=["Object store for documents. Started by docker-compose-base.yml"],
            ),
        ],
        "docker": {
            "compose": ["docker/docker-compose.yml", "docker/docker-compose-base.yml"],
            "services": [
                {"name": "mysql", "image": "mysql:8", "ports": ["3306:3306"]},
                {"name": "es01", "image": "elasticsearch", "ports": ["9200:9200"]},
                {"name": "redis", "image": "redis", "ports": ["6379:6379"]},
                {"name": "minio", "image": "minio/minio", "ports": ["9000:9000"]},
                {"name": "ragflow-cpu", "image": "infiniflow/ragflow", "ports": ["80:80", "9380:9380"]},
            ],
            "dockerfile": True,
            "note": "Copy docker/.env.single-bucket-example to docker/.env then compose up. Do not use sqlite.",
        },
        "launch": {
            "dev": "cd docker && docker compose --profile elasticsearch --profile cpu up -d",
            "frontend": "cd web && npm run dev",
        },
        "modify": {
            "frontend": "web/package.json",
            "backend": "pyproject.toml",
            "env": "docker/.env (from docker/.env.single-bucket-example)",
        },
    },
    "crm": {
        "replace_env": True,
        "replace_databases": True,
        "replace_install": True,
        "studied_from": "curated+templates_apps/crm",
        "packages_promote_root": "frontend",
        "env_files": [".env.example"],
        "env": [
            _env("VITE_SUPABASE_URL", "http://127.0.0.1:54321", required=False, purpose="Supabase API URL (Vite)"),
            _env("VITE_SB_PUBLISHABLE_KEY", "", required=False, purpose="Supabase publishable key (Vite)"),
            _env("CRM_BASE_URL", "http://localhost:5174", required=False, purpose="App URL"),
            _env("POSTMARK_WEBHOOK_USER", "", required=False, purpose="Email / SMTP"),
            _env("POSTMARK_WEBHOOK_PASSWORD", "", required=False, secret=True, purpose="Email / SMTP"),
            _env("VITE_INBOUND_EMAIL", "", required=False, purpose="Inbound email"),
        ],
        "ports": [5174, 5173, 54321],
        "databases": [
            _db(
                "supabase",
                required=True,
                image="supabase/postgres",
                port=54321,
                env_url="VITE_SUPABASE_URL",
                compose=[],
                bootstrap=[
                    "make start  (or npx supabase start). Do not start a bare Postgres container; Atomic CRM needs local Supabase.",
                    "Demo without local Supabase: npm run dev:demo",
                ],
            )
        ],
        "docker": {
            "compose": [],
            "services": [{"name": "supabase", "image": "supabase/postgres", "ports": ["54321:54321"]}],
            "dockerfile": True,
            "note": "Supabase CLI starts Postgres + Auth + Storage. Prefer `make start` or `npm run dev:demo`.",
        },
        "launch": {
            "dev": "npm run dev:demo",
            "start": "make start",
            "build": "npm run build",
        },
        "install": {
            "steps": [
                {"id": "read-playbook", "run": "Read .navin/apps/crm/install.json first. Do not guess env or DB."},
                {"id": "env", "run": "Copy .env.example to .env. Demo mode does not need real Supabase keys."},
                {"id": "node-deps", "run": "npm install at the project root."},
                {"id": "dev", "run": "npm run dev:demo (Vite on :5174, no local Supabase). For full stack: make start."},
                {"id": "overlay", "run": "Customize .navin/apps/crm/overlay.json (agents, prompts, extra env)."},
            ]
        },
        "modify": {
            "frontend": "package.json (Vite + React root)",
            "backend": "supabase/ (migrations, functions, seed.sql)",
            "env": ".env.example -> .env",
        },
    },
}


def apply_curated(slug: str, payload: dict[str, Any]) -> dict[str, Any]:
    overlay = CURATED.get(slug)
    if not overlay:
        return payload
    out = dict(payload)
    replace_env = bool(overlay.get("replace_env"))
    replace_packages = bool(overlay.get("replace_packages"))
    replace_databases = bool(overlay.get("replace_databases"))
    replace_docker = bool(overlay.get("replace_docker"))
    replace_install = bool(overlay.get("replace_install"))

    if overlay.get("studied_from"):
        out["studied_from"] = overlay["studied_from"]

    if "auto_start" in overlay:
        out["auto_start"] = bool(overlay["auto_start"])

    if "docs" in overlay:
        docs = dict(out.get("docs") or {})
        docs.update(overlay["docs"])
        out["docs"] = docs

    if "system" in overlay:
        system = dict(out.get("system") or {})
        system.update(overlay["system"])
        out["system"] = system

    if "tools" in overlay:
        out["tools"] = list(overlay["tools"])

    if "packages" in overlay:
        if replace_packages:
            out["packages"] = overlay["packages"]
        else:
            pkgs = dict(out.get("packages") or {})
            for key in ("frontend", "backend", "all"):
                if key in overlay["packages"]:
                    pkgs[key] = overlay["packages"][key]
            out["packages"] = pkgs

    if overlay.get("packages_promote_root") and not replace_packages:
        pkgs = dict(out.get("packages") or {})
        role = overlay["packages_promote_root"]
        all_pkgs = list(pkgs.get("all") or [])
        promoted = []
        for pkg in all_pkgs:
            item = dict(pkg)
            if item.get("role") == "root" and item.get("path") == "package.json":
                item["role"] = role
            promoted.append(item)
        pkgs["all"] = promoted
        pkgs[role] = [p for p in promoted if p.get("role") == role]
        out["packages"] = pkgs

    if "databases" in overlay and replace_databases:
        out["databases"] = overlay["databases"]

    if "env" in overlay:
        if replace_env:
            out["env"] = list(overlay["env"])
        else:
            seen = {row.get("key") for row in overlay["env"]}
            merged = list(overlay["env"])
            for row in out.get("env") or []:
                if row.get("key") not in seen:
                    merged.append(row)
            out["env"] = merged

    if "env_files" in overlay:
        out["env_files"] = list(overlay["env_files"])

    if "ports" in overlay:
        out["ports"] = list(overlay["ports"])

    if "docker" in overlay and replace_docker:
        out["docker"] = overlay["docker"]
    elif "docker" in overlay:
        docker = dict(out.get("docker") or {})
        docker.update(overlay["docker"])
        out["docker"] = docker

    if "launch" in overlay:
        launch = dict(out.get("launch") or {})
        launch.update(overlay["launch"])
        out["launch"] = launch

    if "install" in overlay and replace_install:
        out["install"] = overlay["install"]

    if "modify" in overlay:
        modify = dict(out.get("modify") or {})
        modify.update(overlay["modify"])
        out["modify"] = modify

    completeness = dict(out.get("completeness") or {})
    completeness["curated"] = True
    completeness["env_count"] = len(out.get("env") or [])
    completeness["package_count"] = len((out.get("packages") or {}).get("all") or [])
    missing = [item for item in (completeness.get("missing") or []) if item not in {"databases", "env", "packages"}]
    completeness["missing"] = missing
    out["completeness"] = completeness
    return out
