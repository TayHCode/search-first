"""Explicit live GitHub smoke run; never part of CI. Records public, projected fixtures."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from search_first.discovery import Discovery
from search_first.github import GitHubClient, discover_token, sanitize

CASES = [
    (
        "idle-tabs",
        "a Chrome extension or script that closes idle browser tabs automatically",
        [
            "chrome idle tab close",
            "auto close tabs",
            "inactive tabs extension",
            "tab auto closer",
        ],
    ),
    (
        "rate-limiter",
        "a Python library for rate limiting outgoing HTTP requests",
        [
            "python rate limiter",
            "http client rate limit",
            "throttle requests python",
            "token bucket rate limit",
        ],
    ),
]
REPO_FIELDS = [
    "id",
    "full_name",
    "html_url",
    "description",
    "stargazers_count",
    "forks_count",
    "pushed_at",
    "archived",
    "private",
    "license",
    "language",
    "topics",
    "size",
    "open_issues_count",
]


def project(path: str, data: Any) -> Any:
    if path == "/search/repositories":
        return {
            "total_count": data["total_count"],
            "incomplete_results": data["incomplete_results"],
            "items": [
                {k: r.get(k) for k in REPO_FIELDS} for r in data["items"] if not r.get("private")
            ],
        }
    if path.endswith("/readme"):
        text = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
        # Keep fixtures small: third-party README text is recorded only as a short excerpt.
        return {"text": sanitize(text)[:2000], "size": data.get("size")}
    if path.endswith("/commits"):
        return [
            {
                "sha": c["sha"],
                "commit": {
                    "committer": {"date": c["commit"]["committer"]["date"]},
                    "message": (c["commit"]["message"].splitlines() or [""])[0],
                },
            }
            for c in data
        ]
    if path.endswith("/pulls"):
        return [
            {k: c.get(k) for k in ("number", "created_at", "closed_at", "merged_at")} for c in data
        ]
    if path.endswith("/releases"):
        return [{k: r.get(k) for k in ("published_at", "prerelease")} for r in data]
    return {k: data.get(k) for k in REPO_FIELDS}


class Recorder(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.entries: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        await response.aread()
        if response.status_code == 200:
            self.entries.append(
                {
                    "path": request.url.path,
                    "params": dict(request.url.params),
                    "status": 200,
                    "headers": {"link": response.headers.get("link", "")},
                    "body": sanitize(project(request.url.path, response.json())),
                }
            )
        return response

    async def aclose(self) -> None:
        # Each GitHub request uses a short-lived client; keep the recorder available.
        pass


async def run(args: argparse.Namespace) -> None:
    fixture_path = Path("tests/fixtures/github.json")
    previous = json.loads(fixture_path.read_text()) if args.replay else None
    now = datetime.fromisoformat(previous["recorded_at"]) if previous else datetime.now(UTC)
    recorder = Recorder()

    async def replay(request: httpx.Request) -> httpx.Response:
        for entry in previous["entries"]:
            if entry["path"].casefold() == request.url.path.casefold() and entry["params"] == dict(
                request.url.params
            ):
                return httpx.Response(entry["status"], json=entry["body"], headers=entry["headers"])
        if args.extend_fixture:
            return await recorder.handle_async_request(request)
        raise RuntimeError(f"Missing fixture: {request.url}")

    token = discover_token(args.use_gh_token)
    client = GitHubClient(token, None, httpx.MockTransport(replay) if args.replay else recorder)
    discovery = Discovery(client, now)
    outputs = Path("docs/examples")
    outputs.mkdir(parents=True, exist_ok=True)
    for slug, idea, queries in CASES:
        result = await discovery.search_existing(idea, queries)
        (outputs / f"{slug}.json").write_text(json.dumps(result, indent=2) + "\n")
        print(
            slug,
            "evaluated:",
            result["unique_candidates_evaluated"],
            "plausible:",
            result["verdict"]["plausible_candidates"],
            "errors:",
            len(result["errors"]),
            flush=True,
        )
        for r in result["candidates"][:3]:
            print(" ", r["name"], r["relevance_score"], r["description"], flush=True)
        if not result["errors"] and result["candidates"]:
            top = result["candidates"][0]["name"]
            inspected = await discovery.inspect_repo(top)
            (outputs / f"{slug}-inspection.json").write_text(json.dumps(inspected, indent=2) + "\n")
    limiter = json.loads((outputs / "rate-limiter.json").read_text())
    if len(limiter["candidates"]) >= 2:
        comparison = await discovery.compare_candidates(
            limiter["idea"], [r["name"] for r in limiter["candidates"][:3]]
        )
        (outputs / "comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")
    fixture = {
        "recorded_at": now.isoformat(),
        "source": "https://api.github.com",
        "note": "Public API responses projected to fields used by tests; no request headers, "
        "credentials, commit authors or emails recorded.",
        "entries": recorder.entries,
    }
    if previous and args.extend_fixture:
        fixture["entries"] = previous["entries"] + recorder.entries
    if not args.replay or args.extend_fixture:
        fixture_path.write_text(json.dumps(fixture, indent=2) + "\n")
    await recorder.inner.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live GitHub smoke test (not a unit test)")
    parser.add_argument("--use-gh-token", action="store_true")
    parser.add_argument(
        "--extend-fixture",
        action="store_true",
        help="With --replay, fetch only missing public inspection responses",
    )
    parser.add_argument("--replay", action="store_true", help="Re-rank recorded responses offline")
    asyncio.run(run(parser.parse_args()))
