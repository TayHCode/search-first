# Security

search-first reads public GitHub data and never executes discovered code, so the main
risks are credential handling, prompt injection through repository text, and abuse of
an exposed HTTP endpoint.

- Report vulnerabilities privately through GitHub's "Report a vulnerability" (security
  advisories) on this repository rather than a public issue.
- A `GITHUB_TOKEN`, if used, stays in the server process. It is scrubbed from cache
  entries and tool output. Use a token with public read access only.
- Repository descriptions, READMEs and commit messages are untrusted data returned to
  the calling agent. They are never executed by the server and must not be followed as
  instructions.
- The HTTP transport listens on loopback by default. Set `SEARCH_FIRST_AUTH_TOKEN` before
  binding another interface; it is a shared secret, not OAuth. Put a real gateway in
  front of any public deployment to enforce per-user access and quotas.
