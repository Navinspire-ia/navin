# navin WebUI Source

This directory contains the React/TypeScript source for the navin WebUI. If
you installed `navin-ai` from PyPI and only want to use the bundled browser UI,
read the user guide in [`docs/webui.md`](../docs/webui.md). You do not need
Node.js, Bun, Vite, or anything in this directory unless you are changing the
frontend.

For the project overview, install guide, and general docs map, see the root [`README.md`](../README.md) and [`docs/README.md`](../docs/README.md).

## Pick a Path

| Goal | Start with | Opens at |
|---|---|---|
| Use the bundled browser UI | [`docs/webui.md`](../docs/webui.md) | `http://127.0.0.1:8765` |
| Use the WebUI from another device | [`docs/webui.md#lan-access`](../docs/webui.md#lan-access) | `http://<your-ip>:8765` |
| Change WebUI source code | [Develop the WebUI (Vite HMR)](#develop-the-webui-vite-hmr) | `http://127.0.0.1:5173` |
| Debug setup failures | [`docs/troubleshooting.md#webui-problems`](../docs/troubleshooting.md#webui-problems) | Diagnosis order and common fixes |

The source app is built with Vite + React 18 + TypeScript + Tailwind 3 +
shadcn/ui. It talks to the gateway over the WebSocket multiplex protocol and
reads session metadata from the embedded REST surface on the same port.

## Layout

```text
webui/                 source tree (this directory)
navin/web/dist/      build output served by the gateway
```

## Develop the WebUI (Vite HMR)

### 1. Install navin from source

From the repository root:

```bash
python -m pip install -e .
```

> Editable installs intentionally **skip** the WebUI bundle step - Vite HMR is faster than rebuilding `dist/` on every change.

### 2. Enable the WebSocket channel

In `~/.navin/config.json`, merge:

```json
{ "channels": { "websocket": { "enabled": true } } }
```

### 3. Start the gateway

In one terminal:

```bash
navin gateway
```

### 4. Start the WebUI dev server

In another terminal:

```bash
cd webui
npm ci                 # package-lock.json is the single lockfile
npm run dev
```

Then open `http://127.0.0.1:5173`.

By default the dev server proxies `/api`, `/webui`, `/auth`, and WebSocket traffic to `http://127.0.0.1:8765`.

If your gateway listens on a non-default port, point the dev server at it:

```bash
NAVIN_API_URL=http://127.0.0.1:9000 npm run dev
```

## Build for packaged runtime

You usually do not need to run this by hand: `python -m build` invokes the WebUI build automatically when packaging the wheel.

If you want to preview the production bundle locally without rebuilding the wheel:

```bash
cd webui
npm run build          # writes to ../navin/web/dist
```

The gateway picks up the new bundle on the next restart.

## Test

```bash
cd webui
npm run test
```

## Acknowledgements

- [`agent-chat-ui`](https://github.com/langchain-ai/agent-chat-ui) for UI and interaction inspiration across the chat surface.
