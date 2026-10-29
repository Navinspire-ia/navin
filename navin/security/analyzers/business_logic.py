# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Business logic analyzer: amounts the client decides.

A price, total, discount, fee or credit read from the request is followed
through the function. Reaching a payment call (Stripe ``PaymentIntent.create``,
``charge``, ``refund``...) is a traced dataflow and confirms the finding;
being stored on an object or passed to a constructor stays potential. A
price looked up on the server (catalog, database) is never flagged: the
client only chose which item, not what it costs.
"""

from __future__ import annotations

import ast
import re

from navin.security.analyzers.common import (
    SourceSet,
    const_str,
    dotted,
    functions,
    line_text,
    route_of,
    short,
)
from navin.security.analyzers.dataflow import Taint, TaintEngine, route_seeds
from navin.security.model import Evidence, Finding
from navin.security.verification.engine import confirm_dataflow
from navin.security.verification.probe import redact

ANALYZER_ID = "navin_business_logic"
CHECKED_CONTROLS = frozenset({"C3.08"})

_MONEY_KEY = re.compile(r"price|amount|total|subtotal|cost|discount|fee|credit|balance", re.I)
_PAYMENT_CALL = re.compile(
    r"(?:^|\.)(?:PaymentIntent|Charge|Invoice|InvoiceItem|Refund|Transfer|Payout|PaymentLink)\.create$|"
    r"(?:^|\.)checkout\.Session\.create$|"
    r"(?:^|\.)(?:charge|capture|create_payment|create_charge|create_checkout_session|create_payment_intent|refund|"
    r"transfer_funds|debit|credit_account|add_credits)$", re.I)


class MoneyEngine(TaintEngine):
    """Taint whose only sources are money fields the client sent."""

    def __init__(self, rel: str, func: ast.FunctionDef | ast.AsyncFunctionDef, user: TaintEngine):
        self.user = user
        super().__init__(rel, func)

    def source(self, node: ast.AST) -> Taint | None:
        key: str | None = None
        base: ast.AST | None = None
        if isinstance(node, ast.Subscript):
            key, base = const_str(node.slice), node.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
            key, base = const_str(node.args[0]), node.func.value
        elif isinstance(node, ast.Attribute):
            key, base = node.attr, node.value
        if not key or not _MONEY_KEY.search(key):
            return None
        if not (self.user.source(base) or self.user.eval(base)):
            return None
        return Taint((f"{self.rel}:{getattr(node, 'lineno', self.func.lineno)} client-supplied {short(node)}",))


def analyze(sources: SourceSet) -> list[Finding]:
    findings: list[Finding] = []
    for src, tree in sources.python_trees():
        for func in functions(tree):
            route = route_of(func)
            if route is None and "request" not in src.segment(func):
                continue  # no request object and no route parameter: the client sent nothing here
            user = TaintEngine(src.rel, func, seeds=route_seeds(src.rel, func))
            if not user.tainted and not _reads_request(func, user):
                continue
            money = MoneyEngine(src.rel, func, user)
            endpoint = route.endpoint if route else ""
            charged = False
            for node in ast.walk(func):
                if isinstance(node, ast.Call) and _PAYMENT_CALL.search(dotted(node.func)):
                    taint = money.any([*node.args, *(k.value for k in node.keywords)])
                    if taint:
                        findings.append(_charged(src.rel, node, taint, endpoint))
                        charged = True
            if charged:
                continue
            stored = _stored(func, money)
            if stored:
                line, detail = stored
                findings.append(Finding(
                    id="navin-logic-client-amount", family="business_logic", severity="medium", cwe="CWE-602",
                    title="Server keeps an amount the client sent", file_path=src.rel, line=line, endpoint=endpoint,
                    controls=["C3.08"], impact="fraud", confidence=0.5, source=ANALYZER_ID,
                    description=detail + ". Recompute prices and totals on the server.",
                    evidence=[Evidence(kind="static", description=detail, tool=ANALYZER_ID,
                                       observed=redact(line_text(src.text, line)), location=f"{src.rel}:{line}")],
                ))
    return findings


def _reads_request(func: ast.FunctionDef | ast.AsyncFunctionDef, user: TaintEngine) -> bool:
    return any(user.source(node) for node in ast.walk(func) if isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)))


def _stored(func: ast.FunctionDef | ast.AsyncFunctionDef, money: MoneyEngine) -> tuple[int, str] | None:
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, (ast.Attribute, ast.Subscript)):
                    key = target.attr if isinstance(target, ast.Attribute) else const_str(target.slice)
                    if key and _MONEY_KEY.search(key) and money.eval(node.value):
                        return node.lineno, f"{short(target)} is set from {short(node.value)}"
        elif isinstance(node, ast.Call):
            name = dotted(node.func).rsplit(".", 1)[-1]
            if not name[:1].isupper():
                continue
            for item in node.keywords:
                if item.arg and _MONEY_KEY.search(item.arg) and money.eval(item.value):
                    return node.lineno, f"{name}({item.arg}=...) takes {short(item.value)}"
    return None


def _charged(rel: str, call: ast.Call, taint: Taint, endpoint: str) -> Finding:
    sink = f"{rel}:{call.lineno} sink {short(call, 120)}"
    item = Finding(
        id="navin-logic-client-price", family="business_logic", severity="high", cwe="CWE-602",
        title="Payment amount set by the client", file_path=rel, line=call.lineno, endpoint=endpoint,
        controls=["C3.08"], impact="fraud", confidence=0.8, source=ANALYZER_ID,
        description=f"{taint.steps[0].split(' ', 1)[-1]} reaches {short(call, 120)}: a buyer can pay any price.",
    )
    confirm_dataflow(item, [*taint.steps, sink], source=taint.steps[0], sink=sink, tool=ANALYZER_ID)
    return item
