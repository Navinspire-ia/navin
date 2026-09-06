"""S5.4 / S5.5 - what the AGI panel and ``navin agi transfer`` read and press.

``transfer_state`` is the whole picture: the flag, the prerequisites, the
suites (counts and lock only, never an item), the last campaign per family,
the safety dossier, the kill drill, and the claim computed by the protocol:

* ``transfer``: ``not_run`` | ``fail`` | ``pass`` | ``void``;
* ``safety``: ``not_run`` | ``fail`` | ``pass``;
* ``claim``: ``forbidden`` | ``discussable``.

``discussable`` needs a passed campaign on the current suites, a green
dossier exercised with S4 steer on, and a kill drill. Even then the product
does not write the word; a human decides, in public, with the protocol
cited. Navin never emits it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.transfer.campaign import replay, run_campaign, spawn_campaign
from navin.transfer.journal import journal, latest_campaign, read_campaigns, read_journal
from navin.transfer.paths import TRANSFER_DIR_NAME, default_suites_dir
from navin.transfer.prereqs import prereqs
from navin.transfer.protocol import FAMILY_IDS, PROTOCOL_VERSION, protocol_summary
from navin.transfer.safety import kill_drill, read_dossier, safety_case
from navin.transfer.settings import (
    FIELDS,
    SETTINGS_NAME,
    read_settings,
    update_settings,
    validate_fields,
)
from navin.transfer.suites import (
    SuitesError,
    SuitesNotFrozenError,
    SuitesTamperedError,
    check_outside,
    describe_suites,
    freeze,
    read_lock,
    suites_dir_for,
    verify,
)

HUMAN = "human"

ACTIONS = (
    "campaign",    # human: run the secret campaign (child process)
    "replay",      # human: replay one campaign (same items, same seeds)
    "safety",      # run the safety dossier
    "kill_drill",  # human: exercise the kill switches
    "verify",      # re-hash the suites against their lock
    "freeze",      # human: freeze the suites (authors / attester required)
)
HUMAN_ONLY = frozenset({"campaign", "replay", "kill_drill", "freeze"})


class TransferActionError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _suites_payload(workspace: Path) -> dict[str, Any]:
    suites_dir = suites_dir_for(workspace)
    payload: dict[str, Any] = {
        "dir": str(suites_dir),
        "default_dir": str(default_suites_dir()),
        "outside": True,
        "placement_error": None,
        "frozen": False,
        "version": None,
        "lock": None,
        "tampered": None,
        "families": {},
        "error": None,
    }
    try:
        check_outside(suites_dir, workspace)
    except SuitesError as exc:
        payload["outside"] = False
        payload["placement_error"] = str(exc)
    if not suites_dir.exists():
        return payload
    described = describe_suites(suites_dir)
    payload["families"] = described["families"]
    payload["error"] = described["error"]
    try:
        lock = verify(suites_dir)
    except SuitesNotFrozenError:
        return payload
    except SuitesTamperedError as exc:
        payload["frozen"] = True
        payload["tampered"] = str(exc)
        try:
            lock = read_lock(suites_dir)
        except SuitesTamperedError:
            lock = None
        if lock is not None:
            payload["version"] = lock.version
            payload["lock"] = lock.as_dict()
        return payload
    except SuitesError as exc:
        payload["error"] = str(exc)
        return payload
    payload["frozen"] = True
    payload["version"] = lock.version
    payload["lock"] = lock.as_dict()
    return payload


def _campaign_payload(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    families = record.get("families") or {}
    return {
        "id": record.get("id"),
        "ts": record.get("ts"),
        "suites_version": record.get("suites_version"),
        "verdict": record.get("verdict"),
        "stopped_at": record.get("stopped_at"),
        "not_run": record.get("not_run") or [],
        "replay_of": record.get("replay_of"),
        "isolation": record.get("isolation"),
        "families": {
            fam: {
                "items": data.get("items"),
                "passed": data.get("passed"),
                "pass_rate": data.get("pass_rate"),
                "bar": data.get("bar"),
                "verdict": data.get("verdict"),
            }
            for fam, data in families.items()
            if isinstance(data, dict)
        },
        "duration_ms": record.get("duration_ms"),
    }


def claim_of(*, campaign: dict[str, Any] | None, suites_version: str | None, tampered: str | None, dossier: dict[str, Any] | None) -> dict[str, Any]:
    """The protocol's answer. Never a claim, only whether one may be discussed."""
    reasons: list[str] = []
    transfer = "not_run"
    if campaign is None:
        reasons.append("no campaign was run")
    elif tampered:
        transfer = "void"
        reasons.append("the suites changed since the lock: every campaign on them is void")
    elif suites_version and campaign.get("suites_version") != suites_version:
        transfer = "void"
        reasons.append("the last campaign ran on another suites version")
    else:
        transfer = "pass" if campaign.get("verdict") == "pass" else "fail"
        if transfer == "fail":
            stopped = campaign.get("stopped_at")
            reasons.append(f"family {stopped} under its bar" if stopped else "campaign failed")
    safety = "not_run"
    if dossier is None:
        reasons.append("no safety dossier")
    else:
        safety = "pass" if dossier.get("ok") else "fail"
        if safety == "fail":
            reasons.append("safety dossier has holes")
        elif not (dossier.get("mode") or {}).get("steer_on"):
            reasons.append("safety dossier was not exercised with policy steer on")
    drill = "not_run"
    if dossier and isinstance(dossier.get("drill"), dict):
        drill = "pass" if dossier["drill"].get("ok") else "fail"
    if drill != "pass":
        reasons.append("kill switch not drilled" if drill == "not_run" else "kill drill has holes")
    status = "discussable" if not reasons else "forbidden"
    return {"status": status, "transfer": transfer, "safety": safety, "drill": drill, "reasons": reasons}


