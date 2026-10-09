"""Synthetic reproductions of weak-model stranger findings; no live GitHub data."""

from datetime import UTC, datetime

import httpx
import pytest

from search_first.discovery import Discovery
from search_first.github import GitHubClient
from search_first.ranking import rank_repo, verdict
from search_first.weights import RankWeights, parse_weights

NOW = datetime(2026, 10, 9, tzinfo=UTC)


def candidate(name, description, stars=1000, license_id="MIT", **extra):
    return {
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "description": description,
        "stargazers_count": stars,
        "pushed_at": "2026-10-01T00:00:00Z",
        "license": {"spdx_id": license_id} if license_id else None,
        **extra,
    }


def discovery(items, **kwargs):
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
    "idea", ["something to make my life easier", "something to make my computer faster"]
)
async def test_vague_idea_never_recommends_a_cdn(idea):
    result = await discovery(
        [candidate("example/cdn", "Make life easier with a faster CDN for your computer", 10743)]
    ).search_existing(idea, ["make life easier", "productivity helper"])
    assert result["verdict"]["recommendation"] == "inconclusive"
    assert result["verdict"]["closest_match"] is None
    assert result["verdict"]["plausible_candidates"] == 0
    assert "too vague" in result["verdict"]["reasoning"]


async def test_terminal_constraint_blocks_extension_and_cookie_manager():
    result = await discovery(
        [
            candidate("example/tab-extension", "Chrome extension closes idle tabs"),
            candidate(
                "example/cookie-manager",
                "Chrome browser extension automatically deletes cookies",
                2016,
            ),
        ]
    ).search_existing(
        "a terminal tool to close idle chrome tabs",
        ["close idle chrome tabs", "chrome tab suspender cli"],
    )
    assert result["verdict"]["recommendation"] == "inconclusive"
    assert "terminal" in result["verdict"]["reasoning"]
    assert all(not row["plausible"] for row in result["candidates"])
    cookie = next(row for row in result["candidates"] if "cookie" in row["name"])
    assert "close" in cookie["missing_key_terms"]
    assert cookie["relevance_score"] < 0.55


@pytest.mark.parametrize("terminal", ["terminal", "CLI", "command line", "command-line", "TUI"])
async def test_terminal_synonyms_match_and_beat_extensions(terminal):
    result = await discovery(
        [
            candidate("example/cookie-manager", "Browser extension deletes Chrome cookies", 100000),
            candidate("example/tab-extension", "Chrome extension closes idle tabs", 10000),
            candidate("example/tab-cli", f"{terminal} closes idle Chrome tabs", 50),
        ]
    ).search_existing(
        "a terminal tool to close idle chrome tabs",
        [
            "terminal chrome close idle tabs",
            "CLI close inactive browser tabs",
            "Chrome tab auto closer",
        ],
    )
    assert result["candidates"][0]["name"] == "example/tab-cli"
    assert result["verdict"]["closest_match"]["name"] == result["candidates"][0]["name"]
    assert result["verdict"]["recommendation"] == "use as is"
    assert result["candidates"][0]["missing_constraints"] == []


async def test_luna_relevance_verdict_does_not_skip_unlicensed_first_row():
    result = await discovery(
        [
            candidate("example/hello-world", "Chrome terminal closes idle tabs", 100000, None),
            candidate("example/vaultab", "Terminal closes idle Chrome tabs", 5),
            candidate("example/cookie-manager", "Chrome cookie management browser extension", 2016),
        ]
    ).search_existing(
        "terminal tool to close idle chrome tabs",
        [
            "terminal chrome close idle tabs",
            "CLI close inactive browser tabs",
            "Chrome tab auto closer",
        ],
    )
    assert result["candidates"][0]["name"] == "example/hello-world"
    assert result["verdict"]["closest_match"]["name"] == result["candidates"][0]["name"]
    assert result["verdict"]["recommendation"] == "borrow parts"
    assert result["candidates"][-1]["name"] == "example/cookie-manager"


async def test_async_python_alone_cannot_score_as_rate_limiter():
    result = await discovery(
        [
            candidate(
                "example/username-scanner",
                "Async Python Instagram username scanner",
                100000,
                language="Python",
            ),
            candidate(
                "example/market-data", "Async Python market data client", 100000, language="Python"
            ),
            candidate("example/limiter", "Async rate limiter for Python", 651, language="Python"),
        ]
    ).search_existing(
        "async rate limiter for python",
        ["python asyncio rate limiter", "async token bucket python"],
    )
    assert result["candidates"][0]["name"] == "example/limiter"
    for row in result["candidates"][1:]:
        assert row["relevance_score"] < 0.8
        assert "rate" in row["missing_key_terms"] and "limit" in row["missing_key_terms"]
    assert result["verdict"]["recommendation"] == "use as is"


@pytest.mark.parametrize(
    "idea,description,missing",
    [
        (
            "self-hosted homelab documentation generator",
            "Homelab documentation generator",
            "self-hosted",
        ),
        ("Windows terminal tab manager", "Linux terminal tab manager", "windows"),
        ("Python async rate limiter", "Async rate limiter in JavaScript", "python"),
        ("Chrome idle tab closer", "Firefox idle tab closer", "chrome"),
    ],
)
def test_hard_constraints_prevent_confident_reuse(idea, description, missing):
    row = rank_repo(
        idea,
        candidate("example/project", description),
        [{"terms": description, "query": "q", "rank": 1}] * 3,
        NOW,
    )
    assert missing in row["missing_constraints"]
    assert row["relevance_score"] < 0.55
    assert verdict([row], complete=True, idea=idea)["recommendation"] not in {
        "use as is",
        "fork and adapt",
    }


async def test_extreme_star_weight_is_kept_but_warned():
    weights = parse_weights("stars=1000000", RankWeights)
    result = await discovery(
        [
            candidate("example/robotics", "A robotics project", 100000),
            candidate("example/tab-cli", "Terminal closes idle Chrome tabs", 3),
        ],
        rank_weights=weights,
    ).search_existing(
        "terminal close idle chrome tabs", ["close idle chrome tabs", "chrome tab suspender cli"]
    )
    assert result["weights"]["rank"]["stars"] == 1
    assert "stars dominates" in result["warnings"][0]
    assert "unrelated" in result["warnings"][0]
    assert result["verdict"]["recommendation"] == "inconclusive"


@pytest.mark.parametrize(
    "description",
    ["Self-hosted homelab documentation generator", "Self hosted homelab documentation generator"],
)
def test_self_hosted_constraint_accepts_both_spellings(description):
    row = rank_repo(
        "self-hosted homelab documentation generator",
        candidate("example/docs", description),
        [{"terms": "homelab documentation generator", "query": "q", "rank": 1}] * 3,
        NOW,
    )
    assert row["missing_key_terms"] == []
    assert row["missing_constraints"] == []
    assert verdict([row], complete=True)["recommendation"] == "use as is"
