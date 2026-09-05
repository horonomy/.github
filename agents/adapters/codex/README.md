# Codex adapter (HORO-508)

Gives Codex the same canonical Horonom governance and shared skills as the
Claude adapter (HORO-507/`.claude/`), without touching the user's global
`~/.codex` and without a second hand-maintained policy corpus.

## Commands

```bash
# Write/refresh $HORONOM_WORKSPACE_ROOT/.codex/config.toml.
python3 agents/adapters/codex/horonom_codex.py sync --root /path/to/workspace

# Report readiness without writing anything.
python3 agents/adapters/codex/horonom_codex.py status

# Exec the real `codex` binary with the scoped environment.
agents/adapters/codex/horonom-codex launch -- <codex args...>
```

## Mechanism

Codex reads its config from `$CODEX_HOME` (default `~/.codex`). `launch`
execs the real `codex` binary with `CODEX_HOME` set to
`$HORONOM_WORKSPACE_ROOT/.codex` **for that process only** — every other
`codex` invocation on the machine, in any other project, is completely
unaffected. `.codex/config.toml` sets `project_root_markers = [".horonom"]`
so Codex's ancestor-directory walk for project instructions extends up to
`$HORONOM_WORKSPACE_ROOT` (marked by the `.horonom/` directory
`scripts/horonom_workspace.py` already creates) instead of stopping at a
product repo's own git root — the same effective reach the Claude adapter
gets from reading the workspace root's generated `AGENTS.md`.

**Verified against the real, installed Codex CLI (0.147.0)**:
`codex doctor` run with `CODEX_HOME` pointed at a scoped test directory
confirms `CODEX_HOME` and `config.toml` both resolve to the scoped path,
and `config.toml parse: ok`; `codex --strict-config doctor` produced no
"unrecognized field" warning for `project_root_markers`, confirming it's a
field this Codex version actually recognizes. `launch -- --version`
exec's the real binary and returns `codex-cli 0.147.0` (exit 0).

## Fails clearly, never silently stale

`launch` refuses to run if `.codex/config.toml` is missing or doesn't
match what `sync` would currently produce (HORO-508 AC: "failure to
find/update governance fails clearly rather than silently running stale
company policy") — run `sync` first.

## Deliberately not built on the personal `ca-codex`/profile-overlay tooling

Some engineers may have a general-purpose directory-scoped Codex profile
system installed locally (`ca-codex`, `~/.coding-agent-profiles`). This
adapter is intentionally self-contained inside `horonomy/.github` instead,
so it behaves the same way for every engineer and in CI, with no
dependency on personal machine tooling.

## Security

- Refuses to set `CODEX_HOME` to the user's real global `~/.codex` — a
  misconfigured `$HORONOM_WORKSPACE_ROOT` (e.g. accidentally `$HOME`
  itself) can never silently defeat the scoping this adapter exists to
  provide.
- Refuses to launch if `codex` resolves back to this adapter itself
  (self-exec guard, matching the pattern in the personal `ca-codex` tool).
- No secret or machine-local private value is ever written into
  `config.toml` — it contains exactly one generated line
  (`project_root_markers`).

## Tests

`agents/adapters/codex/test_horonom_codex.py` (stdlib `unittest`, run via
`python3 -m unittest discover -s agents/adapters/codex`) covers root
resolution, the global-Codex-home aliasing guard, sync idempotency and the
generated-file conflict guard, drift detection after a governance version
bump, the self-exec/not-on-PATH guards, and the `--` stripping fix found
during manual end-to-end testing against the real binary.
