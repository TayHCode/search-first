"""Round-two regressions with synthetic GitHub HTTP, never live data or credentials."""

from datetime import UTC, datetime

import httpx
import pytest

from search_first.discovery import Discovery
from search_first.github import PLACEHOLDER_NOTICE, GitHubClient, discover_token
from search_first.ranking import key_terms, rank_repo, relevance_key, verdict
from search_first.weights import RankWeights

NOW = datetime(2026, 10, 9, tzinfo=UTC)


def repo(name, description, stars=100, **extra):
    return {
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "description": description,
        "stargazers_count": stars,
        "pushed_at": "2026-10-01T00:00:00Z",
        "license": {"spdx_id": "MIT"},
        **extra,
    }


def fake_discovery(items, **kwargs):
    return Discovery(
        GitHubClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"items": items, "total_count": len(items)})
            )
        ),
        NOW,
        **kwargs,
    )


@pytest.mark.parametrize(
    "placeholder",
    [
        "${GITHUB_TOKEN}",
        "${" + "TOKEN" + ":-}",
        "$GITHUB_TOKEN",
        "%GITHUB_TOKEN%",
        "",
        "  ",
        "your-token-here",
        "YOUR_TOKEN_HERE",
        "<your-github-token>",
        "REPLACE_WITH_YOUR_TOKEN",
    ],
)
@pytest.mark.parametrize("source", ["direct", "environment"])
async def test_placeholders_never_send_authorization_and_warn_once(
    monkeypatch, placeholder, source
):
    if source == "environment":
        monkeypatch.setenv("GITHUB_TOKEN", placeholder)
        token = discover_token()
        assert token is None
    else:
        token = placeholder
    calls = []

    def handler(request):
        assert "authorization" not in request.headers
        calls.append(request.url)
        return httpx.Response(200, json={"items": [], "total_count": 0})

    client = GitHubClient(token, transport=httpx.MockTransport(handler))
    discovery = Discovery(client, NOW)
    for _ in range(2):
        result = await discovery.search_existing("async rate limiter in Python", ["rate limiter"])
        assert result["warnings"].count(PLACEHOLDER_NOTICE) == 1
        assert not result["errors"]
    assert len(calls) > 1
    assert client.token is None


async def test_placeholder_primary_allows_usable_gh_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "${GITHUB_TOKEN}")
    token = "fixture-" + "credential"
    monkeypatch.setenv("GH_TOKEN", token)
    assert discover_token() == token
    client = GitHubClient(discover_token())
    assert client.token == token
    assert not client.auth_warnings


async def test_invalid_real_token_still_reports_401():
    token = "invalid-" + "fixture-credential"

    def handler(request):
        assert request.headers["authorization"] == f"Bearer {token}"
        return httpx.Response(401)

    result = await Discovery(
        GitHubClient(token, transport=httpx.MockTransport(handler))
    ).search_existing("async rate limiter in Python", ["rate limiter"])
    assert result["errors"][0]["status"] == 401
    assert not result["warnings"]


@pytest.mark.parametrize("fail_after", [0, 1])
async def test_quota_response_counts_skipped_queries_results_and_retry(fail_after):
    calls = 0
    items = [repo(f"example/limiter-{i}", "Python async rate limiter") for i in range(3)]

    def handler(_):
        nonlocal calls
        calls += 1
        if calls > fail_after:
            return httpx.Response(429, headers={"retry-after": "23"})
        return httpx.Response(200, json={"items": items, "total_count": 3})

    client = GitHubClient(transport=httpx.MockTransport(handler))
    discovery = Discovery(client, NOW)
    result = await discovery.search_existing(
        "async rate limiter in Python", ["rate limiter", "async rate", "python limiting"], limit=1
    )
    count = len(result["queries"])
    assert result["coverage"]["queries_completed"] == fail_after
    assert result["coverage"]["queries_failed"] == 1
    assert result["coverage"]["queries_skipped"] == count - fail_after - 1
    assert result["coverage"]["results_skipped"] == (2 if fail_after else 0)
    assert result["coverage"]["unretrieved_results"] is None
    assert result["retry_after_seconds"] == 23
    assert "Retry in 23 seconds" in result["message"]
    assert "token raises" in result["message"] and "unknown" in result["message"]
    assert not result["verdict"]["search_complete"]
    assert all("is:public" in q["query"] for q in result["queries"])
    await discovery.search_existing("async rate limiter in Python", ["rate limiter"])
    assert calls == fail_after + 1  # cooldown prevents another HTTP request


