"""Exercise Navin's own WindowsTerminalSession, unfrozen, to isolate the exit."""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from navin.webui.terminal_ws import WindowsTerminalSession, prepare_launch  # noqa: E402

events = []


async def on_output(tid, data):
    events.append(("output", len(data), data[:80]))


async def on_exit(tid, code):
    events.append(("exit", code, None))


async def main():
    requested = sys.argv[1] if len(sys.argv) > 1 else None
    cwd = sys.argv[2] if len(sys.argv) > 2 else r"C:\Users\gadhg\.navin\workspace"
    if len(sys.argv) > 3 and sys.argv[3] == "frozen":
        sys.frozen = True
        print("simulating a frozen build", flush=True)
    name, path, args, resolved = prepare_launch(requested, cwd)
    print("prepare_launch ->", name, path, args, resolved, flush=True)

    session = WindowsTerminalSession(
        terminal_id="t1",
        shell_name=name,
        shell_path=path,
        shell_args=args,
        cwd=resolved,
        cols=80,
        rows=24,
        on_output=on_output,
        on_exit=on_exit,
    )
    print("session created, pid:", getattr(session._process, "pid", "?"), flush=True)

    for i in range(20):
        await asyncio.sleep(0.5)
        alive = session._process.isalive()
        if not alive:
            print(f"[{i * 0.5:.1f}s] DIED, exitstatus={session._process.exitstatus}", flush=True)
            break
        if i == 4:
            print("writing a command", flush=True)
            session.write(b"echo NAVIN_PROBE\r\n")
    else:
        print("still alive after 10s", flush=True)

    await asyncio.sleep(0.5)
    print("\n--- events ---", flush=True)
    for kind, a, b in events:
        print(kind, a, b, flush=True)
    session.close()


asyncio.run(main())
