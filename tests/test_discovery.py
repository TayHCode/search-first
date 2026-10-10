from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import pytest

from search_first.discovery import Discovery, parse_repo, query_plan
from search_first.github import GitHubClient
from search_first.ranking import rank_repo, verdict

IDEA = "a Chrome extension or script that closes idle browser tabs automatically"
QUERIES = ["chrome idle tab close", "auto close tabs", "inactive tabs extension", "tab auto closer"]


async def test_recorded_search_deduplicates_and_explains(recorded_transport, recording):
    discovery = Discovery(
        GitHubClient(transport=recorded_transport), datetime.fromisoformat(recording["recorded_at"])
    )
    result = await discovery.search_existing(IDEA, QUERIES, limit=5)
    assert not result["errors"]
    assert len(result["candidates"]) == 5
    assert len({r["name"] for r in result["candidates"]}) == 5
    assert any("tab" in r["name"].lower() for r in result["candidates"][:3])
    assert result["verdict"]["closest_match"]["name"] == result["candidates"][0]["name"]
    assert result["unique_candidates_evaluated"] > 5
    # With 4 agent variants the noisy derived/topic variants are skipped to save quota.
    assert [q["source"] for q in result["queries"]].count("agent") == 4
    assert not any(q["source"] == "derived-topic" for q in result["queries"])
    assert any(q["source"] == "derived-topic" for q in query_plan(IDEA, ["chrome idle tab"]))
    assert all("is:public" in q["query"] for q in result["queries"])
    assert all(r["why_it_matched"] and 0 <= r["relevance_score"] <= 1 for r in result["candidates"])


async def test_filters_survive_unexpected_api_items():
    base = {
        "full_name": "example/tab-close",
        "html_url": "https://github.com/example/tab-close",
        "description": "close idle tabs browser extension",
        "stargazers_count": 20,
        "language": "Python",
        "license": {"spdx_id": "MIT"},
    }
    items = [
        base,
        {**base, "full_name": "example/private", "private": True},
        {**base, "full_name": "example/archived", "archived": True},
        {**base, "full_name": "example/low", "stargazers_count": 1},
        {**base, "full_name": "example/other", "language": "Go"},
    ]

    def handler(request):
        assert 'language:"Python"' in request.url.params["q"]
        return httpx.Response(200, json={"total_count": len(items), "items": items})

    result = await Discovery(GitHubClient(transport=httpx.MockTransport(handler))).search_existing(
        IDEA, language="Python", min_stars=10
    )
    assert [r["name"] for r in result["candidates"]] == ["example/tab-close"]


async def test_partial_search_does_not_claim_nothing_exists():
    client = GitHubClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"retry-after": "60"}))
    )
    result = await Discovery(client).search_existing(IDEA, QUERIES)
    assert result["errors"][0]["retry_after_seconds"] == 60
    assert not result["verdict"]["search_complete"]
    assert result["verdict"]["recommendation"] != "build from scratch"


async def test_empty_complete_search_is_cautious():
    client = GitHubClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"total_count": 0, "items": []})
        )
    )
    result = await Discovery(client).search_existing(IDEA)
    assert result["verdict"]["closest_match"] is None
    assert result["verdict"]["plausible_candidates"] == 0
    assert "not proof" in result["verdict"]["reasoning"]


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "https://evil.example/a/b",
        "a/b/../c",
        "a/..",
        "https://github.com/a/b?" + "token" + "=" + "s" * 6,
        "https://user@" + "github.com/a/b",
    ],
)
def test_rejects_untrusted_repo_addresses(bad):
    with pytest.raises(ValueError):
        parse_repo(bad)


def test_derived_variants_depend_on_agent_variant_count():
    def sources(variants):
        return [q["source"] for q in query_plan(IDEA, variants)]

    assert sources(["a one", "b two", "c three", "d four"]).count("derived") == 1
    assert sources(["a one", "b two"]) == ["agent", "agent", "derived"]
    assert sources(["a one"]) == ["agent", "derived", "derived", "derived", "derived-topic"]
    assert sources(None).count("derived-topic") == 1


def test_repo_url_and_query_validation():
    assert parse_repo("https://github.com/example/repo.git/") == "example/repo"
    assert len(query_plan("browser tabs idle close", ["idle tabs", "idle tabs"])) <= 5
    with pytest.raises(ValueError):
        query_plan(IDEA, ["is:private"])
    with pytest.raises(ValueError):
        query_plan(" ", None)


async def test_recorded_inspection_counts_and_checklist(recorded_transport, recording):
    first = next(
        e["body"]["full_name"]
        for e in recording["entries"]
        if e["path"].startswith("/repos/") and e["path"].count("/") == 3
    )
    result = await Discovery(GitHubClient(transport=recorded_transport)).inspect_repo(first)
    assert result["readme_excerpt"]
    assert result["activity"]["last_commits"]
    assert all(q["answer"] is None for q in result["fit_for_your_idea"])
    assert result["activity"]["open_issues"] >= 0
    assert result["activity"]["open_prs"] >= 0
    assert result["size_kb"] >= 0
    assert not result["errors"]


