"""Tender drafting invariants with fictional source facts and no provider calls."""

from __future__ import annotations

import copy
import io
import json
import shutil
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from navin.tenders.ai import (
    _PROPOSAL_FIELDS,
    _safe_proposal,
    _validated_proposals,
    keeps_only_known_facts,
    polish_response,
    revise_response,
)
from navin.tenders.export import (
    _diagram_spec,
    _pptx_pages,
    attach_exports,
    render_docx,
    render_pptx,
)
from navin.tenders.store import TenderStore
from navin.tenders.writer import analyse_tender, build_response, extract_requirements, go_nogo


@pytest.fixture
def company():
    return {
        "name": "Audit Conseil", "country": "FR", "locale": "fr", "currency": "EUR",
        "crafts": ["Data", "API"], "specialty": "Integration de donnees",
        "methodology": "Cartographie des flux, contrats d'interface, tests de reprise et transfert aux exploitants.",
        "team": [{"name": "Audit Camille", "role": "Architecte"}],
        "certifications": ["ISO 9001"], "price_book": [{"item": "Audit architecte", "amount": 850, "unit": "EUR / jour"}],
        "references": [{"title": "Audit Integration PostgreSQL", "client": "Audit Acheteur", "year": "2024", "excerpt": "Integration de sources PostgreSQL et API REST."}],
        "ai_assist": False,
    }


@pytest.fixture
def notice():
    return {
        "id": "audit-quality", "title": "Audit Plateforme de donnees et API metier", "buyer": "Audit Acheteur",
        "country": "FR", "budget": 360000, "deadline": "2099-11-30", "score": 94,
        "score_breakdown": {"days_left": 50, "technical": 92},
        "description": "Integrer 12 sources PostgreSQL et livrer des API REST. La duree de mission est de 9 mois.",
        "eligibility": "La certification ISO 27001 est obligatoire. Fournir trois references et les CV nominatifs de l'equipe.",
        "submission_method": "Depot sur le portail Audit, offre technique et financiere separees.",
        "cdc_text": "\n".join([
            "EXG-01. Integrer 12 sources PostgreSQL par traitements incrementaux quotidiens; conserver les rejets et permettre la reprise sans doublons.",
            "EXG-02. Exposer des API REST versionnees, filtrer les acces par role et tracer les consultations de donnees sensibles.",
            "EXG-03. Controler la qualite des indicateurs: completude, unicite et coherence; les seuils seront approuves avant recette.",
            "EXG-04. Heberger les donnees en France, chiffrer les echanges et sauvegardes; separer test et production.",
            "EXG-05. Demonstrer la restauration avec RPO de 24 heures et RTO de 4 heures; consigner les horodatages des donnees restaurees.",
            "EXG-06. Livrer le dossier d'architecture, les contrats d'API et les procedures d'exploitation dans des formats editables.",
            "EXG-07. Organiser le transfert aux administrateurs avec une mise en situation autonome et un compte rendu.",
            "EXG-08. Tenir un comite hebdomadaire reliant avancement, charge, risques et decisions aux livrables.",
            "EXG-09. Achever la mission en 9 mois apres notification; faire approuver les acces et les dates intermediaires.",
            "EXG-10. Joindre une matrice reliant chaque exigence a sa reponse, son livrable et sa preuve de recette, avec les reserves.",
        ]),
    }


def test_cdc_numbering_and_unique_obligations_exclude_title_and_synopsis(notice, company):
    rows = extract_requirements(notice, company)
    assert len(rows) == 14
    assert [row["buyer_id"] for row in rows if row["source"] == "cdc"] == [f"EXG-{i:02d}" for i in range(1, 11)]
    assert not any(row["text"] == notice["title"] or row["source"] == "description" for row in rows)
    assert any("CV nominatifs" in row["text"] and "references" not in row["text"] for row in rows)
    assert any("trois references" in row["text"] and "CV" not in row["text"] for row in rows)


