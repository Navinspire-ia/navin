# Deployment

Use this page after `navin agent -m "Hello!"` works locally. Deployment keeps long-running surfaces online: WebUI, chat apps, heartbeat, Dream, cron jobs, and channel connections.

## Before You Deploy

Check these once before Docker, systemd, or LaunchAgent:

| Check | Why it matters |
|---|---|
| `navin status` shows the expected config and workspace | Confirms the process will read the instance you meant to run |
| `navin agent -m "Hello!"` works | Proves install, config, provider, model, and workspace writes before adding a service layer |
| Secrets are in environment variables or protected config files | API keys, bot tokens, OAuth state, and chat credentials should not be world-readable |
| `~/.navin/` or your custom config/workspace path is persistent | Sessions, memory, channel login state, generated artifacts, and cron jobs live there |
| Channel access control is intentional | Use `allowFrom`, pairing, WebSocket `token`/`tokenIssueSecret`, or private test channels before exposing the bot |
| Ports are planned | Gateway health defaults to local-only `127.0.0.1:18790`; WebUI/WebSocket defaults to `8765`; `navin serve` defaults to `8900` |
| Logs are easy to reach | Use `docker compose logs`, `journalctl`, LaunchAgent log files, or `navin gateway --verbose` while diagnosing startup |

Restart the deployed process after editing `config.json`. Long-running processes read config at startup.

## Choose a Runtime

| Runtime | Use it for | State location | Useful first command |
|---|---|---|---|
| Docker Compose | Repeatable container runs on Linux servers or workstations | Bind-mount `~/.navin` to `/home/navin/.navin` | `docker compose run --rm navin-cli agent -m "Hello!"` |
| Docker CLI | Manual container testing or small one-off hosts | Bind-mount `~/.navin` to `/home/navin/.navin` | `docker run -v ~/.navin:/home/navin/.navin --rm navin status` |
| systemd user service | Linux user-level gateway that restarts automatically | Host user's `~/.navin` unless you pass explicit paths | `systemctl --user status navin-gateway` |
| macOS LaunchAgent | macOS gateway that starts after login | Host user's `~/.navin` unless the plist passes explicit paths | `launchctl list | grep ai.navin.gateway` |

## Docker

> [!TIP]
> The `-v ~/.navin:/home/navin/.navin` flag mounts your local config directory into the container, so your config and workspace persist across container restarts.
> The container runs as the non-root user `navin` (UID 1000) and reads config from `/home/navin/.navin`. Always mount your host config directory to `/home/navin/.navin`, not `/root/.navin`.
> If you get **Permission denied**, fix ownership on the host first: `sudo chown -R 1000:1000 ~/.navin`, or pass `--user $(id -u):$(id -g)` to match your host UID. Podman users can use `--userns=keep-id` instead.
>
> [!IMPORTANT]
> Official Docker usage currently means building from this repository with the included `Dockerfile`. Docker Hub images under third-party namespaces are not maintained or verified by EIAGEN/navin-claw; do not mount API keys or bot tokens into them unless you trust the publisher.