def transfer_state(workspace: Path | str, *, journal_limit: int = 30) -> dict[str, Any]:
    workspace = Path(workspace)
    settings = read_settings(workspace)
    gates = prereqs(workspace)
    from navin.transfer.ladder import ladder_dicts

    base: dict[str, Any] = {
        "enabled": settings.enabled,
        "suites_dir": settings.suites_dir,
        "settings_file": f".navin/{SETTINGS_NAME}",
        "transfer_dir": f".navin/{TRANSFER_DIR_NAME}",
        "fields": list(FIELDS),
        "protocol": protocol_summary(),
        "prereqs": gates.as_dict(),
        "ladder": ladder_dicts(workspace),
        "families": list(FAMILY_IDS),
    }
    if not settings.enabled:
        return {
            **base,
            "suites": None,
            "campaign": None,
            "campaigns": [],
            "safety": None,
            "claim": claim_of(campaign=None, suites_version=None, tampered=None, dossier=None),
            "journal": [],
        }
    suites = _suites_payload(workspace)
    campaign = latest_campaign(workspace)
    dossier = read_dossier(workspace)
    return {
        **base,
        "suites": suites,
        "campaign": _campaign_payload(campaign),
        "campaigns": [_campaign_payload(c) for c in read_campaigns(workspace, limit=10)][::-1],
        "safety": dossier,
        "claim": claim_of(campaign=campaign, suites_version=suites.get("version"), tampered=suites.get("tampered"), dossier=dossier),
        "journal": read_journal(workspace, limit=journal_limit)[::-1],
    }


