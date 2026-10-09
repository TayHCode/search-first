"""Bounded public-only GitHub reads and a short-lived, credential-free disk cache."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path
from typing import Any, TypeVar, cast

import httpx

from . import __version__

API = "https://api.github.com"
TOKEN_PATTERN = re.compile(r"(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+)")
PLACEHOLDER_NOTICE = "token placeholder not expanded; running unauthenticated"


def token_placeholder(value: str) -> bool:
    """Recognize config references and example values without guessing at real tokens."""
    value = value.strip()
    if not value or re.search(
        r"\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%", value
    ):
        return True
    label = re.sub(r"[\s_<>-]+", "-", value.lower()).strip("-")
    return label in {
        "your-token",
        "your-token-here",
        "your-github-token",
        "your-github-token-here",
        "token-here",
        "insert-token-here",
        "replace-with-your-token",
        "replace-me",
    }


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
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(name, "").strip()
        if not token_placeholder(token):
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
                token = result.stdout.strip()
                return None if token_placeholder(token) else token
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
        return cast(T, {sanitize(str(k), token): sanitize(v, token) for k, v in value.items()})
    return value


class DiskCache:
    def __init__(self, directory: Path, ttl: int = 300):
        self.directory = directory
        self.ttl = ttl
        self.writes = 0
        self._memory: dict[Path, tuple[float, Any]] = {}

    def is_safe(self) -> bool:
        """An existing cache folder must belong to us and not be group/world writable."""
        if not hasattr(os, "getuid"):
            return False  # Windows stat modes cannot establish POSIX ownership/permissions.
        try:
            info = self.directory.stat()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return info.st_uid == os.getuid() and not info.st_mode & 0o022

    def prune(self) -> None:
        """Delete expired entries so the cache directory cannot grow without bound."""
        self._memory = {
            key: entry for key, entry in self._memory.items() if time.time() - entry[0] < self.ttl
        }
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
                entry = json.loads(file.read_text(encoding="utf-8"))
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
        if self.ttl > 0 and not hasattr(os, "getuid"):
            entry = self._memory.get(self.path(endpoint, params))
            return entry[1] if entry and 0 <= time.time() - entry[0] < self.ttl else None
        if self.ttl <= 0 or not self.is_safe():
            return None
        try:
            entry = json.loads(self.path(endpoint, params).read_text(encoding="utf-8"))
            if 0 <= time.time() - entry["created"] < self.ttl:
                return entry["data"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def put(self, endpoint: str, params: dict[str, Any], data: Any) -> None:
        if self.ttl > 0 and not hasattr(os, "getuid"):
            self.prune()
            self._memory[self.path(endpoint, params)] = (time.time(), data)
            return
        if self.ttl <= 0 or not self.is_safe():
            return
        self.writes += 1
        if self.writes % 200 == 0:
            self.prune()
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory, delete=False
            ) as f:
                json.dump({"created": time.time(), "data": data}, f)
                temporary = Path(f.name)
            temporary.replace(self.path(endpoint, params))
        except OSError:
            # An unwritable cache must not stop a discovery request.
            pass


class GitHubClient:
    SEARCH_INTERVAL_UNAUTH = 6.1
    SEARCH_INTERVAL_AUTH = 2.1

    def __init__(
        self,
        token: str | None = None,
        cache: DiskCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        call_timeout: float = 45,
    ):
        placeholder = token is not None and token_placeholder(token)
        self.token = token.strip() if token is not None and not placeholder else None
        # discover_token has already removed environment placeholders at startup.
        env_placeholder = any(
            name in os.environ and token_placeholder(os.environ[name])
            for name in ("GITHUB_TOKEN", "GH_TOKEN")
        )
        self.auth_warnings = (
            [PLACEHOLDER_NOTICE] if self.token is None and (placeholder or env_placeholder) else []
        )
        self.cache = cache
        self.transport = transport
        self.call_timeout = call_timeout
        self._deadline: ContextVar[float | None] = ContextVar("github_deadline", default=None)
        self._search_lock = asyncio.Lock()
        self._next_search = 0.0
        self.rate_limits: dict[str, dict[str, int]] = {}
        self.cooldown: dict[str, float] = {}
        self._slots = asyncio.Semaphore(3)  # bounded parallel reads, no global serialization

    @contextmanager
    def call_budget(self) -> Iterator[None]:
        """Nested inspections share the outer tool call's deadline."""
        deadline = self._deadline.get()
        marker = self._deadline.set(
            deadline if deadline is not None else time.monotonic() + self.call_timeout
        )
        try:
            yield
        finally:
            self._deadline.reset(marker)

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        deadline = self._deadline.get()
        remaining = deadline - time.monotonic() if deadline is not None else 20.0
        try:
            # Includes queueing, pacing, connection, response and slow body reads.
            async with asyncio.timeout(min(20.0, max(0, remaining))):
                return await self._get(endpoint, params)
        except TimeoutError:
            # Event-loop timers may fire one clock tick early (notably on Windows).
            total = deadline is not None and remaining <= 20.0
            message = (
                f"Search-first total tool-call timeout ({self.call_timeout:g}s); "
                "returning partial results. Narrow queries and retry."
                if total
                else "GitHub request timed out (20s); returning partial results. Retry later."
            )
            raise GitHubError(message, status=408) from None

    async def _pace_search(self) -> None:
        async with self._search_lock:
            delay = self._next_search - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            interval = self.SEARCH_INTERVAL_AUTH if self.token else self.SEARCH_INTERVAL_UNAUTH
            self._next_search = time.monotonic() + interval

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
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
                    "GitHub rate limit: returning partial results; a token raises the limit.",
                    retry_after=max(1, math.ceil(remaining)),
                    status=429,
                )
            if resource == "search":
                await self._pace_search()
                # Another queued request may have received a rate-limit response.
                remaining = self.cooldown.get(resource, 0) - time.time()
                if remaining > 0:
                    raise GitHubError(
                        "GitHub rate limit: returning partial results; a token raises the limit.",
                        retry_after=max(1, math.ceil(remaining)),
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
            except httpx.TimeoutException:
                raise GitHubError(
                    "GitHub request timed out (20s); returning partial results. Retry later.",
                    status=408,
                ) from None
            except httpx.HTTPError:
                raise GitHubError(
                    "GitHub network request failed; check connectivity and retry."
                ) from None
            limits: dict[str, int] = {}
            for name in ("limit", "remaining", "reset"):
                with suppress(KeyError, ValueError):
                    limits[name] = int(response.headers[f"x-ratelimit-{name}"])
            if limits:
                self.rate_limits[resource] = limits
            if response.is_success and limits.get("remaining") == 0:
                self.cooldown[resource] = max(
                    time.time() + 1, limits.get("reset", time.time() + 60)
                )
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
                        "GitHub rate limit reached; returning partial results. "
                        "Retry after retry_after_seconds; a token raises the repository-search "
                        "limit from about 10 to 30 requests/minute (shared quotas still apply).",
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
