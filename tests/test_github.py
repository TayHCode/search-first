from __future__ import annotations

import base64
import json
import stat
import time

import httpx
import pytest

from search_first.github import DiskCache, GitHubClient, GitHubError, discover_token


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


async def test_readme_is_decoded_before_secret_scrubbing_and_caching(tmp_path):
    token = "fixture-" + "only-sensitive-string"
    encoded = base64.b64encode(f"Install this: {token} ghp_fixture12345".encode()).decode()
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
