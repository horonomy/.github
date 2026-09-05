# Claude adapter (HORO-507)

Makes Claude Code consume Horonom company/product/repo governance without
duplication. Unlike the Codex adapter (HORO-508), this one needs **no
launcher or scoped-home mechanism** — Claude Code already discovers
`CLAUDE.md` natively by reading the one in the repo you're working in, so
"the Claude adapter" is the sum of three pieces built elsewhere in this
campaign, working together:

1. **`CLAUDE.md` / `.claude/CLAUDE.md`** — a small generated pointer block
   (`scripts/repo_bootstrap.py adopt`, HORO-511) inserted into the repo's
   own file, preserving all existing repo-specific content. This is what
   a Claude Code session actually reads first in an adopted repo.
2. **`$HORONOM_WORKSPACE_ROOT/CLAUDE.md`/`AGENTS.md`** — the workspace-root
   navigation entrypoint (`scripts/horonom_workspace.py`, HORO-506), read
   when working at the workspace root rather than inside one repo.
3. **`.claude/skills/`** — the shared skill library (HORO-509), now also
   projected **cross-repo**: `scripts/repo_bootstrap.py adopt` calls
   `agents/common/project_skills.py`'s `build_projections(dest_root=...)`
   against the target repo, so an adopted repo gets the exact same
   canonical skill content Claude reads from `horonomy/.github` itself —
   never a second hand-copied implementation (this file's own addition to
   `project_skills.py`: a `dest_root` parameter, defaulting to
   self-projection).

## Diagnostic / probe

`horonom doctor --repo <path>` (HORO-510) **is** the diagnostic this
ticket's scope asks for ("a diagnostic/probe showing exactly which
Horonom governance version/context is expected for a repo") — it already
reports the adopted `governance_version`, whether the `.claude/skills/`
projection matches canonical content, and whether the repo's `CLAUDE.md`
block is current, stale, or hand-edited. No second probe tool was built;
duplicating one would violate the same "don't duplicate canonical
content" rule this adapter itself follows.

## Precedence — real, not asserted

Company → Product → Repository: a repo's own `CLAUDE.md` content outside
the generated block can add to or strengthen a rule, never weaken a
non-waivable one (`governance/README.md`). This is provable, not just
documented — see "Real verification" below.

## Real verification (HORO-507 AC: "a real Claude Code session in at
least two representative Horonom repos")

Two repos were adopted for real (HORO-511) and now carry the full Claude
adapter (pointer block + projected skills):

- **`horonomy/official-website`** — [official-website#84](https://github.com/horonomy/official-website/pull/84)
- **`horonomy/internal-docs`** — [internal-docs#60](https://github.com/horonomy/internal-docs/pull/60)

Live proof from this very campaign session: opening a worktree of either
adopted repo made this Claude Code session's own context automatically
include that repo's `.claude/CLAUDE.md` (org baseline pointer + the
repo's own real content — e.g. `official-website`'s build commands and
its product-narrative-hierarchy rules — both present, neither duplicated
nor lost) and list its five projected skills as available via the `Skill`
tool. That is the "demonstrably follows company + narrower repo/product
guidance" AC, observed directly rather than asserted.

## A real bug this verification found

The first HORO-511 adoption of both repos ran from worktree directories
named `<repo>-wt-HORO-511`, and used that directory's basename as the
embedded repo name — so `official-website`'s own `CLAUDE.md` said
`horonomy/official-website-wt-HORO-511`. `scripts/repo_bootstrap.py` now
resolves the canonical name from the target repo's `git remote -v` output
instead of the local directory name (`resolve_repo_name()`), with tests
covering HTTPS/SSH remote URL shapes and the fallback when no remote
exists. Both adopted repos were corrected (see the PRs above).
