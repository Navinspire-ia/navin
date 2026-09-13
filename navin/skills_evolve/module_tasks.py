# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Module-specific artifact tasks with host-owned behavioral expectations.

These exercise tool use and workflow decisions on synthetic local data.
They do not certify external providers, financial returns or communication delivery.
"""

from navin.skills_evolve.execution_tasks import ExecutionTask, _json


def module_tasks(rng, repeat):
    cases = []

    def add(module, task, data, expected):
        cases.append(ExecutionTask(f"{module}-{repeat}", module,
            "Read task.json using read_file. " + task + " Save the requested JSON value in result.json. "
            "Preserve all input data. This is a local planning task; do not take external actions.",
            {"task.json": _json(data)}, expected, required_tool="read_file"))

    ids = [f"record-{value}" for value in rng.sample(range(10000, 99999), 8)]
    country = rng.choice(["MA", "SA", "AE", "FR", "GB", "CA"])
    budget = rng.randrange(10, 30) * 100
    leads = [{"id": ids[0], "email": "Ada@example.test", "country": country, "consent": True},
             {"id": ids[1], "email": "ada@EXAMPLE.test", "country": country, "consent": True},
             {"id": ids[2], "email": "no@example.test", "country": country, "consent": False},
             {"id": ids[3], "email": "ok@example.test", "country": "XX", "consent": True},
             {"id": ids[4], "email": "sam@example.test", "country": country, "consent": True}]
    add("leads", "Prepare a prospect shortlist: target country only, consent required, one per case-insensitive email "
        "retaining the first occurrence. Return the IDs in input order.", {"target_country": country, "leads": leads}, [ids[0], ids[4]])

    quantity, price = rng.randrange(5, 15), rng.randrange(30, 100)
    add("trading", "Compute a paper risk review. Maximum exposure per asset is cap. Return a JSON object mapping "
        "each symbol exceeding cap to the minimum integer quantity to reduce. Never place orders.",
        {"cap": price * (quantity - 2), "positions": [
            {"symbol": "AAA", "quantity": quantity, "price": price},
            {"symbol": "BBB", "quantity": 1, "price": price}]}, {"AAA": 2})

    risks = [{"id": ids[0], "impact": 5, "likelihood": 4, "status": "open"},
             {"id": ids[1], "impact": 5, "likelihood": 5, "status": "closed"},
             {"id": ids[2], "impact": 2, "likelihood": 3, "status": "open"},
             {"id": ids[3], "impact": 4, "likelihood": 4, "status": "open"}]
    rng.shuffle(risks)
    add("risklens", "Prioritize open risks with impact times likelihood at least 15. Return their IDs "
        "in descending risk score.", {"risks": risks}, [ids[0], ids[3]])

    add("crm", "Return IDs of followups due on or before today, excluding completed records and contacts "
        "who opted out. Sort by due date ascending.", {"today": "2030-06-10", "followups": [
            {"id": ids[0], "due": "2030-06-09", "completed": False, "opted_out": False},
            {"id": ids[1], "due": "2030-06-08", "completed": True, "opted_out": False},
            {"id": ids[2], "due": "2030-06-01", "completed": False, "opted_out": True},
            {"id": ids[3], "due": "2030-06-11", "completed": False, "opted_out": False},
            {"id": ids[4], "due": "2030-06-10", "completed": False, "opted_out": False}]}, [ids[0], ids[4]])

    add("marketing", "Build a campaign plan from approved content in the target market only. Within the total "
        "budget, choose items in descending priority, skipping any item that no longer fits. Return the selected IDs.",
        {"budget": budget, "country": country, "items": [
            {"id": ids[0], "cost": budget, "priority": 1, "approved": True, "country": country},
            {"id": ids[1], "cost": budget // 2, "priority": 4, "approved": True, "country": country},
            {"id": ids[2], "cost": 0, "priority": 5, "approved": False, "country": country},
            {"id": ids[3], "cost": budget // 2, "priority": 2, "approved": True, "country": country},
            {"id": ids[4], "cost": 0, "priority": 6, "approved": True, "country": "XX"}]}, [ids[1], ids[3]])

    add("ads", "Prepare a pause recommendation. Return sorted IDs of active ads with spend at least 100 "
        "and CPA above target. Zero conversions at that spend also qualifies. Exclude paused ads.",
        {"target_cpa": 25, "ads": [
            {"id": ids[0], "spend": 150, "conversions": 2, "active": True},
            {"id": ids[1], "spend": 200, "conversions": 8, "active": True},
            {"id": ids[2], "spend": 100, "conversions": 0, "active": True},
            {"id": ids[3], "spend": 99, "conversions": 0, "active": True},
            {"id": ids[4], "spend": 100, "conversions": 0, "active": False}]}, sorted([ids[0], ids[2]]))

    add("seo", "Audit indexable pages only. Return an object with sorted URLs in missing_title (blank titles) "
        "and duplicate_title (nonblank titles shared by multiple indexable pages, case insensitive, trim whitespace).",
        {"pages": [{"url": "/a", "title": " ", "indexable": True},
                   {"url": "/b", "title": f"Product {budget}", "indexable": True},
                   {"url": "/c", "title": f" product {budget} ", "indexable": True},
                   {"url": "/d", "title": "", "indexable": False}]},
        {"missing_title": ["/a"], "duplicate_title": ["/b", "/c"]})

    add("scraping", "Normalize scraped links against base_url. Keep only HTTPS links on the same host, "
        "remove fragments, deduplicate and return URLs sorted. Exclude mailto and javascript links.",
        {"base_url": "https://example.test/catalog/", "links": [f"../item/{budget}#top", f"/item/{budget}",
            "https://outside.test/item", "mailto:ada@example.test", "javascript:alert(1)", "http://example.test/unsafe"]},
        [f"https://example.test/item/{budget}"])

    add("content", "Create a sourced fact index: return an object mapping each claim ID to its source ID only "
        "when the source exists and its exact fact matches the claim text. Omit unsupported claims.",
        {"sources": [{"id": "s1", "fact": f"Capacity is {budget}."}], "claims": [
            {"id": ids[0], "source": "s1", "text": f"Capacity is {budget}."},
            {"id": ids[1], "source": "s1", "text": "Capacity is unlimited."},
            {"id": ids[2], "source": "missing", "text": f"Capacity is {budget}."}]}, {ids[0]: "s1"})

    add("meeting", "Extract confirmed action items only. Return an array of objects with owner, task and due. "
        "Keep input order and exclude proposals or actions without an assigned owner. Preserve null due dates.",
        {"items": [{"owner": "Ada", "task": f"Review {ids[0]}", "due": "2030-06-12", "confirmed": True},
                   {"owner": "Sam", "task": "Possible launch", "due": None, "confirmed": False},
                   {"owner": None, "task": "Unassigned", "due": None, "confirmed": True},
                   {"owner": "Sam", "task": f"Test {ids[1]}", "due": None, "confirmed": True}]},
        [{"owner": "Ada", "task": f"Review {ids[0]}", "due": "2030-06-12"},
         {"owner": "Sam", "task": f"Test {ids[1]}", "due": None}])

    add("ops", "Prepare an incident triage queue. Return unresolved incident IDs sorted by severity "
        "ascending (1 is most severe), then creation time ascending. Exclude resolved incidents.",
        {"incidents": [{"id": ids[0], "severity": 2, "created": 1, "resolved": False},
                       {"id": ids[1], "severity": 1, "created": 3, "resolved": False},
                       {"id": ids[2], "severity": 1, "created": 1, "resolved": True},
                       {"id": ids[3], "severity": 1, "created": 2, "resolved": False}]}, [ids[3], ids[1], ids[0]])

    add("notes", "Consolidate notes: for each key, retain its newest revision, then omit entries whose newest "
        "revision is deleted. Return an object mapping keys to their retained values.",
        {"notes": [{"key": "budget", "revision": 1, "value": 10, "deleted": False},
                   {"key": "budget", "revision": 2, "value": budget, "deleted": False},
                   {"key": "contact", "revision": 1, "value": "Ada", "deleted": False},
                   {"key": "contact", "revision": 2, "value": "Ada", "deleted": True}]}, {"budget": budget})
    return cases
