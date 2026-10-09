# Changelog

## Unreleased

- `inspect_repo`: the "possibly abandoned" red flag now uses the same reference date as
  the maintenance score (newest default-branch commit, else last push), so the two no
  longer show different day counts. `compare_candidates` rows now include `license_name`.
- Docs: stars scale, flag-replaces-env behaviour, weights and plausible counts, cache
  permissions, and the lexical-retrieval limitation.
- Ranking and comparison weights are now server-start settings:
  `SEARCH_FIRST_RANK_WEIGHTS` / `--rank-weights` and `SEARCH_FIRST_OVERALL_WEIGHTS` /
  `--overall-weights` (`name=value,...`; partial overrides, normalised to sum 1; invalid
  input stops startup). Defaults reproduce the previous scores. `search_existing` and
  `compare_candidates` responses add a `weights` object. Penalties stay fixed.

Fixes from review:

- Ranking: split hyphenated topics, add a tiny stemmer, match on primary language and
  penalize a language the idea did not ask for; weight stars at 16%; require two matched
  terms for plausibility; reject one-word ideas.
- `compare_candidates` rescales scores without retrieval evidence, so it can recommend
  `use as is`; the verdict prefers a licensed, active match and can be `inconclusive`.
- Maintenance ignores a missing PR history and bot PRs, prefers default-branch commit
  dates and caps the backlog sample.
- Reject unbalanced quotes; skip the extra derived queries (always keeping one
  whole-idea variant) when the agent gives two or more variants.
- Rate limits: 60 s default cooldown, detect secondary limits, keep `retry_after_seconds`
  in tool errors. Reject `.`/`..` path segments in the GitHub client.
- Authentication uses a case-insensitive Bearer scheme with no WebSocket bypass.
  Cache: refuse unsafe
  directories and prune periodically. Strip HTML comments and invisible characters from
  README text. `sort_by` is an enum in tool schemas.
- Fixtures keep README excerpts to 2,000 characters; add `THIRD_PARTY_NOTICES.md`,
  package metadata.

Earlier in this release:

- Add a maintenance score to `inspect_repo` and `compare_candidates`: recent commits,
  merged pull requests, PR backlog and release recency, with explained signals.
- Add `sort_by` to `search_existing` (relevance, stars, recent_activity) and
  `compare_candidates` (overall, relevance, stars, maintenance); add an `overall_score`.
- `use as is` now requires adoption (25+ stars) and a push within a year.
- Add optional bearer-token auth for the HTTP transport and refuse unauthenticated
  non-loopback binds.
- Use generic synonym groups and examples.
- Parallelize repository inspection reads, drop the global request lock, and prune the
  expired disk cache at startup.

- Cache pruning preserves unrelated JSON, ignores unsafe directories and is disabled with TTL zero.
- README comment stripping consumes spans once, including unclosed comments.
- Remove a bundled upstream JavaScript description from the recorded release fixtures.
