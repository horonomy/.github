# Workspace bootstrap (HORO-506)

`scripts/horonom_workspace.py`, run from a `horonomy/.github` checkout,
projects a navigation entrypoint and a company/product directory skeleton
into `$HORONOM_WORKSPACE_ROOT` — see `governance/workspace/manifest.yaml`
for the repo list and [ADR-0005](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0005-horonom-governance-and-agent-workspace-architecture.md)
decisions #3–#4 for why this is generated-projection, not a symlink or a
second hand-maintained source.

## Commands

```bash
# First-time setup. $HORONOM_WORKSPACE_ROOT must be set, or pass --root.
python3 scripts/horonom_workspace.py bootstrap --root /path/to/workspace

# Re-run any time — idempotent, safe to call repeatedly (e.g. after a
# governance update lands in .github/main).
python3 scripts/horonom_workspace.py sync

# Report state without writing anything.
python3 scripts/horonom_workspace.py status
python3 scripts/horonom_workspace.py status --json
```

There is deliberately no third "default" workspace location — the tool
refuses to run without `$HORONOM_WORKSPACE_ROOT` or `--root`, so a
personal path is never baked into a script or a generated file.

## What it does and doesn't do

- Writes `$HORONOM_WORKSPACE_ROOT/CLAUDE.md` and `AGENTS.md` (generated,
  provenance-stamped, safe to regenerate).
- Creates the `company/` and `products/` directory skeleton and
  `.horonom/state.json` (bootstrap/adoption state — not policy).
- **Never clones a repo automatically.** For each manifest entry not
  already present as a git checkout at its target path, it prints the
  `git clone` command to run — cloning a dozen-plus private repos
  unattended is exactly the kind of surprising, hard-to-undo action this
  campaign's autonomous-execution rules ask an agent to avoid. If a repo
  is already checked out elsewhere (e.g. adopted in place per
  `governance/workspace/autonomous-execution.md`), symlink or clone it to
  the manifest path yourself — the tool only reports presence, it never
  moves or deletes an existing checkout.
- **Never overwrites a hand-authored `CLAUDE.md`/`AGENTS.md`.** A file
  without the `<!-- horonom:generated -->` marker is left untouched
  (`skipped-conflict`, non-zero exit) unless `--force` is passed.
- Product repos never depend on any of this — see `governance/README.md`'s
  standalone-clone boundary.

## Tests

`scripts/test_horonom_workspace.py` (stdlib `unittest`, run via
`python3 -m unittest discover -s scripts` from the repo root) covers root
resolution (no hard-coded default), manifest validation (unsafe names,
duplicates, unknown categories), bootstrap idempotency, the
generated-file-conflict guard, path-traversal containment, and drift
detection in `status`.
