# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The security rule pack finds every vulnerability class of a planted fixture
and stays quiet on prose, regex sources and placeholders."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.security.rules import A03, LINE_RULES, scan_dependencies
from navin.security.scan import run_security_scan, scan_heuristics

_FIXTURE: dict[str, str] = {
    "app/api.py": (
        "import os, subprocess, pickle, yaml, hashlib, random, sqlite3, requests, jwt\n"
        "from flask import Flask, request, redirect, render_template_string\n"
        "from lxml import etree\n"
        "app = Flask(__name__)\n"
        'app.secret_key = "s3cr3t-fl4sk-k3y-prod-2024"\n'
        'AWS_KEY = "AKIAJ4QX6ZKQ3PDW7Q2A"\n'
        'GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n'
        '@app.route("/user")\n'
        "def user():\n"
        '    cur = sqlite3.connect("db").execute(f"SELECT * FROM users WHERE id = {request.args[\'id\']}")\n'
        '    os.system("ping -c 1 " + request.args["host"])\n'
        '    subprocess.call("nslookup " + request.args["host"], shell=True)\n'
        "    obj = pickle.loads(request.data)\n"
        '    cfg = yaml.load(request.args["cfg"])\n'
        '    return eval(request.args["expr"])\n'
        "def files():\n"
        '    with open("/var/data/" + request.args["name"]) as fh:\n'
        "        return fh.read()\n"
        "def fetch():\n"
        '    return requests.get(request.args["url"], verify=False).text\n'
        "def go():\n"
        '    return redirect(request.args["next"])\n'
        "def hello():\n"
        '    return render_template_string("<h1>" + request.args["name"] + "</h1>")\n'
        "def xml():\n"
        "    return etree.fromstring(request.data, etree.XMLParser(resolve_entities=True))\n"
        "def hash_pw(pw):\n"
        "    return hashlib.md5(pw.encode()).hexdigest()\n"
        "def token():\n"
        "    token = str(random.random())\n"
        "    return token\n"
        "def verify(tok):\n"
        '    return jwt.decode(tok, options={"verify_signature": False})\n'
        "def cors(resp):\n"
        '    resp.headers["Access-Control-Allow-Origin"] = "*"\n'
        '    resp.set_cookie("session", "abc", secure=False, httponly=False)\n'
        "    return resp\n"
        "import pdb; pdb.set_trace()\n"
        'app.run(host="0.0.0.0", debug=True)\n'
    ),
    "web/server.js": (
        'const { exec } = require("child_process");\n'
        'const express = require("express");\n'
        "const app = express();\n"
        'const JWT_SECRET = "changeme";\n'
        'app.use(require("cors")({ origin: "*" }));\n'
        'app.get("/run", (req, res) => {\n'
        '  exec("ls " + req.query.dir, (e, out) => res.send(out));\n'
        "  const fn = new Function(req.query.code);\n"
        '  res.send(fs.readFileSync("/data/" + req.query.f));\n'
        "  const user = User.findOne({ username: req.body.username, password: req.body.password });\n"
        '  jwt.verify(req.query.t, JWT_SECRET, { algorithms: ["none", "HS256"] });\n'
        "  res.cookie(\"sid\", t, { httpOnly: false, secure: false });\n"
        "  res.redirect(req.query.next);\n"
        "  _.merge(cfg, req.body);\n"
        '  res.send("<div>" + req.query.msg + "</div>");\n'
        '  document.getElementById("x").innerHTML = req.query.msg;\n'
        "});\n"
        'https.request({ host: "x", rejectUnauthorized: false });\n'
    ),
    "web/index.php": (
        "<?php\n"
        "$r = mysql_query(\"SELECT * FROM users WHERE id = $id\");\n"
        "system(\"ls \" . $_GET['d']);\n"
        "include($_GET['page'] . \".php\");\n"
        "echo $_GET['msg'];\n"
        "unserialize($_COOKIE['data']);\n"
        "move_uploaded_file($_FILES['f']['tmp_name'], \"/up/\" . $_FILES['f']['name']);\n"
        "extract($_REQUEST);\n"
    ),
    "web/app.rb": (
        "class UsersController < ApplicationController\n"
        "  def show\n"
        "    User.where(\"name = '#{params[:name]}'\")\n"
        "    system(\"ls #{params[:dir]}\")\n"
        "    Marshal.load(params[:data])\n"
        "    render inline: params[:tpl]\n"
        "    redirect_to params[:next]\n"
        "    html = raw(params[:msg])\n"
        "    open(params[:url])\n"
        "  end\n"
        "  skip_before_action :verify_authenticity_token\n"
        "end\n"
    ),
    "web/main.go": (
        "package main\n"
        'func h(w http.ResponseWriter, r *http.Request) {\n'
        '  db.Query("SELECT * FROM t WHERE id = " + r.URL.Query().Get("id"))\n'
        '  exec.Command("sh", "-c", "ls "+r.FormValue("d")).Run()\n'
        "  tr := &tls.Config{InsecureSkipVerify: true, MinVersion: tls.VersionTLS10}\n"
        '  fmt.Fprintf(w, r.FormValue("x"))\n'
        "}\n"
    ),
    "web/Repo.java": (
        "public class Repo {\n"
        '  void q(Connection c, String id) throws Exception {\n'
        '    c.createStatement().executeQuery("SELECT * FROM users WHERE id = " + id);\n'
        '    Runtime.getRuntime().exec("ping " + id);\n'
        "    new ObjectInputStream(new FileInputStream(id)).readObject();\n"
        '    Cipher.getInstance("DES/ECB/PKCS5Padding");\n'
        "  }\n"
        "}\n"
    ),
    "web/Program.cs": (
        "class P {\n"
        '  static void Main(string id) {\n'
        '    var cmd = new SqlCommand($"DELETE FROM u WHERE id = {id}");\n'
        '    Process.Start("cmd.exe", "/c dir " + id);\n'
        "    var bf = new BinaryFormatter();\n"
        "    ServicePointManager.ServerCertificateValidationCallback = (a,b,c,d) => true;\n"
        "  }\n"
        "}\n"
    ),
    "infra/Dockerfile": (
        "FROM ubuntu:latest\n"
        "ENV API_TOKEN=abcdef1234567890abcdef1234567890\n"
        "RUN curl http://example.com/install.sh | sh\n"
        "RUN chmod 777 /app\n"
        "EXPOSE 22\n"
        'CMD ["python", "app.py"]\n'
    ),
    "infra/main.tf": (
        'resource "aws_s3_bucket" "b" {\n  acl = "public-read"\n}\n'
        'resource "aws_security_group" "sg" {\n  ingress {\n    cidr_blocks = ["0.0.0.0/0"]\n  }\n}\n'
        'resource "aws_db_instance" "db" {\n  publicly_accessible = true\n  storage_encrypted = false\n'
        '  password = "Sup3rS3cretDbPass!"\n}\n'
    ),
    "infra/deploy.yaml": (
        "apiVersion: apps/v1\nkind: Deployment\nspec:\n  template:\n    spec:\n      hostPID: true\n"
        "      containers:\n      - name: app\n        image: myapp:latest\n        securityContext:\n"
        "          privileged: true\n          runAsUser: 0\n        env:\n        - name: DB_PASSWORD\n"
        '          value: "pr0d-db-passw0rd-2024"\n'
        "        volumeMounts:\n        - mountPath: /var/run/docker.sock\n"
    ),
    ".github/workflows/ci.yml": (
        "on: pull_request_target\njobs:\n  b:\n    permissions: write-all\n    steps:\n"
        "      - uses: actions/checkout@master\n"
        '      - run: echo "${{ github.event.issue.title }}" | bash\n'
    ),
    ".env": (
        "DATABASE_URL=postgres://admin:RealProdPassw0rd@db.prod.internal:5432/app\n"
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOP\n"
    ),
    "app/id_rsa": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Z3VS5JJcds3xfn\n-----END RSA PRIVATE KEY-----\n",
    "web/settings.py": (
        "DEBUG = True\nALLOWED_HOSTS = [\"*\"]\n"
        'SECRET_KEY = "django-insecure-abc123def456ghi789jkl012mno345pqr678"\n'
        "SESSION_COOKIE_SECURE = False\nCORS_ALLOW_ALL_ORIGINS = True\n"
    ),
    "web/package.json": '{"name":"x","dependencies":{"lodash":"4.17.15","minimist":"1.2.0"}}\n',
    "app/requirements.txt": "flask==0.12.2\npyyaml==5.1\n",
}

