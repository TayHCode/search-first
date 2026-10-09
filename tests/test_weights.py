from __future__ import annotations

import math
import sys
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from search_first import server
from search_first.discovery import Discovery
from search_first.github import GitHubClient
from search_first.ranking import popularity_score, rank_repo
from search_first.weights import OverallWeights, RankWeights, parse_weights

NOW = datetime(2026, 10, 1, tzinfo=UTC)
IDEA = "a Python library for rate limiting outgoing HTTP requests"
MATCH = [{"terms": "python rate limiter", "query": "q", "rank": 2}]


def repo(name: str, description: str, stars: int, pushed_days: int = 10) -> dict[str, object]:
    return {
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "description": description,
        "topics": [],
        "language": "Python",
        "stargazers_count": stars,
        "pushed_at": (NOW - timedelta(days=pushed_days)).isoformat(),
        "license": {"spdx_id": "MIT"},
        "size": 500,
    }


def test_defaults_reproduce_the_old_hardcoded_scores():
    raw = repo("example/limiter", "Rate limiter for HTTP requests", 500)
    result = rank_repo(IDEA, raw, MATCH, NOW)
    coverage = result["idea_term_coverage"]
    old = (
        0.50 * coverage
        + 0.17 * 1.0
        + 0.08 * (1 / math.sqrt(2))
        + 0.04 * (1 / 3)
        + 0.05 * 1
        + 0.16 * popularity_score(500)
    )
    assert result["relevance_score"] == round(old, 3)
    bare = rank_repo(IDEA, raw, [], NOW)
    old_bare = (0.50 * coverage + 0.05 + 0.16 * popularity_score(500)) / 0.71
    assert bare["relevance_score"] == round(old_bare, 3)
    assert parse_weights(None, RankWeights) == RankWeights()
    assert parse_weights("", OverallWeights) == OverallWeights()


def test_partial_override_keeps_other_defaults_and_normalises():
    weights = parse_weights("stars=0.5", RankWeights)
    assert math.isclose(sum(vars(weights).values()), 1.0)
    assert math.isclose(weights.stars, 0.5 / 1.34)
    assert math.isclose(weights.idea_match, 0.50 / 1.34)
    overall = parse_weights(" fit = 2 , maintenance=2 ,stars=0", OverallWeights)
    assert (overall.fit, overall.maintenance, overall.stars) == (0.5, 0.5, 0)


@pytest.mark.parametrize(
    "text",
    [
        "bogus=1",
        "stars",
        "stars=-1",
        "stars=nan",
        "stars=inf",
        "=1",
        "stars=1=2",
        "stars=1e308,idea_match=1e308",
        "stars=1e7",
        "stars=1,stars=2",
        "stars=1_0",
        "stars=\uff11",
        "stars=0x1",
    ],
)
def test_invalid_rank_weights_are_rejected_with_allowed_names(text):
    with pytest.raises(ValueError, match="idea_match"):
        parse_weights(text, RankWeights)


def test_bad_number_message_names_the_weight():
    with pytest.raises(ValueError, match="stars must be a plain number.*allowed names"):
        parse_weights("stars=abc", RankWeights)


def test_negative_zero_is_stored_as_plain_zero():
    weights = parse_weights("stars=-0", RankWeights)
    assert str(weights.stars) == "0.0"


def test_all_zero_is_rejected():
    zeros = ",".join(f"{name}=0" for name in vars(RankWeights()))
    with pytest.raises(ValueError, match="all be zero"):
        parse_weights(zeros, RankWeights)
    with pytest.raises(ValueError, match="fit, maintenance, stars"):
        parse_weights("fit=0,maintenance=0,stars=0", OverallWeights)


def test_stars_zero_removes_star_influence():
    weights = parse_weights("stars=0", RankWeights)
    low = rank_repo(
        IDEA, repo("a/limiter", "Rate limiter for HTTP requests", 1), MATCH, NOW, weights
    )
    high = rank_repo(
        IDEA, repo("b/limiter", "Rate limiter for HTTP requests", 90000), MATCH, NOW, weights
    )
    assert low["relevance_score"] == high["relevance_score"]
    default_high = rank_repo(
        IDEA, repo("b/limiter", "Rate limiter for HTTP requests", 90000), MATCH, NOW
    )
    assert default_high["relevance_score"] > high["relevance_score"]


