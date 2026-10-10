"""Explainable lexical ranking; scores are evidence signals, not semantic guarantees."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from .weights import DEFAULT_RANK, RankWeights

STOP = set(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "of",
        "to",
        "in",
        "on",
        "with",
        "that",
        "this",
        "it",
        "is",
        "are",
        "be",
        "as",
        "by",
        "from",
        "i",
        "my",
        "we",
        "want",
        "need",
        "build",
        "create",
        "make",
        "project",
        "application",
        "app",
        "tool",
        "script",
        "automatically",
        "self",
        "hosted",
        "running",
        "trying",
        "something",
        "library",
        "lib",
        "outgoing",
        "simple",
        "lightweight",
        "using",
    ]
)
# Small, transparent vocabulary: the first word of each group is the canonical form.
# Agents supply domain-specific synonyms in their query variants.
GROUPS = [
    ("windows", "win32", "win64"),
    ("macos", "mac", "osx"),
    ("async", "asyncio", "asynchronous"),
    ("tab", "tabs"),
    ("close", "closes", "closing", "closer", "autoclose"),
    ("idle", "inactive", "unused", "stale"),
    ("auto", "automatic", "automated"),
    ("browser", "chrome", "chromium", "firefox"),
    ("extension", "extensions", "addon", "addons", "plugin", "plugins"),
    ("agent", "agents", "agentic"),
    ("coding", "code", "coder"),
    ("terminal", "tui", "cli", "console"),
    ("orchestrator", "orchestration", "harness", "runner"),
    ("alert", "alerts", "alerting", "alertmanager"),
    ("dashboard", "dashboards"),
    ("monitor", "monitors", "monitoring", "observability"),
    ("package", "sdk", "module"),
    ("convert", "converter", "conversion", "transform"),
    ("server", "service", "daemon"),
    ("database", "db", "datastore"),
    ("schedule", "scheduler", "scheduling", "cron"),
    ("sync", "synchronize", "synchronization", "replicate"),
    ("search", "searcher", "finder", "discovery"),
    ("documentation", "docs", "doc", "document", "documents"),
    ("generate", "generator", "generating", "generated", "generation"),
]
NORMAL = {term: group[0] for group in GROUPS for term in group}


LANGUAGES = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "golang": "go",
    "rust": "rust",
    "java": "java",
    "kotlin": "kotlin",
    "swift": "swift",
    "ruby": "ruby",
    "php": "php",
    "csharp": "c#",
}


def stem(word: str) -> str:
    """Tiny suffix stripper so limiter/limiting/limits compare equal. Not linguistic."""
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 5 and word.endswith("ers"):
        return word[:-3]
    if len(word) > 4 and word.endswith("er"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    """Lowercase terms; hyphenated words yield each part and the joined form."""
    out: list[str] = []
    text = re.sub(r"\bcommand[ -]line\b", "terminal", text, flags=re.I)
    text = re.sub(r"\bself[ -]hosted\b", "selfhosted", text, flags=re.I)
    text = re.sub(r"\bhome[ -]lab\b", "homelab", text, flags=re.I)
    for raw in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower()):
        parts = raw.split("-")
        for part in [*parts, "".join(parts)] if len(parts) > 1 else parts:
            if part in STOP:
                continue
            out.append(NORMAL.get(part) or NORMAL.get(stem(part)) or stem(part))
    return list(dict.fromkeys(out))


# These words describe a general aspiration, not a concrete behavior/domain.
VAGUE_TERMS = set(
    tokens("life easier easy better faster computer useful helpful productivity helper")
)
# Ecosystem/interface qualifiers remain explicit constraints, not core behavior.
# HTTP/request wording must not demote a dedicated rate-limiting library.
QUALIFIERS = set(
    tokens(
        "http https request requests terminal selfhosted windows macos linux "
        "program software utility"
    )
) | set(tokens(" ".join(LANGUAGES.values())))

MIN_REUSE_STARS = 25
MAX_REUSE_PUSH_DAYS = 365


def key_terms(idea: str) -> set[str]:
    return set(tokens(idea)) - VAGUE_TERMS - QUALIFIERS


def specific_idea(idea: str) -> bool:
    return len(key_terms(idea)) >= 2


def missing_constraints(idea: str, text: str) -> list[str]:
    """Small explicit hard-constraint vocabulary; absence means unverified, not impossible."""
    wanted = set(tokens(idea))
    actual = set(tokens(text))
    missing = wanted.intersection({"terminal", "windows", "macos", "linux"}) - actual
    for pattern, label in [
        (r"\bself[ -]hosted\b", "self-hosted"),
        (r"\b(?:chrome|chromium)\b", "chrome"),
    ]:
        if re.search(pattern, idea, re.I) and not re.search(pattern, text, re.I):
            missing.add(label)
    for language in mentioned_languages(idea):
        if language not in text.lower():
            missing.add(language)
    # Homelab is a broad domain. Evidence about one appliance is not evidence of
    # general homelab support; keep this small scope check visible and inspectable.
    if "homelab" in wanted:
        for product in ("unraid", "proxmox", "truenas"):
            if re.search(rf"\b{product}\b", text, re.I) and product not in idea.lower():
                missing.add(f"general homelab support beyond {product}")
    return sorted(missing)


def is_adjacent(row: dict[str, Any]) -> bool:
    return (
        not row["plausible"]
        and row.get("idea_is_specific", True)
        and len(row.get("matched_idea_terms", [])) >= 2
        and row["idea_term_coverage"] >= 0.25
        and row["relevance_score"] >= 0.3
    )


def relevance_group(row: dict[str, Any]) -> tuple[Any, ...]:
    # Apply the same plausibility grouping in all presentation orders.
    return (
        not row["plausible"],
        bool(row.get("missing_key_terms")),
        bool(row.get("missing_constraints")),
        not is_adjacent(row),
        -row["relevance_score"],
    )


def tie_key(row: dict[str, Any]) -> tuple[Any, ...]:
    health = row.get("maintenance") or row.get("metadata_maintenance", {})
    pushed = parse_time(row.get("last_push"))
    return (
        -(health.get("score") or 0),
        -row["stars"],
        -row.get("metadata_term_coverage", 0),
        -pushed.timestamp() if pushed else math.inf,
        row["name"].casefold(),
        row["name"],
    )


def relevance_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (*relevance_group(row), *tie_key(row))


def explain_ties(ranked: list[dict[str, Any]]) -> None:
    """Annotate equal relevance groups, including peers outside the returned row limit."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in ranked:
        groups.setdefault(relevance_group(row), []).append(row)
    criteria = (
        "higher maintenance score",
        "more stars",
        "greater term coverage in name/description/topics",
        "more recent push",
        "alphabetical repository name",
        "repository name spelling",
    )
    for peers in groups.values():
        if len(peers) < 2:
            continue
        winner, runner_up = sorted(peers, key=tie_key)[:2]
        criterion = next(
            (
                label
                for a, b, label in zip(tie_key(winner), tie_key(runner_up), criteria, strict=True)
                if a != b
            ),
            "identical metadata",
        )
        explanation = (
            f"tied with {len(peers) - 1} others at {winner['relevance_score']:.3f}; "
            f"{winner['name']} wins by {criterion}."
        )
        for row in peers:
            row["tie_break"] = explanation
            row["why_it_matched"] += " " + explanation


