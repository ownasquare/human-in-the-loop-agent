"""Bounded demo and Tavily search adapters."""

from __future__ import annotations

import httpx

from relay.config import Settings
from relay.models import Action, WebSearchAction
from relay.tools.base import AdapterResult, ToolExecutionError


class DemoSearchAdapter:
    tool_name = "web_search"

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        if not isinstance(action, WebSearchAction):
            raise ToolExecutionError("Search adapter received the wrong action type.")
        fixtures = [
            {
                "title": "Acme renewal planning guide",
                "url": "https://demo.invalid/acme-renewal",
                "snippet": "Renewal reviews should confirm stakeholders, timing, and next actions.",
            },
            {
                "title": "Vendor review checklist",
                "url": "https://demo.invalid/vendor-review",
                "snippet": "Document calendar, outreach, CRM, and purchasing approvals separately.",
            },
        ]
        return AdapterResult(
            provider="demo_search",
            provider_id=f"fixture:{idempotency_key[-12:]}",
            data={"query": action.query, "results": fixtures[: action.max_results]},
        )


class TavilySearchAdapter:
    tool_name = "web_search"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        if not isinstance(action, WebSearchAction):
            raise ToolExecutionError("Search adapter received the wrong action type.")
        if self.settings.tavily_api_key is None:
            raise ToolExecutionError("Tavily is not configured.", code="connector_not_configured")
        payload = {
            "api_key": self.settings.tavily_api_key.get_secret_value(),
            "query": action.query,
            "max_results": min(action.max_results, self.settings.max_search_results),
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post("https://api.tavily.com/search", json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolExecutionError("Live search failed safely.", code="search_failed") from exc
        results = [
            {
                "title": str(item.get("title", ""))[:300],
                "url": str(item.get("url", ""))[:2000],
                "snippet": str(item.get("content", ""))[:2000],
            }
            for item in body.get("results", [])[: action.max_results]
            if isinstance(item, dict)
        ]
        return AdapterResult(
            provider="tavily",
            provider_id=None,
            data={"query": action.query, "results": results},
        )
