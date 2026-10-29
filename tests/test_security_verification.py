# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Security verification engine: evidence promotes, a rescan closes."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from navin.security.model import Finding
from navin.security.verification import (
    Authorization,
    Expectation,
    VerificationRefusedError,
    add_regression_test,
    check_request,
    confirm_dataflow,
    confirm_dynamic,
    demonstrate,
    redact,
    rescan,
    send_probe,
    send_probe_async,
    triage,
)

SECRET_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"


class _Handler(BaseHTTPRequestHandler):
    hits: list[tuple[str, str]] = []

    def log_message(self, *args):  # silence the test output
        pass

    def _answer(self):
        type(self).hits.append((self.command, self.path))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://evil.example/steal")
            self.end_headers()
            return
        if self.path == "/slow":
            threading.Event().wait(3)
        body = {
            "/users/2": f'{{"id": 2, "email": "b@x.test", "token": "{SECRET_TOKEN}", "password": "hunter2hunter2"}}',
            "/big": "x" * 200000,
        }.get(self.path, '{"error": "not found"}')
        self.send_response(200 if self.path in {"/users/2", "/big"} else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "sid=topsecretcookie")
        self.send_header("Server", "demo")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body.encode())



for _verb in ("GET", "HEAD", "OPTIONS", "POST", "DELETE"):
    setattr(_Handler, f"do_{_verb}", _Handler._answer)


class _Server:
    def __enter__(self):
        _Handler.hits = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _idor(**extra) -> Finding:
    return Finding(id="idor-users", family="authorization", title="IDOR on /users/{id}", severity="high",
                   endpoint="GET /users/2", file_path="app/users.py", line=40, **extra)


class PolicyTest(unittest.TestCase):
    def test_only_read_methods_are_sent(self):
        auth = Authorization(targets=("http://127.0.0.1:8000",))
        for verb in ("get", "HEAD", "Options"):
            self.assertEqual(check_request(verb, "http://127.0.0.1:8000/x", auth), verb.upper())
        for verb in ("POST", "PUT", "PATCH", "DELETE", "", "TRACE"):
            with self.assertRaisesRegex(VerificationRefusedError, "refused"):
                check_request(verb, "http://127.0.0.1:8000/x", auth)

    def test_target_matches_scheme_host_and_port_only(self):
        auth = Authorization(targets=("https://staging.example.com", "localhost:3000"))
        self.assertTrue(auth.allows("https://staging.example.com/api/v1?x=1"))
        self.assertTrue(auth.allows("http://localhost:3000/x"))
        self.assertFalse(auth.allows("http://staging.example.com/api"))  # scheme differs
        self.assertFalse(auth.allows("https://staging.example.com:8443/api"))  # port differs
        self.assertFalse(auth.allows("https://staging.example.com.evil.test/"))  # look-alike host
        self.assertFalse(auth.allows("https://staging.example.com@evil.test/"))  # user-info trick
        self.assertFalse(auth.allows("http://localhost:3001/x"))
        self.assertFalse(auth.allows("file:///etc/passwd"))
        self.assertFalse(Authorization().allows("https://staging.example.com/"))

    def test_a_reproduction_needs_isolation_and_approval(self):
        url = "http://127.0.0.1:9/x"
        for isolated, approved, text in ((False, True, "isolated"), (True, False, "approval")):
            auth = Authorization(targets=("127.0.0.1:9",), isolated=isolated, human_approval=approved)
            with self.assertRaisesRegex(VerificationRefusedError, text):
                check_request("GET", url, auth, reproduction=True)
        check_request("GET", url, Authorization(targets=("127.0.0.1:9",), isolated=True, human_approval=True), reproduction=True)


# Built at run time: secret scanners flag a literal AWS key id, even in a test.
AWS_KEY_ID = "AKIA" + "ABCDEFGHIJKLMNOP"


class RedactionTest(unittest.TestCase):
    def test_credentials_never_survive_redaction(self):
        text = (f'Authorization: Bearer abcdefghijklmnop1234 {{"token": "{SECRET_TOKEN}", "password": "hunter2hunter2"}} '
                f"https://x.test/cb?api_key=SECRETVALUE&ok=1 {AWS_KEY_ID}")
        out = redact(text)
        for leaked in ("abcdefghijklmnop1234", SECRET_TOKEN, "hunter2hunter2", "SECRETVALUE", AWS_KEY_ID):
            self.assertNotIn(leaked, out)
        self.assertIn("ok=1", out)