def mentioned_languages(idea: str) -> set[str]:
    return {LANGUAGES[w] for w in re.findall(r"[a-z0-9#]+", idea.lower()) if w in LANGUAGES}


def days_since(value: str | None, now: datetime | None = None) -> int | None:
    if not value:
        return None
    dt = parse_time(value)
    return max(0, ((now or datetime.now(UTC)) - dt).days) if dt else None


def popularity_score(stars: int) -> float:
    """Logarithmic 0-1 scale: 10 stars ~0.26, 100 ~0.5, 1,000 ~0.75, 10,000+ 1.0."""
    return min(1.0, math.log10(max(0, stars) + 1) / 4)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def pull_request_activity(
    closed: list[dict[str, Any]] | None, open_prs: int | None, now: datetime | None = None
) -> dict[str, Any] | None:
    """Summarize recently closed pull requests (a bounded, most-recently-updated sample)."""
    if closed is None:
        return None
    now = now or datetime.now(UTC)
    merged_90, closed_90, days_to_merge = 0, 0, []
    last_merged: datetime | None = None
    for pr in closed:
        if pr.get("bot"):
            continue  # dependency-bot merges say little about human maintenance
        done = parse_time(pr.get("closed_at"))
        merged = parse_time(pr.get("merged_at"))
        created = parse_time(pr.get("created_at"))
        if merged:
            last_merged = max(last_merged, merged) if last_merged else merged
            if created:
                days_to_merge.append(max(0.0, (merged - created).total_seconds() / 86400))
        if done and (now - done).days <= 90:
            closed_90 += 1
            merged_90 += bool(merged)
    days_to_merge.sort()
    median = days_to_merge[len(days_to_merge) // 2] if days_to_merge else None
    return {
        "merged_last_90_days": merged_90,
        "closed_unmerged_last_90_days": closed_90 - merged_90,
        "last_merged_at": last_merged.isoformat() if last_merged else None,
        "median_days_to_merge": round(median, 1) if median is not None else None,
        "open": open_prs,
        "sample_size": len(closed),
        "sample_limit": 30,
        "note": "Sample of the 30 most recently updated closed PRs, bot PRs excluded; "
        "counts saturate at 30. Many solo projects commit directly and never merge PRs.",
    }


def maintenance(
    summary: dict[str, Any],
    last_commit: str | None,
    pulls: dict[str, Any] | None,
    latest_release: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Transparent 0-1 maintenance score from commits, PR flow and releases.

    Components that cannot be measured are left out and the remaining weights are
    renormalized; confidence drops accordingly. This is a heuristic, not a guarantee.
    """
    parts: list[tuple[str, float, float, str]] = []  # name, weight, value, detail
    # Prefer the default-branch commit date: pushed_at also counts bot/other-branch pushes.
    newest = parse_time(last_commit) or parse_time(summary.get("last_push"))
    if newest:
        age = max(0, ((now or datetime.now(UTC)) - newest).days)
        value = 1.0 if age < 30 else 0.8 if age < 90 else 0.55 if age < 180 else 0.25
        value = value if age < 365 else 0.0
        parts.append(("recent commits", 0.4, value, f"newest push/commit {age} days ago"))
    if pulls and pulls["sample_size"] > 0:
        merged = pulls["merged_last_90_days"]
        parts.append(("merged PRs", 0.25, min(1.0, merged / 5), f"{merged} merged in 90 days"))
        open_prs = pulls.get("open")
        if open_prs is not None and merged + min(open_prs, 30) > 0:
            parts.append(
                (
                    "PR backlog",
                    0.2,
                    merged / (merged + min(open_prs, 30)),
                    f"{open_prs} open vs {merged} merged recently",
                )
            )
    release_time = parse_time(latest_release)
    if release_time:
        age = max(0, ((now or datetime.now(UTC)) - release_time).days)
        parts.append(
            (
                "releases",
                0.15,
                1.0 if age < 365 else 0.5 if age < 730 else 0.0,
                f"latest stable release {age} days ago",
            )
        )
    total = sum(w for _, w, _, _ in parts)
    score = sum(w * v for _, w, v, _ in parts) / total if total else None
    if summary.get("archived"):
        label, score = "archived", 0.0
    elif score is None:
        label = "unknown"
    elif newest and (now or datetime.now(UTC)) - newest > timedelta(days=730):
        label = "likely abandoned"
    else:
        label = (
            "actively maintained"
            if score >= 0.75
            else "maintained"
            if score >= 0.5
            else "slow"
            if score >= 0.25
            else "stale"
        )
    return {
        "score": round(score, 3) if score is not None else None,
        "label": label,
        "confidence": "high" if len(parts) >= 3 else "medium" if len(parts) == 2 else "low",
        "signals": [f"{name}: {detail}" for name, _, _, detail in parts],
        "note": "Heuristic from public metadata (commits, PR flow, releases); "
        "it does not assess code quality or security.",
    }


def repo_summary(repo: dict[str, Any]) -> dict[str, Any]:
    license_data = repo.get("license") or {}
    license_id = license_data.get("spdx_id")
    return {
        "name": repo["full_name"],
        "url": repo["html_url"],
        "description": repo.get("description"),
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "last_push": repo.get("pushed_at"),
        "archived": repo.get("archived", False),
        "license": license_id if license_id and license_id != "NOASSERTION" else None,
        "license_name": license_data.get("name"),
        "language": repo.get("language"),
        "topics": repo.get("topics", []),
        "size_kb": repo.get("size", 0),
    }


def rank_repo(
    idea: str,
    repo: dict[str, Any],
    matches: list[dict[str, Any]],
    now: datetime | None = None,
    weights: RankWeights = DEFAULT_RANK,
) -> dict[str, Any]:
    result = repo_summary(repo)
    metadata_text = " ".join(
        [
            result["name"].split("/")[-1],
            result["description"] or "",
            " ".join(result["topics"]),
        ]
    )
    text = metadata_text + " " + (result["language"] or "")
    idea_terms, repo_terms = key_terms(idea), set(tokens(text))
    matched = sorted(idea_terms & repo_terms)
    coverage = len(matched) / max(1, len(idea_terms))
    query_coverage = max(
        (
            len(key_terms(m["terms"]) & repo_terms) / max(1, len(key_terms(m["terms"])))
            for m in matches
        ),
        default=0,
    )
    retrieval = max((1 / math.sqrt(m["rank"]) for m in matches), default=0)
    multiple = min(1, len(matches) / 3)
    age = days_since(result["last_push"], now)
    freshness = 1 if age is not None and age < 180 else 0.5 if age is not None and age < 730 else 0
    popularity = popularity_score(result["stars"])
    score = (
        weights.idea_match * coverage
        + weights.query_match * query_coverage
        + weights.github_rank * retrieval
        + weights.variant_agreement * multiple
        + weights.freshness * freshness
        + weights.stars * popularity
    )
    if not matches:
        # No retrieval evidence (e.g. compare): rescale so a perfect repo can reach 1.0.
        used = weights.idea_match + weights.freshness + weights.stars
        score = (
            (
                weights.idea_match * coverage
                + weights.freshness * freshness
                + weights.stars * popularity
            )
            / used
            if used > 0
            else 0
        )
    penalties = []
    specific = specific_idea(idea)
    missing_keys = sorted(key_terms(idea) - repo_terms)
    hard_missing = missing_constraints(idea, text)
    credible = result["stars"] >= MIN_REUSE_STARS and age is not None and age < MAX_REUSE_PUSH_DAYS
    if not credible:
        penalties.append(
            "reuse floor unverified: needs at least 25 stars and a push within 365 days"
        )
    if missing_keys:
        score = min(score, 0.79)
        penalties.append("key terms unverified: " + ", ".join(missing_keys))
    if hard_missing:
        score = min(score, 0.54)
        penalties.append("hard constraints unverified: " + ", ".join(hard_missing))
    if not specific:
        score = min(score, 0.29)
        penalties.append("idea too vague; name a concrete behavior and domain")
    wanted = mentioned_languages(idea)
    if wanted and result["language"] and result["language"].lower() not in wanted:
        score -= 0.12
        penalties.append(f"language {result['language']} differs from the requested language")
    if result["archived"]:
        score -= 0.15
        penalties.append("archived")
    if not result["license"]:
        score -= 0.06
        penalties.append("license not confirmed")
    if re.search(r"\b(awesome|curated|resources|tutorial|roadmap|course|collection)\b", text, re.I):
        score -= 0.18
        penalties.append("may be a resource list or tutorial rather than a runnable product")
    result.update(
        {
            "relevance_score": round(max(0, min(1, score)), 3),
            "days_since_push": age,
            "idea_term_coverage": round(coverage, 3),
            "metadata_term_coverage": round(
                len(set(tokens(idea)) & set(tokens(metadata_text))) / max(1, len(tokens(idea))), 3
            ),
            "metadata_maintenance": maintenance(result, None, None, None, now),
            "core_terms": sorted(idea_terms),
            "core_match": "strong"
            if specific and not missing_keys
            else "partial"
            if matched
            else "none",
            "reuse_floor_met": credible,
            "missing_idea_terms": sorted(set(tokens(idea)) - repo_terms),
            "missing_key_terms": missing_keys,
            "missing_constraints": hard_missing,
            "idea_is_specific": specific,
            "matched_idea_terms": matched,
            "plausible": specific
            and credible
            and not missing_keys
            and not hard_missing
            and coverage >= 0.5
            and score >= 0.55
            and len(matched) >= min(2, len(idea_terms)),
            "why_it_matched": (
                ("Strong core match; " if specific and not missing_keys else "")
                + f"Core terms: {len(matched)}/{len(idea_terms)} "
                f"({', '.join(matched) or 'no direct overlap'}); "
                f"{result['stars']} stars, push {age if age is not None else 'unknown'} days ago; "
                + (
                    f"returned by {len(matches)} query variant(s), best GitHub rank "
                    f"{min(m['rank'] for m in matches)}."
                    if matches
                    else "metadata comparison only; no retrieval-rank evidence."
                )
                + (f" Cautions: {'; '.join(penalties)}." if penalties else "")
            ),
            "matched_queries": [m["query"] for m in matches],
        }
    )
    return result


def verdict(
    ranked: list[dict[str, Any]], *, complete: bool, idea: str | None = None
) -> dict[str, Any]:
    plausible = [r for r in ranked if r["plausible"]]
    adjacent = [r for r in ranked if is_adjacent(r)]
    # The caller supplies relevance order; never silently select a lower licensed row.
    closest = ranked[0] if ranked and (ranked[0]["plausible"] or is_adjacent(ranked[0])) else None
    vague = (idea is not None and not specific_idea(idea)) or bool(
        ranked and not ranked[0].get("idea_is_specific", True)
    )
    if not complete:
        recommendation = "inconclusive"
        reasoning = (
            "Search evidence is incomplete. Retry failed/skipped queries or incomplete reads "
            "before choosing to use, fork, borrow or build; retrieved candidates are only leads."
        )
        if vague:
            closest = None
            reasoning += " Name a concrete behavior, domain and must-have constraints."
    elif vague:
        closest = None
        recommendation = "inconclusive"
        reasoning = (
            "Idea is too vague. Name a concrete behavior, domain and must-have constraints "
            "before choosing a reusable base."
        )
    elif closest and (closest.get("missing_constraints") or closest.get("missing_key_terms")):
        recommendation = "inconclusive"
        gaps = sorted(
            set(closest.get("missing_constraints", []) + closest.get("missing_key_terms", []))
        )
        reasoning = (
            "Required behavior or constraints are unverified: "
            + ", ".join(gaps)
            + ". Inspect README and test those requirements; "
            "broaden queries before choosing a base."
        )
    elif not plausible and adjacent:
        recommendation = "inconclusive"
        reasoning = (
            "Core overlap exists, but the reuse floor (25 stars, push within 365 days) "
            "or lexical plausibility threshold is unverified. Inspect "
            "their README and required behavior, then broaden queries before choosing to "
            "build. A low score does not establish that no reusable base exists."
        )
    elif not closest:
        recommendation = "build from scratch" if complete else "inconclusive"
        reasoning = (
            "No plausible candidate in this bounded search. Broaden queries and inspect adjacent "
            "projects before building; this is not proof that nothing exists."
            if complete
            else "Search is incomplete and found no reliable match. Retry the failed queries "
            "before deciding; do not build from this result alone."
        )
    elif not closest["license"] or closest["archived"]:
        recommendation = "borrow parts"
        reasoning = (
            "The first relevance-ranked match has license or archive concerns. "
            "Check permission and inspect alternatives before reusing any code."
        )
    elif (
        closest["idea_term_coverage"] >= 0.85
        and closest["relevance_score"] >= 0.8
        and closest["reuse_floor_met"]
    ):
        recommendation = "use as is"
        reasoning = (
            "Strong metadata overlap and a recognized license. Inspect README and test "
            "the required behavior before deciding that it works unchanged."
        )
    else:
        recommendation = "fork and adapt"
        reasoning = (
            "All core terms and explicit constraints are covered, with at least 25 stars, "
            "a push within 365 days and a recognized license. Inspect and test before forking."
        )
    best = [r for r in ranked if relevance_group(r) == relevance_group(ranked[0])]
    if best and all("terminal" in r.get("missing_constraints", []) for r in best):
        reasoning += " None of these is a terminal tool according to retrieved metadata "
        reasoning += "in the top relevance group; terminal/CLI support is unverified."
    if closest and closest.get("tie_break"):
        reasoning += " " + closest["tie_break"]
    return {
        "closest_match": {"name": closest["name"], "url": closest["url"]} if closest else None,
        "plausible_candidates": len(plausible),
        "adjacent_candidates": len(adjacent),
        "recommendation": recommendation,
        "reasoning": reasoning,
        "provisional": True,
        "search_complete": complete,
    }
