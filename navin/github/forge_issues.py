# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Issue maintenance calls on any forge, built for the triage autopilot.

``navin.webui.forge_api`` covers what the Code panel needs: one page of
issues, a comment, a plain close. Triage needs more, and needs it to be
exhaustive, because a rule like "nobody commented after the label" is only
true if every comment and every event was read:

- full pagination everywhere (``Link: rel=next``, ``X-Next-Page`` or a short
  page), never just the first 100 rows;
- labels added and removed one by one, so closing an issue never replaces
  the labels a maintainer set by hand;
- close with a ``state_reason`` (``duplicate`` / ``not_planned``);
- rate limits honoured (``Retry-After``, ``X-RateLimit-Reset``) with a
  bounded backoff instead of a hard failure on the first 429/403;
- a 404 on a sub-resource (comments, events, reactions) is an empty list,
  not an exception, so one deleted issue cannot stop a sweep.

GitHub is the reference; GitLab and Forgejo are mapped best effort onto the
same normalized shapes. Token resolution, headers and error wording are the
ones from ``forge_api``, so a token that works in the Code panel works here.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from navin.webui import forge_api
from navin.webui.forge_api import FORGEJO, GITHUB, GITLAB, ForgeError, ForgeRemote

_PER_PAGE = 100
# Gitea / Forgejo cap ``limit`` at 50 by default (MAX_RESPONSE_ITEMS).
_FORGEJO_PER_PAGE = 50
_MAX_PAGES = 200
_MAX_RETRIES = 4
_BACKOFF_BASE_S = 1.0
# Longer than this and the sweep stops with an explicit error rather than
# holding a turn (or a cron slot) for most of an hour.
_MAX_WAIT_S = 300.0
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_MAX_COMMENT = 4000

_LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?')
_BOT_SUFFIXES = ("[bot]", "-bot", "_bot")

# GitLab award emoji names -> GitHub reaction contents.
_GITLAB_EMOJI = {"thumbsup": "+1", "thumbsdown": "-1"}

CLOSE_REASONS = ("not_planned", "duplicate", "completed")


def _lower_headers(response: Any) -> dict[str, str]:
    raw = getattr(response, "headers", None) or {}
    try:
        items = raw.items()
    except AttributeError:
        return {}
    return {str(key).lower(): str(value) for key, value in items}


def is_bot_login(login: str | None, *, user_type: str | None = None, extra: frozenset[str] = frozenset()) -> bool:
    """Whether an account is automation (GitHub Apps, CI users, configured bots)."""
    name = str(login or "").strip().lower()
    if str(user_type or "").lower() == "bot":
        return True
    if not name:
        return True  # ghost / deleted accounts never count as human activity
    if name in extra:
        return True
    return name.endswith(_BOT_SUFFIXES)


