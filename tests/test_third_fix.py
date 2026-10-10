"""Round-three regressions: synthetic responses and official MCP error contracts."""

import asyncio
import itertools
import time
from datetime import UTC, datetime

import httpx
import pytest
from mcp import Client

from search_first.discovery import Discovery
from search_first.github import GitHubClient
from search_first.ranking import rank_repo, verdict
from search_first.server import create_server

NOW = datetime(2026, 10, 10, tzinfo=UTC)
IDEA = "terminal tool to close idle chrome tabs"
QUERIES = ["chrome idle tabs", "CLI close inactive tabs", "tab auto closer"]


def repo(name="example/limiter", description="Python async rate limiter", **extra):
    return {
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "description": description,
        "stargazers_count": 1000,
        "pushed_at": "2026-10-01T00:00:00Z",
        "license": {"spdx_id": "MIT"},
        **extra,
    }


def discovery(handler, **kwargs):
    return Discovery(GitHubClient(transport=httpx.MockTransport(handler), **kwargs), NOW)


@pytest.mark.parametrize("status", [429, 408, 500])
async def test_incomplete_search_with_excellent_match_never_recommends_reuse(status):
    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"items": [repo()], "total_count": 1})
        return httpx.Response(status, headers={"retry-after": "17"} if status == 429 else {})

    result = await discovery(handler).search_existing(
        "async rate limiter in Python", ["async rate limiter", "python limiting"]
    )
    assert result["candidates"][0]["plausible"]
    assert result["verdict"]["closest_match"]["name"] == "example/limiter"
    assert result["verdict"]["recommendation"] == "inconclusive"
    assert not result["verdict"]["search_complete"]
    assert "Retry" in result["verdict"]["reasoning"]
    assert "Partial" in result["message"] or "Rate limited" in result["message"]
    if status == 429:
        assert "17 seconds" in result["verdict"]["reasoning"]
    assert calls == 2


@pytest.mark.parametrize("kind", ["use", "fork", "borrow", "empty", "vague"])
def test_incomplete_verdict_overrides_every_decision(kind):
    idea = "async rate limiter in Python" if kind != "vague" else "make my life easier"
    row = rank_repo(
        idea,
        repo(license=None) if kind == "borrow" else repo(),
        [{"terms": "rate limiter", "query": "q", "rank": 1}],
        NOW,
    )
    if kind == "fork":
        row["relevance_score"] = 0.7
    rows = [] if kind == "empty" else [row]
    result = verdict(rows, complete=False, idea=idea)
    assert result["recommendation"] == "inconclusive"
    assert "Retry" in result["reasoning"]


@pytest.mark.parametrize("failure", ["network", "timeout", "rate", "auth", "server", "invalid"])
async def test_zero_completed_is_actual_mcp_error_and_never_partial(failure):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if failure == "network":
            raise httpx.ConnectError("private transport details", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private transport details", request=request)
        if failure == "invalid":
            return httpx.Response(200, content=b"invalid json")
        return httpx.Response(
            {"rate": 429, "auth": 401, "server": 503}[failure],
            headers={"retry-after": "11"} if failure == "rate" else {},
        )

    engine = discovery(handler)
    async with Client(create_server(engine)) as client:
        result = await client.call_tool("search_existing", {"idea": IDEA, "queries": QUERIES})
    assert result.is_error
    text = " ".join(block.text for block in result.content if hasattr(block, "text"))
    assert "No search completed: GitHub unreachable/rate limited; nothing to show" in text
    assert "partial" not in text.lower()
    assert "private transport details" not in text
    assert "retry" in text.lower() or "replace" in text.lower()
    if failure == "rate":
        assert "11 seconds" in text
    assert calls == 1


async def test_total_deadline_with_no_completed_query_is_error_and_configurable():
    async def handler(_):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"items": []})

    engine = discovery(handler, call_timeout=0.03)
    assert GitHubClient().call_timeout == 15
    async with Client(create_server(engine)) as client:
        start = time.monotonic()
        result = await client.call_tool("search_existing", {"idea": IDEA, "queries": QUERIES})
        assert time.monotonic() - start < 0.5
    assert result.is_error
    assert "total tool-call timeout (0.03s)" in result.content[0].text
    assert "partial" not in result.content[0].text.lower()


async def test_github_incomplete_results_blocks_reuse_but_bounded_truncation_does_not():
    for incomplete in (True, False):
        result = await discovery(
            lambda _, flag=incomplete: httpx.Response(
                200,
                json={"items": [repo()], "total_count": 80, "incomplete_results": flag},
            )
        ).search_existing("async rate limiter in Python", ["rate limiter"])
        assert result["coverage"]["truncated"]
        assert result["verdict"]["search_complete"] is not incomplete
        assert result["verdict"]["recommendation"] == (
            "inconclusive" if incomplete else "use as is"
        )


