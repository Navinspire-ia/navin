# App templates

App templates are complete AI products (CRM, chat, RAG, agents, business apps). They are not generated websites. The gallery lives in the Dev workbench. **Use** installs or creates a project, then starts env, database, and the app. Preview opens when a URL is ready.

This page is the source of truth for the public docs site. Keep it in sync with `site/front/content/docs/app-templates.md` (and the docs catalogue).

## Open the gallery

1. Open **Dev** (`#/code`).
2. Open the **Templates** panel (`#/code?panel=templates`).
3. Search or filter: Chat, RAG, Agents, Business.
4. Click **Use** on a card.

The list is loaded from the public S3 catalogue first. If S3 is unreachable, the gateway catalogue is used, then a built-in fallback.

Public catalogue:

`https://navinagent.s3.eu-north-1.amazonaws.com/templates/v1/catalog.json`

Package URL pattern:

`https://navinagent.s3.eu-north-1.amazonaws.com/templates/v1/<slug>.tar.gz`

## Use: Install here or Create folder

The modal offers two actions.

**Create folder** (recommended for a new app)

1. Choose **Create folder**.
2. Set the project folder (example: `./mon-ai-chat`).
3. Click **Create**.

Navin downloads the S3 package (or a local study cache if S3 has no package), writes the Navin overlay, prepares `.env`, starts required databases when Docker is available, installs dependencies, then starts the app. Preview opens on the first non-database port from the playbook.

**Install here**

Use this only when the current workspace **already is** that app (source files from the playbook are present). Navin writes `.navin/apps/<slug>/` (navin.json, overlay.json, agents, install.json) and starts env / database / app in that folder.

If you click **Install here** inside the Navin repo itself, only the overlay is written. Nothing is started and no `.env` is created at the repo root. Create a new folder instead.

## Progress in the modal

While Create or Install runs, the modal shows steps and a filling bar. Create also shows a download graph.

| Step | What happens |
|---|---|
| Download | Fetch the S3 package into the new folder (Create only) |
| Install | Write project files and install dependencies |
| Config | Copy env, playbook, overlay |
| Start | Start database containers when needed, then the app process |

The bar reaches 100% when the request succeeds. The modal then closes and Preview opens if a URL was returned.

## After install

- Overlay and agents: `.navin/apps/<slug>/`
- Playbook: `.navin/apps/<slug>/install.json` (system, packages, env, Docker, launch, how to modify)
- Customize agents in `.navin/apps/<slug>/overlay.json` (overlay wins over `navin.json`)
- Ecommerce (Medusa) does not auto-start the framework monorepo. Postgres and Redis can start. Build a store with the playbook `create` step, not by running the clone as a shop.

Licenses in the catalogue are MIT or Apache-2.0 only. AWS publish stays blocked until a template is audited (`audit_status=passed` and `status=normalized`).

## Gateway API

All routes are **GET**. The WebUI session token is required (`Authorization: Bearer <token>`). The gateway WebSocket handshake does not accept POST for these routes.

Base: the local gateway (default `http://127.0.0.1:8766`).

### List

`GET /api/webui/app-templates`

Returns `{ templates, installed, aws_prefix, aws_base_url, aws_catalog_url, source }`.

`source` is `aws` when the remote catalogue loaded, otherwise `builtin`.

### Detail

`GET /api/webui/app-templates/<slug>`

Example: `GET /api/webui/app-templates/crm`

### Create

`GET /api/webui/app-templates/create?slug=<slug>&dest=<folder>`

Example: `GET /api/webui/app-templates/create?slug=ai-chat&dest=./mon-ai-chat`

`dest` may be relative to the current workspace. The folder must be empty.

Create starts the app in the background and returns as soon as files are written.

### Install

`GET /api/webui/app-templates/install?slug=<slug>`

If `dest` is also set, this route behaves like Create (same query as above). That is the fallback the WebUI uses when `/create` is missing on an older gateway.

### Success body (create or install)

```json
{
  "ok": true,
  "action": "create",
  "slug": "ai-chat",
  "dest": "/path/to/mon-ai-chat",
  "plugin_dir": "/path/to/mon-ai-chat/.navin/apps/ai-chat",
  "copied_source": true,
  "source": "aws",
  "preview_url": "http://127.0.0.1:3000",
  "started": ["starting"],
  "bootstrap": {
    "env": "copied",
    "databases": [],
    "packages": [],
    "started": ["starting"],
    "preview_url": "http://127.0.0.1:3000",
    "playbook": true,
    "has_source": true
  },
  "audit_status": "pending",
  "status": "source",
  "can_publish": false
}
```

Install uses `action: "install"` and `workspace` instead of `dest`. When the workspace has no app source, `bootstrap.env` is `no-source`, `started` is empty, and `preview_url` is null.

### Errors

| Status | Meaning |
|---|---|
| 401 | Missing or invalid session token |
| 404 | Unknown slug, or the route is not registered on this gateway |
| 409 | Create destination is not empty |

## Related

- [Marketplace](./marketplace.md) - signed skills (different from app templates)
- [Dev / Code](./navin_dev/en/README.md) - workbench that hosts the Templates panel
- [Dev plugins](./navin_dev/en/plugins.md) - plugin packs, not full apps
