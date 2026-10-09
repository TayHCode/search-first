# search-first

**Before you build this, here is what already exists on GitHub.**

An MCP server that discovers existing public projects, explains a ranked shortlist,
inspects maintenance and reuse signals, and compares candidates. Search first,
inspect and test a candidate, then use it or fork and adapt it. Build from scratch
only after a broader search fails to find a suitable base.

Version 0.1, MIT licensed. It is not yet published as a package or a hosted service,
and the project and distribution (`search-first-mcp`) names may still change.

## Install and run

The minimum is **Python 3.11** (the server uses Python 3.11's `asyncio.timeout`
and `datetime.UTC`). Git is needed to clone the source. The recommended route is
**uvx**, which can download a supported Python without changing your system Python
or needing administrator access. Install [uv using its official installer](https://docs.astral.sh/uv/getting-started/installation/):

- Linux (including Debian/Ubuntu): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

Open a new terminal so `uv` and `uvx` are on PATH. Until a package is published,
clone the source and run it locally (these commands also work in PowerShell):

```sh
git clone https://github.com/TayHCode/search-first.git
cd search-first
uvx --python 3.12 --from . search-first --help
uvx --python 3.12 --from . search-first
```

The help command verifies installation without GitHub credentials. The next command
starts stdio: **a server waiting silently for an MCP client is normal**. Use Ctrl+C
to exit. Stdout is reserved for MCP messages. For local HTTP instead:

```sh
uvx --python 3.12 --from . search-first --transport streamable-http
```

The endpoint is `http://127.0.0.1:8000/mcp`. Connect an MCP client, then stop the
server with Ctrl+C when finished.

### Alternative: pip in a virtual environment

Check the interpreter **before installing dependencies**. The preflight prints
`search-first needs Python 3.11 or newer; you have X` on an old interpreter,
rather than pip's misleading dependency error `from versions: none`.

Linux/macOS, from the clone:

```sh
python3 scripts/check_python.py
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
search-first --help
```

Stop if the preflight fails. Stock macOS `/usr/bin/python3` may be **3.9.6**:
use the uvx route above, or install Python 3.11+ from [python.org](https://www.python.org/downloads/macos/)
and run the preflight with that interpreter. On Debian/Ubuntu, missing `ensurepip`
means you need the matching `python3-venv` package (`sudo apt install python3-venv`
with administrator access); the uvx route works without sudo. For a uv-created
venv use `uv venv --python 3.12` followed by `uv pip install --python .venv .`;
`uv venv` alone does not install pip.

Windows PowerShell, with Python 3.11 installed from [python.org](https://www.python.org/downloads/windows/):

```powershell
py -3.11 scripts/check_python.py
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\search-first.exe --help
```

Use `py -3.12` if 3.12 is installed instead. Direct executable paths avoid changing
PowerShell's activation-script policy. To run development checks, install `'.[dev]'`
in place of `.` (quote it in PowerShell too); see "Proof and development" below.

The server does **not** call a model or require model credentials. Your calling agent
provides query variants, judges functional fit, and controls any subsequent work.

Optional GitHub authentication:

- Set `GITHUB_TOKEN` in the launching process, preferably with public read access
  only. Never put a token in tracked configuration, command arguments or examples.
- Or explicitly opt in to existing CLI credentials: `search-first --use-gh-token`.
  `GITHUB_TOKEN` takes precedence. The CLI fallback is off by default.
- Without either, GitHub's unauthenticated search quota is smaller. On a rate limit,
  the server stops further variants and returns a retry interval and incomplete
  evidence. Retry later; do not treat an empty partial search as permission to build.

Only public repositories are returned; inspecting private metadata is refused.
GitHub receives your search terms. Do not include confidential requirements or secrets.
Cache defaults to `$XDG_CACHE_HOME/search-first` (or `~/.cache/search-first`) for
five minutes; `--cache-dir PATH` changes it and `--cache-ttl 0` disables it. The
cache directory is created with mode 0700 and cache files are written 0600; an existing
directory that you do not own, or that others can write to, is not used. Cache entries contain public response data, not authentication headers. Known credentials
and recognizable GitHub tokens are scrubbed, including decoded README text. Expired
entries are pruned at startup and replaced when reused.

## Tools

### `search_existing`

```json
{
  "idea": "a Chrome extension or script that closes idle browser tabs automatically",
  "queries": [
    "chrome idle tab close", "auto close tabs",
    "inactive tabs extension", "tab auto closer"
  ],
  "language": null,
  "min_stars": 0,
  "include_archived": false,
  "limit": 15,
  "sort_by": "relevance"
}
```

Pass **3 to 6 short query variants**: synonyms, different framings, ecosystem terms.
Queries are plain terms, optionally quoted, not GitHub qualifiers. The server always
adds one idea-derived variant (the first four meaningful words of the idea). If you pass
fewer than two variants it adds up to three more, including a repository-topic search. It queries
the GitHub REST repository search API, retrieves up to 30 best matches per query,
filters and deduplicates them, and returns at most `limit` rows (1–50).
`language` filters the primary GitHub language, which can exclude mixed-language
projects. Archived projects are excluded unless explicitly requested. `sort_by`
orders the returned rows: `relevance` (default), `stars`, or `recent_activity`
(newest push first). Implausible matches always sort last, whatever `sort_by` is, so
`stars` orders only the plausible rows by stars and then lists the implausible ones.
The verdict always uses relevance order. `use as is` additionally requires at least 25 stars and a push
within a year; a strong match with less adoption is reported as `fork and adapt`.

Each candidate includes name, URL, description, stars, forks, last push, archived
flag, SPDX license identifier, language, topics, size, a 0–1 relevance score,
matched queries, missing idea terms and a plain explanation. `verdict` contains
`closest_match`, `plausible_candidates`, one of `use as is`, `fork and adapt`,
`borrow parts`, `build from scratch`, `inconclusive`, and reasoning. All recommendations are
provisional. Plausible counts describe the retrieved sample, not an exhaustive
GitHub census. Errors and truncation are explicit.

An abbreviated **real recorded result**, not invented example data:

```json
{
  "verdict": {
    "closest_match": {
      "name": "maverick-360/vaultab",
      "url": "https://github.com/maverick-360/vaultab"
    },
    "plausible_candidates": 72,
    "recommendation": "fork and adapt",
    "provisional": true
  },
  "candidate": {
    "name": "maverick-360/vaultab",
    "stars": 5,
    "forks": 0,
    "license": "MIT",
    "language": "JavaScript",
    "relevance_score": 0.871,
    "days_since_push": 94,
    "why_it_matched": "Idea terms: browser, close, extension, idle, tab; returned by 4 query variant(s), best GitHub rank 1."
  }
}
```

This excerpt omits fields and uses `candidate` for a single row; the actual tool
returns `candidates` as an array. See [full search output](docs/examples/idle-tabs.json)
for the complete schema and timestamped facts.
The example file is a frozen snapshot, so its counts (it reports 65 plausible of 85
evaluated) differ from the excerpt above, and live counts drift as GitHub changes.
The recorded data is a trimmed projection: some candidates and descriptions are
omitted, two candidate identifiers are neutral placeholders, and README bodies and
commit messages are synthetic placeholders. See
[the provenance note](THIRD_PARTY_NOTICES.md). Placeholders are not proof of what an
upstream project's README says.

### `inspect_repo`

```json
{"repo": "maverick-360/vaultab"}
```

Accepts `owner/repo` or `https://github.com/owner/repo`. Returns an 8,000-character
README excerpt, SPDX license/name, size in KB, up to five last commits, open issue
and PR counts and their ratio, cadence of stable releases in the last ten GitHub
releases, README install/run hints, and heuristic red flags: archived, possibly
abandoned (>730 days without a commit or push), stale maintenance, missing recognized
license, or tiny (<10 KB).

**Maintenance.** `maintenance` is a transparent 0–1 score with a label (`actively
maintained`, `maintained`, `slow`, `stale`, `likely abandoned`, `archived`), a
confidence level and the signals behind it: recency of the newest commit/push (40%),
pull requests merged in the last 90 days from a sample of the 30 most recently
updated closed PRs (25%), merged-vs-open PR backlog (20%) and age of the latest
stable release (15%). Signals that cannot be measured are dropped and the weights
renormalized, lowering confidence. `activity.pull_requests` shows merged and
closed-unmerged counts, the median days to merge and the last merge date. It is a
heuristic on public metadata: it says nothing about code quality or security, and
projects that commit directly to `main` look quieter than they are.
Missing resources remain explicit errors, not fabricated zero activity.
The "possibly abandoned" red flag and the maintenance recency signal use the same
reference date: the newest default-branch commit, or GitHub's last push if no commit
could be read. GitHub's `pushed_at` also counts pushes to other branches, so the
`last_push` field can be newer than that reference.

`fit_for_your_idea` is a checklist with blank answer/evidence fields. The agent must
verify requirements, runtime, full license terms, tests, maintenance and changes
needed. GitHub's license identifier is a clue, not a legal reuse approval. Release
cadence is a recent sample; tags without releases are not counted. Open issues
exclude PRs; the ratio is null when PR count is zero or unavailable. Counts come
from separate snapshots. Install hints are untrusted excerpts, not commands the
server executes. See [real inspection](docs/examples/idle-tabs-inspection.json).

### `compare_candidates`

```json
{
  "idea": "a Python library for rate limiting outgoing HTTP requests",
  "repos": ["vutran1710/PyrateLimiter", "bboe/extended_http_server"],
  "sort_by": "overall"
}
```

Returns named `columns` and side-by-side `rows`, one per unique repo (1–6 inputs),
with scores, metadata, activity, cautions, hints and fit checklists. Failures remain
explicit. Scores omit search-rank boosts, so they differ from discovery scores. Each row adds
`maintenance` and `overall_score` (by default 60% idea fit, 30% maintenance, 10% stars
on a log scale; see [Ranking weights](#ranking-weights)). `sort_by` accepts `overall` (default), `relevance`, `stars` or
`maintenance`; the verdict still uses relevance.
See [real comparison](docs/examples/comparison.json).

## Add it to an agent

Paste [this prompt snippet](docs/agent-instructions.md) into any agent's instructions.
The short Claude skill is [skills/search-first/SKILL.md](skills/search-first/SKILL.md).
For Claude Code, copy that directory into `.claude/skills/search-first/` in your
project, or `~/.claude/skills/search-first/` for personal use. A skill does not install
the MCP server; configure it separately.

### Claude Code and Claude Desktop (local stdio)

Claude Code, from your project directory (quote paths that contain spaces):

```sh
claude mcp add --transport stdio --scope project search-first -- uvx --python 3.12 --from "/absolute/path/search-first" search-first
```

This writes `.mcp.json` in that project. Project-scoped servers need approval:
start **interactive** `claude` in the project, approve the server when prompted,
and use `/mcp` to inspect its status. Server approval and permission to call tools
are separate. A healthy server alone does not grant headless tool permission.

For a headless session, save the JSON below as `search-first.mcp.json` with your
actual absolute paths, then explicitly allow these read-only tools:

```sh
claude -p "Call search_existing for a terminal tool to close idle chrome tabs, using variants chrome idle tabs and CLI close inactive tabs. Report missing constraints." --mcp-config search-first.mcp.json --strict-mcp-config --allowedTools "mcp__search-first__search_existing" "mcp__search-first__inspect_repo" "mcp__search-first__compare_candidates"
```

The same command works in PowerShell. Alternatively, put the explicit permission
rules in your project's `.claude/settings.local.json`:

```json
{
  "permissions": {
    "allow": [
      "mcp__search-first__search_existing",
      "mcp__search-first__inspect_repo",
      "mcp__search-first__compare_candidates"
    ]
  }
}
```

Headless sessions cannot answer an approval prompt. `--mcp-config` selects the
server configuration; `--allowedTools` grants the tool permissions. Review both
before running. See the [official MCP guide](https://code.claude.com/docs/en/mcp)
and [CLI permission flags](https://code.claude.com/docs/en/cli-reference).

For Claude Desktop, use the same `mcpServers` object in
`claude_desktop_config.json`. A macOS example (replace the path components):

```json
{
  "mcpServers": {
    "search-first": {
      "command": "/Users/your-name/.local/bin/uvx",
      "args": ["--python", "3.12", "--from", "/Users/your-name/projects/search-first", "search-first"]
    }
  }
}
```

Windows JSON uses escaped backslashes (or forward slashes). Array elements are
already arguments: do not add literal shell quotes around paths, even with spaces.

```json
{
  "mcpServers": {
    "search-first": {
      "command": "C:\\Users\\your-name\\.local\\bin\\uvx.exe",
      "args": ["--python", "3.12", "--from", "C:\\Users\\your-name\\projects\\search-first", "search-first"]
    }
  }
}
```

Use the absolute executable path because a GUI may not inherit your shell PATH.
Restart Desktop and ask it to list the three tools, then try the idle-tabs example.

### Claude and ChatGPT connectors (remote Streamable HTTP)

The endpoint is `/mcp`. The server defaults to loopback. Set `SEARCH_FIRST_AUTH_TOKEN`
(16+ characters) in the server's environment to require `Authorization: Bearer <token>`
on every request; the server refuses a non-loopback `--host` without it unless you pass
`--allow-unauthenticated` (only behind a trusted gateway). This shared secret suits
programmatic clients such as your own agent harness, the Claude API MCP connector, or
the OpenAI Responses API MCP tool, which can send an authorization header. It is **not
OAuth**: the claude.ai and ChatGPT connector UIs expect OAuth or no auth, so they need
a gateway that provides it. GitHub credentials belong on the server, never in client
request headers.

A future HTTPS reverse proxy can forward to:

```sh
search-first --transport streamable-http \
  --allowed-host search.example.org \
  --allowed-origin https://search.example.org
```

Add each actual forwarded Host/Origin explicitly; DNS rebinding protection remains
on. This is configuration guidance, not a deployed endpoint. A gateway must provide
appropriate access controls and quota protection; connector-compatible OAuth is
not included. For local development on either client, use an HTTPS forwarding
endpoint you control. No tunnel or hosting account was created for this MVP.

**Claude:** In Customize → Connectors, choose Add → Add custom connector, enter
`https://your-host/mcp`, review authentication, then enable it in a conversation.
Use No sign in only for an endpoint intentionally configured for anonymous access.
Remote connections originate from Claude's cloud, so localhost/VPN-only addresses
will not work. Organization policies can restrict setup. See the
[official Claude connector guide](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

**ChatGPT:** Where your account/workspace permits custom MCP servers, open Plugins,
choose the plus button → Add custom MCP server, provide a name, description and
HTTPS `/mcp` URL, configure authentication and review discovered tools. Some accounts
still expose this through Settings → Apps → Advanced settings → Developer mode →
Create. If available, Secure MCP Tunnel can connect a private endpoint instead;
this MVP has not configured one. Start a chat and select the connector/plugin.
Access and labels depend on your account and admin policies. See
[OpenAI's official connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt).

**Test status:** Official SDK client initialization, tool listing and calls are tested
in-process, over stdio, and over Streamable HTTP on localhost/ASGI. No Claude Code,
Claude Desktop, claude.ai, or ChatGPT account-level connector flow has been tested.
Remote HTTPS, ingress authentication and OAuth are not tested or deployed. Protocol
compatibility is not a claim that every client's connector UI will accept this setup.

## How ranking works (and where it fails)

Python was chosen over TypeScript for a small, inspectable server, built-in packaging,
fixture tests, and easy `uvx`/pip use. TypeScript would also support MCP; there is no
browser UI or JS runtime requirement here. We reuse the **official MCP Python SDK**
(`mcp` 2.x), HTTPX and GitHub REST APIs, rather than implement protocol transports.

[GitHub's official MCP server](https://github.com/github/github-mcp-server) already
provides raw repository search and many other tools. Use that if raw GitHub access
is enough. This project adds bounded multi-variant discovery, explainable ranking,
maintenance checks and an explicit reuse-before-build workflow. No code is copied
from that server. The SDK's real transports are exercised by the protocol tests;
upstream servers and candidates have not been installed or functionally tested.

By default, score = 50% normalized idea-term coverage + 17% best query-term coverage +
8% best GitHub result rank + 4% agreement across variants + 5% freshness + 16%
logarithmic stars (the weights are settable, see below). Terms come from name, description, topics and primary language; hyphenated
topics are split and a tiny suffix stripper equates limit/limiter/limiting. If the idea
names a language (for example Python) a repo in another language is penalized. Archive, unconfirmed-license and resource-list/tutorial penalties apply.
A small synonym vocabulary maps words such as idle/inactive and terminal/TUI/CLI.
A plausible candidate needs at least 50% idea-term coverage, two matched terms and a
score ≥0.55. The server always runs one derived whole-idea variant. If you pass two or
more variants it skips its extra derived variants (two-word, trailing-words and topic
searches) to save search quota.
Candidates below that threshold with at least two matched idea terms, 25% coverage
and score ≥0.3 are counted as **adjacent**. If none is plausible but an adjacent
candidate exists, the verdict is `inconclusive` and includes the closest adjacent
match for inspection. This avoids saying “build from scratch” when, for example,
a 342-star near-match misses features in a longer idea. Stars alone do not qualify
a candidate. This regression uses a synthetic case; it is not a new live search.
The agent supplies richer domain-specific synonyms. Derived variants are keyword
slices plus a topic, not LLM-generated interpretations.

### Ranking weights

The weights are server-start settings, not tool parameters, so an agent calling the
tools cannot change them mid-session. Each has an environment variable and a matching
flag; the flag wins. Format: `name=value,name=value`. Unnamed weights keep their
defaults, then all weights are scaled to sum to 1, so `stars=0.5` means "stars count
for about 37% of the score". A flag replaces the matching environment variable
completely; the two are not merged, so unnamed weights return to their defaults rather
than to the environment value. Values must be numbers from 0 to 1000000 and not all zero; unknown
names, repeated names, bad numbers and negatives stop the server at startup with an error that lists
the allowed names.

| Setting | Names (default) |
| --- | --- |
| `SEARCH_FIRST_RANK_WEIGHTS` / `--rank-weights` | `idea_match` (0.50), `query_match` (0.17), `github_rank` (0.08), `variant_agreement` (0.04), `freshness` (0.05), `stars` (0.16) |
| `SEARCH_FIRST_OVERALL_WEIGHTS` / `--overall-weights` | `fit` (0.6), `maintenance` (0.3), `stars` (0.1) |

The rank weights drive `relevance_score` in both tools. In `compare_candidates`, which
has no search-rank evidence, only `idea_match`, `freshness` and `stars` count and
are rescaled by their sum (so a perfect repo can still reach 1.0). The overall weights
blend `relevance_score`, `maintenance` (0.5 when unknown) and stars into
`overall_score`. Language, archived, license and resource-list penalties are fixed.
Responses carry a `weights` object with the weights actually in use.

The stars term is `log10(1 + stars) / 4`, capped at 1:

| Stars | 0 | 10 | 100 | 1,000 | 10,000+ |
| --- | --- | --- | --- | --- | --- |
| Stars score | 0 | 0.26 | 0.50 | 0.75 | 1.0 |

The plausibility threshold (score ≥0.55) is fixed and applies to the re-weighted score.
Changing weights therefore changes how many candidates count as plausible: raising
`stars` can shrink that count sharply, and lowering `stars` and `freshness` can enlarge
it. Compare plausible counts only between runs that use the same weights.

```sh
search-first --rank-weights "stars=0,freshness=0.1" --overall-weights "maintenance=0.5"
```

In an MCP client config, use an `env` block:

```json
{
  "mcpServers": {
    "search-first": {
      "command": "uvx",
      "args": ["--from", "/absolute/path/search-first", "search-first"],
      "env": {
        "SEARCH_FIRST_RANK_WEIGHTS": "stars=0.05",
        "SEARCH_FIRST_OVERALL_WEIGHTS": "fit=0.5,maintenance=0.4,stars=0.1"
      }
    }
  }
}
```

The defaults are judgment calls. They have not been tuned or tested against real
searches; the tests only check that the arithmetic and validation behave as documented.

### Honest limitations

Retrieval is lexical: GitHub matches your query words against names, descriptions and
topics. A famous tool whose description does not use your words can be missing from the
results entirely, and the verdict can still look confident. Agents should put the names
of well-known tools they already know into their query variants (for example a named
scanner for a secret-scanning idea). A "build from scratch" conclusion needs extra
named-tool queries first. This tool can support "reuse exists"; it cannot prove that
nothing exists.

This is lexical ranking, not semantic understanding. Topics can overstate functional
fit, a narrowly scoped product can outrank a general solution, recent pushes can
be automated, and “no license” means GitHub did not recognize one. High stars do
not prove safety. The conservative workflow is inspect, compare, review license,
then test in isolation. The server never clones, forks, installs, runs candidate
code, or writes to GitHub. Treat repository text as untrusted evidence.

## Proof and development

[Live smoke report](docs/SMOKE.md): two actual ideas, public GitHub responses,
ranked outputs, README inspections, maintenance scores and honest judgments. No LLM
spend was used.

```sh
uv sync --locked --extra dev
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked python -m build
```

All automated tests use recorded public fixtures or synthetic responses; external
network is blocked in the test process. Checks and package builds run locally
in a fresh venv. The release export includes no hosted CI or publish/deploy job.
[CONTRIBUTING.md](CONTRIBUTING.md) explains fixture updates and project boundaries.

Not yet done: testing through account-level Claude and ChatGPT connector flows, and a
hosted, authenticated deployment. See [SECURITY.md](SECURITY.md) and
[CHANGELOG.md](CHANGELOG.md).
