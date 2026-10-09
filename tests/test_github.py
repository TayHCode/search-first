from __future__ import annotations

import base64
import json
import os
import stat
import time

import httpx
import pytest

from search_first.github import DiskCache, GitHubClient, GitHubError, discover_token


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX disk permissions")
async def test_cache_ttl_and_no_credential_persistence(tmp_path):
    calls = 0
    token = "test-" + "credential-never-persist"

    def handler(request):
        nonlocal calls
        calls += 1
        assert request.headers["Authorization"] == f"Bearer {token}"
        return httpx.Response(200, json={"private": False, "description": token})

    cache = DiskCache(tmp_path / "cache")
    client = GitHubClient(token, cache, httpx.MockTransport(handler))
    assert (await client.get("/repos/example/repo"))["description"] == "[REDACTED]"
    await client.get("/repos/example/repo")
    assert calls == 1
    path = cache.path("/repos/example/repo", {})
    assert token not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = json.loads(path.read_text())
    data["created"] = time.time() - 301
    path.write_text(json.dumps(data))
    await client.get("/repos/example/repo")
    assert calls == 2


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX disk cache")
async def test_readme_is_decoded_before_secret_scrubbing_and_caching(tmp_path):
    token = "fixture-" + "only-sensitive-string"
    recognizable = "gh" + "p_" + "fixture" + str(12345)
    encoded = base64.b64encode(f"Install this: {token} {recognizable}".encode()).decode()
    cache = DiskCache(tmp_path)
    client = GitHubClient(
        token,
        cache,
        httpx.MockTransport(
            lambda _: httpx.Response(200, json={"encoding": "base64", "content": encoded})
        ),
    )
    data = await client.get("/repos/example/repo/readme")
    assert data["text"] == "Install this: [REDACTED] [REDACTED]"
    assert token not in cache.path("/repos/example/repo/readme", {}).read_text()
    assert encoded not in cache.path("/repos/example/repo/readme", {}).read_text()


async def test_rate_limit_stops_requests_and_returns_retry_time():
    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "retry-after": "60"})

    client = GitHubClient(transport=httpx.MockTransport(handler))
    for _ in range(2):
        with pytest.raises(GitHubError) as exc:
            await client.get("/search/repositories", {"q": "test is:public"})
        assert 1 <= exc.value.retry_after <= 60
    assert calls == 1


@pytest.mark.parametrize("status", [401, 403, 404, 422, 500, 301])
async def test_errors_do_not_echo_github_response_or_token(status, tmp_path):
    client = GitHubClient(
        "secret",
        DiskCache(tmp_path),
        httpx.MockTransport(lambda _: httpx.Response(status, json={"message": "secret"})),
    )
    with pytest.raises(GitHubError) as exc:
        await client.get("/repos/example/repo")
    assert "secret" not in str(exc.value)
    assert exc.value.status == status
    assert not list(tmp_path.glob("*.json"))


async def test_private_metadata_is_refused_before_cache(tmp_path):
    client = GitHubClient(
        cache=DiskCache(tmp_path),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"private": True, "description": "private content"})
        ),
    )
    with pytest.raises(GitHubError, match="Only public"):
        await client.get("/repos/example/repo")
    assert not list(tmp_path.glob("*.json"))


async def test_unauthenticated_mode_and_invalid_response(tmp_path):
    def handler(request):
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=b"invalid")

    client = GitHubClient(cache=DiskCache(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(GitHubError, match="invalid JSON"):
        await client.get("/repos/example/repo")


def test_token_lookup_is_optional(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: pytest.fail("gh was not requested"))
    assert discover_token() == "env-token"
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert discover_token() is None


def test_gh_fallback_requires_explicit_opt_in(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "fixture-token\n", ""),
    )
    assert discover_token(use_gh=True) == "fixture-token"


async def test_network_failure_is_clear():
    def handler(request):
        raise httpx.ConnectError("may contain credentials", request=request)

    with pytest.raises(GitHubError, match="network request failed"):
        await GitHubClient(transport=httpx.MockTransport(handler)).get("/repos/example/repo")


async def test_windows_cache_is_memory_only_and_respects_ttl(monkeypatch, tmp_path):
    monkeypatch.delattr(os, "getuid", raising=False)
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"private": False, "description": "public"})

    directory = tmp_path / "cache"
    cache = DiskCache(directory, ttl=5)
    client = GitHubClient(cache=cache, transport=httpx.MockTransport(handler))
    await client.get("/repos/example/repo")
    await client.get("/repos/example/repo")
    assert calls == 1
    clock[0] += 6
    await client.get("/repos/example/repo")
    assert calls == 2
    cache.prune()
    assert not directory.exists()
    assert not cache.is_safe()
    disabled = DiskCache(directory, ttl=0)
    disabled.put("/repos/example/repo", {}, {"public": True})
    assert disabled.get("/repos/example/repo", {}) is None


