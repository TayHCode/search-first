# Third-party material

## Release snapshot

The release fixture preserves a subset of public API metadata. Candidate rows with
private-term or internal-looking identifiers are omitted. Descriptions may be
redacted. README bodies and commit messages are **synthetic placeholders**, rather
than redistributed upstream text. Examples are regenerated offline from that
projection. The original captured evidence remains in private history and outside
the public export. No upstream source code is included.

The provenance list below is retained for the original capture. The MIT license
covers this project's code, not third-party dependencies. An installed dependency
license audit is part of the release proof; upstream LICENSE/NOTICE files remain
in their installed distributions. Dependency versions and source hashes are pinned
in `uv.lock`; that file does not itself contain dependency license texts.

Two search-result candidate identifiers use neutral placeholders. An unused API
license metadata field is omitted; its license name and SPDX identifier are preserved.

## Original capture provenance

The test fixture `tests/fixtures/github.json` and the examples in `docs/examples/` and
`docs/SMOKE.md` record **public GitHub API responses** (repository metadata, commit
dates, pull-request timestamps and short README excerpts of at most 2,000 characters).
They are included only to make the tests and documentation reproducible. The text
belongs to the respective authors and remains under their licenses; README excerpts are
not covered by this project's MIT license. Repositories whose README excerpts are
recorded, with the license GitHub reported on 2026-10-07:

| Repository | License |
| --- | --- |
| https://github.com/bboe/extended_http_server | BSD-2-Clause |
| https://github.com/maverick-360/vaultab | MIT |
| https://github.com/urbanophile/parareq | MIT |
| https://github.com/vignesh476/httpplus | MIT |
| https://github.com/vutran1710/PyrateLimiter | MIT |
| https://github.com/whoisjayd/blazeserve | MIT |

Other repositories appear only as names, URLs, descriptions, topics and counts. If an
author asks for their excerpt to be removed, delete it from the fixture and re-run
`scripts/smoke.py --replay`. Python dependencies are listed in `uv.lock` with their own
licenses.

## Retained historical release-snapshot wording

The following earlier project notice wording is retained verbatim for attribution
continuity. The original capture provenance above applies to this block too.

The test fixture preserves a subset of public API metadata. Some candidate rows and
descriptions are omitted. README bodies and commit messages are **synthetic
placeholders**, rather than redistributed upstream text. Examples are regenerated
offline from that projection. No upstream source code is included.

The provenance list below describes the original capture. The MIT license
covers this project's code, not third-party dependencies. Upstream LICENSE/NOTICE
files remain in their installed distributions. Dependency versions and source hashes are pinned
in `uv.lock`; that file does not itself contain dependency license texts.

## Capture provenance

