# New-repository checklist: scaffold mechanics

This complements the [New-repository checklist](CONTRIBUTING.md#new-repository-checklist)
in `CONTRIBUTING.md`, which covers naming, canonical domains, and Product
Registry rules. This file covers the other half: CI shape, CODEOWNERS,
default branch, branch protection, and where a repo-level `.claude/CLAUDE.md`
goes. It's grounded in what `horonomy/circinus`, `horonomy/ophiuchus`, and
`horonomy/official-website` actually do today — checked directly, not
aspirational.

## CI workflow shape

Every Horonom repo checked runs a `.github/workflows/ci.yml` gated on
`pull_request` and `push` to `main`, with the same broad shape regardless of
language: **lint → type-check → test → build**, roughly in that order. The
exact steps differ by toolchain:

| Repo | Stack | Gates (in order) |
|---|---|---|
| `circinus` | Python / uv | `ruff check` → `ruff format --check` → `mypy` → `pytest --cov` |
| `ophiuchus` | Python / uv | `ruff check` + `ruff format --check` → `ty check` → unit tests → integration tests (Postgres service container) → build+install-from-clean-venv → smoke tests |
| `official-website` | Node / pnpm | `pnpm typecheck` → `pnpm check:registry` (repo-specific content validation) → `pnpm build` → `pnpm check:claims` (post-build content check) |

Takeaways for a new repo, not a prescription to copy verbatim:

- Lint and type-check run before tests — fail fast on the cheap checks first.
- A build/packaging gate exists where the language has one (Python wheel
  build-and-install, Node build). Don't skip it just because tests pass.
- `ophiuchus` pins third-party actions by commit SHA rather than a mutable
  tag (`actions/checkout@<sha> # v7.0.1`); `circinus` and `official-website`'s
  older `build` job still use tags. New workflow jobs should pin by SHA —
  that's the direction the org is moving, not a settled universal rule yet.
- If the repo needs a database or other service for integration tests, use a
  GitHub Actions service container scoped to the job, with a dedicated
  `*_TEST_DATABASE_URL`-style env var distinct from any production/deployed
  connection string name, exactly as `ophiuchus` does — this prevents a test
  suite from ever pointing at real data by variable-name collision.

## CODEOWNERS

A `.github/CODEOWNERS` file **is** the existing convention — all three repos
checked have one, each a single blanket rule:

```
* @Chisanan232
```

None of these repos are large enough yet to need a per-directory split. A new
repo should add a minimal `.github/CODEOWNERS` with the same blanket-owner
pattern; per-directory ownership is optional and only worth adding once a
repo actually has multiple maintainers with distinct areas.

Note from `ophiuchus`'s copy: on a private repo without GitHub's paid tier,
branch protection (and therefore CODEOWNERS-driven automatic reviewer
assignment) is unavailable — the API returns 403 ("Upgrade to GitHub Pro or
make this repository public"). Where that applies, CODEOWNERS is advisory
only (it records intent) rather than enforced, and that should be said
explicitly in the file's own header comment, as `ophiuchus` does, so nobody
assumes enforcement that isn't there.

## Default branch and branch protection

- Default branch is **`main`** in every repo checked (`circinus`, `ophiuchus`,
  `official-website`). Use `main` for a new repo.
- Recommendation: **PR-only to `main`, no direct push.** This matches what
  `circinus`'s and `ophiuchus`'s own `.claude/CLAUDE.md` files state as their
  convention. Where the repo's GitHub plan doesn't offer branch protection
  (both of those repos are on a tier where the branch-protection API 403s),
  this is enforced by discipline, not tooling — treat it as a hard rule
  regardless, and say so in the repo's own CLAUDE.md the way those two do.

### Merge strategy — repos disagree, said explicitly

Existing repos do **not** agree on merge strategy, and this checklist isn't
picking a winner:

- `circinus` and `ophiuchus` both have squash merge **disabled** — they use
  rebase-merge or merge-commit specifically to preserve individual atomic
  commits in `main`'s history.
- `CONTRIBUTING.md`'s own PR section says merge strategy is picked by "the
  reviewer or assignee" per repo, "typically squash or rebase."
- `horonomy/.github` itself (this repo) has used a squash-style merge
  (single commit per PR) in its own history.

A new repo should pick one strategy deliberately at setup time and record it
in the repo's own CLAUDE.md (see below) rather than leaving it to whoever
merges the first PR to decide implicitly.

## Repo-level `.claude/CLAUDE.md`

Every Horonom repo checked (`circinus`, `ophiuchus`, `official-website`) has
its own `.claude/CLAUDE.md` (or root `CLAUDE.md`), and each one opens the same
way: point at the org baseline first, then state only what's specific to that
repo. For example, `circinus`'s opens with:

> Read the org baseline first:
> [horonomy/.github/CLAUDE.md](https://github.com/horonomy/.github/blob/main/CLAUDE.md).
> It owns the universal rules — company context, repo map, Jira project
> `HORO`, and the documentation conventions. This file adds only what is true
> of *this* repository and does not repeat the baseline.

A new repo should add `.claude/CLAUDE.md` (or a root `CLAUDE.md` if the repo
has no other reason for a `.claude/` directory) with the same structure:

1. A pointer to `horonomy/.github/CLAUDE.md` as the authoritative baseline.
2. An explicit precedence statement — the narrower, repo-specific file wins
   on conflict (all three repos checked state this the same way).
3. Only what's actually specific to the new repo: what it is, its stack,
   its test/build commands, any commit/branch/merge conventions that add to
   or narrow the baseline (like the merge-strategy choice above), and a
   "never commit" list for anything repo-specific (real credentials, local
   runtime state, etc.).

Don't restate command-format strings (commit message shape, branch naming,
PR title format) from `horonomy/.github/CONTRIBUTING.md` — reference it
instead. Every repo checked treats restating those as something that would
drift out of sync with the source of truth.
