# Live GitHub smoke test

The descriptions and judgments below describe the original live recording. The
stored snapshot omits some candidates and fields and uses synthetic
README/commit-message placeholders. `docs/examples/` is an **offline replay of that
trimmed projection**, so its counts can differ from the original numbers below. README
inspection placeholders do not verify an upstream project's installation or
functional fit.

Run against the public GitHub REST API on 2026-10-07 using an existing GitHub CLI
credential, explicitly opted in. No model or candidate code was invoked. The query
variants are in `scripts/smoke.py`; full output is in `docs/examples/`. Each search
retrieved up to 30 GitHub best matches per query, then deduplicated and ranked them.
Topic searches exceed that limit, so these are samples, not exhaustive counts.

Recorded raw API responses were projected to the fields used by the tools in
`tests/fixtures/github.json`. Tests replay that snapshot without credentials or
network. Stars and activity will change after this snapshot.

## Idle browser tabs

Idea: a Chrome extension or script that closes idle browser tabs automatically.

90 unique repositories evaluated; 72 met the lexical plausible threshold. Top:

1. [maverick-360/vaultab](https://github.com/maverick-360/vaultab) — 0.871, MIT,
   JavaScript, 5 stars, last push 94 days earlier.
2. [danielmarquesads/tabflow](https://github.com/danielmarquesads/tabflow) — 0.813, 4 stars.
3. [vladbulyukhin/tab-reaper](https://github.com/vladbulyukhin/tab-reaper) — 0.796, 2 stars.

**Judgment:** a sensible shortlist of small projects. The closest candidate's README
describes the requested feature, but with 5 stars the verdict is `fork and adapt`, not
`use as is`. Its inspection shows no PR history, so maintenance rests on commit recency
alone (`maintained`, confidence low); that is a missing signal, not a penalty. Permissions
and correctness still need an isolated browser test; no extension was installed.

[Search](examples/idle-tabs.json) · [Inspection](examples/idle-tabs-inspection.json)

## Python HTTP rate limiting

Idea: a Python library for rate limiting outgoing HTTP requests.

149 unique repositories evaluated; 68 met the plausible threshold. Top by relevance:

1. [bboe/extended_http_server](https://github.com/bboe/extended_http_server) — 0.792,
   BSD-2-Clause, Python, 92 stars.
2. [vutran1710/PyrateLimiter](https://github.com/vutran1710/PyrateLimiter) — 0.782,
   MIT, Python, 525 stars.
3. [urbanophile/parareq](https://github.com/urbanophile/parareq) — 0.767, 1 star.

**Judgment:** mixed. The first result is an HTTP server with throttling, not a client
rate limiter: lexical matching cannot tell the difference, which is why the agent must
read candidates. The three-way comparison blends fit, maintenance and adoption and
corrects the order: PyrateLimiter first (overall 0.822; `actively maintained`, 7 PRs
merged in 90 days, release 38 days ago), the HTTP server `maintained` (0.673), and
parareq `likely abandoned` (last commit 904 days ago). A review found the
earlier version missed PyrateLimiter (hyphenated topics did not match and language was
ignored); those were fixed and verified against this recording.

[Search](examples/rate-limiter.json) · [Inspection](examples/rate-limiter-inspection.json)
· [Comparison](examples/comparison.json)

## Limits and verification

Both searches completed without search API errors. Full output retains
`coverage.truncated` to show limited retrieval. Inspection errors, if any, remain
in the corresponding JSON instead of being converted into fictional facts.

The plausibility threshold is deliberately loose (50% term coverage and score ≥0.55)
and can overstate suitability. Query variants matter; automatic keyword slicing
cannot interpret arbitrary requirements. Topic metadata can make a narrow product
appear to satisfy a broader idea, and metadata ranking misses features documented
only in README or source. Maintenance counts sample the 30 most recently updated
closed PRs (bot PRs excluded); projects with no PR history are scored on the
signals that exist. README text in fixtures is limited to 2,000-character excerpts. Future
evaluation should use a labeled set of ideas and candidate-fit decisions rather than
tuning on these cases.

Offline checks exercise filtering, deduplication, ranking and sort orders,
maintenance scoring, partial/rate-limit errors, credential scrubbing, caching and
pruning, issue/PR ratios, release samples, missing resources, comparison, bearer
authentication and the official MCP tool contracts. Stdio subprocess and Streamable
HTTP initialization/listing/calls pass. Claude and ChatGPT account-level connector
flows and remote HTTPS ingress remain untested.
