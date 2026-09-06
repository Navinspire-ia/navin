"""CRM ids, stages, roles, and record shapes stored under ``.navin/crm/``."""

from __future__ import annotations

LEAD_STAGES = ("nouveau", "contacte", "qualifie", "converti", "perdu")
OPP_STAGES = ("nouveau", "qualifie", "proposition", "negociation", "gagne", "perdu")
ACTIVITY_KINDS = (
    "appel",
    "email",
    "whatsapp",
    "reunion",
    "tache",
    "note",
    "document",
    "teams",
)
CONTACT_STATUSES = ("actif", "inactif")
# Public list/create kinds used by crm_api and the crm tool.
KINDS = ("companies", "contacts", "leads", "opportunities", "activities")
# Extra tables in the SQLite store (not a 6th Studio menu).
STORE_KINDS = KINDS + ("products", "opportunity_lines", "members", "invitations", "audit", "files")

OPEN_OPP_STAGES = frozenset({"nouveau", "qualifie", "proposition", "negociation"})
WON_STAGE = "gagne"
LOST_STAGE = "perdu"

ROLES = ("owner", "admin", "member", "viewer")
INVITE_STATUSES = ("pending", "accepted", "declined")

# viewer: read only
# member: create/update own records + log activities
# admin: write all, convert, move stages, manage products
# owner: invite/kick + role changes
ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}

FOLLOWUP_DEFAULT_DAYS = 7