async def test_missing_resources_remain_unknown_and_flags_are_heuristic():
    meta = {
        "full_name": "example/stub",
        "html_url": "https://github.com/example/stub",
        "private": False,
        "archived": True,
        "pushed_at": "2020-01-01T00:00:00Z",
        "size": 1,
        "open_issues_count": 4,
    }

    def handler(request):
        return (
            httpx.Response(200, json=meta)
            if request.url.path == "/repos/example/stub"
            else httpx.Response(404)
        )

    result = await Discovery(GitHubClient(transport=httpx.MockTransport(handler))).inspect_repo(
        "example/stub"
    )
    assert result["activity"]["open_prs"] is None
    assert result["activity"]["open_issues_to_prs_ratio"] is None
    assert len(result["errors"]) == 5
    assert any("abandoned" in flag for flag in result["red_flags"])
    assert any("tiny" in flag for flag in result["red_flags"])
    assert "archived" in result["red_flags"]


async def test_pr_pagination_and_release_intervals():
    def handler(request):
        path = request.url.path
        if path.endswith("/pulls"):
            return httpx.Response(
                200,
                json=[{"number": 1}],
                headers={
                    "link": "<https://api.github.com/repos/example/repo/pulls?state=open&"
                    'per_page=1&page=3>; rel="last"'
                },
            )
        if path.endswith("/releases"):
            return httpx.Response(
                200,
                json=[
                    {"published_at": "2026-06-11T00:00:00Z", "prerelease": False},
                    {"published_at": "2026-06-01T00:00:00Z", "prerelease": False},
                ],
            )
        if path == "/repos/example/repo":
            return httpx.Response(
                200,
                json={
                    "full_name": "example/repo",
                    "html_url": "https://github.com/example/repo",
                    "open_issues_count": 9,
                },
            )
        return httpx.Response(404)

    result = await Discovery(GitHubClient(transport=httpx.MockTransport(handler))).inspect_repo(
        "example/repo"
    )
    assert result["activity"]["open_prs"] == 3
    assert result["activity"]["open_issues"] == 6
    assert result["activity"]["open_issues_to_prs_ratio"] == 2
    assert result["activity"]["release_cadence"]["mean_interval_days"] == 10


async def test_comparison_reports_unavailable_candidates(recorded_transport, recording):
    names = [
        e["body"]["full_name"]
        for e in recording["entries"]
        if e["path"].startswith("/repos/") and e["path"].count("/") == 3
    ][:2]
    result = await Discovery(
        GitHubClient(token="test" + "-credential", transport=recorded_transport)
    ).compare_candidates(IDEA, [*names, names[0]])
    assert len(result["rows"]) == 2
    assert "license" in result["columns"]
    assert all("fit_for_your_idea" in row for row in result["rows"])
    assert all(row["license_name"] for row in result["rows"])


def test_specific_small_product_beats_popular_resource_list():
    base = {
        "full_name": "example/tab-close",
        "html_url": "https://github.com/example/tab-close",
        "description": "Chrome extension closes idle browser tabs",
        "license": {"spdx_id": "MIT"},
    }
    small = rank_repo(
        IDEA,
        {**base, "stargazers_count": 10},
        [{"terms": "idle tabs", "query": "idle tabs", "rank": 1}],
    )
    large = rank_repo(
        IDEA,
        {
            **base,
            "full_name": "example/awesome-browser",
            "description": "A curated collection of browser extension resources",
            "stargazers_count": 100000,
        },
        [{"terms": "browser", "query": "browser", "rank": 1}],
    )
    assert small["relevance_score"] > large["relevance_score"]
    # A tiny match beats a resource list but is not a credible fork recommendation.
    assert verdict([small], complete=True)["recommendation"] == "inconclusive"


def test_342_star_near_match_is_not_a_build_from_scratch_verdict(recording):
    idea = "a Python command line program to close idle browser tabs with saved schedules"
    # Reuse recorded candidate metadata; star count and sparse wording reproduce the reported edge.
    captured = next(
        item
        for entry in recording["entries"]
        if isinstance(entry["body"], dict)
        for item in entry["body"].get("items", [])
        if item.get("full_name") == "maverick-360/vaultab"
    )
    candidate = rank_repo(
        idea,
        {
            **captured,
            "full_name": "example/tab-sweep",
            "html_url": "https://github.com/example/tab-sweep",
            "description": "Close unused browser tabs",
            "language": "JavaScript",
            "stargazers_count": 342,
            "license": {"spdx_id": "MIT"},
        },
        [{"terms": "close unused tabs", "query": "close unused tabs", "rank": 1}],
    )
    assert not candidate["plausible"]
    result = verdict([candidate], complete=True)
    assert result["recommendation"] == "inconclusive"
    assert result["closest_match"]["name"] == "example/tab-sweep"
    assert result["adjacent_candidates"] == 1
    assert result["plausible_candidates"] == 0
    assert "Inspect" in result["reasoning"]