def test_additional_description_obligation_survives_a_detailed_cdc(notice, company):
    notice["description"] = "Le titulaire doit fournir 27 terminaux physiques pour les agents de terrain."
    rows = extract_requirements(notice, company)
    assert any(row["source"] == "description" and "27 terminaux" in row["text"] for row in rows)


def test_no_requirement_truncation_or_forty_row_ceiling(notice, company):
    long_clause = "EXG-55. Le titulaire doit documenter " + "les conditions de verification et les exceptions " * 10 + "FIN-EXIGENCE-55."
    notice["description"] = ""
    notice["cdc_text"] = "\n".join([f"EXG-{i:02d}. Documenter la procedure operationnelle du lot {i}." for i in range(1, 55)] + [long_clause])
    response = build_response(notice, company)
    cdc = [row for row in response["requirement_responses"] if row["source"] == "cdc"]
    assert len(cdc) == 55
    assert cdc[-1]["text"] == long_clause
    assert long_clause in response["functional"]
    assert long_clause in response["compliance_matrix"]
    assert response["coverage"]["omitted"] == 0


def test_certification_identity_blocks_go_and_declaration_still_requires_proof(notice, company):
    analysis = analyse_tender(notice, company)
    assert any("ISO 27001" in gap for gap in analysis["eligibility_blockers"])
    assert not go_nogo(notice, company)["go"]
    company["certifications"] = ["ISO 27001"]
    assert go_nogo(notice, company)["go"]
    response = build_response(notice, company)
    certification = next(row for row in response["requirement_responses"] if row["category"] == "certification")
    assert certification["status"] == "evidence_to_verify"
    assert not response["submission_ready"]


def test_optional_certification_is_not_a_mandatory_gate(notice, company):
    notice["eligibility"] = "Certification ISO 27001 facultative."
    assert go_nogo(notice, company)["go"]


def test_cv_and_reference_evidence_are_not_inferred_from_skills_or_team(notice, company):
    response = build_response(notice, company)
    cv = next(row for row in response["requirement_responses"] if row["category"] == "cv")
    references = next(row for row in response["requirement_responses"] if row["category"] == "reference")
    assert cv["status"] == "missing_evidence"
    assert not cv["evidence_ids"]
    assert references["status"] == "insufficient_evidence"
    assert "References demandees: 3; declarations disponibles: 1" in " ".join(references["dependencies"])
    assert "Audit Camille" in response["staffing"]


def test_methods_follow_the_obligation_and_quantities_drive_acceptance(notice, company):
    response = build_response(notice, company)
    by_id = {row["buyer_id"]: row for row in response["requirement_responses"] if row.get("buyer_id")}
    assert "data" in by_id["EXG-01"]["facets"]
    assert "data" not in by_id["EXG-02"]["facets"]
    assert "continuity" not in by_id["EXG-04"]["facets"]
    assert by_id["EXG-05"]["facets"] == ["continuity"]
    assert by_id["EXG-06"]["facets"] == ["documentation"]
    assert by_id["EXG-07"]["facets"] == ["transfer"]
    assert by_id["EXG-09"]["facets"] == ["governance"]
    assert by_id["EXG-10"]["facets"] == ["traceability"]
    assert "12 sources" in " ".join(by_id["EXG-01"]["acceptance"])
    assert "RPO 24 heures" in " ".join(by_id["EXG-05"]["acceptance"])
    assert "RTO 4 heures" in " ".join(by_id["EXG-05"]["acceptance"])
    assert "9 mois" in " ".join(by_id["EXG-09"]["method"])


def test_no_model_path_is_explicit_and_keeps_remarks_out_of_letter(notice, company):
    base = build_response(notice, company)
    with patch("navin.tenders.ai.ask", side_effect=AssertionError("provider must not run")):
        response = polish_response(notice, company, base)
        revised = revise_response(notice, company, response, "Ajouter un engagement de 3 jours non valide.")
    assert response["generation"]["mode"] == "deterministic"
    assert response["generation"]["status"] == "needs_review"
    assert "desactivee" in " ".join(response["generation"]["warnings"])
    assert "3 jours" in revised["revision_notes"]
    assert "3 jours" not in revised["letter"]
    assert not revised["revision_applied"]


