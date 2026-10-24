from navin.agent.tools.shell import _desktop_logs_note, unbounded_mount_scan


def test_blocks_unbounded_find_on_windows_drive():
    assert unbounded_mount_scan("cd ~; find /mnt/c/Users/me/.navin -type f | head")
    assert unbounded_mount_scan("grep -rl foo /mnt/c/Users/me")
    assert unbounded_mount_scan("ls -R /mnt/d/data")


def test_allows_bounded_or_non_recursive():
    assert unbounded_mount_scan("find /mnt/c/x -maxdepth 3 -name '*.jsonl'") is None
    assert unbounded_mount_scan("timeout 20 find /mnt/c/x -type f") is None
    assert unbounded_mount_scan("ls /mnt/c/Users/me/.navin") is None
    assert unbounded_mount_scan("cat /mnt/c/x/a.jsonl") is None
    assert unbounded_mount_scan("find . -type f") is None
    assert unbounded_mount_scan("cd /mnt/c/x && ls -la; find ~/p -name a") is None


def test_desktop_logs_note_points_to_webui():
    note = _desktop_logs_note()
    assert "websocket_<id>.jsonl" in note and "webui" in note


def test_blocks_unbounded_scan_of_wsl_tree_from_windows():
    assert unbounded_mount_scan(r"Get-ChildItem -Recurse \\wsl.localhost\Ubuntu\home\me")
    assert unbounded_mount_scan(r"dir /s \\wsl$\Ubuntu\home\me")
    assert unbounded_mount_scan("find //wsl$/Ubuntu/home -name a")
    assert unbounded_mount_scan(r"Get-ChildItem -Recurse -Depth 2 \\wsl$\Ubuntu\home") is None
    assert unbounded_mount_scan(r"Get-ChildItem -Recurse C:\Users\me\proj") is None


def test_blocks_unbounded_scan_of_macos_network_and_cloud_folders():
    assert unbounded_mount_scan("find /Volumes/NAS/projects -name '*.py'")
    assert unbounded_mount_scan("grep -r TODO ~/Library/Mobile\\ Documents/ /Users/me/Library/Mobile Documents/x")
    assert unbounded_mount_scan("du -sh /Users/me/Library/CloudStorage/Dropbox")
    assert unbounded_mount_scan("find /Volumes/NAS -maxdepth 2 -name a") is None
    assert unbounded_mount_scan("ls /Volumes") is None
    assert unbounded_mount_scan("find /Users/me/proj -name a") is None


def test_macos_privacy_denial_gets_an_actionable_hint(monkeypatch):
    from navin.agent.tools import shell

    out = "ls: /Users/me/Desktop/proj: Operation not permitted"
    monkeypatch.setattr(shell.sys, "platform", "darwin")
    assert "Privacy & Security" in shell._failure_hint(out)
    assert "Privacy & Security" not in shell._failure_hint("rm: /usr/bin/x: Operation not permitted")
    monkeypatch.setattr(shell.sys, "platform", "linux")
    assert "Privacy & Security" not in shell._failure_hint(out)


def test_macos_denial_is_logged_once_and_not_retried(monkeypatch):
    """After a TCC refusal the agent gets a clear log line and moves on, no retry loop."""
    import asyncio

    from navin.agent.tools import shell

    monkeypatch.setattr(shell, "_MACOS_DENIED", set())
    monkeypatch.setattr(shell.sys, "platform", "darwin")
    logged = []
    monkeypatch.setattr(shell.logger, "warning", lambda msg, *a: logged.append(msg.format(*a)))
    out = "cat: /Users/me/Documents/notes.txt: Operation not permitted"
    hint = shell._failure_hint(out)
    shell._failure_hint(out)
    assert "continue" in hint and "/Users/me/Documents" in hint
    assert len(logged) == 1 and "System Settings" in logged[0]

    assert shell.macos_denied_refusal("ls /Users/me/Documents/x")
    assert shell.macos_denied_refusal("ls /Users/me/proj") is None

    async def never_spawn(*a, **k):
        raise AssertionError("a denied folder must not be retried")

    monkeypatch.setattr(shell.asyncio, "create_subprocess_exec", never_spawn)
    monkeypatch.setattr(shell.asyncio, "create_subprocess_shell", never_spawn)
    tool = shell.ExecTool(working_dir="/tmp")
    result = asyncio.run(tool.execute(command="cat /Users/me/Documents/notes.txt"))
    assert "not retrying" in str(result)
