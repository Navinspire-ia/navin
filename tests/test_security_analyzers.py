# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Security analyzers on real code: dataflow, auth, tenant isolation, business logic.

The fixtures below are deliberately vulnerable source code written to a
temporary folder and read as text by the analyzers: none of it is imported or run.
"""

from __future__ import annotations

import asyncio
import tempfile
import textwrap
import unittest
from pathlib import Path

from navin.security.analyzers import (
    ANALYZERS,
    analyze_project,
    analyze_project_async,
    checked_controls,
)
from navin.security.analyzers.common import collect_sources

FLASK_APP = '''
import os, pickle, sqlite3, subprocess
import requests, yaml
from flask import Flask, request, render_template_string, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
db = sqlite3.connect(":memory:")

@app.route("/search")
def search():
    term = request.args.get("q", "")
    query = f"SELECT * FROM items WHERE name = '{term}'"
    return str(db.execute(query).fetchall())

@app.route("/item")
def item():
    item_id = request.args.get("id", type=int)
    return str(db.execute(f"SELECT * FROM items WHERE id = {item_id}").fetchall())

@app.route("/item/<int:item_id>")
def item_by_path(item_id):
    return str(db.execute(f"SELECT * FROM items WHERE id = {item_id}").fetchall())

@app.route("/safe")
def safe():
    term = request.args.get("q", "")
    return str(db.execute("SELECT * FROM items WHERE name = ?", (term,)).fetchall())

@app.route("/ping", methods=["POST"])
def ping():
    host = request.form["host"]
    return subprocess.check_output("ping -c 1 " + host, shell=True)

@app.route("/ping-list", methods=["POST"])
def ping_list():
    host = request.form["host"]
    return subprocess.check_output(["ping", "-c", "1", host])

@app.route("/download")
def download():
    name = request.args["file"]
    return send_file(os.path.join("/srv/files", name))

@app.route("/upload", methods=["POST"])
def upload():
    doc = request.files["doc"]
    target = os.path.join("/srv/uploads", secure_filename(doc.filename))
    with open(target, "wb") as out:
        out.write(doc.read())
    return "ok"

@app.route("/calc")
def calc():
    return str(eval(request.args["expr"]))

@app.route("/import", methods=["POST"])
def import_data():
    return str(pickle.loads(request.data))

@app.route("/config", methods=["POST"])
def config():
    return str(yaml.load(request.data))

@app.route("/config-safe", methods=["POST"])
def config_safe():
    return str(yaml.load(request.data, Loader=yaml.SafeLoader))

@app.route("/preview")
def preview():
    url = request.args["url"]
    return requests.get(url, timeout=5).text

@app.route("/hello")
def hello():
    return render_template_string("Hello " + request.args.get("name", ""))

def offline_job(path):
    return open(path).read()
'''

FASTAPI_FETCH = '''
import httpx
from fastapi import Depends, FastAPI

app = FastAPI()

@app.get("/fetch")
def fetch(url: str):
    return httpx.get(url).text

@app.get("/items/{item_id}")
def read_item(item_id: int, db=Depends(get_db)):
    return db.execute(f"SELECT * FROM items WHERE id = {item_id}")
'''

EXPRESS_APP = '''
const express = require("express");
const { exec } = require("child_process");
const app = express();
app.get("/users", (req, res) => {
  db.query(`SELECT * FROM users WHERE name = '${req.query.name}'`, (err, rows) => res.json(rows));
});
app.post("/run", (req, res) => {
  exec(`convert ${req.body.file} out.png`, () => res.send("ok"));
});
app.get("/safe", (req, res) => {
  db.query("SELECT * FROM users WHERE name = ?", [req.query.name]);
});
'''

AUTH_APP = '''
from typing import Annotated
import jwt
from fastapi import Depends, FastAPI, HTTPException, Request

app = FastAPI()

def get_current_user():
    return {"id": 1}

@app.post("/orders")
def create_order(payload: dict):
    return {"ok": True}

@app.post("/orders/secure")
def create_order_secure(payload: dict, user=Depends(get_current_user)):
    return {"ok": True}

@app.delete("/orders/{order_id}")
def delete_order(order_id: int, user: Annotated[dict, Depends(get_current_user)]):
    return {}

@app.put("/profile", dependencies=[Depends(get_current_user)])
def update_profile(payload: dict):
    return {}

@app.patch("/settings")
def update_settings(payload: dict, request: Request):
    if not request.user.is_authenticated:
        raise HTTPException(status_code=401)
    return {}

@app.get("/orders")
def list_orders():
    return []

@app.post("/login")
def login(form: dict):
    return {}

@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    event = await request.json()
    return {"received": event["type"]}

@app.post("/webhooks/github")
async def github_webhook(request: Request):
    body = await request.body()
    if not hmac.compare_digest(sign(body), request.headers["X-Hub-Signature-256"]):
        raise HTTPException(status_code=401)
    return {}

def read_token(token):
    return jwt.decode(token, "secret", algorithms=["HS256", "none"], options={"verify_exp": False})

def read_token_unsafe(token):
    return jwt.decode(token, options={"verify_signature": False})

def read_token_ok(token):
    return jwt.decode(token, KEY, algorithms=["HS256"])
'''

AUTH_JS = '''
const jwt = require("jsonwebtoken");
function user(req) { return jwt.decode(req.headers.authorization); }
app.post("/webhook", (req, res) => { handle(req.body); res.sendStatus(200); });
app.post("/billing/webhook", (req, res) => {
  const event = stripe.webhooks.constructEvent(req.body, req.headers["stripe-signature"], secret);
  res.sendStatus(200);
});
'''

TENANT_SQL = '''
CREATE TABLE invoices (
  id SERIAL PRIMARY KEY,
  tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  amount NUMERIC(10, 2)
);
CREATE TABLE notes (
  id SERIAL PRIMARY KEY,
  tenant_id INTEGER NOT NULL,
  body TEXT
);
ALTER TABLE notes ENABLE ROW LEVEL SECURITY;
CREATE TABLE countries (id SERIAL PRIMARY KEY, name TEXT);
'''

TENANT_MODELS = '''
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base
from pydantic import BaseModel

Base = declarative_base()

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    name = Column(String)

class Country(Base):
    __tablename__ = "country_codes"
    id = Column(Integer, primary_key=True)

class ProjectIn(BaseModel):
    organization_id: int
'''

TENANT_REPO = '''
from models import Country, Project

def get_invoice(cursor, invoice_id):
    cursor.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
    return cursor.fetchone()

def get_invoice_scoped(cursor, invoice_id, tenant_id):
    cursor.execute("SELECT * FROM invoices WHERE id = %s AND tenant_id = %s", (invoice_id, tenant_id))
    return cursor.fetchone()

def get_note(cursor, note_id):
    sql = "SELECT * FROM notes WHERE id = %s"
    cursor.execute(sql, (note_id,))
    return cursor.fetchone()

def get_country(session, cid):
    session.execute("SELECT * FROM countries WHERE id = %s", (cid,))
    return session.get(Country, cid)

def get_project(session, project_id):
    return session.get(Project, project_id)

def get_project_scoped(session, project_id, user):
    project = session.get(Project, project_id)
    if project.organization_id != user.organization_id:
        raise PermissionError
    return project
'''

TENANT_ROUTES = '''
from fastapi import Depends, FastAPI, Request

app = FastAPI()

@app.get("/reports")
async def reports(request: Request):
    tenant = request.headers.get("X-Tenant-ID")
    return {"tenant": tenant}

@app.post("/members")
def add_member(payload: MemberIn):
    return create(payload.organization_id, payload.email)

@app.get("/tenants/{tenant_id}/stats")
def stats(tenant_id: int):
    return load(tenant_id)

@app.get("/tenants/{tenant_id}/usage")
def usage(tenant_id: int, user=Depends(current)):
    ensure_member(user, tenant_id)
    return load(tenant_id)
'''

TENANT_PRISMA = '''
model Invoice {
  id        Int    @id
  tenantId  Int
  amount    Int
}

model Country {
  id   Int    @id
  name String
}
'''

TENANT_TS = '''
export async function getInvoice(id: number) {
  return prisma.invoice.findUnique({ where: { id } });
}
export async function getInvoiceScoped(id: number, tenantId: number) {
  return prisma.invoice.findFirst({ where: { id, tenantId } });
}
export async function getCountry(id: number) {
  return prisma.country.findUnique({ where: { id } });
}
'''

SHOP_APP = '''
import stripe
from fastapi import Depends, FastAPI

app = FastAPI()
PRICES = {"basic": 900, "pro": 2900}

@app.post("/checkout")
def checkout(payload: CheckoutIn, user=Depends(current_user)):
    amount = payload.price * payload.quantity
    intent = stripe.PaymentIntent.create(amount=amount, currency="eur")
    return {"client_secret": intent.client_secret}

@app.post("/checkout-safe")
def checkout_safe(payload: CheckoutIn, user=Depends(current_user)):
    amount = PRICES[payload.plan] * payload.quantity
    return stripe.PaymentIntent.create(amount=amount, currency="eur")

@app.post("/orders")
def create_order(payload: OrderIn, user=Depends(current_user)):
    return Order(total=payload.total, user_id=user.id)

@app.post("/cart")
def update_cart(request_data: dict, user=Depends(current_user)):
    cart = load_cart(user)
    cart.discount = request_data["discount"]
    return cart
'''


def _project(files: dict[str, str]) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return tmp


def _pairs(report, prefix: str) -> set[tuple[str, str]]:
    return {(f.id, f.endpoint or f"{f.file_path}:{f.line}") for f in report.findings if f.id.startswith(prefix)}


class DataflowTest(unittest.TestCase):
    def test_python_sources_reach_sinks_and_sanitizers_stop_them(self):
        with _project({"app.py": FLASK_APP, "api.py": FASTAPI_FETCH}) as tmp:
            report = analyze_project(tmp, only=["navin_dataflow"])
        self.assertEqual(_pairs(report, "navin-dataflow"), {
            ("navin-dataflow-sql", "GET /search"),
            ("navin-dataflow-command", "POST /ping"),
            ("navin-dataflow-path", "GET /download"),
            ("navin-dataflow-code", "GET /calc"),
            ("navin-dataflow-deserialization", "POST /import"),
            ("navin-dataflow-deserialization", "POST /config"),
            ("navin-dataflow-ssrf", "GET /preview"),
            ("navin-dataflow-ssrf", "GET /fetch"),
            ("navin-dataflow-template", "GET /hello"),
        })
        sql = next(f for f in report.findings if f.id == "navin-dataflow-sql")
        self.assertEqual((sql.status, sql.family, sql.cwe, sql.controls), ("confirmed", "injection", "CWE-89", ["C1.01"]))
        trace = sql.evidence[0].observed
        self.assertEqual(sql.evidence[0].kind, "dataflow")
        self.assertIn("source request.args.get('q', '')", trace)
        self.assertIn("term = request.args.get", trace)
        self.assertIn("query = f", trace)
        self.assertIn("sink db.execute(query)", trace)
        self.assertIn("parameterized", sql.remediation)
        fetch = next(f for f in report.findings if f.endpoint == "GET /fetch")
        self.assertIn("parameter url of GET /fetch", fetch.evidence[0].observed)

    def test_javascript_rules_stay_potential(self):
        with _project({"server.js": EXPRESS_APP}) as tmp:
            report = analyze_project(tmp, only=["navin_dataflow"])
        self.assertEqual({(f.id, f.line) for f in report.findings}, {("navin-dataflow-sql", 5), ("navin-dataflow-command", 8)})
        self.assertTrue(all(f.status == "potential" and f.evidence[0].kind == "static" for f in report.findings))

    def test_unparsable_files_are_listed_never_silently_clean(self):
        with _project({"broken.py": "def oops(:\n    pass\n", "ok.py": "x = 1\n"}) as tmp:
            report = analyze_project(tmp)
        self.assertEqual(report.findings, [])
        self.assertTrue(any(s.startswith("broken.py: not parsed") for s in report.skipped))
        self.assertEqual(report.files_scanned, 2)


class AuthTest(unittest.TestCase):
    def test_routes_webhooks_login_and_jwt(self):
        with _project({"app.py": AUTH_APP, "auth.js": AUTH_JS}) as tmp:
            report = analyze_project(tmp, only=["navin_auth"])
        self.assertEqual(_pairs(report, "navin-auth"), {
            ("navin-auth-missing", "POST /orders"),
            ("navin-auth-login-throttle", "POST /login"),
            ("navin-auth-webhook-unsigned", "POST /webhooks/stripe"),
            ("navin-auth-webhook-unsigned", "POST /webhook"),
            ("navin-auth-jwt", "app.py:53"),
            ("navin-auth-jwt", "app.py:56"),
            ("navin-auth-jwt", "auth.js:2"),
        })
        jwt_none = next(f for f in report.findings if f.id == "navin-auth-jwt" and f.line == 53)
        self.assertEqual(jwt_none.severity, "high")
        self.assertIn("'none' algorithm", jwt_none.description)
        self.assertIn("verify_exp is disabled", jwt_none.description)
        self.assertTrue(all(f.status == "potential" for f in report.findings))

    def test_global_middleware_and_a_rate_limiter_silence_the_route_checks(self):
        app = AUTH_APP.replace("app = FastAPI()", "app = FastAPI()\napp.add_middleware(AuthenticationMiddleware)\nlimiter = Limiter(key_func=ip)")
        with _project({"app.py": app}) as tmp:
            report = analyze_project(tmp, only=["navin_auth"])
        ids = {f.id for f in report.findings}
        self.assertNotIn("navin-auth-missing", ids)
        self.assertNotIn("navin-auth-login-throttle", ids)
        self.assertIn("navin-auth-webhook-unsigned", ids)  # a global login check does not sign webhooks
        self.assertTrue(any("global authentication found" in note for note in report.notes))


class TenantTest(unittest.TestCase):
    def test_unscoped_access_and_client_chosen_tenants(self):
        files = {"db/schema.sql": TENANT_SQL, "models.py": TENANT_MODELS, "repo.py": TENANT_REPO,
                 "routes.py": TENANT_ROUTES, "prisma/schema.prisma": TENANT_PRISMA, "invoices.ts": TENANT_TS}
        with _project(files) as tmp:
            report = analyze_project(tmp, only=["navin_tenant"])
        found = {(f.id, f.file_path, f.line) for f in report.findings}
        self.assertEqual(found, {
            ("navin-tenant-unscoped", "repo.py", 4),
            ("navin-tenant-unscoped", "repo.py", 21),
            ("navin-tenant-unscoped", "invoices.ts", 2),
            ("navin-tenant-client-chosen", "routes.py", 7),
            ("navin-tenant-client-chosen", "routes.py", 12),  # add_member() is a name, not a membership check
            ("navin-tenant-client-chosen", "routes.py", 15),
        })
        stats = next(f for f in report.findings if f.endpoint == "GET /tenants/{tenant_id}/stats")
        self.assertIn("the URL", stats.description)
        self.assertTrue(all(f.family == "multitenancy" and f.controls == ["C2.09"] for f in report.findings))

    def test_without_a_tenant_schema_the_report_says_what_was_not_checked(self):
        with _project({"repo.py": TENANT_REPO}) as tmp:
            report = analyze_project(tmp, only=["navin_tenant"])
        self.assertEqual(report.findings, [])
        self.assertIn("no tenant-scoped table or model found: unscoped queries were not checked", report.notes)

    def test_crm_accounts_are_not_tenants_unless_users_belong_to_them(self):
        crm_db = (
            'DDL = """\n'
            "CREATE TABLE IF NOT EXISTS contacts (\n"
            "  id TEXT PRIMARY KEY,\n"
            "  account_id TEXT REFERENCES accounts(id),\n"
            "  email TEXT\n"
            ");\n"
            "CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT);\n"
            '"""\n'
        )
        store = (
            "def get_contact(conn, contact_id):\n"
            "    return conn.execute(\"SELECT * FROM contacts WHERE id = ?\", (contact_id,)).fetchone()\n"
            "\n"
            "@app.post(\"/contacts\")\n"
            "def create_contact(payload: ContactIn):\n"
            "    return add(payload.account_id, payload.email)\n"
        )
        with _project({"crm/db.py": crm_db, "crm/store.py": store}) as tmp:
            self.assertEqual(analyze_project(tmp, only=["navin_tenant"]).findings, [])  # account = a customer company
        saas_db = crm_db.replace("users (id TEXT PRIMARY KEY, email TEXT)", "users (id TEXT PRIMARY KEY, account_id TEXT, email TEXT)")
        with _project({"crm/db.py": saas_db, "crm/store.py": store}) as tmp:
            report = analyze_project(tmp, only=["navin_tenant"])
        self.assertEqual({(f.id, f.line) for f in report.findings},
                         {("navin-tenant-unscoped", 2), ("navin-tenant-client-chosen", 6)})  # users belong to accounts: a tenant

    def test_the_nearest_definition_of_a_table_wins(self):
        campaigns_db = 'DDL = """CREATE TABLE contacts (id TEXT PRIMARY KEY, email TEXT);"""\n'
        crm_db = 'DDL = """CREATE TABLE contacts (id TEXT PRIMARY KEY, tenant_id TEXT, email TEXT);"""\n'
        query = ("def get_contact(conn, contact_id):\n"
                 "    return conn.execute(\"SELECT * FROM contacts WHERE id = ?\", (contact_id,)).fetchone()\n")
        with _project({"app/campaigns/db.py": campaigns_db, "app/campaigns/store.py": query,
                       "app/crm/db.py": crm_db, "app/crm/store.py": query}) as tmp:
            report = analyze_project(tmp, only=["navin_tenant"])
        self.assertEqual({(f.file_path, f.line) for f in report.findings}, {("app/crm/store.py", 2)})