> [!IMPORTANT]
> The gateway and WebSocket channel default to `host: "127.0.0.1"` in `config.json` (set in `navin/config/schema.py`). Docker `-p` port forwarding cannot reach a container's loopback interface, so for the host or LAN to reach the exposed ports you must set both binds to `0.0.0.0` in `~/.navin/config.json` before starting the container. To serve the bundled WebUI from Docker, bind the WebSocket channel externally and protect bootstrap with a secret:
>
> ```json
> {
>   "gateway": { "host": "0.0.0.0" },
>   "channels": {
>     "websocket": {
>       "host": "0.0.0.0",
>       "port": 8765,
>       "tokenIssueSecret": "your-secret-here"
>     }
>   }
> }
> ```
>
> When the WebSocket `host` is `0.0.0.0`, the channel refuses to start unless `token` or `tokenIssueSecret` is also configured. See [`webui.md#lan-access`](./webui.md#lan-access) for details.
> The gateway health route itself is intentionally minimal and unauthenticated. When the
> container binds it to `0.0.0.0`, publish port `18790` to host loopback only; place any
> remotely monitored health endpoint behind a firewall or reverse proxy. If another host
> must probe it directly, replace `127.0.0.1` in the port mapping with a trusted host
> interface and restrict inbound traffic to the monitoring system.

### Docker Compose

```bash
docker compose run --rm navin-cli onboard   # first-time setup
vim ~/.navin/config.json                     # add API keys
docker compose up -d navin-gateway           # start gateway
```

```bash
docker compose run --rm navin-cli agent -m "Hello!"   # run CLI
docker compose logs -f navin-gateway                   # view logs
docker compose down                                      # stop
```

### Docker

```bash
# Build the image
docker build -t navin .

# Initialize config (first time only)
docker run -v ~/.navin:/home/navin/.navin --rm navin onboard

# Edit config on host to add API keys
vim ~/.navin/config.json

# Run gateway (connects to enabled channels, e.g. Telegram/Discord/Slack).
# Mirrors the security caps and port mappings declared in docker-compose.yml:
#   - `--cap-drop ALL --cap-add SYS_ADMIN` + unconfined apparmor/seccomp are required
#     when `tools.exec.sandbox: "bwrap"` is enabled (bwrap needs CAP_SYS_ADMIN for
#     user namespaces). Without them, `bwrap` exits with `clone3: Operation not permitted`.
#   - `-p 8765:8765` exposes the WebSocket channel / WebUI alongside the gateway health
#     endpoint on 18790.
docker run \
  --cap-drop ALL --cap-add SYS_ADMIN \
  --security-opt apparmor=unconfined \
  --security-opt seccomp=unconfined \
  -v ~/.navin:/home/navin/.navin \
  -p 127.0.0.1:18790:18790 -p 8765:8765 \
  navin gateway

# Or run a single command
docker run -v ~/.navin:/home/navin/.navin --rm navin agent -m "Hello!"
docker run -v ~/.navin:/home/navin/.navin --rm navin status
```

## Linux Service

Run the gateway as a systemd user service so it starts automatically and restarts on failure.

Preview the generated unit first:

```bash
navin gateway install-service --manager systemd --dry-run
```

Install, enable, and start it:

```bash
navin gateway install-service --manager systemd
```

For a custom instance, pass the same config/workspace selector you use to run the gateway:

```bash
navin gateway install-service \
  --manager systemd \
  --name navin-telegram \
  --config ~/.navin-telegram/config.json \
  --workspace ~/.navin-telegram/workspace
```

Common operations:

```bash
systemctl --user status navin-gateway        # check status
systemctl --user restart navin-gateway       # restart after config changes
journalctl --user -u navin-gateway -f        # follow logs
navin gateway uninstall-service --manager systemd
```

The installer writes `~/.config/systemd/user/navin-gateway.service`, runs
`systemctl --user daemon-reload`, enables the unit, and restarts it. It uses the
current Python executable with `python -m navin gateway --foreground`, so the
service runs in the same environment you used to install navin.

> **Note:** User services only run while you are logged in. To keep the gateway running after logout, enable lingering:
>
> ```bash
> loginctl enable-linger $USER
> ```

## macOS LaunchAgent

Use a LaunchAgent when you want `navin gateway` to stay online after you log in, without keeping a terminal open.

Preview the generated plist first:

```bash
navin gateway install-service --manager launchd --dry-run
```

Install, load, enable, and start it:

```bash
navin gateway install-service --manager launchd
```

For a custom instance:

```bash
navin gateway install-service \
  --manager launchd \
  --name navin-telegram \
  --config ~/.navin-telegram/config.json \
  --workspace ~/.navin-telegram/workspace
```

Common operations:

```bash
launchctl list | grep ai.navin.gateway
launchctl kickstart -k gui/$(id -u)/ai.navin.gateway
navin gateway uninstall-service --manager launchd
```

The installer writes `~/Library/LaunchAgents/ai.navin.gateway.plist`, uses the
current Python executable with `python -m navin gateway --foreground`, and
writes LaunchAgent logs under `~/.navin/logs/`.

> **Note:** if startup fails with "address already in use", stop the manually started `navin gateway` process first.
