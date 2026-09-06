"""Campaign submission and daemon reachability, gateway side.

The Evolve daemon is reached over a loopback TCP port it publishes, with a
token, in ``.navin/evolve/endpoint.json``. That replaced a Unix domain socket,
which left Windows with no daemon at all: CPython has no ``AF_UNIX`` there, and
the missing attribute used to raise ``AttributeError`` straight out of the
route. The Windows-shaped interpreter is still simulated here - by deleting the
attribute - but now to prove that nothing needs it.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from navin.webui import evolve_api


@pytest.fixture
def capture_daemon(monkeypatch, tmp_path):
    """Route enqueue_campaign around the real daemon and capture the payload."""
    captured: dict = {}
    monkeypatch.setattr(evolve_api, "_project_root", lambda raw: tmp_path)
    monkeypatch.setattr(evolve_api, "_save_autorun", lambda *args, **kwargs: None)

    def fake_call(root, method, payload):
        captured["method"] = method
        captured["payload"] = payload
        return {"job": 7}

    monkeypatch.setattr(evolve_api, "daemon_call", fake_call)
    return captured


class TestDirtyFlag:
    """`dirty` is the review panel's "Prove this change": prove the working
    tree with its pending, uncommitted fixes instead of the last commit."""

    def test_a_true_dirty_flag_reaches_the_daemon_as_a_bool(self, capture_daemon, tmp_path):
        evolve_api.enqueue_campaign(
            str(tmp_path), "proof.run", {"dirty": ["true"], "profile": ["standard"]}
        )
        params = capture_daemon["payload"]["params"]
        assert params["dirty"] is True
        assert params["profile"] == "standard"

    def test_one_counts_as_true_and_zero_as_false(self, capture_daemon, tmp_path):
        evolve_api.enqueue_campaign(str(tmp_path), "proof.run", {"dirty": ["1"]})
        assert capture_daemon["payload"]["params"]["dirty"] is True

        evolve_api.enqueue_campaign(str(tmp_path), "proof.run", {"dirty": ["0"]})
        assert capture_daemon["payload"]["params"]["dirty"] is False

    def test_an_absent_flag_is_not_sent_at_all(self, capture_daemon, tmp_path):
        """Default proofs must keep proving HEAD exactly as before."""
        evolve_api.enqueue_campaign(str(tmp_path), "proof.run", {})
        assert "dirty" not in capture_daemon["payload"]["params"]


class FakeDaemon:
    """A loopback listener speaking the engine's line protocol.

    Real sockets rather than a stubbed ``daemon_call``: the behaviour worth
    pinning - the token on the first frame, event frames arriving before the
    answer, a connection dropped mid-request - only exists on the wire.
    """

    def __init__(self, root: Path, token: str = "0f" * 32) -> None:
        self.root = root
        self.token = token
        self.requests: list[dict] = []
        self.preamble: list[dict] = []
        self.result: object = {"engine": "test", "jobs": []}
        self.error: dict | None = None
        self.close_without_answering = False

        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        # A blocked accept() keeps the listening socket alive even after
        # another thread closes it, so a "dead" daemon would go on answering
        # and every offline test would pass for the wrong reason.
        self._listener.settimeout(0.05)
        self.port: int = self._listener.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def publish(self, **overrides: object) -> None:
        """Write the endpoint file exactly as the Rust daemon does."""
        payload: dict = {
            "transport": "tcp",
            "host": "127.0.0.1",
            "port": self.port,
            "token": self.token,
            "pid": 4242,
            "protocol": 2,
        }
        payload.update(overrides)
        path = evolve_api.endpoint_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def close(self) -> None:
        """Stop answering, for good: the port must refuse the next connect."""
        self._stop.set()
        self._thread.join(timeout=2)
        try:
            self._listener.close()
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        with client:
            client.settimeout(5)
            buffer = b""
            while b"\n" not in buffer:
                try:
                    chunk = client.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
            request = json.loads(buffer.split(b"\n", 1)[0])
            self.requests.append(request)
            if self.close_without_answering:
                return
            if request.get("token") != self.token:
                self._send(
                    client,
                    {
                        "id": request.get("id"),
                        "error": {"code": "unauthorized", "message": "invalid token"},
                    },
                )
                return
            for frame in self.preamble:
                self._send(client, frame)
            if self.error is not None:
                self._send(client, {"id": request["id"], "error": self.error})
            else:
                self._send(client, {"id": request["id"], "result": self.result})

    @staticmethod
    def _send(client: socket.socket, frame: dict) -> None:
        client.sendall(json.dumps(frame).encode("utf-8") + b"\n")


@pytest.fixture
def daemon(tmp_path):
    fake = FakeDaemon(tmp_path)
    fake.publish()
    yield fake
    fake.close()


@pytest.fixture
def without_unix_sockets(monkeypatch):
    """A Windows-shaped interpreter: `socket` has no `AF_UNIX`."""
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)


class TestEndpointDiscovery:
    """The endpoint file is how a client finds the daemon, and it sits in a
    project directory - which can come from anywhere. Every field is checked
    before anything is dialled."""

    def test_a_published_endpoint_is_read_back(self, daemon, tmp_path):
        endpoint = evolve_api.read_endpoint(tmp_path)
        assert endpoint == {
            "host": "127.0.0.1",
            "port": daemon.port,
            "token": daemon.token,
            "pid": 4242,
        }

    def test_no_file_at_all_means_no_daemon_rather_than_an_error(self, tmp_path):
        assert evolve_api.read_endpoint(tmp_path) is None

    @pytest.mark.parametrize(
        "overrides",
        [
            {"port": 0},
            {"port": 70000},
            {"port": "8080"},
            {"port": True},
            {"token": ""},
            {"token": None},
            {"transport": "pipe"},
        ],
        ids=[
            "zero port",
            "port out of range",
            "port as text",
            "port as bool",
            "empty token",
            "missing token",
            "another transport",
        ],
    )
    def test_an_unusable_endpoint_reads_as_absent(self, daemon, tmp_path, overrides):
        daemon.publish(**overrides)
        assert evolve_api.read_endpoint(tmp_path) is None

    def test_a_half_written_file_reads_as_absent(self, tmp_path):
        path = evolve_api.endpoint_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text('{"transport": "tcp", "por', encoding="utf-8")
        assert evolve_api.read_endpoint(tmp_path) is None

    def test_an_endpoint_pointing_off_the_loopback_is_refused(self, daemon, tmp_path):
        """A daemon only ever binds loopback. A file naming anything else was
        not written by one, and following it would turn the gateway into a
        client of somebody else's server."""
        daemon.publish(host="10.0.0.7")
        assert evolve_api.read_endpoint(tmp_path) is None

    def test_clearing_removes_the_file_and_tolerates_its_absence(self, daemon, tmp_path):
        evolve_api.clear_endpoint(tmp_path)
        assert not evolve_api.endpoint_path(tmp_path).exists()
        evolve_api.clear_endpoint(tmp_path)


