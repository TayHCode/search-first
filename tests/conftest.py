"""Hermetic tests: temporary user paths, no external network, recorded GitHub responses."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import pytest


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    # Fake HTTP tests spend no wall-clock time on pacing; dedicated tests restore it.
    from search_first.github import GitHubClient

    monkeypatch.setattr(GitHubClient, "SEARCH_INTERVAL_UNAUTH", 0)
    monkeypatch.setattr(GitHubClient, "SEARCH_INTERVAL_AUTH", 0)
    original = socket.socket.connect

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("Tests must not contact an external network")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)


@pytest.fixture
def recording():
    return json.loads((Path(__file__).parent / "fixtures/github.json").read_text(encoding="utf-8"))


@pytest.fixture
def recorded_transport(recording):
    def handler(request):
        for entry in recording["entries"]:
            if entry["path"].casefold() == request.url.path.casefold() and entry["params"] == dict(
                request.url.params
            ):
                return httpx.Response(entry["status"], json=entry["body"], headers=entry["headers"])
        raise AssertionError(f"No recorded fixture for {request.url}")

    return httpx.MockTransport(handler)