class ForgeIssueClient:
    """Paginated, rate-limit aware issue API for one repository."""

    def __init__(
        self,
        remote: ForgeRemote,
        token: str,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        max_retries: int = _MAX_RETRIES,
        max_wait_s: float = _MAX_WAIT_S,
        max_pages: int = _MAX_PAGES,
        bot_logins: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> None:
        self.remote = remote
        self.token = token
        self._sleep = sleep
        self._clock = clock
        self._max_retries = max(0, int(max_retries))
        self._max_wait_s = float(max_wait_s)
        self._max_pages = max(1, int(max_pages))
        self.bot_logins = frozenset(str(b).strip().lower() for b in bot_logins if str(b).strip())
        self._login: str | None = None
        self._login_loaded = False
        self._forgejo_labels: dict[str, int] | None = None
        self.waits: list[float] = []

    # -- plumbing ------------------------------------------------------------

    @property
    def kind(self) -> str:
        return self.remote.kind

    def _repo(self) -> str:
        if self.kind == GITLAB:
            return f"{self.remote.api_base}/projects/{quote(self.remote.slug, safe='')}"
        return f"{self.remote.api_base}/repos/{self.remote.slug}"

    def _issue(self, number: int) -> str:
        return f"{self._repo()}/issues/{int(number)}"

    def _rate_limited(self, status: int, headers: dict[str, str], text: str) -> bool:
        if status == 429:
            return True
        if status != 403:
            return False
        if headers.get("x-ratelimit-remaining") == "0" or headers.get("ratelimit-remaining") == "0":
            return True
        if "retry-after" in headers:
            return True
        lowered = (text or "").lower()
        return "rate limit" in lowered or "abuse detection" in lowered

    def _wait_for(self, headers: dict[str, str], attempt: int) -> float:
        retry_after = headers.get("retry-after", "").strip()
        if retry_after:
            if retry_after.isdigit():
                return float(retry_after)
            try:
                return max(0.0, parsedate_to_datetime(retry_after).timestamp() - self._clock())
            except (TypeError, ValueError, IndexError, OverflowError):
                pass
        reset = (headers.get("x-ratelimit-reset") or headers.get("ratelimit-reset") or "").strip()
        if reset.isdigit():
            value = float(reset)
            # GitLab sends an epoch too; a small number is a delta in seconds.
            wait = value - self._clock() if value > 10**9 else value
            return max(1.0, wait + 1.0)
        return _BACKOFF_BASE_S * (2**attempt)

    def _call(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> tuple[int, Any, str, dict[str, str]]:
        """One REST call with rate-limit backoff. Redirects are never followed."""
        attempt = 0
        while True:
            try:
                response = httpx.request(
                    method,
                    url,
                    headers=forge_api._headers(self.kind, self.token),
                    params=params,
                    json=json_body,
                    timeout=forge_api._HTTP_TIMEOUT_S,
                    follow_redirects=False,
                )
            except (httpx.HTTPError, OSError, ValueError) as exc:
                raise ForgeError(
                    f"cannot reach {self.remote.host}: "
                    f"{forge_api.redact_url_credentials(str(exc))[:200]}",
                    status=502,
                    code="forge_unreachable",
                    host=self.remote.host,
                    forge=self.kind,
                ) from exc
            text = getattr(response, "text", "") or ""
            try:
                payload = response.json() if text.strip() else None
            except ValueError:
                payload = None
            status = int(response.status_code)
            headers = _lower_headers(response)
            limited = self._rate_limited(status, headers, text)
            if not limited and status not in _RETRYABLE_STATUS:
                return status, payload, text, headers
            if attempt >= self._max_retries:
                if limited:
                    raise ForgeError(
                        f"{forge_api.FORGE_LABELS.get(self.kind, self.kind)} rate limit "
                        f"still exhausted after {attempt} retries; run triage again later",
                        status=429,
                        code="forge_rate_limited",
                        host=self.remote.host,
                        forge=self.kind,
                    )
                return status, payload, text, headers
            wait = self._wait_for(headers, attempt)
            if wait > self._max_wait_s:
                raise ForgeError(
                    f"{forge_api.FORGE_LABELS.get(self.kind, self.kind)} rate limit resets "
                    f"in {int(wait)}s; run triage again after that",
                    status=429,
                    code="forge_rate_limited",
                    host=self.remote.host,
                    forge=self.kind,
                )
            self.waits.append(wait)
            self._sleep(wait)
            attempt += 1

    def _check(self, status: int, payload: Any, text: str, action: str) -> None:
        forge_api._raise_for_status(self.remote, status, payload, text, action=action)

    def _paginate(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        action: str,
        limit: int | None = None,
    ) -> list[Any]:
        """Every row behind ``url``. A 404 is an empty list."""
        per_page = _FORGEJO_PER_PAGE if self.kind == FORGEJO else _PER_PAGE
        size_key = "limit" if self.kind == FORGEJO else "per_page"
        query: dict[str, Any] | None = {**(params or {}), size_key: per_page, "page": 1}
        next_url = url
        api_host = urlsplit(self.remote.api_base).hostname
        rows: list[Any] = []
        for _page in range(self._max_pages):
            status, payload, text, headers = self._call("GET", next_url, params=query)
            if status == 404:
                return rows
            self._check(status, payload, text, action)
            batch = payload if isinstance(payload, list) else []
            rows.extend(batch)
            if limit is not None and len(rows) >= limit:
                return rows[:limit]
            link = _LINK_NEXT.search(headers.get("link", ""))
            if link:
                candidate = link.group(1)
                # Never send the token to another host a header points at.
                if urlsplit(candidate).hostname != api_host:
                    return rows
                next_url, query = candidate, None
                continue
            next_page = headers.get("x-next-page", "").strip()
            if self.kind == GITLAB and "x-next-page" in headers:
                if not next_page.isdigit():
                    return rows
                query = {**(params or {}), size_key: per_page, "page": int(next_page)}
                next_url = url
                continue
            if len(batch) < per_page or query is None:
                # A short page, or the end of a Link chain: nothing further.
                return rows
            current = int((query or {}).get("page") or 1)
            query = {**(params or {}), size_key: per_page, "page": current + 1}
            next_url = url
        return rows

    # -- identity ------------------------------------------------------------

    def whoami(self) -> str | None:
        """Login behind the token, cached; ``None`` when the forge will not say."""
        if self._login_loaded:
            return self._login
        self._login_loaded = True
        try:
            status, payload, _text, _headers = self._call("GET", f"{self.remote.api_base}/user")
        except ForgeError:
            return None
        if 200 <= status < 300 and isinstance(payload, dict):
            login = payload.get("login") or payload.get("username")
            self._login = str(login) if login else None
        return self._login

    def is_bot(self, login: str | None, user_type: str | None = None) -> bool:
        return is_bot_login(login, user_type=user_type, extra=self.bot_logins)

    # -- reads ---------------------------------------------------------------

    def _normalize_issue(self, row: dict[str, Any]) -> dict[str, Any]:
        issue = forge_api.normalize_issue(self.kind, row)
        if self.kind == GITLAB:
            issue["locked"] = bool(row.get("discussion_locked"))
            upvotes = row.get("upvotes")
        elif self.kind == GITHUB:
            issue["locked"] = bool(row.get("locked"))
            reactions = row.get("reactions") if isinstance(row.get("reactions"), dict) else {}
            upvotes = reactions.get("+1")
        else:
            issue["locked"] = bool(row.get("is_locked") or row.get("locked"))
            upvotes = None
        issue["upvotes"] = int(upvotes) if isinstance(upvotes, (int, float)) else None
        user = row.get("user") if isinstance(row.get("user"), dict) else row.get("author")
        issue["author_type"] = (user or {}).get("type") if isinstance(user, dict) else None
        return issue

    def list_open_issues(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Every open issue (pull requests excluded), oldest update first."""
        if self.kind == GITLAB:
            params: dict[str, Any] = {"state": "opened", "order_by": "updated_at", "sort": "asc"}
        elif self.kind == GITHUB:
            params = {"state": "open", "sort": "updated", "direction": "asc"}
        else:
            params = {"state": "open", "type": "issues"}
        rows = self._paginate(f"{self._repo()}/issues", params, action="list issues")
        issues = [
            self._normalize_issue(row)
            for row in rows
            if isinstance(row, dict) and not forge_api._is_pull_request_row(self.kind, row)
        ]
        issues.sort(key=lambda item: str(item.get("updated_at") or ""))
        return issues[:limit] if limit is not None else issues

    def get_issue(self, number: int) -> dict[str, Any] | None:
        status, payload, text, _headers = self._call("GET", self._issue(number))
        if status == 404 or not isinstance(payload, dict):
            return None
        self._check(status, payload, text, "read the issue")
        if forge_api._is_pull_request_row(self.kind, payload):
            return None
        return self._normalize_issue(payload)

    def issue_upvotes(self, number: int) -> int:
        """Thumbs-up count on the issue itself (Forgejo lists omit it)."""
        if self.kind == GITLAB:
            url = f"{self._issue(number)}/award_emoji"
        else:
            url = f"{self._issue(number)}/reactions"
        rows = self._paginate(url, action="read reactions")
        return sum(1 for row in rows if self._reaction_content(row) == "+1")

    def comments(self, number: int) -> list[dict[str, Any]]:
        """Every comment on an issue, oldest first (GitLab system notes dropped)."""
        if self.kind == GITLAB:
            url = f"{self._issue(number)}/notes"
            params: dict[str, Any] | None = {"sort": "asc", "order_by": "created_at"}
        else:
            url = f"{self._issue(number)}/comments"
            params = None
        rows = self._paginate(url, params, action="read issue comments")
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if self.kind == GITLAB and row.get("system"):
                continue
            user = row.get("author") if self.kind == GITLAB else row.get("user")
            user = user if isinstance(user, dict) else {}
            login = user.get("login") or user.get("username")
            user_type = user.get("type") or ("Bot" if user.get("bot") else None)
            out.append(
                {
                    "id": row.get("id"),
                    "author": str(login) if login else None,
                    "author_is_bot": self.is_bot(login, user_type),
                    "body": str(row.get("body") or ""),
                    "created_at": str(row.get("created_at") or ""),
                }
            )
        out.sort(key=lambda item: item["created_at"])
        return out

    def label_events(self, number: int) -> list[dict[str, Any]]:
        """``labeled`` / ``unlabeled`` events, oldest first, every page."""
        out: list[dict[str, Any]] = []
        if self.kind == GITLAB:
            rows = self._paginate(f"{self._issue(number)}/resource_label_events", action="read issue events")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                label = row.get("label") if isinstance(row.get("label"), dict) else {}
                user = row.get("user") if isinstance(row.get("user"), dict) else {}
                out.append(
                    {
                        "event": "labeled" if row.get("action") == "add" else "unlabeled",
                        "label": str((label or {}).get("name") or ""),
                        "created_at": str(row.get("created_at") or ""),
                        "actor": user.get("username"),
                    }
                )
        elif self.kind == GITHUB:
            rows = self._paginate(f"{self._issue(number)}/events", action="read issue events")
            for row in rows:
                if not isinstance(row, dict) or row.get("event") not in ("labeled", "unlabeled"):
                    continue
                label = row.get("label") if isinstance(row.get("label"), dict) else {}
                actor = row.get("actor") if isinstance(row.get("actor"), dict) else {}
                out.append(
                    {
                        "event": row.get("event"),
                        "label": str((label or {}).get("name") or ""),
                        "created_at": str(row.get("created_at") or ""),
                        "actor": actor.get("login"),
                    }
                )
        else:
            rows = self._paginate(f"{self._issue(number)}/timeline", action="read issue events")
            for row in rows:
                if not isinstance(row, dict) or row.get("type") != "label":
                    continue
                label = row.get("label") if isinstance(row.get("label"), dict) else {}
                user = row.get("user") if isinstance(row.get("user"), dict) else {}
                out.append(
                    {
                        # Gitea stores "1" in the body of an "added" label comment.
                        "event": "labeled" if str(row.get("body") or "") == "1" else "unlabeled",
                        "label": str((label or {}).get("name") or ""),
                        "created_at": str(row.get("created_at") or ""),
                        "actor": user.get("login"),
                    }
                )
        out.sort(key=lambda item: item["created_at"])
        return out

    @staticmethod
    def _reaction_content(row: Any) -> str:
        if not isinstance(row, dict):
            return ""
        if "content" in row:
            return str(row.get("content") or "")
        return _GITLAB_EMOJI.get(str(row.get("name") or ""), str(row.get("name") or ""))

    def comment_reactions(self, comment_id: Any, *, number: int | None = None) -> list[dict[str, Any]]:
        """``[{content, user}]`` on one comment; GitLab needs the issue number."""
        if self.kind == GITLAB:
            if number is None:
                return []
            url = f"{self._issue(number)}/notes/{comment_id}/award_emoji"
        else:
            url = f"{self._repo()}/issues/comments/{comment_id}/reactions"
        rows = self._paginate(url, action="read comment reactions")
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            user = row.get("user") if isinstance(row.get("user"), dict) else {}
            out.append(
                {
                    "content": self._reaction_content(row),
                    "user": user.get("login") or user.get("username"),
                }
            )
        return out

    # -- writes --------------------------------------------------------------

    def comment(self, number: int, body: str) -> dict[str, Any]:
        text_body = (body or "").strip()[:_MAX_COMMENT]
        if not text_body:
            raise ForgeError(
                "refusing to post an empty comment",
                status=400,
                code="forge_empty_comment",
                host=self.remote.host,
                forge=self.kind,
            )
        suffix = "notes" if self.kind == GITLAB else "comments"
        status, payload, text, _headers = self._call(
            "POST", f"{self._issue(number)}/{suffix}", json_body={"body": text_body}
        )
        self._check(status, payload, text, "comment on the issue")
        data = payload if isinstance(payload, dict) else {}
        return {"ok": True, "number": int(number), "id": data.get("id")}

    def _forgejo_label_map(self) -> dict[str, int]:
        if self._forgejo_labels is None:
            rows = self._paginate(f"{self._repo()}/labels", action="list labels")
            self._forgejo_labels = {
                str(row.get("name") or "").strip().lower(): int(row["id"])
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("id"), int)
            }
        return self._forgejo_labels

    def _forgejo_label_id(self, name: str, *, create: bool) -> int | None:
        found = self._forgejo_label_map().get(name.strip().lower())
        if found is not None or not create:
            return found
        status, payload, text, _headers = self._call(
            "POST", f"{self._repo()}/labels", json_body={"name": name, "color": "#cccccc"}
        )
        self._check(status, payload, text, "create a label")
        if isinstance(payload, dict) and isinstance(payload.get("id"), int):
            self._forgejo_label_map()[name.strip().lower()] = int(payload["id"])
            return int(payload["id"])
        return None

    def add_labels(self, number: int, labels: list[str] | tuple[str, ...]) -> dict[str, Any]:
        """Add labels, keeping every label already on the issue."""
        names = [str(label).strip() for label in labels if str(label).strip()]
        if not names:
            return {"ok": True, "added": []}
        if self.kind == GITLAB:
            status, payload, text, _headers = self._call(
                "PUT", self._issue(number), json_body={"add_labels": ",".join(names)}
            )
        elif self.kind == GITHUB:
            # POST /labels appends; PATCH {"labels": [...]} would replace them.
            status, payload, text, _headers = self._call(
                "POST", f"{self._issue(number)}/labels", json_body={"labels": names}
            )
        else:
            ids = [i for i in (self._forgejo_label_id(n, create=True) for n in names) if i is not None]
            status, payload, text, _headers = self._call(
                "POST", f"{self._issue(number)}/labels", json_body={"labels": ids}
            )
        self._check(status, payload, text, "label the issue")
        return {"ok": True, "added": names}

    def remove_label(self, number: int, label: str) -> dict[str, Any]:
        """Remove one label; a label that is not there is not an error."""
        name = str(label or "").strip()
        if not name:
            return {"ok": True, "removed": None}
        if self.kind == GITLAB:
            status, payload, text, _headers = self._call(
                "PUT", self._issue(number), json_body={"remove_labels": name}
            )
        elif self.kind == GITHUB:
            status, payload, text, _headers = self._call(
                "DELETE", f"{self._issue(number)}/labels/{quote(name, safe='')}"
            )
        else:
            label_id = self._forgejo_label_id(name, create=False)
            if label_id is None:
                return {"ok": True, "removed": None}
            status, payload, text, _headers = self._call(
                "DELETE", f"{self._issue(number)}/labels/{label_id}"
            )
        if status == 404:
            return {"ok": True, "removed": None}
        self._check(status, payload, text, "unlabel the issue")
        return {"ok": True, "removed": name}

    def close(self, number: int, *, reason: str = "not_planned") -> dict[str, Any]:
        """Close with a reason; labels and assignees are left untouched."""
        state_reason = reason if reason in CLOSE_REASONS else "not_planned"
        if self.kind == GITLAB:
            status, payload, text, _headers = self._call(
                "PUT", self._issue(number), json_body={"state_event": "close"}
            )
        elif self.kind == GITHUB:
            status, payload, text, _headers = self._call(
                "PATCH",
                self._issue(number),
                json_body={"state": "closed", "state_reason": state_reason},
            )
            if status == 422 and state_reason == "duplicate":
                # Older GitHub Enterprise servers predate the duplicate reason.
                state_reason = "not_planned"
                status, payload, text, _headers = self._call(
                    "PATCH",
                    self._issue(number),
                    json_body={"state": "closed", "state_reason": state_reason},
                )
        else:
            status, payload, text, _headers = self._call(
                "PATCH", self._issue(number), json_body={"state": "closed"}
            )
        self._check(status, payload, text, "close the issue")
        return {"ok": True, "number": int(number), "state": "closed", "state_reason": state_reason}
