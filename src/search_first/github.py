"""Bounded public-only GitHub reads and a short-lived, credential-free disk cache."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, TypeVar, cast

import httpx

from . import __version__

API = "https://api.github.com"
TOKEN_PATTERN = re.compile(r"(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+)")


class GitHubError(Exception):
    def __init__(self, message: str, *, retry_after: int | None = None, status: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status = status

    def as_dict(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "status": self.status,
            "retry_after_seconds": self.retry_after,
        }


def discover_token(use_gh: bool = False) -> str | None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    if use_gh:
        try:
            result = subprocess.run(
                ["gh", "auth", "token", "--hostname", "github.com"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


T = TypeVar("T")


def sanitize(value: T, token: str | None = None) -> T:
    """Never persist/return the configured credential or recognizable GitHub tokens."""
    if isinstance(value, str):
        text = value.replace(token, "[REDACTED]") if token else value
        return cast(T, TOKEN_PATTERN.sub("[REDACTED]", text))
    if isinstance(value, list):
        return cast(T, [sanitize(v, token) for v in value])
    if isinstance(value, dict):
        return cast(T, {str(k): sanitize(v, token) for k, v in value.items()})
    return value


class DiskCache:
    def __init__(self, directory: Path, ttl: int = 300):
        self.directory = directory
        self.ttl = ttl
        self.writes = 0

    def is_safe(self) -> bool:
        """An existing cache folder must belong to us and not be group/world writable."""
        try:
            info = self.directory.stat()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return info.st_uid == os.getuid() and not info.st_mode & 0o022

    def prune(self) -> None:
        """Delete expired entries so the cache directory cannot grow without bound."""
        if self.ttl <= 0 or not self.is_safe():
            return
        try:
            files = list(self.directory.glob("*.json"))
        except OSError:
            return
        for file in files:
            try:
                if file.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}\.json", file.name):
                    continue
                entry = json.loads(file.read_text())
                if (
                    not isinstance(entry, dict)
                    or set(entry) != {"created", "data"}
                    or not isinstance(entry["created"], (int, float))
                ):
                    continue
                if time.time() - file.stat().st_mtime > self.ttl:
                    file.unlink()
            except (OSError, ValueError, TypeError):
                pass

    def path(self, endpoint: str, params: dict[str, Any]) -> Path:
        key = json.dumps([endpoint, params], sort_keys=True).encode()
        return self.directory / (hashlib.sha256(key).hexdigest() + ".json")

    def get(self, endpoint: str, params: dict[str, Any]) -> Any:
        if self.ttl <= 0 or not self.is_safe():
            return None
        try:
            entry = json.loads(self.path(endpoint, params).read_text())
            if 0 <= time.time() - entry["created"] < self.ttl:
                return entry["data"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def put(self, endpoint: str, params: dict[str, Any], data: Any) -> None:
        if self.ttl <= 0 or not self.is_safe():
            return
        self.writes += 1
        if self.writes % 200 == 0:
            self.prune()
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", dir=self.directory, delete=False) as f:
                json.dump({"created": time.time(), "data": data}, f)
                temporary = Path(f.name)
            temporary.replace(self.path(endpoint, params))
        except OSError:
            # An unwritable cache must not stop a discovery request.
            pass


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        cache: DiskCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.token = token
        self.cache = cache
        self.transport = transport
        self.cooldown: dict[str, float] = {}
        self._slots = asyncio.Semaphore(3)  # bounded parallel reads, no global serialization

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        if not re.fullmatch(
            r"/(?:search/repositories|repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
            r"(?:/(?:readme|commits|pulls|releases))?)",
            endpoint,
        ) or any(part in (".", "..") for part in endpoint.split("/")):
            raise ValueError("Unsupported GitHub endpoint")
        params = params or {}
        async with self._slots:
            cached = self.cache.get(endpoint, params) if self.cache else None
            if cached is not None:
                return cached
            resource = "search" if endpoint.startswith("/search/") else "core"
            remaining = self.cooldown.get(resource, 0) - time.time()
            if remaining > 0:
                raise GitHubError(
                    "GitHub rate limit: wait before retrying.",
                    retry_after=max(1, int(remaining) + 1),
                    status=429,
                )
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": f"search-first-mcp/{__version__}",
            }
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            try:
                async with httpx.AsyncClient(
                    base_url=API,
                    headers=headers,
                    timeout=20,
                    transport=self.transport,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(endpoint, params=params)
            except httpx.HTTPError:
                raise GitHubError(
                    "GitHub network request failed; check connectivity and retry."
                ) from None
            if response.status_code in (403, 429):
                if (
                    response.status_code == 429
                    or response.headers.get("x-ratelimit-remaining") == "0"
                    or "retry-after" in response.headers
                    or b"rate limit" in response.content[:2000].lower()
                ):
                    delay = 60  # GitHub advises waiting at least a minute when it is silent
                    try:
                        if int(response.headers.get("retry-after", "0")) > 0:
                            delay = int(response.headers["retry-after"])
                        elif response.headers.get("x-ratelimit-reset"):
                            delay = int(response.headers["x-ratelimit-reset"]) - int(time.time())
                    except ValueError:
                        pass
                    delay = max(1, min(delay, 3600))
                    self.cooldown[resource] = time.time() + delay
                    raise GitHubError(
                        "GitHub rate limit reached; retry later or use GITHUB_TOKEN.",
                        retry_after=delay,
                        status=response.status_code,
                    )
                raise GitHubError(
                    "GitHub denied this request; check token permissions or retry later.",
                    status=403,
                )
            if response.status_code == 401:
                raise GitHubError(
                    "GitHub rejected GITHUB_TOKEN; replace it or use no token.", status=401
                )
            if response.status_code == 404:
                raise GitHubError("Public repository or requested resource not found.", status=404)
            if response.status_code in (301, 302, 307, 308):
                raise GitHubError(
                    "Repository moved; use its current owner/name.", status=response.status_code
                )
            if response.status_code >= 400:
                raise GitHubError(
                    "GitHub request failed; check query syntax or retry later.",
                    status=response.status_code,
                )
            if len(response.content) > 2_000_000:
                raise GitHubError(
                    "GitHub response exceeds the 2 MB safety limit; narrow the request."
                )
            try:
                data = response.json()
            except ValueError:
                raise GitHubError("GitHub returned invalid JSON; retry later.") from None
            if isinstance(data, dict) and data.get("private"):
                raise GitHubError("Only public repositories may be inspected.", status=403)
            if endpoint.endswith("/pulls") and params.get("state", "open") == "open":
                # per_page=1: the last page is the exact open-PR count at this snapshot.
                link = response.headers.get("link", "")
                match = re.search(r'<[^>]*[?&]page=(\d+)[^>]*>; rel="last"', link)
                data = {"count": int(match.group(1)) if match else len(data)}
            elif endpoint.endswith("/pulls"):
                data = [
                    {
                        **{
                            k: pr.get(k) for k in ("number", "created_at", "closed_at", "merged_at")
                        },
                        "bot": (pr.get("user") or {}).get("type") == "Bot",
                    }
                    for pr in data
                ]
            if endpoint.endswith("/readme") and data.get("encoding") == "base64":
                try:
                    text = base64.b64decode(data.get("content", "")).decode(
                        "utf-8", errors="replace"
                    )
                except ValueError:
                    raise GitHubError("GitHub README could not be decoded.") from None
                data = {"text": text, "size": data.get("size")}
            # Search results must never cache private repository metadata, even on a bad response.
            if endpoint == "/search/repositories":
                data["items"] = [r for r in data.get("items", []) if not r.get("private")]
            data = sanitize(data, self.token)
            if self.cache:
                self.cache.put(endpoint, params, data)
            return data
