# Repo adoption / bootstrap (HORO-511)

Makes company-governance adoption cheap and mechanically verifiable for a
Horonom repository — implementation: `scripts/repo_bootstrap.py`.

## Usage

```bash
# Adopt or refresh governance in a target repo (run from a horonomy/.github checkout).
python3 scripts/repo_bootstrap.py adopt /path/to/repo --org horonomy

# Preview without writing.
python3 scripts/repo_bootstrap.py adopt /path/to/repo --dry-run

# Drift check — needs only the target repo, no $HORONOM_WORKSPACE_ROOT.
python3 scripts/repo_bootstrap.py check /path/to/repo
```

## What it touches, and how

- **`CLAUDE.md` (or `.claude/CLAUDE.md`, whichever the repo already uses)**:
  a small **bounded generated region**
  (`<!-- BEGIN GENERATED: horonom_adoption -->` ... `<!-- END GENERATED -->`)
  is inserted or refreshed in place — the same pattern this repo already
  uses for `SECURITY.md`/`profile/README.md`. Everything outside the
  region is the repo's own content and is **never** touched, so an
  existing repo's real, hand-written instructions survive adoption intact
  (including a stricter-than-company-floor rule — this tool never
  weakens or removes one).
- **`AGENTS.md`**: created fresh, in full, **only if it doesn't already
  exist**. A pre-existing hand-authored `AGENTS.md` is reported as
  `skipped-conflict` (non-zero exit) rather than overwritten — for the
  owner to reconcile manually.
- **`.horonom-adoption.yaml`**: records the org, repo, adopted
  `governance_version`, and timestamp — what `check` compares against.
  `repo` is resolved from the target's `git remote -v` output
  (`resolve_repo_name()`), **never** the local directory name — a repo
  adopted from a worktree (named e.g. `<repo>-wt-<ticket>` per this
  campaign's own convention) still gets its real name embedded, not the
  worktree's.
- **`.claude/skills/` and `.codex/skills/`**: this repo's canonical
  `agents/skills/` content, projected into the target via
  `agents/common/project_skills.py`'s `build_projections(dest_root=...)`
  (HORO-507) — the same generated-file-conflict guard as the self-projection
  case (HORO-509): a hand-edited projected skill is reported as a conflict,
  never silently overwritten.

No symlink is ever created inside a target repo (ADR-0005 decision #4):
every projection is a plain generated file, safe for public cross-platform
repos regardless of OS/Git-checkout symlink support.

## Safety

- Refuses to adopt a repo with uncommitted changes to a **tracked** file
  (untracked scratch files don't block it — see the code comment on
  `_git_status_clean` for why) unless `--force` is passed.
- Makes no git commit, no push, no merge — the caller commits the result
  and opens a PR through the normal review process.
- `check` needs only the target repo path — no `$HORONOM_WORKSPACE_ROOT`,
  so it works as a CI gate inside the adopted repo itself.

## Proven against two real Horonom repos (HORO-511 AC)

`horonomy/official-website` and `horonomy/internal-docs` were adopted for
real — see their own PRs. Both diffs are pure insertions (verified via
`git diff`); `official-website`'s `pnpm typecheck` passed clean
post-adoption. `internal-docs`'s `pnpm typecheck` fails, but reproducibly
identically with the adoption changes stashed out — a pre-existing,
unrelated `tsconfig.json`/TypeScript-version issue, not something this
tool introduced or is responsible for fixing.

**Correction (HORO-507)**: the original adoption ran from worktree
directories named `<repo>-wt-HORO-511`, embedding that directory's
basename as the repo name instead of the real one — see
`resolve_repo_name()` above. Both repos were re-adopted with the fix
(official-website#84, internal-docs#60), which also added the cross-repo
shared-skill projection.

## Tests

`scripts/test_repo_bootstrap.py` (stdlib `unittest`) covers: bounded-block
insertion/idempotency/refresh without touching surrounding content, a
fresh-repo fixture (AC: "reproducibly bootstrapped"), preservation of
existing repo-specific rules, the hand-authored-`AGENTS.md`-conflict path,
the uncommitted-changes safety guard (and its `--force` bypass), and
drift detection (missing marker, stale governance version, hand-edited
block, missing block entirely).