def test_stars_alone_do_not_make_an_unrelated_repo_an_adjacent_match():
    candidate = rank_repo(
        IDEA,
        {
            "full_name": "example/database",
            "html_url": "https://github.com/example/database",
            "description": "Distributed storage engine",
            "stargazers_count": 100000,
            "license": {"spdx_id": "MIT"},
        },
        [{"terms": "database", "query": "database", "rank": 1}],
    )
    result = verdict([candidate], complete=True)
    assert result["closest_match"] is None
    assert result["adjacent_candidates"] == 0


async def test_empty_commit_message_does_not_break_inspection():
    def handler(request):
        if request.url.path == "/repos/example/repo":
            return httpx.Response(
                200,
                json={"full_name": "example/repo", "html_url": "https://github.com/example/repo"},
            )
        if request.url.path.endswith("/commits"):
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "abc123",
                        "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}, "message": ""},
                    }
                ],
            )
        return httpx.Response(404)

    result = await Discovery(GitHubClient(transport=httpx.MockTransport(handler))).inspect_repo(
        "example/repo"
    )
    assert result["activity"]["last_commits"][0]["message"] == ""


async def test_comparison_preserves_repository_errors():
    client = GitHubClient(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    result = await Discovery(client).compare_candidates("idle browser tabs", ["example/missing"])
    assert result["rows"] == []
    assert result["errors"][0]["status"] == 404
    assert not result["verdict"]["search_complete"]


@pytest.mark.parametrize(
    ("pushed", "commit", "flagged", "days"),
    [
        ("2020-01-01T00:00:00Z", "2026-09-20T00:00:00Z", False, 11),
        ("2026-09-30T00:00:00Z", "2020-01-01T00:00:00Z", True, 0),
    ],
)
async def test_abandoned_flag_and_maintenance_signal_share_one_reference(
    pushed, commit, flagged, days
):
    meta = {
        "full_name": "example/clock",
        "html_url": "https://github.com/example/clock",
        "private": False,
        "pushed_at": pushed,
        "size": 500,
        "license": {"spdx_id": "MIT", "name": "MIT License"},
    }
    commits = [{"sha": "abc123", "commit": {"committer": {"date": commit}, "message": "x"}}]

    def handler(request):
        if request.url.path == "/repos/example/clock":
            return httpx.Response(200, json=meta)
        if request.url.path == "/repos/example/clock/commits":
            return httpx.Response(200, json=commits)
        return httpx.Response(404)

    result = await Discovery(
        GitHubClient(transport=httpx.MockTransport(handler)),
        now=datetime(2026, 10, 1, tzinfo=UTC),
    ).inspect_repo("example/clock")
    signal = next(s for s in result["maintenance"]["signals"] if "recent commits" in s)
    flag = next((f for f in result["red_flags"] if "possibly abandoned" in f), None)
    assert (flag is not None) is flagged
    signal_days = int(re.search(r"(\d+) days", signal).group(1))
    if flag:
        assert int(re.search(r"(\d+) days", flag).group(1)) == signal_days
    else:
        assert signal_days == days


async def test_total_search_timeout_preserves_earlier_candidates():
    import asyncio
    import time

    calls = 0

    async def handler(_):
        nonlocal calls
        calls += 1
        if calls > 1:
            await asyncio.sleep(1)
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "full_name": "example/tab-close",
                        "html_url": "https://github.com/example/tab-close",
                        "description": "Terminal tool closes idle Chrome tabs",
                        "license": {"spdx_id": "MIT"},
                    }
                ],
            },
        )

    client = GitHubClient(transport=httpx.MockTransport(handler), call_timeout=0.02)
    started = time.monotonic()
    result = await Discovery(client).search_existing(
        "a terminal tool to close idle chrome tabs", ["chrome idle tabs", "terminal tab close"]
    )
    assert time.monotonic() - started < 0.5
    assert len(result["candidates"]) == 1
    assert not result["verdict"]["search_complete"]
    assert result["errors"][0]["status"] == 408
    assert "Partial results" in result["message"]
    assert calls == 2


async def test_compare_shares_one_deadline_across_all_repos():
    import asyncio
    import time

    async def handler(request):
        await asyncio.sleep(0.015)
        if request.url.path.count("/") == 3:
            name = request.url.path.removeprefix("/repos/")
            return httpx.Response(
                200, json={"full_name": name, "html_url": f"https://github.com/{name}"}
            )
        return httpx.Response(404)

    client = GitHubClient(transport=httpx.MockTransport(handler), call_timeout=0.03)
    started = time.monotonic()
    result = await Discovery(client).compare_candidates(
        "idle browser tabs", ["example/one", "example/two", "example/three"]
    )
    assert time.monotonic() - started < 0.3
    assert not result["verdict"]["search_complete"]
    assert any(error["status"] == 408 for error in result["errors"])
