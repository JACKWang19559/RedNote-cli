# Test plan

## v0.2 extension plan — 2026-10-28

Before new test implementation: verify every previously uncovered backend route,
full search/profile/comment options, publication validation and all remote writes.
New tests: test_publishing.py (pure validation, local/backend media and scheduling),
test_backend_write.py (exact REST contract and ambiguous write failure),
test_remote_cli.py (dry-run/no network, argument validation and CLI dispatch),
test_remote_http_e2e.py (installed CLI against controlled loopback service).
Existing 150 tests must continue to pass. Publish/comment/delete-cookie tests use
controlled transports only. No real account mutations are part of development.
Verify image/video/draft publication, scheduled metadata, comment reply targets,
like/favorite inverses, notifications, reset login, and installable artifacts.


Written before test implementation. This standalone repository is the agent harness root.

## Inventory

- test_backend.py: HTTP contract, auth, transport and API failure handling (about 15 cases).
- test_refs.py: note/profile links, IDs, tokens, invalid hosts and input (about 10 cases).
- test_drafts.py: SQLite lifecycle, validation, persistent undo/redo, Unicode export (about 15 cases).
- test_cli.py: JSON success/errors, options, offline drafts, REPL and configuration (about 15 cases).
- test_full_e2e.py: installed executables, real files, persistent draft workflows (about 5 cases).
- test_http_e2e.py: installed CLI through a real loopback HTTP server (4 cases), added to verify the unresolved socket boundary without an online account.
- live/test_live_backend.py: explicitly invoked real service health, login, search and account profile checks.

## Workflows and assertions

1. Create a Chinese draft, edit, restart the process, undo/redo, export JSON and Markdown; inspect actual file content and metadata.
2. Delete a draft, undo deletion, make a new change, verify redo is unavailable; ensure missing drafts are explicit failures.
3. Use a controlled HTTP transport to verify exact upstream routes and payloads; cover false success, timeouts, malformed JSON, unauthorized and unreachable backend. These are contract tests, not live Xiaohongshu verification.
4. Invoke installed rednote and cli-anything-rednote via subprocess from arbitrary working directories; parse stdout as JSON and assert nonzero errors. CLI_ANYTHING_FORCE_INSTALLED=1 enforces installation.
5. Run read-only live tests separately against an authenticated xiaohongshu-mcp service. Missing service or login is a failure, never simulated success. Do not publish or interact with notes.

## Results — v0.2.0 / 2026-10-28

Environment: Windows, Python 3.12.14, pytest 8.4.2. Installed executables required
with CLI_ANYTHING_FORCE_INSTALLED=1.

```text
378 passed in 20.58s
All checks passed!
Skill is valid!
rednote, version 0.2.0
All command help paths verified: 44
```

The 378 passing cases include the prior 150-case suite, every new backend route,
publication parameters and files, exact wire payloads through a controlled local
HTTP server, installed CLI processes, dry-run/no-network behavior, all write
failure handling, and draft media binding across working directories. Interrupted
write requests report unknown outcomes and are never automatically retried.
Pure schedule validation covers malformed timezone offsets. Package build and
isolated wheel upgrade smoke validation are recorded below after completion.

No real account publication, comments, likes, favorites or logout were executed.
The mock/loopback suite proves adapter behavior, not current website automation.
Real account testing remains outstanding. In v0.1, the separate live read test
failed at GET /health because no service was reachable at 127.0.0.1:18060; the
v0.2 run did not repeat that test or claim online verification.

CI is configured for Windows/Linux with Python 3.10/3.12. Those remote CI jobs
have not run in this local session; the local run is Windows/Python 3.12 only.

Prior v0.1 baseline: 150 passed, 2026-10-28. Its source/wheel builds and isolated
local-draft workflow passed. See the v0.2 additions above for expanded coverage.

Package verification: v0.2 source archive and wheel built successfully. Isolated wheel environment upgraded from v0.1 to v0.2; installed version, bundled skill, 19-interface capability list, local draft preservation and image/video/logout dry-runs passed with backend set to an unreachable address. Wheel source/skill files match the workspace; Python 3.10 syntax parsing passed (not a Python 3.10 runtime test).