def _model_rows(batch, original, *, changed=False):
    results = []
    for row in batch:
        base = original[row["id"]]
        item = {"requirement_id": row["id"], "evidence_ids": base["evidence_ids"], **{key: copy.deepcopy(base[key]) for key in _PROPOSAL_FIELDS}}
        if changed:
            item["acceptance"].append("Verifier avec les utilisateurs un exemple nominal et un exemple en erreur; conserver pour chacun le resultat attendu, le resultat observe et la decision de recette.")
        results.append(item)
    return json.dumps({"requirements": results}, ensure_ascii=False)


def test_model_receives_all_source_rows_and_skill_bodies_but_not_copyable_boilerplate(notice, company, monkeypatch):
    monkeypatch.delenv("NAVIN_TENDERS_AI", raising=False)
    company["ai_assist"] = True
    base = build_response(notice, company)
    original = {row["id"]: row for row in base["requirement_responses"]}
    captured = []

    def fake_model(task, system, user, **kwargs):
        request = json.loads(user)
        captured.append(request)
        assert task == "write"
        assert "rfp-writer" in system and "professional-writer" in system and "archify" in system
        assert len(system) > 8000
        assert all("method" not in row for row in request["requirements"])
        return _model_rows(request["requirements"], original, changed=True), "audit-model"

    with patch("navin.tenders.ai.ask", side_effect=fake_model):
        response = polish_response(notice, company, base)
    expected = {row["id"] for row in base["requirement_responses"] if row["category"] == "technical"}
    assert {row["id"] for request in captured for row in request["requirements"]} == expected
    assert response["generation"]["mode"] == "model"
    assert response["generation"]["model_requirements"] == len(expected)
    assert response["letter"] == base["letter"]
    assert response["staffing"] == base["staffing"]
    assert response["financial_schedule"] == base["financial_schedule"]
    assert response["evidence_register"] == base["evidence_register"]


def test_exact_copy_is_not_reported_as_model_enrichment(notice, company, monkeypatch):
    monkeypatch.delenv("NAVIN_TENDERS_AI", raising=False)
    company["ai_assist"] = True
    base = build_response(notice, company)
    original = {row["id"]: row for row in base["requirement_responses"]}
    with patch("navin.tenders.ai.ask", side_effect=lambda task, system, user, **kwargs: (_model_rows(json.loads(user)["requirements"], original), "audit-model")):
        response = polish_response(notice, company, base)
    assert response["generation"]["mode"] == "deterministic"
    assert response["generation"]["model_requirements"] == 0
    assert "aucun enrichissement" in " ".join(response["generation"]["warnings"])
    assert "model" not in response


@pytest.mark.parametrize("failure", ["figure", "evidence", "missing_requirement", "unstructured"])
def test_invalid_model_batch_keeps_all_facts_and_discloses_rejection(notice, company, monkeypatch, failure):
    monkeypatch.delenv("NAVIN_TENDERS_AI", raising=False)
    company["ai_assist"] = True
    base = build_response(notice, company)
    original = {row["id"]: row for row in base["requirement_responses"]}

    def unsafe_model(task, system, user, **kwargs):
        data = json.loads(_model_rows(json.loads(user)["requirements"], original, changed=True))
        if failure == "figure":
            data["requirements"][0]["method"] = ["Nous avons livre 9 projets similaires pour Audit Acheteur."]
        elif failure == "evidence":
            data["requirements"][0]["evidence_ids"].append("E999")
        elif failure == "missing_requirement":
            data["requirements"].pop()
        else:
            return "Une belle lettre sans structure ni preuve.", "audit-model"
        return json.dumps(data), "audit-model"

    with patch("navin.tenders.ai.ask", side_effect=unsafe_model):
        response = polish_response(notice, company, base)
    assert "rejetee" in " ".join(response["generation"]["warnings"])
    for row, original_row in zip(response["requirement_responses"], base["requirement_responses"]):
        for field in ("id", "text", "source", "evidence_ids", "status"):
            assert row[field] == original_row[field]
        assert "9 projets" not in " ".join(row["method"])
    if failure == "unstructured":
        assert response["generation"]["mode"] == "deterministic"
    else:
        assert response["generation"]["mode"] == "model"
        assert response["generation"]["model_requirements"] > 0
    assert response["generation"]["repair_attempted"] is True