class BusinessLogicTest(unittest.TestCase):
    def test_client_amounts_are_followed_to_the_payment_call(self):
        with _project({"shop.py": SHOP_APP}) as tmp:
            report = analyze_project(tmp, only=["navin_business_logic"])
        self.assertEqual({(f.id, f.endpoint, f.status) for f in report.findings}, {
            ("navin-logic-client-price", "POST /checkout", "confirmed"),
            ("navin-logic-client-amount", "POST /orders", "potential"),
            ("navin-logic-client-amount", "POST /cart", "potential"),
        })
        charged = next(f for f in report.findings if f.id == "navin-logic-client-price")
        self.assertIn("client-supplied payload.price", charged.evidence[0].observed)
        self.assertIn("stripe.PaymentIntent.create", charged.evidence[0].observed)


class ProjectRunTest(unittest.TestCase):
    def test_full_run_controls_caps_and_async(self):
        with _project({"app.py": FLASK_APP, "shop.py": SHOP_APP, "node_modules/x/evil.py": FLASK_APP,
                       "big.py": "x = 1\n" + "#" * 500_000}) as tmp:
            report = analyze_project(tmp)
            again = asyncio.run(analyze_project_async(tmp, only=["navin_dataflow"]))
            capped = analyze_project(tmp, max_files=1)
        self.assertEqual(report.ran, list(ANALYZERS))
        self.assertEqual(set(report.durations), {"read", *ANALYZERS})
        self.assertFalse(any(f.file_path.startswith("node_modules") for f in report.findings))
        self.assertTrue(any(s.startswith("big.py: larger than") for s in report.skipped))
        self.assertEqual({f.id for f in again.findings if f.id.startswith("navin-dataflow")},
                         {f.id for f in report.findings if f.id.startswith("navin-dataflow")})
        self.assertTrue(any("stopped after 1 files" in n for n in capped.notes))
        controls = checked_controls(report.ran)
        self.assertTrue({"C1.01", "C1.04", "C2.03", "C2.08", "C2.09", "C3.08", "C3.10"} <= controls)
        self.assertNotIn("C1.02", controls)  # XSS is not checked by these analyzers: never reported clean
        self.assertIn("C1.10", checked_controls(["gitleaks"]))
        payload = report.to_dict()
        self.assertEqual(payload["checked_controls"], sorted(controls))
        with self.assertRaisesRegex(ValueError, "unknown analyzer"):
            analyze_project(".", only=["navin_magic"])

    def test_sources_are_collected_once_with_their_kinds(self):
        with _project({"a.py": "x = 1\n", "b.ts": "let a = 1\n", "c.min.js": "x", "d.sql": "select 1;\n",
                       "e.prisma": "model A { id Int @id }\n", ".hidden/f.py": "x = 1\n"}) as tmp:
            found = collect_sources(tmp)
        self.assertEqual(([s.rel for s in found.python], [s.rel for s in found.scripts], [s.rel for s in found.sql], [s.rel for s in found.prisma]),
                         (["a.py"], ["b.ts"], ["d.sql"], ["e.prisma"]))
        with self.assertRaises(NotADirectoryError):
            collect_sources("/definitely/not/here")


if __name__ == "__main__":
    unittest.main()