def transfer_update(workspace: Path | str, fields: dict[str, Any]) -> dict[str, Any]:
    """Write the flag. ``enabled: true`` needs the prerequisites (409); a
    suites folder inside the project or a git checkout is refused (400)."""
    workspace = Path(workspace)
    try:
        validate_fields(fields)
    except ValueError as exc:
        raise TransferActionError(str(exc)) from exc
    current = read_settings(workspace)
    if fields.get("enabled") is True and not current.enabled:
        gates = prereqs(workspace)
        if not gates.ok:
            raise TransferActionError(
                "the transfer protocol unlocks by itself once skills evolution, the world model and the policy have passed their exams; still missing: "
                + "; ".join(gates.reasons),
                409,
            )
    if fields.get("suites_dir"):
        try:
            check_outside(Path(str(fields["suites_dir"])), workspace)
        except SuitesError as exc:
            raise TransferActionError(str(exc)) from exc
    try:
        settings = update_settings(workspace, fields)
    except ValueError as exc:
        raise TransferActionError(str(exc)) from exc
    if "enabled" in fields:
        journal(workspace, "enabled" if settings.enabled else "disabled", actor=HUMAN)
    if "suites_dir" in fields:
        journal(workspace, "suites_dir", value=settings.suites_dir, actor=HUMAN)
    return transfer_state(workspace)


def transfer_action(
    workspace: Path | str,
    action: str,
    *,
    actor: str = "auto",
    name: str | None = None,
    authors: list[str] | None = None,
    attester: str | None = None,
    trainers: list[str] | None = None,
    junior_baseline: dict[str, float] | None = None,
    inline: bool = False,
    runtime_factory: Any | None = None,
) -> dict[str, Any]:
    """Press one button. 400 bad request, 403 human required, 409 flag off or
    state forbids. ``inline`` runs the campaign in this process (tests)."""
    workspace = Path(workspace)
    if action not in ACTIONS:
        raise TransferActionError(f"unknown action: {action}")
    settings = read_settings(workspace)
    if not settings.enabled:
        raise TransferActionError("transfer protocol is off for this project", 409)
    if action in HUMAN_ONLY and actor != HUMAN:
        raise TransferActionError(f"{action} needs a human", 403)
    result: dict[str, Any]
    if action == "campaign":
        if inline:
            result = run_campaign(workspace, actor=HUMAN, runtime_factory=runtime_factory)
        else:
            result = spawn_campaign(workspace, actor=HUMAN)
    elif action == "replay":
        if not name:
            raise TransferActionError("a campaign id is required")
        if inline:
            result = replay(workspace, name, actor=HUMAN, runtime_factory=runtime_factory)
        else:
            result = spawn_campaign(workspace, actor=HUMAN, replay_of=name)
    elif action == "safety":
        result = safety_case(workspace, actor=actor)
    elif action == "kill_drill":
        result = kill_drill(workspace, actor=HUMAN)
    elif action == "verify":
        try:
            lock = verify(suites_dir_for(workspace))
            result = {"status": "frozen", "version": lock.version, "families": lock.families}
        except SuitesTamperedError as exc:
            result = {"status": "tampered", "reason": str(exc)}
        except SuitesError as exc:
            result = {"status": "not_frozen", "reason": str(exc)}
    else:
        try:
            lock = freeze(
                suites_dir_for(workspace),
                actor=HUMAN,
                authors=authors or [],
                attester=attester or "",
                trainers=trainers or [],
                junior_baseline=junior_baseline,
                workspace=workspace,
            )
        except SuitesError as exc:
            raise TransferActionError(str(exc), 409) from exc
        journal(workspace, "frozen", version=lock.version, authors=list(lock.authors), attester=lock.attester, actor=HUMAN)
        result = {"status": "frozen", **lock.as_dict()}
    status = result.get("status")
    if status in ("refused", "skipped", "not_found"):
        raise TransferActionError(str(result.get("reason") or status), 409)
    return {"ok": status not in ("void", "error", "holes", "tampered"), "action": action, "result": result, "state": transfer_state(workspace)}


__all__ = ["ACTIONS", "HUMAN_ONLY", "PROTOCOL_VERSION", "TransferActionError", "claim_of", "transfer_action", "transfer_state", "transfer_update"]