def test_single_digits_and_reversed_recovery_objectives_are_rejected():
    assert not keeps_only_known_facts("Experience de 2 ans.", "Experience de 9 ans.")
    assert not _safe_proposal("RPO de 24 heures et RTO de 4 heures", "Verifier RPO de 4 heures et RTO de 24 heures.")
    assert not _safe_proposal("EXG-05. Fournir les procedures de recette.", "Prevoir une recette pendant 5 jours.")
    assert not _safe_proposal("Integrer 12 sources.", "Prevoir une mission de 12 mois.")


def test_risk_and_mitigation_are_atomic_even_with_equal_lengths(notice, company):
    response = build_response(notice, company)
    row = next(row for row in response["requirement_responses"] if row.get("buyer_id") == "EXG-01")
    original = {row["id"]: row}
    data = json.loads(_model_rows([row], original, changed=True))
    data["requirements"][0]["risks"] = ["Les habilitations peuvent etre fermees pendant la recette."]
    data["requirements"][0]["mitigations"] = ["Nous avons livre 9 projets et garantissons cette disponibilite."]
    evidence = {item["id"]: item for item in response["evidence_register"]}
    issues = []
    validated = _validated_proposals(json.dumps(data), [row], evidence, feedback=issues)
    assert "acceptance" in validated[row["id"]]
    assert "risks" not in validated[row["id"]]
    assert "mitigations" not in validated[row["id"]]
    assert any(issue["reason"] == "risk_mitigation_mismatch" for issue in issues)


def test_single_bounded_repair_reserves_risk_and_mitigation_together(notice, company, monkeypatch):
    monkeypatch.delenv("NAVIN_TENDERS_AI", raising=False)
    company["ai_assist"] = True
    base = build_response(notice, company)
    original = {row["id"]: row for row in base["requirement_responses"]}
    repairs = []
    calls = []

    def fake_model(task, system, user, **kwargs):
        request = json.loads(user)
        calls.append(request)
        rows = request["requirements"]
        data = json.loads(_model_rows(rows, original, changed=True))
        if "allowed_fields" in rows[0]:
            repairs.append(rows)
            for item, source in zip(data["requirements"], rows):
                for field in set(_PROPOSAL_FIELDS) - set(source["allowed_fields"]):
                    item.pop(field)
        else:
            for item in data["requirements"]:
                item["risks"] = ["Les habilitations peuvent etre fermees pendant la recette."]
                item["mitigations"] = ["Nous avons livre 9 projets et garantissons cette disponibilite."]
        return json.dumps(data), "audit-model"

    with patch("navin.tenders.ai.ask", side_effect=fake_model):
        response = polish_response(notice, company, base)
    assert len(calls) == 3
    assert len(repairs) == 1
    assert sum(len(row["allowed_fields"]) for row in repairs[0]) <= 16
    for row in repairs[0]:
        assert ("risks" in row["allowed_fields"]) == ("mitigations" in row["allowed_fields"])
    assert response["generation"]["repaired_fields"] > 0
    for row in response["requirement_responses"]:
        assert len(row["risks"]) == len(row["mitigations"])
        assert "9 projets" not in " ".join(row["mitigations"])