def test_environment_token_precedence(monkeypatch):
    alternate = "alternate-" + "credential"
    primary = "primary-" + "credential"
    monkeypatch.setenv("GH_TOKEN", alternate)
    assert discover_token() == alternate
    monkeypatch.setenv("GITHUB_TOKEN", primary)
    assert discover_token(use_gh=True) == primary
    monkeypatch.setenv("GITHUB_TOKEN", "  ")
    assert discover_token() == alternate


@pytest.mark.parametrize("authenticated,interval", [(False, 6.1), (True, 2.1)])
async def test_search_pacing_does_not_charge_cached_or_core_reads(
    monkeypatch, tmp_path, authenticated, interval
):
    import asyncio

    clock = [100.0]
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(GitHubClient, "SEARCH_INTERVAL_UNAUTH", 6.1)
    monkeypatch.setattr(GitHubClient, "SEARCH_INTERVAL_AUTH", 2.1)
    client = GitHubClient(
        "fixture-" + "credential" if authenticated else None,
        DiskCache(tmp_path),
        httpx.MockTransport(lambda _: httpx.Response(200, json={"items": []})),
    )
    await client.get("/search/repositories", {"q": "first"})
    await client.get("/search/repositories", {"q": "first"})  # cache hit
    await client.get("/repos/example/repo")  # core reads do not spend search quota
    await client.get("/search/repositories", {"q": "second"})
    assert waits == pytest.approx([interval])


async def test_successful_last_quota_read_prevents_next_network_request():
    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"items": []},
            headers={
                "x-ratelimit-resource": "search",
                "x-ratelimit-limit": "30",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": str(int(time.time()) + 30),
            },
        )

    client = GitHubClient(transport=httpx.MockTransport(handler))
    await client.get("/search/repositories")
    with pytest.raises(GitHubError, match="partial results") as exc:
        await client.get("/search/repositories")
    assert exc.value.retry_after >= 1
    assert client.rate_limits["search"]["limit"] == 30
    assert calls == 1


async def test_rate_limit_reset_and_secondary_backoff():
    for headers, content, expected in [
        ({"x-ratelimit-reset": str(int(time.time()) + 10)}, b"rate limit", 10),
        ({"retry-after": "7"}, b"", 7),
        ({}, b"secondary rate limit", 60),
        ({"retry-after": "invalid"}, b"rate limit", 60),
    ]:
        client = GitHubClient(
            transport=httpx.MockTransport(
                lambda _, h=headers, c=content: httpx.Response(403, headers=h, content=c)
            )
        )
        with pytest.raises(GitHubError) as exc:
            await client.get("/search/repositories")
        assert 1 <= exc.value.retry_after <= expected
        assert "partial results" in str(exc.value)


async def test_http_timeout_is_clear_and_does_not_echo_transport_details():
    def handler(request):
        raise httpx.ReadTimeout("fixture-sensitive-detail", request=request)

    client = GitHubClient(transport=httpx.MockTransport(handler))
    with pytest.raises(GitHubError, match="timed out") as exc:
        await client.get("/search/repositories")
    assert exc.value.status == 408
    assert "fixture-sensitive-detail" not in str(exc.value)


async def test_slow_body_is_bounded_by_total_call_budget():
    import asyncio

    async def handler(_):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"items": []})

    client = GitHubClient(transport=httpx.MockTransport(handler), call_timeout=0.02)
    started = time.monotonic()
    with client.call_budget(), pytest.raises(GitHubError, match="total tool-call timeout"):
        await client.get("/search/repositories")
    assert time.monotonic() - started < 0.5


async def test_cached_response_answers_offline_until_ttl(monkeypatch, tmp_path):
    clock = [1000.0]
    online = [True]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    def handler(request):
        if not online[0]:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"items": [], "total_count": 0})

    cache = DiskCache(tmp_path / "cache", ttl=5)
    client = GitHubClient(cache=cache, transport=httpx.MockTransport(handler))
    expected = await client.get("/search/repositories", {"q": "example"})
    online[0] = False
    assert await client.get("/search/repositories", {"q": "example"}) == expected
    clock[0] += 6
    with pytest.raises(GitHubError, match="network request failed"):
        await client.get("/search/repositories", {"q": "example"})