class TestDaemonCallOverLoopback:
    def test_a_call_reaches_the_daemon_and_returns_its_result(self, daemon, tmp_path):
        result = evolve_api.daemon_call(tmp_path, "engine.status", {"a": 1})
        assert result == {"engine": "test", "jobs": []}
        assert daemon.requests[0]["method"] == "engine.status"
        assert daemon.requests[0]["params"] == {"a": 1}

    def test_every_request_carries_the_endpoint_token(self, daemon, tmp_path):
        evolve_api.daemon_call(tmp_path, "engine.status", {})
        assert daemon.requests[0]["token"] == daemon.token

    def test_event_frames_arriving_first_do_not_become_the_answer(self, daemon, tmp_path):
        daemon.preamble = [
            {"kind": "event", "event": "run.started", "payload": {"job": 1}},
            {"id": 99, "result": "somebody else's"},
        ]
        assert evolve_api.daemon_call(tmp_path, "engine.status", {}) == daemon.result

    def test_a_rejected_token_asks_for_a_restart_not_for_a_login(self, daemon, tmp_path):
        """The endpoint file and the process on that port disagree. Nothing
        is wrong with the user's session, so this must not surface as a 401
        the dashboard would translate into "sign in again"."""
        daemon.publish(token="a-token-nobody-issued")
        with pytest.raises(evolve_api.EvolveApiError) as caught:
            evolve_api.daemon_call(tmp_path, "engine.status", {})
        assert caught.value.status == 502
        assert "start it again" in caught.value.message

    def test_no_endpoint_file_is_a_503_naming_the_file(self, tmp_path):
        with pytest.raises(evolve_api.EvolveApiError) as caught:
            evolve_api.daemon_call(tmp_path, "engine.status", {})
        assert caught.value.status == 503
        assert "endpoint.json" in caught.value.message

    def test_a_stale_endpoint_is_a_503_rather_than_a_hang(self, daemon, tmp_path):
        daemon.close()
        with pytest.raises(evolve_api.EvolveApiError) as caught:
            evolve_api.daemon_call(tmp_path, "engine.status", {})
        assert caught.value.status == 503
        assert "not reachable" in caught.value.message

    def test_a_daemon_hanging_up_mid_request_is_a_502(self, daemon, tmp_path):
        daemon.close_without_answering = True
        with pytest.raises(evolve_api.EvolveApiError) as caught:
            evolve_api.daemon_call(tmp_path, "engine.status", {})
        assert caught.value.status == 502

    def test_a_daemon_error_frame_keeps_its_message(self, daemon, tmp_path):
        daemon.error = {"code": "busy", "message": "queue is full"}
        with pytest.raises(evolve_api.EvolveApiError) as caught:
            evolve_api.daemon_call(tmp_path, "engine.status", {})
        assert caught.value.status == 502
        assert "queue is full" in caught.value.message