EXPECTED_IDS = {
    # secrets
    "aws-key", "github-token", "flask-secret", "private-key", "key-file-committed", "env-file-committed",
    "db-url-credentials", "openai-key", "django-insecure-key", "weak-jwt-secret", "tf-hardcoded-password",
    "docker-secret-env", "k8s-env-secret",
    # injection / exec
    "sql-injection", "sql-raw-var", "sql-concat-call", "nosql-injection", "os-system", "shell-true", "node-exec",
    "go-exec-shell", "java-exec", "php-exec", "ruby-exec", "cs-process-start", "eval-exec", "js-new-function",
    "ssti", "php-extract", "prototype-pollution", "go-fmt-print-input",
    # deserialization
    "pickle", "yaml-load", "java-deser", "php-unserialize", "ruby-marshal", "cs-binaryformatter",
    # xss
    "inner-html", "reflected-xss", "rails-raw",
    # crypto / tls
    "weak-hash", "weak-cipher", "insecure-random", "tls-verify-off", "old-tls",
    # auth / session / access
    "jwt-none", "insecure-cookie", "csrf-disabled",
    # files / ssrf / redirect / xxe / upload
    "path-traversal", "path-traversal-var", "php-lfi", "ssrf", "ruby-open-url", "redirect-input", "xxe",
    "file-upload",
    # misconfiguration
    "cors-wildcard", "debug-true", "allowed-hosts", "pdb-trace", "chmod-777", "curl-pipe-sh", "bind-all",
    # infra
    "tf-public-s3", "tf-public-ingress", "tf-public-db", "tf-unencrypted",
    "k8s-privileged", "k8s-host-ns", "k8s-root", "k8s-docker-sock", "k8s-latest-tag",
    "docker-latest", "docker-root-user", "docker-ssh-port",
    "gha-pr-target", "gha-injection", "gha-write-all", "gha-unpinned",
    # dependencies
    "dep:lodash", "dep:minimist", "dep:flask", "dep:pyyaml",
}


