"""Bounded tool output, economical search plans and distinct transport failures."""

import asyncio
import json
import time

import httpx
import pytest
from mcp import Client

from search_first.discovery import Discovery
from search_first.github import GitHubClient, GitHubError
from search_first.server import create_server

IDEA = "terminal tool to close idle chrome tabs"
QUERIES = ["chrome idle tabs", "CLI close inactive tabs", "tab auto closer"]


def giant_items():
    return [
        {
            "full_name": f"example/tabs-{index}",
            "html_url": f"https://github.com/example/tabs-{index}",
            "description": "CLI closes idle Chrome tabs " + "x" * 107_498,
            "topics": ["x" * 1000] * 11,
            "stargazers_count": 100,
            "pushed_at": "2026-10-01T00:00:00Z",
            "license": {"spdx_id": "MIT"},
        }
        for index in range(12)
    ]


async def test_giant_description_default_mcp_output_is_compact_and_detail_is_bounded():
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"items": giant_items(), "total_count": 12})
    )
    engine = Discovery(GitHubClient(transport=transport))
    async with Client(create_server(engine)) as client:
        result = await client.call_tool("search_existing", {"idea": IDEA, "queries": QUERIES})
        text = "".join(block.text for block in result.content if hasattr(block, "text"))
        assert len(text) < 8000
        data = result.structured_content
        assert len(data["candidates"]) == 8
        assert "description" not in data["candidates"][0]
        assert data["candidates"][0]["missing_constraints"] == []
        detailed = await client.call_tool(
            "search_existing", {"idea": IDEA, "queries": QUERIES, "detail": True}
        )
        row = detailed.structured_content["candidates"][0]
        assert len(row["description"]) == 300
        assert len(row["topics"]) == 10
        assert all(len(topic) <= 100 for topic in row["topics"])
        assert len(json.dumps(detailed.structured_content)) < 40_000


@pytest.mark.parametrize("authenticated,budget", [(False, 2), (True, 6)])
async def test_production_search_budget_and_completion_counts(monkeypatch, authenticated, budget):
    monkeypatch.setattr(GitHubClient, "SEARCH_VARIANTS_ANONYMOUS", 2)
    monkeypatch.setattr(GitHubClient, "SEARCH_VARIANTS_SIGNED_IN", 6)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"items": [], "total_count": 0})

    token = "test" + "-credential" if authenticated else None
    engine = Discovery(GitHubClient(token, transport=httpx.MockTransport(handler)))
    result = await engine.search_existing(IDEA, QUERIES + ["browser cli", "idle tab cleanup"])
    assert len(calls) == budget
    assert result["coverage"]["queries_completed"] == budget
    assert result["coverage"]["queries_skipped"] == len(result["queries"]) - budget
    assert result["verdict"]["search_complete"]
    assert result["coverage"]["bounded_plan_complete"]
    assert bool(result["notes"]) is not authenticated
    if not authenticated:
        assert "set GITHUB_TOKEN for complete searches" in result["notes"][0]
        assert engine.github.call_timeout == 30
    else:
        assert engine.github.call_timeout == 15


async def test_known_quota_can_reduce_selected_plan(monkeypatch):
    monkeypatch.setattr(GitHubClient, "SEARCH_VARIANTS_ANONYMOUS", 2)
    github = GitHubClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))
    github.rate_limits["search"] = {"remaining": 1, "reset": int(time.time()) + 60}
    result = await Discovery(github).search_existing(IDEA, QUERIES)
    assert result["coverage"]["query_budget"] == 1
    assert result["coverage"]["queries_completed"] == 1
    assert result["verdict"]["search_complete"]


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError])
async def test_dead_network_has_specific_mcp_error_and_stops_plan(failure):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise failure("untrusted proxy details", request=request)

    async with Client(
        create_server(Discovery(GitHubClient(transport=httpx.MockTransport(handler))))
    ) as client:
        start = time.monotonic()
        result = await client.call_tool("search_existing", {"idea": IDEA, "queries": QUERIES})
        assert time.monotonic() - start < 0.5
    assert result.is_error
    text = result.content[0].text
    assert "No search completed: GitHub unreachable from this machine: check network/proxy" in text
    assert "rate limit" not in text and "untrusted proxy details" not in text
    assert calls == 1


async def test_connect_timeout_is_short_but_read_timeout_is_slow_server():
    def handler(request):
        assert request.extensions["timeout"]["connect"] == 2.5
        assert request.extensions["timeout"]["read"] == 20
        raise httpx.ReadTimeout("private details", request=request)

    client = GitHubClient(transport=httpx.MockTransport(handler))
    with pytest.raises(GitHubError, match="request timed out"):
        await client.get("/search/repositories")


@pytest.mark.parametrize("status", [403, 429])
async def test_rate_limited_error_has_reset_interval_and_token_hint(status):
    github = GitHubClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status,
                headers={
                    "x-ratelimit-remaining": "0",
                    "x-ratelimit-reset": str(int(time.time()) + 17),
                },
            )
        )
    )
    async with Client(create_server(Discovery(github))) as client:
        result = await client.call_tool("search_existing", {"idea": IDEA, "queries": QUERIES})
    assert result.is_error
    assert "rate limited; retry in 17 s" in result.content[0].text
    assert "a token raises" in result.content[0].text
    assert "unreachable" not in result.content[0].text


async def test_core_budget_shared_by_concurrent_reads_and_nested_inspections():
    calls = 0

    async def handler(_):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return httpx.Response(200, json={})

    client = GitHubClient(transport=httpx.MockTransport(handler))
    with client.call_budget():
        results = await asyncio.gather(
            *(client.get("/repos/example/test") for _ in range(9)), return_exceptions=True
        )
        assert sum(isinstance(result, GitHubError) for result in results) == 3
        with client.call_budget(), pytest.raises(GitHubError, match="budget exhausted"):
            await client.get("/repos/example/test")
    assert calls == 6
    with client.call_budget():
        await client.get("/repos/example/test")
    assert calls == 7


async def test_inspection_readme_hints_and_all_text_are_bounded():
    def handler(request):
        if request.url.path == "/repos/example/test":
            return httpx.Response(200, json=giant_items()[0])
        if request.url.path.endswith("readme"):
            return httpx.Response(200, json={"text": "pip install " + "x" * 10_000})
        if request.url.path.endswith("pulls") and request.url.params.get("state") == "open":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[])

    result = await Discovery(GitHubClient(transport=httpx.MockTransport(handler))).inspect_repo(
        "example/test"
    )
    assert len(result["readme_excerpt"]) == 500
    assert result["readme_truncated"]
    assert all(len(hint["excerpt"]) <= 500 for hint in result["install_run_hints"])
    assert result["notes"]