@pytest.mark.parametrize(
    "idea",
    ["async rate limiter in Python", "an async rate limiter for outgoing HTTP requests in Python"],
)
async def test_651_star_library_beats_four_star_cli_and_explains_core_terms(idea):
    result = await fake_discovery(
        [
            repo(
                "example/reqstorm",
                "Async Python CLI for outgoing HTTP requests with rate limiting",
                4,
            ),
            repo(
                "example/throttled-py",
                "Python rate limiting library",
                651,
                language="Python",
                topics=["async", "rate-limiting", "token-bucket"],
            ),
        ]
    ).search_existing(idea, ["async rate limiting", "python http rate limiter"])
    library, cli = result["candidates"]
    assert library["name"] == "example/throttled-py"
    assert library["missing_key_terms"] == []
    assert library["core_terms"] == ["async", "limit", "rate"]
    assert library["plausible"] and not cli["plausible"]
    assert library["reuse_floor_met"] and not cli["reuse_floor_met"]
    assert result["verdict"]["closest_match"]["name"] == library["name"]
    assert result["verdict"]["recommendation"] in {"use as is", "fork and adapt"}
    assert "Core terms: 3/3" in library["why_it_matched"]
    assert "651 stars" in library["why_it_matched"]
    assert "http" not in key_terms(idea) and "python" not in key_terms(idea)


async def test_terminal_behavior_beats_proxmox_extension_robotics_and_cookie_manager():
    result = await fake_discovery(
        [
            repo("example/PROXMUX-Manager", "Proxmox administration Chrome extension", 5000),
            repo("example/robotics", "Terminal Chrome dashboard for robotics competition", 100000),
            repo("example/cookies", "Chrome extension close tabs and delete cookies", 2016),
            repo("example/cdpidletabs", "CLI to close idle Chrome tabs over DevTools protocol", 0),
        ]
    ).search_existing(
        "terminal tool to close idle chrome tabs", ["chrome idle tabs", "CLI close tabs"]
    )
    match = result["candidates"][0]
    assert match["name"] == "example/cdpidletabs"
    assert match["missing_key_terms"] == [] and match["missing_constraints"] == []
    assert match["core_match"] == "strong"
    assert not match["plausible"]  # exact new project is a lead, not an automatic fork
    assert "terminal" not in match["core_terms"]
    assert result["verdict"]["recommendation"] == "inconclusive"
    assert result["verdict"]["plausible_candidates"] == 0


async def test_general_homelab_generator_beats_unraid_specific_project():
    result = await fake_discovery(
        [
            repo(
                "example/unraid-config-guardian",
                "Self-hosted homelab documentation generator for Unraid",
                1000,
            ),
            repo("example/general-docs", "Self-hosted home lab docs generator", 200),
            repo(
                "example/private-kit",
                "Self-hosted homelab documentation generator",
                2000,
                private=True,
            ),
        ]
    ).search_existing(
        "self-hosted homelab documentation generator", ["homelab docs", "documentation generator"]
    )
    assert [c["name"] for c in result["candidates"]] == [
        "example/general-docs",
        "example/unraid-config-guardian",
    ]
    assert result["candidates"][0]["missing_key_terms"] == []
    assert "general homelab support beyond unraid" in result["candidates"][1]["missing_constraints"]
    assert not result["candidates"][1]["plausible"]
    assert result["verdict"]["closest_match"]["name"] == "example/general-docs"
    assert result["verdict"]["recommendation"] in {"use as is", "fork and adapt"}


@pytest.mark.parametrize(
    "stars,pushed", [(4, "2026-10-01T00:00:00Z"), (500, "2020-01-01T00:00:00Z"), (500, None)]
)
def test_fork_requires_adoption_and_known_recent_maintenance(stars, pushed):
    idea = "async rate limiter in Python"
    row = rank_repo(
        idea,
        repo("example/limiter", "Python async rate limiter", stars, pushed_at=pushed),
        [{"terms": "rate limiter", "query": "q", "rank": 1}],
        NOW,
    )
    result = verdict([row], complete=True, idea=idea)
    assert result["recommendation"] == "inconclusive"
    assert not row["reuse_floor_met"]


def test_mature_core_match_order_overrides_retrieval_bias_and_weights():
    idea = "async rate limiter in Python"
    weights = RankWeights(
        idea_match=0, query_match=0, github_rank=1, variant_agreement=0, freshness=0, stars=0
    )
    library = rank_repo(
        idea,
        repo("example/library", "Python async rate limiter", 651),
        [{"terms": "async rate", "query": "q", "rank": 2}],
        NOW,
        weights,
    )
    tiny = rank_repo(
        idea,
        repo("example/cli", "Python HTTP async rate limiter CLI", 4),
        [{"terms": "async rate", "query": "q", "rank": 1}],
        NOW,
        weights,
    )
    assert library["relevance_score"] < tiny["relevance_score"]
    ranked = sorted([tiny, library], key=relevance_key)
    assert ranked[0] is library
    assert verdict(ranked, complete=True)["recommendation"] == "fork and adapt"