class TestDaemonOnHostsWithoutUnixSockets:
    """CPython does not expose ``AF_UNIX`` on Windows. The daemon used to
    speak nothing else, so touching the socket there raised ``AttributeError``
    out of the route and the gateway answered with the ``websockets``
    handshake boilerplate ("Failed to open a WebSocket connection") - printed
    as-is by the dashboard. Since the transport is a loopback port, the same
    interpreter is simply an ordinary client."""

    def test_the_transport_is_available_without_af_unix(self, without_unix_sockets):
        assert evolve_api.daemon_transport_supported() is True

    def test_a_full_call_works_on_an_interpreter_with_no_af_unix(
        self, without_unix_sockets, daemon, tmp_path
    ):
        assert evolve_api.daemon_call(tmp_path, "engine.status", {}) == daemon.result

    def test_the_snapshot_never_reports_unsupported_any_more(
        self, without_unix_sockets, tmp_path
    ):
        snapshot = evolve_api.daemon_snapshot(tmp_path)
        assert snapshot["supported"] is True
        assert snapshot["reason"] == evolve_api.DAEMON_REASON_NOT_RUNNING

    def test_the_overview_still_renders_with_its_artefacts(
        self, without_unix_sockets, monkeypatch, tmp_path
    ):
        """The dashboard reads artefact files straight off disk: no daemon
        must not empty it."""
        proofs = tmp_path / ".navin" / "proofs"
        proofs.mkdir(parents=True)
        (proofs / "p1.json").write_text('{"robustness_score": 91}', encoding="utf-8")
        monkeypatch.setattr(evolve_api, "_manifest_hint", lambda root: None)
        monkeypatch.setattr(evolve_api, "resolve_engine_bin", lambda: None)
        monkeypatch.setattr(evolve_api, "_model_presets", lambda: ([], None))

        payload = evolve_api.overview(str(tmp_path))
        assert payload["daemon"]["supported"] is True
        assert payload["proofs"][0]["robustness_score"] == 91

    def test_starting_a_daemon_is_no_longer_refused_for_the_platform(
        self, without_unix_sockets, monkeypatch, tmp_path
    ):
        """It may still fail for want of a binary - a 501 naming the engine -
        but never because the host cannot host one."""
        monkeypatch.setattr(evolve_api, "resolve_engine_bin", lambda: None)
        with pytest.raises(evolve_api.EvolveApiError) as caught:
            evolve_api.start_daemon(str(tmp_path))
        assert "navin-engine binary not found" in caught.value.message

    def test_stopping_without_a_daemon_is_a_no_op(self, without_unix_sockets, tmp_path):
        assert evolve_api.stop_daemon(str(tmp_path)) == {"stopped": False, "online": False}