async def test_exact_six_way_terminal_tie_is_explained_even_with_limit_one():
    items = [
        repo(f"example/extension-{i}", "Close idle Chrome tabs", stargazers_count=100 + i)
        for i in range(6)
    ]
    # Newest maintained item beats the older, more starred peers, all capped at .54.
    items[2]["pushed_at"] = "2026-10-09T00:00:00Z"
    for i in (0, 1, 3, 4, 5):
        items[i]["pushed_at"] = "2026-08-01T00:00:00Z"
    for order in (items, list(reversed(items))):
        result = await discovery(
            lambda _, rows=order: httpx.Response(200, json={"items": rows, "total_count": 6})
        ).search_existing(IDEA, QUERIES, limit=1)
        winner = result["candidates"][0]
        assert winner["relevance_score"] == 0.54
        assert winner["core_terms"] == ["browser", "close", "idle", "tab"]
        assert winner["missing_constraints"] == ["terminal"]
        assert winner["name"] == "example/extension-2"
        assert "tied with 5 others" in winner["tie_break"]
        assert "higher maintenance score" in winner["why_it_matched"]
        assert "None of these is a terminal tool" in result["verdict"]["reasoning"]
        assert result["verdict"]["recommendation"] == "inconclusive"


async def test_best_group_missing_terminal_is_plain_even_when_lower_rows_are_clis():
    items = [
        repo("example/extension", "Close idle Chrome tabs"),
        repo("example/cli", "CLI to list Chrome tabs"),
    ]
    result = await discovery(
        lambda _: httpx.Response(200, json={"items": items, "total_count": 2})
    ).search_existing(IDEA, QUERIES)
    assert result["candidates"][0]["name"] == "example/extension"
    assert result["candidates"][1]["missing_constraints"] == []
    assert "None of these is a terminal tool" in result["verdict"]["reasoning"]
    assert "top relevance group" in result["verdict"]["reasoning"]


@pytest.mark.parametrize("criterion", ["stars", "coverage", "recency", "name"])
async def test_equal_score_tiebreak_criteria_are_order_independent_and_explained(criterion):
    left = repo("example/a", "Close idle Chrome tabs", stargazers_count=100)
    right = repo("example/b", "Close idle Chrome tabs", stargazers_count=100)
    idea = IDEA
    expected = "example/a"
    label = "alphabetical repository name"
    if criterion == "stars":
        right["stargazers_count"] = 200
        expected, label = "example/b", "more stars"
    elif criterion == "coverage":
        idea += " over HTTP"
        right["topics"] = ["http"]
        expected, label = "example/b", "greater term coverage in name/description/topics"
    elif criterion == "recency":
        right["pushed_at"] = "2026-10-01T12:00:00Z"
        expected, label = "example/b", "more recent push"
    for items in itertools.permutations([left, right]):
        result = await discovery(
            lambda _, rows=items: httpx.Response(200, json={"items": rows, "total_count": 2})
        ).search_existing(idea, QUERIES)
        assert result["candidates"][0]["name"] == expected
        assert "tied with 1 others" in result["candidates"][0]["tie_break"]
        assert label in result["candidates"][0]["tie_break"]


@pytest.mark.parametrize("evidence", ["CLI", "command-line", "command line", "topic"])
async def test_cli_evidence_beats_extensions_without_special_owner_or_language_rules(evidence):
    extra = {"topics": ["cli"]} if evidence == "topic" else {}
    cli = repo(
        "unrelated-owner/tab-sweeper",
        ("" if evidence == "topic" else evidence + " ") + "closes idle Chrome tabs",
        stargazers_count=0,
        language="Python",
        **extra,
    )
    extension = repo("example/extension", "Close idle Chrome tabs", language="Python")
    result = await discovery(
        lambda _: httpx.Response(200, json={"items": [extension, cli], "total_count": 2})
    ).search_existing(IDEA, QUERIES)
    assert result["candidates"][0]["name"] == cli["full_name"]
    assert not result["candidates"][0]["missing_constraints"]
    assert result["candidates"][1]["missing_constraints"] == ["terminal"]
    assert result["verdict"]["recommendation"] == "inconclusive"  # zero stars still a lead


async def test_vague_idea_omits_unrelated_popular_rows_but_preserves_counts():
    result = await discovery(
        lambda _: httpx.Response(200, json={"items": [repo()], "total_count": 1})
    ).search_existing("something to make my life easier", ["productivity helper"])
    assert result["verdict"]["recommendation"] == "inconclusive"
    assert result["verdict"]["closest_match"] is None
    assert result["candidates"] == []
    assert result["unique_candidates_evaluated"] == 1
    assert result["coverage"]["results_skipped"] == 1
    assert any("Candidates omitted" in warning for warning in result["warnings"])
