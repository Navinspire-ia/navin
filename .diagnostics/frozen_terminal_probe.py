"""Drive the packaged Navin's terminal over its own WebSocket and report the failure."""

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EXE = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Navin\navin-cli.exe")
LAUNCH = [EXE]
if len(sys.argv) > 1 and sys.argv[1] == "source":
    LAUNCH = [sys.executable, "-m", "navin"]
PORT = 8790
GATEWAY_PORT = 18795
LOG = os.path.expandvars(r"%USERPROFILE%\navin-term-diag-stderr.log")
PROJECT_UNC = r"\\wsl.localhost\Ubuntu\home\aymen\projects\deploy7\navin-ai-v2"


def wait_ready(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2):
                return True
        except Exception:
            time.sleep(1)
    return False


def bootstrap():
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/webui/bootstrap",
        headers={"Origin": f"http://127.0.0.1:{PORT}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def drive(info):
    import websockets

    path = info.get("ws_path") or "/"
    token = info.get("token")
    url = f"ws://127.0.0.1:{PORT}{path}?token={token}"
    print("connecting:", url, flush=True)
    async with websockets.connect(url, max_size=None) as ws:
        print("connected", flush=True)
        for shell in (None, "PowerShell", "cmd", "WSL: Ubuntu"):
            payload = {
                "type": "terminal_open",
                "terminal_id": f"diag-{int(time.time() * 1000)}",
                "cols": 80,
                "rows": 24,
            }
            if shell:
                payload["shell"] = shell
            print("\n>>> sending:", json.dumps(payload), flush=True)
            await ws.send(json.dumps(payload))
            deadline = time.time() + 12
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:
                    print("recv error:", type(exc).__name__, exc, flush=True)
                    break
                text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
                try:
                    msg = json.loads(text)
                except Exception:
                    continue
                event = msg.get("event") or msg.get("type") or ""
                if "terminal" in str(event):
                    print("<<<", text[:600], flush=True)
                    if event in ("terminal_exit", "terminal_error"):
                        break


def main():
    print("launching:", LAUNCH, flush=True)
    log = open(LOG, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            *LAUNCH,
            "webui",
            "--port",
            str(PORT),
            "--gateway-port",
            str(GATEWAY_PORT),
            "--no-open",
            "--yes",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        if not wait_ready():
            print("SERVER NEVER BECAME READY", flush=True)
            return
        print("server ready", flush=True)
        info = bootstrap()
        print("bootstrap keys:", sorted(info), flush=True)
        asyncio.run(drive(info))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        log.close()
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
        )
        print("\n===== SERVER LOG =====", flush=True)
        with open(LOG, encoding="utf-8", errors="replace") as handle:
            print(handle.read()[-6000:], flush=True)


if __name__ == "__main__":
    main()