class TestDaemonSnapshot:
    def test_a_missing_endpoint_reads_as_not_running(self, monkeypatch, tmp_path):
        def refuse(root, method, params):
            raise evolve_api.EvolveApiError(503, "engine daemon not reachable: nope")

        monkeypatch.setattr(evolve_api, "daemon_call", refuse)
        assert evolve_api.daemon_snapshot(tmp_path) == {
            "online": False,
            "status": None,
            "supported": True,
            "reason": evolve_api.DAEMON_REASON_NOT_RUNNING,
        }

    def test_a_daemon_that_answers_badly_is_not_confused_with_an_absent_one(
        self, monkeypatch, tmp_path
    ):
        def misbehave(root, method, params):
            raise evolve_api.EvolveApiError(504, "daemon did not answer in time")

        monkeypatch.setattr(evolve_api, "daemon_call", misbehave)
        snapshot = evolve_api.daemon_snapshot(tmp_path)
        assert snapshot["reason"] == evolve_api.DAEMON_REASON_UNREACHABLE
        assert snapshot["online"] is False

    def test_a_live_daemon_reports_online_with_its_status(self, daemon, tmp_path):
        assert evolve_api.daemon_snapshot(tmp_path) == {
            "online": True,
            "status": {"engine": "test", "jobs": []},
            "supported": True,
            "reason": None,
        }


class TestDaemonLifecycle:
    def test_starting_an_already_serving_daemon_reports_it_rather_than_spawning(
        self, daemon, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            evolve_api.subprocess,
            "Popen",
            lambda *a, **k: pytest.fail("a second daemon was spawned"),
        )
        assert evolve_api.start_daemon(str(tmp_path)) == {"started": False, "online": True}

    def test_the_start_wait_outlasts_a_cold_wsl_boot(self):
        assert evolve_api.DAEMON_START_WAIT_S == 25.0

    def test_a_stale_endpoint_is_dropped_before_the_new_daemon_is_awaited(
        self, tmp_path, monkeypatch
    ):
        """A daemon killed without cleanup leaves a file pointing at a dead
        port. Kept, it would make the wait below poll the corpse and time out
        while the new daemon is up."""
        dead = FakeDaemon(tmp_path)
        dead.publish()
        dead.close()
        live = FakeDaemon(tmp_path)

        class Spawned:
            pid = 321

            def __init__(self, *args, **kwargs):
                live.publish()

        monkeypatch.setattr(evolve_api, "resolve_engine_bin", lambda: "/usr/bin/true")
        monkeypatch.setattr(evolve_api.subprocess, "Popen", Spawned)
        try:
            assert evolve_api.start_daemon(str(tmp_path)) == {"started": True, "online": True}
        finally:
            live.close()
        assert (tmp_path / ".navin" / "evolve" / "daemon.pid").read_text() == "321"

    def test_stopping_asks_over_ipc_first(self, daemon, tmp_path):
        """The IPC path is the only one that reaches a daemon running inside
        a WSL distribution, where the recorded pid is the wsl.exe relay."""

        def retract(_frame=None):
            evolve_api.clear_endpoint(tmp_path)

        daemon.result = {"stopping": True}
        original = daemon._handle

        def handle(client):
            original(client)
            retract()

        daemon._handle = handle
        assert evolve_api.stop_daemon(str(tmp_path)) == {"stopped": True, "online": False}
        assert daemon.requests[0]["method"] == "engine.shutdown"

    def test_stopping_falls_back_to_the_recorded_pid(self, tmp_path, monkeypatch):
        dead = FakeDaemon(tmp_path)
        dead.publish()
        dead.close()
        evolve_dir = tmp_path / ".navin" / "evolve"
        (evolve_dir / "daemon.pid").write_text("777", encoding="utf-8")
        ended: list[int] = []
        monkeypatch.setattr(evolve_api, "_terminate", lambda pid: ended.append(pid) or True)

        assert evolve_api.stop_daemon(str(tmp_path)) == {"stopped": True, "online": False}
        assert ended == [777]
        # The signalled daemon never retracted its own file; the gateway did.
        assert evolve_api.read_endpoint(tmp_path) is None

    def test_stopping_without_a_usable_pid_is_a_conflict_not_a_crash(self, tmp_path):
        dead = FakeDaemon(tmp_path)
        dead.publish()
        dead.close()
        with pytest.raises(evolve_api.EvolveApiError) as caught:
            evolve_api.stop_daemon(str(tmp_path))
        assert caught.value.status == 409