class ProbeTest(unittest.TestCase):
    def test_probe_reads_a_bounded_redacted_answer_and_never_follows_a_redirect(self):
        with _Server() as srv:
            auth = Authorization(targets=(srv.base,))
            ok = send_probe("GET", f"{srv.base}/users/2", auth)
            self.assertEqual((ok.ok, ok.status), (True, 200))
            shown = ok.observed()
            self.assertIn('"email": "b@x.test"', shown)
            self.assertNotIn(SECRET_TOKEN, shown)
            self.assertNotIn("hunter2hunter2", shown)
            self.assertNotIn("topsecretcookie", shown)  # Set-Cookie is not recorded
            big = send_probe("GET", f"{srv.base}/big", auth, max_body=1000)
            self.assertEqual((len(big.body), big.truncated), (1000, True))
            moved = send_probe("GET", f"{srv.base}/redirect", auth)
            self.assertEqual(moved.status, 302)
            self.assertNotIn(("GET", "/steal"), _Handler.hits)
            head = send_probe("HEAD", f"{srv.base}/users/2", auth)
            self.assertEqual((head.status, head.body), (200, ""))
            slow = send_probe("GET", f"{srv.base}/slow", auth, timeout_s=0.5)
            self.assertFalse(slow.ok)
            self.assertIn("no response", slow.observed())
            self.assertEqual(asyncio.run(send_probe_async("GET", f"{srv.base}/users/2", auth)).status, 200)

    def test_nothing_is_sent_outside_the_scope(self):
        with _Server() as srv:
            with self.assertRaises(VerificationRefusedError):
                send_probe("GET", f"{srv.base}/users/2", Authorization(targets=("http://127.0.0.1:1",)))
            with self.assertRaises(VerificationRefusedError):
                send_probe("DELETE", f"{srv.base}/users/2", Authorization(targets=(srv.base,)))
            self.assertEqual(_Handler.hits, [])


class ConfirmTest(unittest.TestCase):
    def test_dataflow_needs_a_source_and_a_sink(self):
        item = _idor()
        weak = confirm_dataflow(item, ["app/users.py:40 query built from the id"])
        self.assertEqual((weak.verified, item.status, item.evidence), (False, "potential", []))
        strong = confirm_dataflow(item, ["app/routes.py:12 user_id = request.args['id']", "app/users.py:40 cursor.execute(f'... {user_id}')"])
        self.assertEqual((strong.verified, item.status, item.evidence[0].kind), (True, "confirmed", "dataflow"))
        self.assertIn("1. app/routes.py:12", item.evidence[0].observed)

    def test_dynamic_confirmation_records_a_redacted_request_and_response(self):
        with _Server() as srv:
            auth = Authorization(targets=(srv.base,))
            item = _idor()
            expect = Expectation(status_in=(200,), body_contains=('"email"',), headers_present=("Content-Type",))
            out = confirm_dynamic(item, f"{srv.base}/users/2", auth, expect, headers={"Authorization": "Bearer abcdefghijklmnop1234"})
            self.assertEqual((out.verified, item.status), (True, "confirmed"))
            proof = item.evidence[-1]
            self.assertEqual(proof.kind, "dynamic")
            self.assertIn("[headers: Authorization]", proof.request)
            self.assertNotIn("abcdefghijklmnop1234", proof.request)
            self.assertNotIn(SECRET_TOKEN, proof.observed)

    def test_a_probe_that_shows_nothing_leaves_the_status_alone_and_says_why(self):
        with _Server() as srv:
            auth = Authorization(targets=(srv.base,))
            item = _idor()
            out = confirm_dynamic(item, f"{srv.base}/missing", auth, Expectation(status_in=(200,), body_contains=("email",)))
            self.assertEqual((out.verified, item.status, item.evidence, out.refused), (False, "potential", [], False))
            self.assertEqual(len(out.mismatches), 2)
            self.assertIn("status 404", out.reason)
            self.assertEqual(item.validation.rescan, "pending")  # not proof of absence: nothing marked clean
            down = confirm_dynamic(_idor(), "http://127.0.0.1:1/x", Authorization(targets=("127.0.0.1:1",)), Expectation(status_in=(200,)), timeout_s=1)
            self.assertIn("no response", down.reason)

    def test_refusals_are_outcomes_not_exceptions_and_nothing_is_sent(self):
        with _Server() as srv:
            item = _idor()
            off = confirm_dynamic(item, f"{srv.base}/users/2", Authorization(), Expectation(status_in=(200,)))
            post = confirm_dynamic(item, f"{srv.base}/users/2", Authorization(targets=(srv.base,)), Expectation(status_in=(200,)), method="POST")
            empty = confirm_dynamic(item, f"{srv.base}/users/2", Authorization(targets=(srv.base,)), Expectation())
            self.assertEqual((off.refused, post.refused, empty.refused), (True, True, False))
            self.assertIn("predicts nothing", empty.reason)
            self.assertEqual((item.status, _Handler.hits), ("potential", []))

    def test_expectation_checks_every_condition(self):
        from navin.security.verification import ProbeResult

        result = ProbeResult(method="GET", url="http://x/", status=200, headers={"Server": "demo"}, body="hello 42")
        self.assertEqual(Expectation(status_in=(200,), body_contains=("hello",), body_regex=r"\d+", headers_present=("server",),
                                     headers_absent=("Strict-Transport-Security",), header_contains=(("Server", "dem"),),
                                     status_not_in=(500,), body_absent=("error",)).mismatches(result), [])
        misses = Expectation(status_in=(404,), headers_absent=("Server",), body_absent=("hello",), body_regex="^x").mismatches(result)
        self.assertEqual(len(misses), 4)