def _write(root: Path) -> None:
    for rel, body in _FIXTURE.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


class RulePackTest(unittest.TestCase):
    def test_every_rule_has_cwe_owasp_and_compiles(self):
        ids = [rule["id"] for rule in LINE_RULES]
        self.assertEqual(len(ids), len(set(ids)), "duplicate rule ids")
        self.assertGreater(len(ids), 100)
        for rule in LINE_RULES:
            self.assertTrue(rule["cwe"].startswith("CWE-"), rule["id"])
            self.assertRegex(rule["owasp"], r"^A(0[1-9]|10):2021", rule["id"])
            self.assertIn(rule["severity"], {"critical", "high", "medium", "low", "info"})
            # Summaries must survive the FP filter's hard exclusions.
            for banned in ("potentially", "could be", "theoretical", "open redirect", "rate limit", "denial of service"):
                self.assertNotIn(banned, rule["summary"].lower(), rule["id"])

    def test_fixture_covers_every_vulnerability_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root)
            result = run_security_scan(root, kind="quick", max_findings=300)
        self.assertTrue(result["ok"])
        found = {f["id"] for f in result["findings"]}
        missing = sorted(EXPECTED_IDS - found)
        self.assertEqual(missing, [], f"rules that missed the fixture: {missing}")
        self.assertGreaterEqual(result["counts"]["critical"], 25)
        self.assertLessEqual(result["dropped_count"], 3)
        # Every kept finding is grounded: location, excerpt, payload, PoC, CWE.
        for item in result["findings"]:
            self.assertTrue(item["file_path"], item["id"])
            self.assertTrue(item["malicious_input_example"], item["id"])
            self.assertIn(item["file_path"], item["poc_sketch"], item["id"])
            self.assertTrue(item["cwe"], item["id"])
        owasp = {f["owasp"] for f in result["findings"]}
        self.assertIn(A03, owasp)
        self.assertGreaterEqual(len(owasp), 8)

    def test_prose_regex_and_placeholders_stay_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "doc.py").write_text(
                '"""Reminder: never use shell=True, eval( or pickle.load on input.\n'
                'verify=False is also bad.\n"""\n'
                "# subprocess.run(cmd, shell=True) would be wrong here\n"
                'HINT = "install with: curl https://x.sh | sh"\n'
                'LABEL = {"password": "Password", "token": "Access token"}\n'
                'DSN = "postgres://user:password@localhost:5432/app"\n'
                # Built at runtime: a literal that looks like a Stripe key, even
                # an all-x placeholder, trips GitHub push protection on the
                # public mirror.
                'STRIPE = "sk_live_' + "x" * 24 + '"\n'
                'PATTERN = re.compile(r"shell\\s*=\\s*True|yaml\\.load\\(")\n'
                'digest = hashlib.md5(data, usedforsecurity=False).hexdigest()\n'
                "rows = conn.execute(f\"SELECT * FROM {_TABLE} WHERE id IN ({','.join('?' * n)})\", ids)\n",
                encoding="utf-8",
            )
            (root / "Dockerfile").write_text("FROM $BASE AS build\nUSER app\nHEALTHCHECK CMD true\n", encoding="utf-8")
            findings = scan_heuristics(root)
        ids = sorted(f["id"] for f in findings)
        self.assertEqual(ids, [], f"noise: {ids}")

    def test_nosec_marker_silences_a_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.py").write_text(
                "subprocess.run(cmd, shell=True)  # nosec: constant command\n"
                "subprocess.run(other, shell=True)\n",
                encoding="utf-8",
            )
            findings = scan_heuristics(root)
        self.assertEqual([f["line"] for f in findings if f["id"] == "shell-true"], [2])

    def test_dependency_pins(self):
        hits = scan_dependencies("package.json", '{"dependencies":{"lodash":"^4.17.21","minimist":"1.2.5"}}')
        self.assertEqual([h["id"] for h in hits], ["dep:minimist"])
        hits = scan_dependencies("requirements.txt", "pyyaml==6.0\nflask==0.12\n")
        self.assertEqual([h["id"] for h in hits], ["dep:flask"])
        self.assertIn("CVE-", hits[0]["summary"])


if __name__ == "__main__":
    unittest.main()
