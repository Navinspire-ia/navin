"""SEO engine tool, intended for the SEO product module."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from pydantic import Field

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.path_utils import project_rooted_path
from navin.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from navin.agent.tools.scrape_ops import parse_sitemap_urls
from navin.config_base import Base
from navin.security.workspace_access import current_tool_workspace
from navin.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from navin.seo.crawler import SeoCrawler
from navin.seo.models import AuditResult, Confidence, Evidence, Finding, PageRecord, Severity
from navin.seo.onpage import audit_site
from navin.seo.performance import PerformanceClient
from navin.seo.report import report_payload, write_report
from navin.seo.scoring import health_score
from navin.seo.serp import DataForSeoAdapter, SemrushAdapter, SerpStore
from navin.seo.structured import validate_jsonld


class SeoToolConfig(Base):
    enabled: bool = True
    max_pages: int = Field(default=50, ge=1, le=500)
    max_depth: int = Field(default=2, ge=0, le=8)
    timeout_seconds: float = Field(default=30, ge=1, le=300)
    allow_private_network: bool = False
    psi_api_key: str | None = Field(default=None, repr=False)
    crux_api_key: str | None = Field(default=None, repr=False)
    dataforseo_login: str | None = Field(default=None, repr=False)
    dataforseo_password: str | None = Field(default=None, repr=False)
    semrush_api_key: str | None = Field(default=None, repr=False)
    serp_store_path: str = "seo/serp-history.jsonl"


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "SEO action.",
            enum=[
                "crawl", "audit", "schema", "psi", "crux", "serp_snapshot",
                "serp_history", "score", "report", "pipeline",
            ],
        ),
        url=StringSchema("Target page or site URL."),
        origin=StringSchema("Origin for CrUX, for example https://example.com."),
        data=StringSchema("JSON payload containing pages, findings, audit, or raw HTML."),
        keyword=StringSchema("SERP keyword."),
        location=StringSchema("Provider location name or database code."),
        language=StringSchema("Language code."),
        provider=StringSchema("SERP provider.", enum=["dataforseo", "semrush"]),
        format=StringSchema("Report format.", enum=["json", "md", "html"]),
        path=StringSchema("Workspace-relative report or SERP store path."),
        max_pages=IntegerSchema(description="Maximum pages.", minimum=1, maximum=500),
        max_depth=IntegerSchema(description="Maximum crawl depth.", minimum=0, maximum=8),
        required=["action"],
    )
)
class SeoTool(Tool):
    """Grounded SEO audit, performance, SERP, scoring and reporting engine."""

    config_key = "seo"
    _scopes = {"core"}
    product_module = "seo"

    @classmethod
    def config_cls(cls):
        return SeoToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.seo.enabled

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace, config=ctx.config.seo)

    def __init__(self, *, workspace: str | Path, config: SeoToolConfig) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config

    @property
    def name(self) -> str:
        return "seo"

    @property
    def description(self) -> str:
        return (
            "SEO-only engine for grounded crawling, audits, JSON-LD validation, PSI v5, "
            "CrUX v1, sourced SERP snapshots, deterministic scoring and reports. "
            "Never invent rankings, volume, performance, or schema evidence."
        )

    @property
    def read_only(self) -> bool:
        return False

    def _path(self, raw: str) -> Path:
        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        workspace = access.project_path or self.workspace
        try:
            resolved = resolve_allowed_path(
                project_rooted_path(raw, workspace, [access.allowed_root]),
                workspace=workspace,
                allowed_root=access.allowed_root,
                strict=False,
            )
        except WorkspaceBoundaryError as exc:
            raise ValueError("path must stay inside the project") from exc
        return Path(resolved)

    def _crawler(self) -> SeoCrawler:
        return SeoCrawler(
            timeout=self.config.timeout_seconds,
            allow_private_network=self.config.allow_private_network,
        )

    @staticmethod
    def _audit(data: str) -> AuditResult:
        payload = json.loads(data)
        if "schema_version" in payload and "findings" in payload:
            return AuditResult.model_validate(payload)
        pages = [PageRecord.model_validate(item) for item in payload.get("pages", payload)]
        findings = audit_site(pages)
        for page in pages:
            findings.extend(validate_jsonld(page.html, page.final_url))
        return AuditResult(pages=pages, findings=findings)

    def _serp_adapter(self, provider: str):
        if provider == "dataforseo":
            login = self.config.dataforseo_login or os.getenv("DATAFORSEO_LOGIN")
            password = self.config.dataforseo_password or os.getenv("DATAFORSEO_PASSWORD")
            if not login or not password:
                return None
            return DataForSeoAdapter(login, password)
        key = self.config.semrush_api_key or os.getenv("SEMRUSH_API_KEY")
        return SemrushAdapter(key) if key else None

    async def execute(
        self,
        action: str,
        url: str | None = None,
        origin: str | None = None,
        data: str | None = None,
        keyword: str | None = None,
        location: str | None = None,
        language: str | None = None,
        provider: str | None = None,
        format: str | None = None,  # noqa: A002
        path: str | None = None,
        max_pages: int | None = None,
        max_depth: int | None = None,
        **_: Any,
    ) -> str:
        try:
            action = action.strip().lower()
            if action == "crawl":
                if not url:
                    raise ValueError("url is required")
                pages = self._crawler().crawl(
                    url, max_pages=max_pages or self.config.max_pages,
                    max_depth=self.config.max_depth if max_depth is None else max_depth,
                )
                return self._json({"pages": [page.model_dump(mode="json") for page in pages]})
            if action == "audit":
                if not data:
                    raise ValueError("data is required")
                return self._json(report_payload(self._audit(data)))
            if action == "schema":
                if not url or data is None:
                    raise ValueError("url and data HTML are required")
                return self._json({
                    "findings": [
                        item.model_dump(mode="json")
                        for item in validate_jsonld(data, url)
                    ]
                })
            performance = PerformanceClient(
                psi_api_key=self.config.psi_api_key,
                crux_api_key=self.config.crux_api_key,
            )
            if action == "psi":
                if not url:
                    raise ValueError("url is required")
                return self._json(performance.psi(url))
            if action == "crux":
                return self._json(performance.crux(url=url, origin=origin))
            if action == "serp_snapshot":
                if not keyword or not provider:
                    raise ValueError("keyword and provider are required")
                adapter = self._serp_adapter(provider)
                if adapter is None:
                    return self._json({
                        "status": "data_gap", "service": provider,
                        "reason": "Provider credentials are not configured",
                        "results": None,
                    })
                snapshot = adapter.snapshot(
                    keyword, location=location or "United States", language=language or "en"
                )
                store = SerpStore(self._path(path or self.config.serp_store_path))
                store.append(snapshot)
                return self._json(snapshot.model_dump(mode="json"))
            if action == "serp_history":
                store = SerpStore(self._path(path or self.config.serp_store_path))
                return self._json({
                    "snapshots": [
                        item.model_dump(mode="json")
                        for item in store.history(keyword=keyword)
                    ]
                })
            if action == "score":
                if not data:
                    raise ValueError("data is required")
                audit = self._audit(data)
                return self._json(health_score(audit.findings, page_count=len(audit.pages)))
            if action == "report":
                if not data or not path:
                    raise ValueError("data and path are required")
                audit = self._audit(data)
                output = write_report(audit, self._path(path), format or "html")
                return self._json({"path": str(output), "health": report_payload(audit)["health"]})
            if action == "pipeline":
                if not url:
                    raise ValueError("url is required")
                return self._pipeline(
                    url, path=path, format=format or "html",
                    max_pages=max_pages or self.config.max_pages,
                    max_depth=self.config.max_depth if max_depth is None else max_depth,
                )
            raise ValueError("unknown SEO action")
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Error: SEO action failed: {exc}")

    def _pipeline(
        self, url: str, *, path: str | None, format: str, max_pages: int, max_depth: int
    ) -> str:
        crawler = self._crawler()
        pages = crawler.crawl(url, max_pages=max_pages, max_depth=max_depth)
        findings = audit_site(pages)
        for page in pages:
            findings.extend(validate_jsonld(page.html, page.final_url))
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}/"
        assets = crawler.crawl(urljoin(origin, "robots.txt"), max_pages=1, max_depth=0)
        assets += crawler.crawl(urljoin(origin, "sitemap.xml"), max_pages=1, max_depth=0)
        for asset, code in zip(assets, ("robots_unavailable", "sitemap_unavailable"), strict=False):
            if asset.error or asset.status >= 400 or asset.status == 0:
                findings.append(Finding(
                    code=code, category="indexability", severity=Severity.HIGH,
                    title=code.replace("_", " ").title(),
                    message=f"{asset.url} was not available.",
                    url=asset.url, evidence=[Evidence(
                        kind="http_status", value=asset.status, source=asset.url
                    )],
                    recommendation="Publish a valid file and reference the sitemap in robots.txt.",
                    confidence=Confidence.HIGH,
                ))
        if len(assets) >= 1 and assets[0].status < 400 and "sitemap:" not in assets[0].text.lower():
            findings.append(Finding(
                code="robots_sitemap_missing", category="indexability",
                severity=Severity.LOW, title="Sitemap not declared in robots.txt",
                message="robots.txt contains no Sitemap directive.", url=assets[0].url,
                evidence=[Evidence(kind="robots_text", value=assets[0].text, source=assets[0].url)],
                recommendation="Add an absolute Sitemap directive to robots.txt.",
            ))
        if len(assets) >= 2 and assets[1].status < 400:
            sitemap_urls = parse_sitemap_urls(assets[1].html or assets[1].text)
            if not sitemap_urls:
                findings.append(Finding(
                    code="sitemap_empty", category="indexability", severity=Severity.HIGH,
                    title="Sitemap contains no URLs",
                    message="No valid loc entries were parsed from sitemap.xml.",
                    url=assets[1].url, evidence=[Evidence(
                        kind="sitemap_url_count", value=0, source=assets[1].url
                    )],
                    recommendation="Publish valid sitemap XML with absolute loc entries.",
                ))
        performance = PerformanceClient(
            psi_api_key=self.config.psi_api_key,
            crux_api_key=self.config.crux_api_key,
        )
        try:
            psi = performance.psi(url)
        except RuntimeError as exc:
            psi = {"status": "data_gap", "service": "psi_v5", "reason": str(exc), "metrics": None}
        try:
            crux = performance.crux(origin=origin.rstrip("/"))
        except RuntimeError as exc:
            crux = {"status": "data_gap", "service": "crux_v1", "reason": str(exc), "metrics": None}
        data_gaps = [
            {"service": str(item["service"]), "reason": str(item["reason"])}
            for item in (psi, crux)
            if item.get("status") == "data_gap"
        ]
        audit = AuditResult(
            pages=pages,
            findings=findings,
            data_gaps=data_gaps,
            metadata={"performance": {"psi": psi, "crux": crux}},
        )
        output = self._path(path or f"seo/seo-report.{format}")
        write_report(audit, output, format)
        return self._json({"path": str(output), **report_payload(audit)})

    @staticmethod
    def _json(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
