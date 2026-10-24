"""Static data schema extraction: tables, code->table READS/WRITES, candidate business rules."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_CODE_EXT = {".py", ".js", ".ts", ".tsx", ".php", ".java", ".rb", ".go", ".cs", ".sql"}
_MAX_BYTES = 1_000_000
_ID = r"[`\"\[]?([A-Za-z_][\w$]*)[`\"\]]?(?:\.[`\"\[]?([A-Za-z_][\w$]*)[`\"\]]?)?"
_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?" + _ID + r"\s*\(", re.I)
_ALTER_ADD = re.compile(r"ALTER\s+TABLE\s+" + _ID + r"\s+ADD\s+(?:COLUMN\s+)?" + _ID + r"\s+([A-Za-z]+)", re.I)
_SQL_OPS = [
    ("READS", re.compile(r"\bSELECT\b[^;]*?\bFROM\s+" + _ID, re.I | re.S)),
    ("READS", re.compile(r"\bJOIN\s+" + _ID, re.I)),
    ("WRITES", re.compile(r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+" + _ID, re.I)),
    ("WRITES", re.compile(r"\bUPDATE\s+" + _ID + r"\s+SET\b", re.I)),
    ("WRITES", re.compile(r"\bDELETE\s+FROM\s+" + _ID, re.I)),
]
_ORM_READ = r"(?:objects\.(?:filter|get|all|exclude)|query\.(?:filter|filter_by|get|all)|findAll|findOne|findMany|find|where)"
_ORM_WRITE = r"(?:objects\.(?:create|bulk_create|update|delete)|session\.(?:add|delete)|\.save|\.create|\.update|\.destroy|\.delete)"
_MODEL_USE = re.compile(r"\b([A-Z][A-Za-z0-9]+)\.(" + _ORM_READ + r"|" + _ORM_WRITE + r")\b")
_ORM_WRITE_RE = re.compile(_ORM_WRITE)
_DJANGO = re.compile(r"^class\s+(\w+)\((?:[\w.]*Model)\):", re.M)
_SQLA = re.compile(r"^class\s+(\w+)\([^)]*(?:Base|db\.Model)[^)]*\):", re.M)
_TABLENAME = re.compile(r"__tablename__\s*=\s*['\"](\w+)['\"]")
_FIELD = re.compile(r"^\s+(\w+)\s*(?::[^=\n]+)?=\s*(?:models\.|db\.)?(\w*(?:Field|Key|Column)\w*)\(", re.M)
_COND = re.compile(
    r"\b(?:if|elif|when|case|where|unless)\b[^\n]*?(?:(?:rate|taux|tax|vat|tva|timbre|stamp|threshold|seuil|"
    r"limit|plafond|country|pays|amount|montant)\w*)[^\n]*", re.I)
_CMP = re.compile(r"(==|!=|>=|<=|>|<|===| in | not in )")
_NUM = re.compile(r"\d")
_COUNTRY = re.compile(r"\b(?:country|pays)\w*\s*(?:==|===|!=|in)\s*[\[(]?\s*['\"]([A-Za-z]{2,}(?:['\"]\s*,\s*['\"][A-Za-z]{2,})*)", re.I)
_TOPICS = {"rate": r"rate|taux|tva|vat|tax", "threshold": r"threshold|seuil|limit|plafond",
           "country": r"country|pays", "stamp": r"timbre|stamp"}


def _name(m: re.Match[str]) -> str:
    return (m.group(2) or m.group(1)).lower()


def _balanced(text: str, start: int) -> str:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    return text[start + 1:]


def _columns(body: str) -> list[str]:
    cols = []
    for part in re.split(r",(?![^()]*\))", body):
        w = part.strip().split()
        if w and w[0].strip('`"[]').upper() not in {"PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "KEY", "INDEX", "CHECK"}:
            cols.append(w[0].strip('`"[]'))
    return cols


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def extract_data_schema(root: Path | str, files: list[str] | None = None) -> dict[str, Any]:
    """Scan *root* and return tables, links and candidate business rules."""
    root = Path(root)
    if files is None:
        from navin.reverse.repo import _walk
        files = _walk(root)
    tables: dict[str, dict[str, Any]] = {}
    models: dict[str, str] = {}
    links: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    for rel in files:
        p = root / rel
        if p.suffix.lower() not in _CODE_EXT:
            continue
        try:
            if p.stat().st_size > _MAX_BYTES:
                continue
            texts[rel] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    for rel, text in texts.items():
        ext = Path(rel).suffix.lower()
        for m in _CREATE.finditer(text):
            t = tables.setdefault(_name(m), {"name": _name(m), "columns": [], "source": f"{rel}:{_line(text, m.start())}", "origin": "sql"})
            for c in _columns(_balanced(text, m.end() - 1)):
                if c not in t["columns"]:
                    t["columns"].append(c)
        for m in _ALTER_ADD.finditer(text):
            t = tables.setdefault(m.group(2) and m.group(2).lower() or m.group(1).lower(), {"name": m.group(2) or m.group(1), "columns": [], "source": rel, "origin": "sql"})
            col = re.match(r"\s*(?:COLUMN\s+)?[`\"\[]?(\w+)", re.split(r"\bADD\b", text[m.start():m.end()], flags=re.I)[1], re.I)
            if col and col.group(1) not in t["columns"]:
                t["columns"].append(col.group(1))
        if ext == ".py":
            for rx, kind in ((_DJANGO, "django"), (_SQLA, "sqlalchemy")):
                for m in rx.finditer(text):
                    nxt = re.search(r"^class\s", text[m.end():], re.M)
                    body = text[m.end(): m.end() + nxt.start()] if nxt else text[m.end():]
                    tn = _TABLENAME.search(body)
                    tname = (tn.group(1) if tn else m.group(1)).lower()
                    models[m.group(1)] = tname
                    t = tables.setdefault(tname, {"name": tname, "columns": [], "source": f"{rel}:{_line(text, m.start())}", "origin": kind})
                    t["model"] = m.group(1)
                    for f in _FIELD.finditer(body):
                        if f.group(1) != "__tablename__" and f.group(1) not in t["columns"]:
                            t["columns"].append(f.group(1))
    for rel, text in texts.items():
        if rel.lower().endswith(".sql") and not re.search(r"\b(select|insert|update|delete)\b", text, re.I):
            continue
        seen: set[tuple[str, str, int]] = set()
        for kind, rx in _SQL_OPS:
            for m in rx.finditer(text):
                tn = _name(m) if not (m.re.pattern.startswith("\\bSELECT")) else (m.group(m.lastindex or 1) and _name(m))
                if tn in tables and (tn, kind, _line(text, m.start())) not in seen:
                    seen.add((tn, kind, _line(text, m.start())))
                    links.append({"file": rel, "line": _line(text, m.start()), "type": kind, "table": tn, "via": "sql"})
        for m in _MODEL_USE.finditer(text):
            tn = models.get(m.group(1))
            if tn:
                kind = "WRITES" if _ORM_WRITE_RE.search(m.group(2)) else "READS"
                links.append({"file": rel, "line": _line(text, m.start()), "type": kind, "table": tn, "via": "orm"})
        for i, ln in enumerate(text.splitlines(), 1):
            if rel.lower().endswith(".sql") or not _COND.search(ln) or not _CMP.search(ln):
                continue
            if not (_NUM.search(ln) or _COUNTRY.search(ln) or re.search(r"['\"][A-Z]{2}['\"]", ln)):
                continue
            topics = sorted(k for k, v in _TOPICS.items() if re.search(v, ln, re.I))
            c = _COUNTRY.search(ln)
            rules.append({"file": rel, "line": i, "condition": ln.strip()[:200], "topics": topics,
                          "countries": re.findall(r"[A-Za-z]{2,}", c.group(1)) if c else []})
    uniq = {(lk["file"], lk["line"], lk["type"], lk["table"]): lk for lk in links}
    links = sorted(uniq.values(), key=lambda lk: (lk["file"], lk["line"]))
    tl = sorted(tables.values(), key=lambda t: t["name"])
    for t in tl:
        t["readers"] = sorted({lk["file"] for lk in links if lk["table"] == t["name"] and lk["type"] == "READS"})
        t["writers"] = sorted({lk["file"] for lk in links if lk["table"] == t["name"] and lk["type"] == "WRITES"})
    return {"tables": tl, "links": links, "business_rules": rules,
            "test_cases": candidate_test_cases(rules, links),
            "counts": {"tables": len(tl), "links": len(links), "rules": len(rules)}}


def candidate_test_cases(rules: list[dict[str, Any]], links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One test case per rule; a country rule asks to check the other countries stay untouched.

    Lynara pattern: a stamp duty (timbre fiscal) rule must impact the tables written by
    the same file (encaissements, G50) and leave other countries (France) unchanged.
    """
    out = []
    for r in rules:
        tabs = sorted({lk["table"] for lk in links if lk["file"] == r["file"] and lk["type"] == "WRITES"})
        case = {"rule": f"{r['file']}:{r['line']}", "topics": r["topics"], "impacted_tables": tabs,
                "given": r["condition"], "expect_impacted": tabs}
        if "stamp" in r["topics"] or r["countries"]:
            case["expect_unchanged_for_other_countries"] = True
        out.append(case)
    return out