class TestDaemonLaunchAcrossTheWslBoundary:
    """A project opened from Windows at ``\\\\wsl.localhost\\...`` is a Linux
    project: its toolchain, its dependencies and the commands the engine runs
    for a proof all live in the distribution. The daemon is started there,
    binds a loopback port inside the guest, and is reached from the Windows
    host through WSL 2's loopback forwarding."""

    UNC = "\\\\wsl.localhost\\Ubuntu\\home\\me\\proj"

    @pytest.fixture
    def on_windows(self, monkeypatch):
        monkeypatch.setattr(evolve_api.sys, "platform", "win32")
        monkeypatch.setattr(evolve_api.wsl, "wsl_executable", lambda: "wsl.exe")
        monkeypatch.setattr(evolve_api.wsl, "resolve_distro", lambda name: "Ubuntu")

    def test_the_daemon_is_started_inside_the_distribution(self, on_windows):
        argv, cwd = evolve_api._daemon_argv(Path(self.UNC))
        assert argv[0] == "wsl.exe"
        assert argv[1:4] == ["-d", "Ubuntu", "--cd"]
        assert argv[4] == "/home/me/proj"
        assert argv[-3:-1] == ["bash", "-lc"]
        # The posix path, never the UNC one: inside the guest the share does
        # not exist.
        assert "/home/me/proj" in argv[-1]
        assert "wsl.localhost" not in argv[-1]
        # wsl.exe cannot hold a UNC working directory; --cd is what places it.
        assert cwd is None

    def test_a_login_shell_is_used_so_the_guest_toolchain_is_on_path(self, on_windows):
        argv, _ = evolve_api._daemon_argv(Path(self.UNC))
        assert argv[-2] == "-lc"
        assert argv[-1].startswith("exec navin-engine daemon ")

    def test_without_wsl_exe_the_refusal_names_the_reason(self, on_windows, monkeypatch):
        monkeypatch.setattr(evolve_api.wsl, "wsl_executable", lambda: None)
        with pytest.raises(evolve_api.EvolveApiError) as caught:
            evolve_api._daemon_argv(Path(self.UNC))
        assert caught.value.status == 501
        assert "WSL" in caught.value.message

    def test_an_ordinary_windows_project_runs_the_windows_binary(
        self, on_windows, monkeypatch
    ):
        monkeypatch.setattr(evolve_api, "resolve_engine_bin", lambda: "C:\\navin-engine.exe")
        argv, cwd = evolve_api._daemon_argv(Path("C:\\code\\proj"))
        assert argv == ["C:\\navin-engine.exe", "daemon", "C:\\code\\proj"]
        assert cwd == "C:\\code\\proj"

    def test_a_unix_host_never_reaches_for_wsl(self, monkeypatch, tmp_path):
        monkeypatch.setattr(evolve_api.sys, "platform", "linux")
        monkeypatch.setattr(
            evolve_api.wsl,
            "parse_unc",
            lambda text: pytest.fail("WSL routing was consulted off Windows"),
        )
        monkeypatch.setattr(evolve_api, "resolve_engine_bin", lambda: "/opt/navin-engine")
        argv, cwd = evolve_api._daemon_argv(tmp_path)
        assert argv == ["/opt/navin-engine", "daemon", str(tmp_path)]
        assert cwd == str(tmp_path)
