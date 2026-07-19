from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from relay.config import Settings
from relay.models import WebSearchAction
from relay.tools.base import ToolExecutionError
from relay.tools.search import TavilySearchAdapter


def _settings(tmp_path) -> Settings:
    return Settings(
        mode="live",
        data_dir=tmp_path,
        tavily_api_key=SecretStr("test-tavily-key"),
        max_search_results=2,
    )


@respx.mock
async def test_tavily_bounds_and_sanitizes_results(tmp_path) -> None:
    route = respx.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "A" * 400,
                        "url": "https://example.com/" + "x" * 2100,
                        "content": "B" * 2200,
                    },
                    {"title": "Second", "url": "https://example.net", "content": "Summary"},
                    {"title": "Third", "url": "https://example.org", "content": "Ignored"},
                ]
            },
        )
    )

    result = await TavilySearchAdapter(_settings(tmp_path)).execute(
        WebSearchAction(query="public Relay project", max_results=3),
        idempotency_key="unused-read-key",
    )

    assert route.called
    request_body = route.calls[0].request.content.decode()
    assert '"max_results":2' in request_body
    assert '"include_answer":false' in request_body
    assert '"include_raw_content":false' in request_body
    assert len(result.data["results"]) == 2
    assert len(result.data["results"][0]["title"]) == 300
    assert len(result.data["results"][0]["url"]) == 2000
    assert len(result.data["results"][0]["snippet"]) == 2000


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json=[{"results": []}]),
        httpx.Response(200, json={"results": "not-a-list"}),
    ],
)
async def test_tavily_exposes_only_safe_failure(tmp_path, response: httpx.Response) -> None:
    respx.post("https://api.tavily.com/search").mock(return_value=response)

    with pytest.raises(ToolExecutionError, match="failed safely") as caught:
        await TavilySearchAdapter(_settings(tmp_path)).execute(
            WebSearchAction(query="public Relay project", max_results=1),
            idempotency_key="unused-read-key",
        )

    assert caught.value.code == "search_failed"


@respx.mock
async def test_tavily_ignores_non_dict_results(tmp_path) -> None:
    respx.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    "untrusted string",
                    {"title": "Valid", "url": "", "content": ""},
                    None,
                ]
            },
        )
    )

    result = await TavilySearchAdapter(_settings(tmp_path)).execute(
        WebSearchAction(query="public Relay project", max_results=3),
        idempotency_key="unused-read-key",
    )

    assert result.data["results"] == [{"title": "Valid", "url": "", "snippet": ""}]


@respx.mock
async def test_tavily_requires_configuration_before_request(tmp_path) -> None:
    with pytest.raises(ToolExecutionError, match="not configured") as caught:
        await TavilySearchAdapter(Settings(mode="live", data_dir=tmp_path)).execute(
            WebSearchAction(query="public Relay project", max_results=1),
            idempotency_key="unused-read-key",
        )

    assert caught.value.code == "connector_not_configured"
    assert respx.calls.call_count == 0
