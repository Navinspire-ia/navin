"""Official stickerdaniel/linkedin-mcp-server catalogue (README order).

Shared by Tenders (buyer research) and Career (user-session jobs / profile).
Never a scrape of linkedin.com. Write tools need confirm=true.
"""

from __future__ import annotations

LINKEDIN_MCP_DOCS = "https://github.com/stickerdaniel/linkedin-mcp-server"
LINKEDIN_MCP_COMMAND = "uvx mcp-server-linkedin@latest"
LINKEDIN_MCP_LOGIN = ("--login", "--import-from-browser")
LINKEDIN_MCP_OFFICIAL: tuple[str, ...] = (
    "get_person_profile",
    "get_my_profile",
    "connect_with_person",
    "get_sidebar_profiles",
    "get_inbox",
    "get_conversation",
    "search_conversations",
    "send_message",
    "get_company_profile",
    "get_company_posts",
    "search_companies",
    "get_company_employees",
    "search_jobs",
    "get_saved_jobs",
    "search_people",
    "get_job_details",
    "get_feed",
    "search_posts",
    "close_session",
)
LINKEDIN_MCP_GROUPS: dict[str, tuple[str, ...]] = {
    "company": (
        "get_company_profile",
        "get_company_posts",
        "search_companies",
        "get_company_employees",
    ),
    "people": (
        "search_people",
        "get_person_profile",
        "get_my_profile",
        "get_sidebar_profiles",
    ),
    "posts": ("get_feed", "search_posts"),
    "inbox": ("get_inbox", "get_conversation", "search_conversations"),
    "jobs": ("search_jobs", "get_saved_jobs", "get_job_details"),
    "session": ("close_session",),
}
LINKEDIN_MCP_CONFIRM: tuple[str, ...] = ("connect_with_person", "send_message")
LINKEDIN_MCP_JOBS: tuple[str, ...] = LINKEDIN_MCP_GROUPS["jobs"]
LINKEDIN_MCP_TOOLS: tuple[str, ...] = (
    *LINKEDIN_MCP_GROUPS["company"],
    *LINKEDIN_MCP_GROUPS["people"],
    *LINKEDIN_MCP_GROUPS["posts"],
    *LINKEDIN_MCP_GROUPS["inbox"],
    *LINKEDIN_MCP_GROUPS["session"],
)


def linkedin_mcp_row(*, role: str) -> dict[str, object]:
    return {
        "id": "linkedin",
        "name": "LinkedIn",
        "recommended": True,
        "role": role,
        "docs": LINKEDIN_MCP_DOCS,
        "command": LINKEDIN_MCP_COMMAND,
        "login": list(LINKEDIN_MCP_LOGIN),
        "official": list(LINKEDIN_MCP_OFFICIAL),
        "tools": list(LINKEDIN_MCP_TOOLS),
        "jobs": list(LINKEDIN_MCP_JOBS),
        "confirm": list(LINKEDIN_MCP_CONFIRM),
        "groups": {key: list(names) for key, names in LINKEDIN_MCP_GROUPS.items()},
    }
