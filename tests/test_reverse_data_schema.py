from pathlib import Path

from navin.reverse.data_schema import extract_data_schema
from navin.reverse.repo import build_repo_inventory


def _repo(tmp: Path) -> Path:
    (tmp / "migrations").mkdir()
    (tmp / "migrations" / "001.sql").write_text(
        "CREATE TABLE IF NOT EXISTS encaissements (\n id INT PRIMARY KEY,\n montant NUMERIC,\n"
        " timbre NUMERIC,\n PRIMARY KEY (id)\n);\nCREATE TABLE g50 (id INT, total NUMERIC);\n"
        "ALTER TABLE g50 ADD COLUMN periode TEXT;\n")
    (tmp / "models.py").write_text(
        "from django.db import models\n\nclass Client(models.Model):\n"
        "    name = models.CharField(max_length=10)\n    country = models.CharField(max_length=2)\n")
    (tmp / "billing.py").write_text(
        "def pay(db, c, amount):\n"
        "    rows = db.execute('SELECT * FROM encaissements WHERE id = 1')\n"
        "    if c.country == 'DZ' and amount > 10000:\n"
        "        timbre = amount * 0.01\n"
        "    db.execute('INSERT INTO encaissements (montant, timbre) VALUES (1, 2)')\n"
        "    db.execute('UPDATE g50 SET total = total + 1')\n"
        "    Client.objects.filter(name='a')\n    Client.objects.create(name='b')\n")
    return tmp


def test_schema_links_rules(tmp_path):
    ds = extract_data_schema(_repo(tmp_path))
    names = {t["name"]: t for t in ds["tables"]}
    assert {"encaissements", "g50", "client"} <= set(names)
    assert names["encaissements"]["columns"] == ["id", "montant", "timbre"]
    assert "periode" in names["g50"]["columns"]
    got = {(lk["table"], lk["type"]) for lk in ds["links"] if lk["file"] == "billing.py"}
    assert {("encaissements", "READS"), ("encaissements", "WRITES"), ("g50", "WRITES"),
            ("client", "READS"), ("client", "WRITES")} <= got
    assert any(r["countries"] == ["DZ"] for r in ds["business_rules"])


def test_lynara_case_and_report(tmp_path):
    root = _repo(tmp_path)
    ds = extract_data_schema(root)
    case = next(c for c in ds["test_cases"] if c["rule"].startswith("billing.py"))
    assert {"encaissements", "g50"} <= set(case["impacted_tables"])
    assert case["expect_unchanged_for_other_countries"] is True
    inv = build_repo_inventory(root, name="fx")
    assert inv["data_schema"]["counts"]["tables"] == 3
