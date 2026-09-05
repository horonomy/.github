# `horonom doctor` (HORO-510)

Detects drift between a repo/workspace's actual governance adoption and
what's expected — mechanically, not by assuming config is wired correctly.
Implementation: `scripts/doctor.py` (CLI) + `scripts/doctor_checks.py`
(individual checks, independently unit-tested).

## Usage

```bash
# Check a repo checkout.
python3 scripts/doctor.py --repo /path/to/some/horonom-repo

# Check the local agent workspace.
python3 scripts/doctor.py --workspace-root /path/to/workspace
# (or just $HORONOM_WORKSPACE_ROOT, picked up automatically)

# Both, plus public-release adoption for a specific product.
python3 scripts/doctor.py --repo . --workspace-root "$HORONOM_WORKSPACE_ROOT" --product circinus

# Machine-readable.
python3 scripts/doctor.py --repo . --json
```

Exit code is `1` when the overall verdict is `FAIL`, `0` otherwise — a
`WARN`-only result doesn't fail a CI gate, since most `WARN`s (an
unsynced Codex adapter, a missing `CONTRIBUTING.md`) are things to fix,
not correctness defects.

## States

`PASS` / `WARN` / `FAIL` / `NOT_APPLICABLE`, same shape as the Public
Release Surface Contract's classification philosophy. Overall status is
the worst of `FAIL`/`WARN` present; `NOT_APPLICABLE` never makes the
overall result look worse — a repo that hasn't adopted governance yet, or
a workspace-only run with no `--repo`, correctly reports `NOT_APPLICABLE`
for the checks that don't apply rather than `FAIL`.

## Checks

| Check | Applies to | What it catches |
|---|---|---|
| `skill_adapter_markers` | `--repo` | A projected `.claude/skills/**/SKILL.md` or `.codex/skills/*.md` missing its generated-provenance marker — someone hand-edited a file that's supposed to be regenerated only |
| `contributing_present` / `pr_template_present` / `claude_entrypoint_present` | `--repo` | Missing repo-development baseline files |
| `remote_sanity` | `--repo` | No remote pointing at the expected GitHub org — never assumes `origin` is canonical |
| `workspace_bootstrap` | `--workspace-root` | Not bootstrapped, or `CLAUDE.md`/`AGENTS.md`/manifest drift (delegates to `scripts/horonom_workspace.py status`) |
| `codex_adapter` | `--workspace-root` | Codex adapter config missing or stale (delegates to `agents/adapters/codex/horonom_codex.py status`) — `WARN`, not `FAIL`, since Codex adoption is optional |
| `public_release_adoption` | `--repo --product <name>` | Delegates to `scripts/public_release_reconcile.py`; maps its 7 states onto PASS/WARN/FAIL/NOT_APPLICABLE — **`NOT_YET_PUBLIC` and `DEFERRED` map to `NOT_APPLICABLE`, never `PASS`**, so doctor can never report a not-yet-public product's surface as "complete" |
| `no_secrets_in_generated_files` | both | Scans a fixed, named list of generated files (never arbitrary repo content) for credential-shaped patterns, reusing `scripts/generate_company_metadata.py`'s existing guard regexes |

## Bounded, read-only

Every check reads a fixed, named set of files or runs one bounded command
(`git remote -v`) — none of them walk or scan a repo's full tree. Doctor
never writes anything; where a check found a problem its tool can fix, the
`fix` field names that tool's own `sync`/`bootstrap` command rather than
doctor attempting a repair itself, per this ticket's own "any `--fix`
behavior must be explicit and bounded" scope note (no such mode exists
yet).

## Tests

`scripts/test_doctor.py` (stdlib `unittest`) includes: an intentionally
broken adoption fixture (a hand-edited generated skill file → `FAIL`) and
a healthy-workspace fixture (a freshly bootstrapped workspace → `PASS`),
satisfying this ticket's AC directly; a regex-injection guard test for
`--expected-org`; and dedicated tests proving `NOT_YET_PUBLIC`/`DEFERRED`
never map to `PASS`. Real (non-mocked) runs against this repo itself, a
freshly bootstrapped workspace, and the adjacent `circinus` checkout are
recorded in the HORO-510 PR as additional evidence.