def test_docx_preserves_partial_template_complete_cdc_and_real_toc(notice, company, tmp_path):
    docx = pytest.importorskip("docx")
    template = docx.Document()
    template.add_paragraph("En-tete Audit {{company_name}}")
    path = tmp_path / "audit-template.docx"
    template.save(path)
    company["templates"] = {"word": [{"name": path.name, "path": path.name, "file_id": "audit-template"}]}
    response = build_response(notice, company)
    data = render_docx(SimpleNamespace(files_dir=tmp_path), notice, company, response)
    doc = docx.Document(io.BytesIO(data))
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    text += "\n" + "\n".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
    assert "En-tete Audit Audit Conseil" in text
    assert "EXG-10" in text and "RPO 24 heures" in text and "Matrice de conformite" in text
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode()
        settings = archive.read("word/settings.xml").decode()
    assert 'TOC \\o "1-2"' in xml
    assert "tblHeader" in xml and "updateFields" in settings
    assert "\u2013" not in xml and "\u2014" not in xml


def test_pptx_pagination_keeps_long_last_line_and_slide_shapes_in_bounds(notice, company, tmp_path):
    pptx = pytest.importorskip("pptx")
    long_line = "Audit " + "verification de la derniere exigence " * 50 + "FIN-AUDIT-55"
    pages = _pptx_pages(long_line)
    assert len(pages) > 1
    assert "FIN-AUDIT-55" in pages[-1]
    response = build_response(notice, company)
    response["assumptions"] += "\n" + long_line
    data = render_pptx(SimpleNamespace(files_dir=tmp_path), notice, company, response)
    presentation = pptx.Presentation(io.BytesIO(data))
    texts = []
    for slide in presentation.slides:
        for shape in slide.shapes:
            assert shape.left + shape.width <= presentation.slide_width + 5
            assert shape.top + shape.height <= presentation.slide_height + 5
            if shape.has_text_frame:
                texts.append(shape.text)
    assert "FIN-AUDIT-55" in "\n".join(texts)


def test_archify_labels_and_tags_share_the_source_of_each_quantity(notice, company):
    response = build_response(notice, company)
    spec = _diagram_spec(notice, response)
    nodes = {row["id"]: row for row in spec["nodes"]}
    assert nodes["bid_delivery"]["label"] == "12 sources"
    assert nodes["bid_acceptance"]["sublabel"] == "RPO 24h / RTO 4h"
    assert nodes["bid_acceptance"]["tag"] == "EXG-05"
    assert nodes["bid_handover"]["tag"] == "EXG-07"
    assert spec["meta"]["quality_profile"] == "showcase"


def test_diagram_alone_does_not_make_a_word_powerpoint_pack_ready(notice, company, tmp_path):
    response = build_response(notice, company)

    def only_diagram(store, tender, out, exports):
        exports["diagram_html"] = {"kind": "html", "path": "audit.html"}

    with patch("navin.tenders.export._attach_diagram_exports", side_effect=only_diagram), patch("navin.tenders.export.render_docx", return_value=None), patch("navin.tenders.export.render_pptx", return_value=None):
        result = attach_exports(TenderStore(tmp_path), notice, company, response)
    assert "diagram_html" in result["exports"]
    assert result["pack_ready"] is False
    assert "Aucun export Word/PPT" in " ".join(result["generation"]["warnings"])


@pytest.mark.skipif(not shutil.which("node"), reason="Local Node renderer unavailable")
def test_real_archify_export_is_embedded_and_receipt_does_not_claim_visual_review(notice, company, tmp_path):
    pytest.importorskip("cairosvg")
    pytest.importorskip("docx")
    pytest.importorskip("pptx")
    response = attach_exports(TenderStore(tmp_path), notice, company, build_response(notice, company))
    assert {"diagram_html", "diagram_svg", "diagram_png", "diagram_spec", "docx", "pptx"} <= set(response["exports"])
    assert response["technical_diagram"]["validation"]["checksPassed"] == 9
    assert response["technical_diagram"]["validation"]["warnings"] == 0
    assert response["technical_diagram"]["visual_review"] == "not_performed"
    for kind, prefix in (("docx", "word/media/"), ("pptx", "ppt/media/")):
        with zipfile.ZipFile(tmp_path / "files" / response["exports"][kind]["path"]) as archive:
            assert any(name.startswith(prefix) and name.endswith(".png") for name in archive.namelist())