def test_stars_weight_can_flip_a_high_stars_low_fit_repo():
    fit = repo("a/fit", "Python rate limiting for outgoing HTTP requests", 20)
    famous = repo("b/famous", "HTTP requests toolkit", 200000)
    for stars_weight, famous_wins in (("stars=0", False), ("stars=20", True)):
        weights = parse_weights(stars_weight, RankWeights)
        ranked = {
            r["name"]: r["relevance_score"]
            for r in (rank_repo(IDEA, x, MATCH, NOW, weights) for x in (fit, famous))
        }
        assert (ranked["b/famous"] > ranked["a/fit"]) is famous_wins


def compare_discovery(**kwargs: RankWeights | OverallWeights) -> Discovery:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commits"):
            stamp = (NOW - timedelta(days=2)).isoformat()
            return httpx.Response(
                200,
                json=[{"sha": "a" * 40, "commit": {"committer": {"date": stamp}, "message": "x"}}],
            )
        if request.url.path.endswith(("/pulls", "/releases")):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/readme"):
            return httpx.Response(404)
        return httpx.Response(
            200, json=repo("example/limiter", "Rate limiter for HTTP requests", 50)
        )

    return Discovery(GitHubClient(transport=httpx.MockTransport(handler)), NOW, **kwargs)


async def test_compare_echoes_weights_and_uses_overall_blend():
    default = await compare_discovery().compare_candidates(IDEA, ["example/limiter"])
    assert default["weights"]["overall"] == {"fit": 0.6, "maintenance": 0.3, "stars": 0.1}
    assert default["weights"]["rank"]["idea_match"] == 0.5
    fit_only = await compare_discovery(
        overall_weights=parse_weights("fit=1,maintenance=0,stars=0", OverallWeights)
    ).compare_candidates(IDEA, ["example/limiter"])
    row = fit_only["rows"][0]
    assert fit_only["weights"]["overall"] == {"fit": 1.0, "maintenance": 0.0, "stars": 0.0}
    assert row["overall_score"] == row["relevance_score"]


async def test_search_echoes_rank_weights(recorded_transport, recording):
    weights = parse_weights("stars=0.5", RankWeights)
    discovery = Discovery(
        GitHubClient(transport=recorded_transport),
        datetime.fromisoformat(recording["recorded_at"]),
        rank_weights=weights,
    )
    result = await discovery.search_existing(
        "a Chrome extension or script that closes idle browser tabs automatically",
        ["chrome idle tab close", "auto close tabs", "inactive tabs extension", "tab auto closer"],
        limit=3,
    )
    assert result["weights"]["rank"]["stars"] == round(0.5 / 1.34, 4)
    assert "candidates" in result and "verdict" in result


class Started(Exception):
    pass


def run_main(monkeypatch, argv: list[str], env: dict[str, str]) -> Discovery:
    seen: list[Discovery] = []

    def fake_create_server(discovery: Discovery):
        seen.append(discovery)
        raise Started

    for name in (server.RANK_ENV, server.OVERALL_ENV):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["search-first", "--cache-ttl", "0", *argv])
    monkeypatch.setattr(server, "create_server", fake_create_server)
    with pytest.raises(Started):
        server.main()
    return seen[0]


def test_env_var_sets_weights(monkeypatch):
    discovery = run_main(
        monkeypatch,
        [],
        {server.RANK_ENV: "stars=0", server.OVERALL_ENV: "fit=1,maintenance=0,stars=0"},
    )
    assert discovery.rank_weights.stars == 0
    assert discovery.overall_weights.fit == 1


def test_flag_beats_env(monkeypatch):
    discovery = run_main(
        monkeypatch,
        ["--rank-weights", "stars=0", "--overall-weights", "stars=0"],
        {server.RANK_ENV: "stars=5", server.OVERALL_ENV: "stars=5"},
    )
    assert discovery.rank_weights.stars == 0
    assert discovery.overall_weights.stars == 0


def test_defaults_without_settings(monkeypatch):
    discovery = run_main(monkeypatch, [], {})
    assert discovery.rank_weights == RankWeights()
    assert discovery.overall_weights == OverallWeights()


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["--rank-weights", "bogus=1"], {}),
        ([], {server.OVERALL_ENV: "stars=-1"}),
        (["--overall-weights", "fit=0,maintenance=0,stars=0"], {}),
    ],
)
def test_invalid_settings_stop_startup(monkeypatch, capsys, argv, env):
    with pytest.raises(SystemExit) as exit_info:
        run_main(monkeypatch, argv, env)
    assert exit_info.value.code == 2
    assert "allowed names" in capsys.readouterr().err
