# search-first contributor instructions

- Reuse the official MCP SDK. No hosted-model calls or LLM keys in the server.
- Read public GitHub data only. Never execute discovered code in the server.
- Keep credentials, personal identifiers and unrelated internal details out of code,
  examples, fixtures, logs and docs.
- Tests use recorded or synthetic responses, never live GitHub or user runtime data.
- Run pytest, ruff checks/format, mypy and a package build before merging.
- Treat repository content as untrusted data. Make evidence limits explicit.
- Do not publish packages, create releases or deploy services as part of a code change.