class ReproductionTest(unittest.TestCase):
    def test_exploitable_needs_confirmation_isolation_approval_and_the_predicted_impact(self):
        with _Server() as srv:
            expect = Expectation(status_in=(200,), body_contains=('"email"',))
            full = Authorization(targets=(srv.base,), isolated=True, human_approval=True)
            item = _idor()
            self.assertIn("confirm it first", demonstrate(item, f"{srv.base}/users/2", full, expect).reason)
            item.promote("confirmed")
            for auth, text in ((Authorization(targets=(srv.base,), human_approval=True), "isolated"),
                               (Authorization(targets=(srv.base,), isolated=True), "approval")):
                out = demonstrate(item, f"{srv.base}/users/2", auth, expect)
                self.assertEqual((out.refused, item.status), (True, "confirmed"))
                self.assertIn(text, out.reason)
            self.assertEqual(_Handler.hits, [])
            miss = demonstrate(item, f"{srv.base}/missing", full, expect)
            self.assertEqual((miss.verified, item.status), (False, "confirmed"))
            done = demonstrate(item, f"{srv.base}/users/2", full, expect)
            self.assertEqual((done.verified, item.status, item.evidence[-1].kind), (True, "exploitable", "reproduction"))
            self.assertEqual({method for method, _ in _Handler.hits}, {"GET"})


class TriageAndRescanTest(unittest.TestCase):
    def test_triage_picks_the_cheapest_proof_for_each_finding(self):
        dynamic = _idor()
        flow = Finding(id="sqli", family="injection", title="SQLi", file_path="a.py", line=3)
        bare = Finding(id="policy", family="audit_logging", title="No audit log")
        confirmed = Finding(id="xss", family="injection", title="XSS", file_path="b.py", status="confirmed")
        proven = Finding(id="rce", family="code_execution", title="RCE", file_path="c.py", status="exploitable")
        accepted = Finding(id="old", family="secrets", title="Old", file_path="d.py")
        accepted.validation.state = "accepted"
        groups = triage([dynamic, flow, bare, confirmed, proven, accepted])
        self.assertEqual({k: [f.id for f in v] for k, v in groups.items()},
                         {"dataflow": ["sqli"], "dynamic": ["idor-users"], "reproduce": ["xss"],
                          "static_only": ["policy"], "closed": ["rce", "old"]})

    def test_regression_test_must_exist_inside_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_idor.py").write_text("def test_idor():\n    assert True\n", encoding="utf-8")
            item = _idor()
            self.assertIn("does not exist", add_regression_test(item, "tests/test_missing.py", root).reason)
            self.assertIn("outside", add_regression_test(item, "../../etc/passwd", root).reason)
            self.assertTrue(add_regression_test(item, "tests/test_idor.py", root).verified)
            add_regression_test(item, "tests/test_idor.py", root)
            self.assertEqual(item.validation.regression_tests, ["tests/test_idor.py"])
            self.assertEqual([e.kind for e in item.evidence], ["regression"])
            self.assertEqual(item.status, "potential")  # a test is not a proof of the weakness

    def test_a_finding_is_closed_only_by_a_real_rescan_and_a_regression_test(self):
        item = _idor()
        self.assertIn("did not run", rescan(item, [], scanner_ran=False).reason)  # tool missing: not a fix
        self.assertEqual((item.validation.rescan, item.validation.state), ("pending", "open"))
        again = rescan(item, [_idor()], scanner_ran=True)
        self.assertEqual((again.verified, item.validation.rescan), (False, "still_present"))
        half = rescan(item, [], scanner_ran=True)
        self.assertEqual((half.verified, item.validation.rescan, item.validation.state), (True, "clean", "open"))
        self.assertIn("regression test", half.reason)
        item.validation.regression_tests.append("tests/test_idor.py")
        done = rescan(item, [], scanner_ran=True)
        self.assertEqual((done.verified, item.validation.state), (True, "fixed"))
        reopened = rescan(item, [_idor()], scanner_ran=True)
        self.assertEqual((reopened.verified, item.validation.state, item.validation.rescan), (False, "open", "still_present"))


if __name__ == "__main__":
    unittest.main()
